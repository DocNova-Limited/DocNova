"""DocNova invoices for direct sales (hospital colleagues, WhatsApp, in person).

The owner creates an invoice in /admin. The customer gets an email with a private invoice page
(/invoice/<token>) showing DocNova's bank details (DOCNOVA_BANK_DETAILS, set only in the hosting
environment) and, when Stripe is live, a "Pay by card" button.
When the invoice is paid (card: automatically; bank transfer or cash: the owner clicks "Mark paid"),
a normal paid order is recorded, so purchases and DocNova Rounds work exactly like website orders.
Past sales (e.g. from Zoho Invoice) can be recorded as already paid, without emailing anyone.
"""
import html, json, os, re, secrets, time
import shop, loyalty

PREFIX, FIRST = 'DN-INV-', 1001
MAX_LINES, MAX_QTY = 30, 50

def init_db():
    with shop._lock, shop.db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS invoices (number TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, created_at INTEGER NOT NULL,
            issue_date INTEGER NOT NULL, due_at INTEGER, customer_name TEXT NOT NULL, email TEXT, phone TEXT, address TEXT,
            lines_json TEXT NOT NULL, subtotal INTEGER NOT NULL, discount INTEGER NOT NULL DEFAULT 0, delivery INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL, note TEXT, status TEXT NOT NULL, paid_at INTEGER, paid_method TEXT, order_number TEXT,
            pay_session TEXT, sent_at INTEGER, source TEXT DEFAULT 'admin')''')

def _get(number=None, token=None):
    with shop.db() as conn:
        r = conn.execute('SELECT * FROM invoices WHERE ' + ('number=?' if number else 'token=?'), (number or token,)).fetchone()
    return dict(r) if r else None

def get(number):
    return _get(number=number)

def by_token(token):
    token = str(token or '')
    return _get(token=token) if re.fullmatch(r'[A-Za-z0-9_-]{20,80}', token) else None

def listing():
    with shop.db() as conn:
        rows = [dict(r) for r in conn.execute('SELECT i.number, i.issue_date, i.due_at, i.customer_name, i.email, i.total, i.status, i.paid_at, i.paid_method, '
                                              'i.order_number, i.sent_at, i.source, i.token, o.status AS order_status, '
                                              '(SELECT COALESCE(SUM(amount),0) FROM returns r WHERE r.order_number=i.order_number) AS refunded '
                                              'FROM invoices i LEFT JOIN orders o ON o.order_number=i.order_number ORDER BY i.created_at DESC LIMIT 500')]
    return rows

def catalogue_for_admin():
    return sorted(({'id': p['id'], 'name': p['name'], 'category': p['category'], 'pence': p['pence'], 'device': p['device']}
                   for p in shop.catalogue().values()), key=lambda p: (p['device'], p['category'], p['name']))

def _pence(value, label):
    try:
        v = round(float(value or 0) * 100)
    except (TypeError, ValueError):
        raise ValueError(label + ' must be an amount in pounds, e.g. 5 or 4.95.')
    if v < 0 or v > 1000000:
        raise ValueError(label + ' looks wrong.')
    return v

def _date(value):
    if not value:
        return None
    try:
        return int(time.mktime(time.strptime(str(value)[:10], '%Y-%m-%d'))) + 12 * 3600
    except ValueError:
        raise ValueError('Please give the date as YYYY-MM-DD.')

def create(data):
    """data: name, email, phone, address, lines[{id,size,qty,price?}], discount, delivery, note, due_days,
    already_paid, paid_method, paid_date, send."""
    name = ' '.join(str(data.get('name') or '').split())[:100]
    if not name:
        raise ValueError('Please enter the customer’s name.')
    raw_email = str(data.get('email') or '').strip()
    email = loyalty.norm(raw_email)
    if raw_email and not email:
        raise ValueError('Please check the email address.')
    raw = data.get('lines')
    if not isinstance(raw, list) or not raw or len(raw) > MAX_LINES:
        raise ValueError('Please add at least one item.')
    cat, lines = shop.catalogue(), []
    for l in raw:
        p = cat.get(str((l or {}).get('id', '')))
        if not p:
            raise ValueError('Please choose a product for every line.')
        size = str(l.get('size') or ('Standard' if p['device'] else ''))
        if p['device']:
            size = 'Standard'
        elif size not in shop.SIZES:
            raise ValueError('Please choose a size for ' + p['name'] + '.')
        try:
            qty = int(l.get('qty'))
        except (TypeError, ValueError):
            qty = 0
        if not 1 <= qty <= MAX_QTY:
            raise ValueError('Quantities must be between 1 and %d.' % MAX_QTY)
        unit = _pence(l['price'], 'Price') if l.get('price') not in (None, '') else p['pence']
        lines.append({'id': p['id'], 'name': p['name'], 'size': size, 'qty': qty, 'unit_pence': unit})
    subtotal = sum(l['unit_pence'] * l['qty'] for l in lines)
    discount = min(_pence(data.get('discount'), 'Discount'), subtotal)
    delivery = _pence(data.get('delivery'), 'Delivery')
    total = subtotal - discount + delivery
    paid = data.get('already_paid') is True
    method = str(data.get('paid_method') or 'bank') if paid else None
    if method and method not in ('bank', 'cash', 'card'):
        raise ValueError('Unknown payment method.')
    issue = _date(data.get('paid_date')) if paid and data.get('paid_date') else int(time.time())
    try:
        due_days = max(0, min(int(data.get('due_days') or 7), 90))
    except (TypeError, ValueError):
        due_days = 7
    if not paid and not email:
        raise ValueError('An email address is needed to send the invoice.')
    now = int(time.time())
    with shop._lock, shop.db() as conn:
        n = conn.execute("SELECT MAX(CAST(substr(number, ?) AS INTEGER)) FROM invoices WHERE number LIKE ?", (len(PREFIX) + 1, PREFIX + '%')).fetchone()[0]
        number = PREFIX + str(max(FIRST, (n or 0) + 1))
        keep = str(data.get('number') or '').strip().upper()
        if paid and keep:
            # a past invoice keeps its original number (e.g. INV-000786 from Zoho Invoice)
            if not re.fullmatch(r'[A-Z]{2,5}-\d{3,8}', keep) or keep.startswith(PREFIX):
                raise ValueError('The original invoice number should look like INV-000786.')
            if conn.execute('SELECT 1 FROM invoices WHERE number=?', (keep,)).fetchone():
                raise ValueError('Invoice ' + keep + ' has already been added.')
            number = keep
        conn.execute('INSERT INTO invoices (number, token, created_at, issue_date, due_at, customer_name, email, phone, address, lines_json, '
                     'subtotal, discount, delivery, total, note, status, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (number, secrets.token_urlsafe(24), now, issue, issue + due_days * 86400, name, email or None,
                      str(data.get('phone') or '').strip()[:40] or None, str(data.get('address') or '').strip()[:300] or None,
                      json.dumps(lines), subtotal, discount, delivery, total, str(data.get('note') or '').strip()[:500] or None,
                      'unpaid', str(data.get('source') or 'admin')[:20]))
    if name and email:
        _remember_name(email, name)
    if paid:
        mark_paid(number, method, when=issue, notify=False)
    inv = get(number)
    if paid and email and data.get('welcome') is True:
        inv['welcome_sent'] = send_rounds_welcome(number)
    if not paid and data.get('send') is not False:
        try:
            send(number)
        except Exception as e:
            shop.log_email_error('invoice ' + number, e)
            inv['email_error'] = 'The invoice was saved but the email could not be sent: ' + str(e)[:120]
    return inv

def _remember_name(email, name):
    with shop._lock, shop.db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS customer_notes (email TEXT PRIMARY KEY, name TEXT, note TEXT, updated_at INTEGER)')
        conn.execute("INSERT INTO customer_notes (email, name, note, updated_at) VALUES (?,?,'',?) "
                     "ON CONFLICT(email) DO UPDATE SET name=COALESCE(NULLIF(customer_notes.name,''), excluded.name)",
                     (email, name, int(time.time())))

def mark_paid(number, method, when=None, notify=True, payment_intent=None):
    inv = get(number)
    if not inv or inv['status'] == 'cancelled':
        raise ValueError('Invoice not found or cancelled.')
    if inv['status'] == 'paid':
        return inv
    when = int(when or time.time())
    order_number = shop.new_order_number()
    with shop._lock, shop.db() as conn:
        while conn.execute('SELECT 1 FROM orders WHERE order_number=?', (order_number,)).fetchone():
            order_number = shop.new_order_number()
        conn.execute('INSERT INTO orders (order_number, status, created_at, updated_at, items_json, subtotal, discount, shipping, total, '
                     'email, customer_name, phone, paid_at, confirmation_sent, delivery_method, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)',
                     (order_number, 'paid', inv['issue_date'], int(time.time()), inv['lines_json'], inv['subtotal'], inv['discount'],
                      inv['delivery'], inv['total'], inv['email'], inv['customer_name'], inv['phone'], when, 'collect',
                      'Invoice ' + number + ' · ' + method))
        if payment_intent:
            conn.execute('UPDATE orders SET payment_intent_id=? WHERE order_number=?', (payment_intent, order_number))
        conn.execute("UPDATE invoices SET status='paid', paid_at=?, paid_method=?, order_number=? WHERE number=? AND status<>'paid'",
                     (when, method, order_number, number))
    order = shop.get_order(order_number)
    if inv['email']:
        try:
            loyalty.earn_from_order(order)        # DocNova Rounds, exactly like a website order
        except Exception as e:
            shop.log_email_error('rounds for invoice ' + number, e)
        if notify:
            send_receipt(number)
    return get(number)

def undo_paid(number):
    """For a payment marked by mistake: the invoice goes back to unpaid and its rounds are removed."""
    inv = get(number)
    if not inv or inv['status'] != 'paid':
        raise ValueError('Only a paid invoice can be marked unpaid.')
    order = shop.get_order(inv['order_number']) if inv['order_number'] else None
    import returns
    if order and returns.for_order(order['order_number']):
        raise ValueError('This invoice already has a return or refund recorded, so it cannot be marked unpaid.')
    if order:
        loyalty.reverse_order(order)
        shop.update_order(order['order_number'], status='cancelled', note=(order['note'] or '') + ' · payment undone')
    with shop._lock, shop.db() as conn:
        conn.execute("UPDATE invoices SET status='unpaid', paid_at=NULL, paid_method=NULL, order_number=NULL WHERE number=?", (number,))
    return get(number)

def cancel(number):
    inv = get(number)
    if not inv or inv['status'] != 'unpaid':
        raise ValueError('Only an unpaid invoice can be cancelled.')
    with shop._lock, shop.db() as conn:
        conn.execute("UPDATE invoices SET status='cancelled' WHERE number=?", (number,))
    return get(number)

# ------------------------------------------------------------------ card payment
def card_link(inv, base):
    return base + '/invoice/' + inv['token'] + '/pay' if shop.stripe_ready() and inv['status'] == 'unpaid' else ''

def start_card_payment(token, base):
    inv = by_token(token)
    if not inv or inv['status'] != 'unpaid':
        return None
    lines = json.loads(inv['lines_json'])
    desc = ', '.join('%d × %s%s' % (l['qty'], l['name'], '' if l['size'] == 'Standard' else ' (' + l['size'] + ')') for l in lines)[:450]
    session = shop.stripe_request('POST', '/v1/checkout/sessions', {
        'mode': 'payment',
        'line_items': [{'quantity': 1, 'price_data': {'currency': 'gbp', 'unit_amount': inv['total'],
                                                     'product_data': {'name': 'DocNova invoice ' + inv['number'], 'description': desc}}}],
        'customer_email': inv['email'] or None,
        'client_reference_id': inv['number'],
        'metadata': {'invoice_number': inv['number']},
        'payment_intent_data': {'description': 'DocNova invoice ' + inv['number'], 'metadata': {'invoice_number': inv['number']}},
        'success_url': base + '/invoice/' + inv['token'] + '?paid=1',
        'cancel_url': base + '/invoice/' + inv['token'],
        'integration_identifier': shop.INTEGRATION_ID,
    })
    with shop._lock, shop.db() as conn:
        conn.execute('UPDATE invoices SET pay_session=? WHERE number=?', (session['id'], inv['number']))
    return session['url']

def check_card(inv):
    """Ask Stripe whether the latest card checkout for this invoice was paid."""
    if not inv or inv['status'] != 'unpaid' or not inv.get('pay_session') or not shop.stripe_ready():
        return inv
    try:
        s = shop.stripe_request('GET', '/v1/checkout/sessions/' + inv['pay_session'])
    except shop.StripeError:
        return inv
    if s.get('payment_status') == 'paid' and (s.get('metadata') or {}).get('invoice_number') == inv['number'] \
            and s.get('amount_total') == inv['total']:
        pi = s.get('payment_intent')
        return mark_paid(inv['number'], 'card', payment_intent=pi['id'] if isinstance(pi, dict) else pi)
    return inv

def check_open_card_payments():
    with shop.db() as conn:
        rows = [r[0] for r in conn.execute("SELECT number FROM invoices WHERE status='unpaid' AND pay_session IS NOT NULL")]
    for n in rows:
        try:
            check_card(get(n))
        except Exception:
            pass

# ------------------------------------------------------------------ documents and emails
def _esc(v):
    return html.escape(str(v or ''))

def bank_details():
    return os.getenv('DOCNOVA_BANK_DETAILS', '').replace('\\n', '\n').strip()

def page(inv, base, just_paid=False):
    lines = json.loads(inv['lines_json'])
    day = lambda t: time.strftime('%d %B %Y', time.localtime(t)) if t else ''
    rows = ''.join('<tr><td>%s%s</td><td class="n">%d</td><td class="n">%s</td><td class="n">%s</td></tr>' % (
        _esc(l['name']), '' if l['size'] == 'Standard' else ' <span class="m">· Size %s</span>' % _esc(l['size']), l['qty'],
        shop.money(l['unit_pence']), shop.money(l['unit_pence'] * l['qty'])) for l in lines)
    status = inv['status']
    stamp = {'paid': '<div class="stamp paid">PAID%s</div>' % (' · ' + day(inv['paid_at']) if inv['paid_at'] else ''),
             'cancelled': '<div class="stamp void">CANCELLED</div>'}.get(status, '')
    bank = bank_details()
    pay = ''
    if status == 'unpaid':
        card = card_link(inv, base)
        pay = ('<div class="pay"><h3>How to pay</h3>%s%s</div>' % (
            ('<p><strong>Bank transfer</strong> — please use <strong>%s</strong> as the payment reference.</p><pre>%s</pre>' % (_esc(inv['number']), _esc(bank)))
            if bank else '<p>Please contact us at info@docnova.co.uk for bank transfer details, quoting <strong>%s</strong>.</p>' % _esc(inv['number']),
            ('<p class="noprint"><a class="btn" href="%s">Pay %s by card</a></p><p class="m noprint">Secure card payment by Stripe (Apple Pay and Google Pay too).</p>'
             % (_esc(card), shop.money(inv['total']))) if card else ''))
    thanks = '<div class="ok noprint">Thank you — your payment has been received.</div>' if just_paid and status == 'paid' else ''
    if just_paid and status != 'paid':
        thanks = '<div class="ok noprint">Thank you — we are confirming your payment. This page updates in a few seconds.</div><script>setTimeout(()=>location.replace(location.pathname),4000)</script>'
    totals = '<tr><td colspan="3">Subtotal</td><td class="n">%s</td></tr>' % shop.money(inv['subtotal'])
    if inv['discount']:
        totals += '<tr><td colspan="3">Discount</td><td class="n">−%s</td></tr>' % shop.money(inv['discount'])
    if inv['delivery']:
        totals += '<tr><td colspan="3">Delivery</td><td class="n">%s</td></tr>' % shop.money(inv['delivery'])
    totals += '<tr class="t"><td colspan="3">%s</td><td class="n">%s</td></tr>' % ('Total paid' if status == 'paid' else 'Total due', shop.money(inv['total']))
    refunds = []
    if inv.get('order_number'):
        import returns
        refunds = returns.for_order(inv['order_number'])
    for r in refunds:
        what = ', '.join('%d × %s' % (l['qty'], l['name']) for l in json.loads(r['lines_json'])) or 'Refund'
        totals += '<tr><td colspan="3" class="m">Returned %s: %s</td><td class="n">−%s</td></tr>' % (day(r['created_at']), _esc(what), shop.money(r['amount']))
    if refunds:
        net = inv['total'] - sum(r['amount'] for r in refunds)
        totals += '<tr class="t"><td colspan="3">Net paid after refunds</td><td class="n">%s</td></tr>' % shop.money(net)
        stamp = '<div class="stamp void">%s</div>' % ('REFUNDED' if net <= 0 else 'PARTLY REFUNDED')
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>DocNova invoice %(num)s</title><style>
body{margin:0;background:#f3f1ec;color:#182130;font:15px/1.55 Arial,Helvetica,sans-serif}
.doc{max-width:760px;margin:24px auto;background:#fff;border:1px solid #e4e0d8;position:relative}
.head{background:#182130;color:#fff;padding:24px 32px;display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}
.head img{width:150px;height:auto;display:block}.head .k{font:600 12px Arial;letter-spacing:.2em;color:#d9b97f}
.body{padding:28px 32px}.grid{display:flex;gap:24px;flex-wrap:wrap;justify-content:space-between;margin-bottom:22px}
h3{margin:0 0 6px;font:600 11px Arial;letter-spacing:.16em;color:#004c9b;text-transform:uppercase}
table{width:100%%;border-collapse:collapse}th,td{padding:9px 6px;border-bottom:1px solid #eee9e0;text-align:left;vertical-align:top}
th{font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.05em}.n{text-align:right;white-space:nowrap}
.t td{font-weight:700;font-size:17px;border-bottom:0}.m{color:#6b7280;font-size:13px}
.pay{margin-top:24px;background:#f6f8fb;border:1px dashed #9aa6b8;padding:16px 18px}.pay pre{font:15px/1.6 Arial;margin:6px 0 0;white-space:pre-wrap}
.btn{display:inline-block;background:#d9b97f;color:#101826;font-weight:700;text-decoration:none;padding:13px 22px}
.stamp{display:inline-block;transform:rotate(-4deg);border:3px solid;padding:6px 14px;font:700 18px Arial;letter-spacing:.12em;margin:0 0 18px}
.paid{color:#1d7a46}.void{color:#b42318}.ok{max-width:760px;margin:16px auto 0;background:#e5f3ea;color:#1d7a46;padding:12px 16px;font-weight:600}
.foot{border-top:1px solid #ece9e3;padding:16px 32px;font-size:12px;color:#8a909a;text-align:center}
.print{max-width:760px;margin:0 auto 30px;text-align:right}.print button{font:600 14px Arial;padding:10px 16px;border:1px solid #182130;background:#fff;cursor:pointer}
@media print{body{background:#fff}.noprint,.print{display:none}.doc{border:0;margin:0}}
</style></head><body>%(thanks)s<div class="doc">
<div class="head"><img src="%(base)s/assets/docnova-logo-white.png" alt="DocNova"><div class="k">INVOICE</div></div>
<div class="body"><div class="grid">
<div><h3>From</h3>DocNova Ltd<br>Cambridge, United Kingdom<br>info@docnova.co.uk<br><span class="m">Company No. 16502835 (England &amp; Wales)</span></div>
<div><h3>Bill to</h3>%(cname)s%(cemail)s%(cphone)s%(caddr)s</div>
<div><h3>Invoice</h3><strong>%(num)s</strong><br>Date: %(date)s%(due)s</div></div>
%(stamp)s<table><tr><th>Item</th><th class="n">Qty</th><th class="n">Price</th><th class="n">Amount</th></tr>%(rows)s%(totals)s</table>
%(note)s%(pay)s</div>
<div class="foot">Thank you for choosing DocNova — premium scrubs and medical essentials for the people who care for others.<br>
Every DocNova scrub set earns a DocNova Round · collect 10 for a free set · %(base_host)s</div></div>
<div class="print noprint"><button onclick="print()">Print or save as PDF</button></div></body></html>''' % {
        'thanks': thanks, 'stamp': stamp, 'base': _esc(base), 'num': _esc(inv['number']), 'cname': _esc(inv['customer_name']),
        'cemail': '<br>' + _esc(inv['email']) if inv['email'] else '', 'cphone': '<br>' + _esc(inv['phone']) if inv['phone'] else '',
        'caddr': '<br>' + _esc(inv['address']).replace('\n', '<br>') if inv['address'] else '', 'date': day(inv['issue_date']),
        'due': ('<br>Due: ' + day(inv['due_at'])) if status == 'unpaid' and inv['due_at'] else '', 'rows': rows, 'totals': totals,
        'note': '<p class="m" style="margin-top:16px">%s</p>' % _esc(inv['note']) if inv['note'] else '', 'pay': pay,
        'base_host': _esc(re.sub(r'^https?://', '', base))}

def _greet(name):
    """'Dr. Abiola Kehinde' -> 'Dr Kehinde'; 'Sarah Khan' -> 'Sarah'; a company name stays as it is."""
    words = (name or '').split()
    if len(words) > 1 and words[0].rstrip('.') in ('Dr', 'Mr', 'Mrs', 'Ms', 'Miss', 'Prof'):
        return words[0].rstrip('.') + ' ' + words[-1]
    if len(words) > 1 and words[-1].rstrip('.').lower() in ('ltd', 'limited', 'llp', 'plc', 'trust', 'nhs'):
        return name
    return words[0] if words else 'there'

def _email_html(inv, base, heading, lead, button, url, extra=''):
    return ('<!doctype html><html><body style="margin:0;background:#f3f1ec"><table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="background:#f3f1ec">'
            '<tr><td align="center" style="padding:28px 14px"><table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff;border:1px solid #e4e0d8">'
            '<tr><td style="background:#182130;padding:22px 30px"><img src="%s/assets/docnova-logo-white.png" width="140" alt="DocNova" style="display:block;border:0"></td></tr>'
            '<tr><td style="padding:32px 30px 10px;font:16px/1.65 Arial,Helvetica,sans-serif;color:#4a5160">'
            '<h1 style="margin:0 0 14px;font:normal 26px/1.25 Georgia,serif;color:#182130">%s</h1>%s</td></tr>'
            '<tr><td align="center" style="padding:14px 30px 26px"><a href="%s" style="display:inline-block;background:#182130;color:#fff;font:600 15px Arial;text-decoration:none;padding:15px 28px">%s</a></td></tr>%s'
            '<tr><td style="border-top:1px solid #ece9e3;padding:18px 30px;font:12px/1.6 Arial;color:#8a909a;text-align:center">DocNova Ltd · Registered in England &amp; Wales No. 16502835 · Cambridge, UK</td></tr>'
            '</table></td></tr></table></body></html>' % (_esc(base), _esc(heading), lead, _esc(url), _esc(button), extra))

def send(number):
    inv = get(number)
    if not inv or not inv['email'] or inv['status'] != 'unpaid':
        raise ValueError('This invoice cannot be emailed (no email address, or it is not unpaid).')
    if not shop.smtp_configured():
        raise ValueError('Email sending is not set up on the website.')
    base = os.environ['DOCNOVA_PUBLIC_URL'].rstrip('/')
    url = base + '/invoice/' + inv['token']
    first = _greet(inv['customer_name'])
    due = time.strftime('%d %B %Y', time.localtime(inv['due_at'])) if inv['due_at'] else ''
    lead = ('<p style="margin:0 0 14px">Hi %s, thank you for your DocNova order. Your invoice <strong style="color:#182130">%s</strong> for '
            '<strong style="color:#182130">%s</strong> is ready%s.</p><p style="margin:0">You can pay by bank transfer (details on the invoice) '
            '%s. Once it’s paid, your purchase is added to your DocNova Rounds card automatically.</p>'
            % (_esc(first), _esc(inv['number']), shop.money(inv['total']), (' — due by ' + due) if due else '',
               'or by card from the invoice page' if card_link(inv, base) else ''))
    text = ('Hi %s,\n\nThank you for your DocNova order. Invoice %s for %s is ready%s.\n\nView and pay your invoice: %s\n\n%s'
            'Once it is paid, your purchase is added to your DocNova Rounds card automatically.\n\nDocNova Ltd · Cambridge, UK\n'
            % (first, inv['number'], shop.money(inv['total']), (', due by ' + due) if due else '', url,
               ('Bank transfer (reference ' + inv['number'] + '):\n' + bank_details() + '\n\n') if bank_details() else ''))
    shop.send_mail(inv['email'], 'DocNova invoice %s — %s' % (inv['number'], shop.money(inv['total'])), text,
                   _email_html(inv, base, 'Your DocNova invoice', lead, 'View & pay invoice', url))
    with shop._lock, shop.db() as conn:
        conn.execute('UPDATE invoices SET sent_at=? WHERE number=?', (int(time.time()), number))
    return True

def send_receipt(number):
    inv = get(number)
    if not inv or not inv['email'] or not shop.smtp_configured():
        return False
    base = os.environ['DOCNOVA_PUBLIC_URL'].rstrip('/')
    url = base + '/invoice/' + inv['token']
    progress = loyalty.progress_line(inv['email'])
    card = ''
    try:
        card = base + '/#/rounds?t=' + loyalty.new_token(inv['email'])
    except Exception:
        pass
    lead = ('<p style="margin:0 0 14px">Thank you — we’ve received your payment of <strong style="color:#182130">%s</strong> for invoice '
            '<strong style="color:#182130">%s</strong>.</p>%s' % (shop.money(inv['total']), _esc(inv['number']),
            ('<p style="margin:0 0 14px;color:#182130;font-weight:600">%s</p>%s' % (_esc(progress), loyalty.card_img(loyalty.balance(inv['email']), card))) if progress else ''))
    extra = ('<tr><td align="center" style="padding:0 30px 26px"><a href="%s" style="color:#004c9b;font:600 14px Arial">View my DocNova Rounds card</a></td></tr>' % _esc(card)) if card else ''
    text = 'Thank you, we have received your payment of %s for invoice %s.\n\n%s\n\nYour paid invoice: %s\n%s\nDocNova Ltd · Cambridge, UK\n' % (
        shop.money(inv['total']), inv['number'], progress, url, ('Your Rounds card: ' + card + '\n') if card else '')
    try:
        shop.send_mail(inv['email'], 'Payment received — DocNova invoice ' + inv['number'], text,
                       _email_html(inv, base, 'Payment received', lead, 'View paid invoice', url, extra))
    except Exception as e:
        shop.log_email_error('receipt for ' + number, e)
        return False
    return True

def send_rounds_welcome(number):
    """For a past sale brought in from another system: welcome the customer to DocNova Rounds with their card."""
    inv = get(number)
    if not inv or not inv['email'] or not shop.smtp_configured():
        return False
    email = inv['email']
    m = loyalty.ensure_member(email, joined=True)
    base = os.environ['DOCNOVA_PUBLIC_URL'].rstrip('/')
    link = base + '/#/rounds?t=' + loyalty.new_token(email)
    share = base + '/#/rounds?ref=' + m['ref_code']
    bal = loyalty.balance(email)
    greet = _greet(inv['customer_name'])
    ref = 'invoice ' + inv['number']
    lead = ('Dear %s, thank you for choosing DocNova. As one of our first customers, you are now a member of '
            '<strong>DocNova Rounds</strong>, our rewards programme. We have added your earlier purchase (%s) to your card, '
            'plus your 1-round joining bonus: you have <strong>%s of 10 rounds</strong>. Every scrub set earns 1 round, every top or pair of '
            'trousers ½ round, and when you complete 10 rounds your next scrub set is on us.' % (html.escape(greet), html.escape(ref), loyalty.fmt(bal)))
    text = ('Dear %s,\n\nThank you for choosing DocNova. As one of our first customers, you are now a member of DocNova Rounds, our rewards programme. '
            'We have added your earlier purchase (%s) to your card, plus your 1-round joining bonus: you have %s of 10 rounds.\n\n'
            'Every scrub set earns 1 round, every top or pair of trousers half a round. Complete 10 rounds and your next scrub set is free.\n\n'
            'View your card: %s (private link, works for %d days)\nRefer a colleague: %s (you earn a bonus round on their first order)\n\n'
            'Shop online: %s\n\nDocNova Ltd · Cambridge, UK' % (greet, ref, loyalty.fmt(bal), link, loyalty.LINK_DAYS, share, base))
    try:
        shop.send_mail(email, 'You’re now a DocNova Rounds member — %s of 10 rounds already on your card' % loyalty.fmt(bal), text,
                       loyalty.card_html(lead, bal, link, 'View my Rounds card', share))
    except Exception as e:
        shop.log_email_error('rounds welcome for ' + number, e)
        return False
    return True
