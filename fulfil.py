"""Website orders for the owner: an instant 'new order' email, the Orders board on the dashboard
(what to pack, post or hand over), and a printable invoice / packing slip for each website order."""
import json, os, threading, time
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo
import shop

try:
    UK = ZoneInfo('Europe/London')
except Exception:                      # no time-zone data on the server: fall back to UK winter time (GMT)
    from datetime import timezone
    UK = timezone.utc
OWNER_EMAIL = lambda: os.getenv('DOCNOVA_OWNER_EMAIL') or 'info@docnova.co.uk'
# Website orders only: invoices made on the dashboard are recorded by the owner herself.
WEBSITE = ("o.checkout_session_id IS NOT NULL AND NOT EXISTS "
           "(SELECT 1 FROM invoices i WHERE i.order_number=o.order_number)")
SHOWN = ('processing', 'paid', 'dispatched', 'delivered', 'partially_refunded', 'refunded', 'cancelled')
CATCH_UP_HOURS = 72

def init_db():
    with shop._lock, shop.db() as conn:
        cols = [r[1] for r in conn.execute('PRAGMA table_info(orders)')]
        if 'owner_notified' not in cols:
            conn.execute('ALTER TABLE orders ADD COLUMN owner_notified INTEGER DEFAULT 0')
            # Orders paid before this feature existed are not emailed again, except the last 72 hours.
            conn.execute('UPDATE orders SET owner_notified=1 WHERE paid_at IS NULL OR paid_at < ?',
                         (int(time.time()) - CATCH_UP_HOURS * 3600,))

def uk_time(ts):
    return datetime.fromtimestamp(ts, UK)

def nice_day(dt):
    return dt.strftime('%a %-d %b')

def working_days_after(dt, n):
    d = dt.date()
    while n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d

def dispatch_by(order):
    """The website promises dispatch within 1–2 working days. Click & Collect: contact the customer the same day."""
    if not order.get('paid_at'):
        return None, ''
    paid = uk_time(order['paid_at'])
    if (order.get('delivery_method') or 'uk') == 'collect':
        return paid.date(), 'Contact the customer on %s to arrange collection' % nice_day(paid)
    due = working_days_after(paid, 2)
    return due, 'Post by %s (1–2 working days)' % due.strftime('%a %-d %b')

def address_lines(order):
    ship = json.loads(order['shipping_json']) if order.get('shipping_json') else {}
    a = (ship or {}).get('address') or {}
    country = {'GB': 'United Kingdom', 'IE': 'Ireland'}.get(a.get('country'), a.get('country'))
    lines = [ship.get('name') or order.get('customer_name'), a.get('line1'), a.get('line2'), a.get('city'), a.get('state'), a.get('postal_code'), country]
    return [x for x in lines if x] if a else []

def item_lines(order):
    return [{'name': shop.line_name(l), 'size': l['size'], 'qty': l['qty'], 'unit_pence': l['unit_pence']}
            for l in json.loads(order['items_json'] or '[]')]

def stage(order):
    s, collect = order['status'], (order.get('delivery_method') or 'uk') == 'collect'
    if s == 'paid':
        return 'prepare_collect' if collect else 'to_post'
    if s == 'dispatched':
        return 'ready_collect' if collect else 'on_way'
    if s == 'delivered':
        return 'done'
    if s == 'processing':
        return 'waiting'
    return 'closed'

STAGE_LABEL = {'prepare_collect': 'Prepare for Click & Collect', 'to_post': 'To post', 'ready_collect': 'Ready – waiting for collection',
               'on_way': 'Posted – on its way', 'done': 'Completed', 'waiting': 'Payment still clearing', 'closed': 'Refunded / cancelled'}

def describe(o):
    due, due_text = dispatch_by(o)
    st = stage(o)
    today = datetime.now(UK).date()
    method = o.get('delivery_method') or 'uk'
    return {'number': o['order_number'], 'status': o['status'], 'status_label': shop.STATUS_LABELS.get(o['status'], o['status']),
            'stage': st, 'stage_label': STAGE_LABEL[st], 'method': method,
            'method_label': shop.DELIVERY.get(method, shop.DELIVERY['uk'])['label'],
            'paid_at': o['paid_at'], 'created_at': o['created_at'], 'dispatched_at': o['dispatched_at'], 'delivered_at': o['delivered_at'],
            'name': o['customer_name'] or '', 'email': o['email'] or '', 'phone': o['phone'] or '', 'address': address_lines(o),
            'items': item_lines(o), 'pieces': sum(l['qty'] for l in json.loads(o['items_json'] or '[]')),
            'subtotal': o['subtotal'] or 0, 'discount': o['discount'] or 0, 'coupon': o['coupon'] or '', 'shipping': o['shipping'] or 0, 'total': o['total'] or 0,
            'carrier': o['carrier'] or '', 'tracking_number': o['tracking_number'] or '', 'tracking_url': o['tracking_url'] or '',
            'due': due.isoformat() if due else None, 'due_text': due_text,
            'late': bool(due and st in ('to_post', 'prepare_collect') and due < today),
            'due_today': bool(due and st in ('to_post', 'prepare_collect') and due == today)}

def board():
    with shop.db() as conn:
        rows = [dict(r) for r in conn.execute('SELECT o.* FROM orders o WHERE ' + WEBSITE + ' AND o.status IN (%s) ORDER BY COALESCE(o.paid_at, o.created_at) DESC'
                                              % ','.join('?' * len(SHOWN)), SHOWN)]
    orders = [describe(o) for o in rows]
    count = lambda *stages: sum(1 for o in orders if o['stage'] in stages)
    today = datetime.now(UK).date()
    return {'orders': orders, 'counts': {
        'placed': sum(1 for o in orders if o['status'] != 'cancelled'),
        'today': sum(1 for o in orders if o['paid_at'] and uk_time(o['paid_at']).date() == today),
        'to_fulfil': count('to_post', 'prepare_collect', 'ready_collect'),
        'to_post': count('to_post'), 'prepare_collect': count('prepare_collect'), 'ready_collect': count('ready_collect'),
        'on_way': count('on_way'), 'done': count('done'), 'closed': count('closed'), 'waiting': count('waiting')}}

def _website_order(number):
    with shop.db() as conn:
        r = conn.execute('SELECT o.* FROM orders o WHERE o.order_number=? AND ' + WEBSITE, (number,)).fetchone()
    if not r:
        raise ValueError('Website order not found.')
    return dict(r)

def act(number, action, carrier='', tracking=''):
    """Move a website order along: dispatch (post), ready (Click & Collect), delivered / collected, or undo one step."""
    o = _website_order(str(number or '').strip().upper())
    n, now, collect = o['order_number'], int(time.time()), (o.get('delivery_method') or 'uk') == 'collect'
    emailed = False
    if action == 'dispatch':
        if collect or o['status'] != 'paid':
            raise ValueError('Only a paid order for posting can be marked as posted.')
        carrier = ' '.join(str(carrier or 'Royal Mail').split())[:40] or 'Royal Mail'
        tracking = ''.join(str(tracking or '').split()).upper()[:40]
        shop.update_order(n, status='dispatched', carrier=carrier, tracking_number=tracking or None,
                          tracking_url=shop.tracking_url_for(carrier, tracking) if tracking else None, dispatched_at=now)
        emailed = shop.send_order_email(shop.get_order(n), 'dispatch')
    elif action == 'ready':
        if not collect or o['status'] != 'paid':
            raise ValueError('Only a paid Click & Collect order can be marked ready.')
        shop.update_order(n, status='dispatched', dispatched_at=now)
    elif action == 'delivered':
        if o['status'] != 'dispatched':
            raise ValueError('Mark the order as posted (or ready) first.')
        shop.update_order(n, status='delivered', delivered_at=now)
    elif action == 'undo':
        if o['status'] == 'delivered':
            shop.update_order(n, status='dispatched', delivered_at=None)
        elif o['status'] == 'dispatched':
            shop.update_order(n, status='paid', dispatched_at=None, carrier=None, tracking_number=None, tracking_url=None, dispatch_sent=0)
        else:
            raise ValueError('Nothing to undo.')
    else:
        raise ValueError('Unknown action.')
    return {'order': describe(shop.get_order(n)), 'emailed': emailed}

# ---------------------------------------------------------------- owner email
def _claim(number):
    with shop._lock, shop.db() as conn:
        return conn.execute('UPDATE orders SET owner_notified=2 WHERE order_number=? AND COALESCE(owner_notified,0)=0', (number,)).rowcount == 1

def _set_flag(number, value):
    with shop._lock, shop.db() as conn:
        conn.execute('UPDATE orders SET owner_notified=? WHERE order_number=?', (value, number))

def owner_email(order):
    d = describe(order)
    base = (os.getenv('DOCNOVA_PUBLIC_URL') or 'https://docnova.co.uk').rstrip('/')
    when = uk_time(order['paid_at'] or order['created_at']).strftime('%a %-d %b %Y, %H:%M')
    size = lambda l: '' if l['size'] == 'Standard' else ' · Size ' + l['size']
    subject = 'NEW ORDER %s · %s · %s · %s' % (d['number'], shop.money(d['total']), d['name'] or d['email'],
                                               'CLICK & COLLECT' if d['method'] == 'collect' else d['method_label'])
    money_rows = [('Items', shop.money(d['subtotal']))]
    if d['discount']:
        money_rows.append(('Discount' + (' (' + d['coupon'] + ')' if d['coupon'] else ''), '−' + shop.money(d['discount'])))
    money_rows += [(d['method_label'], shop.money(d['shipping'])), ('TOTAL PAID', shop.money(d['total']))]
    text = '\n'.join(['New website order — please fulfil.', '', 'Order: ' + d['number'], 'Paid: ' + when + ' (UK time)', 'Delivery: ' + d['method_label'],
                      d['due_text'], '', 'Customer: ' + d['name'], 'Email: ' + d['email'], 'Phone: ' + (d['phone'] or '—'), '',
                      ('Send to:\n  ' + '\n  '.join(d['address'])) if d['address'] and d['method'] != 'collect' else 'Click & Collect — no posting address needed.', '',
                      'Items:'] + ['  %d × %s%s  (%s each)' % (l['qty'], l['name'], size(l), shop.money(l['unit_pence'])) for l in d['items']] +
                     [''] + ['%s: %s' % r for r in money_rows] + ['', 'Open the Orders page: ' + base + '/admin'])
    td = 'padding:7px 0;border-bottom:1px solid #eee9e0'
    rows = ''.join('<tr><td style="%s"><strong>%d ×</strong> %s<span style="color:#b0751a;font-weight:700">%s</span></td><td align="right" style="%s">%s</td></tr>'
                   % (td, l['qty'], escape(l['name']), escape(size(l)), td, shop.money(l['unit_pence'] * l['qty'])) for l in d['items'])
    rows += ''.join('<tr><td style="padding:5px 0;%s">%s</td><td align="right" style="padding:5px 0;%s">%s</td></tr>'
                    % ('font-weight:700' if k == 'TOTAL PAID' else '', escape(k), 'font-weight:700' if k == 'TOTAL PAID' else '', escape(v)) for k, v in money_rows)
    box = 'background:#fff8e8;border:1px solid #f0dfb5;padding:12px 14px;margin:0 0 14px'
    lead = ('<p style="%s"><strong style="color:#182130">%s</strong><br>%s</p>'
            '<p style="margin:0 0 4px"><strong style="color:#182130">Order %s</strong> · paid %s</p>'
            '<p style="margin:0 0 14px">%s<br>%s · %s</p>%s'
            '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="font:15px/1.5 Arial;color:#182130">%s</table>'
            % (box, escape(d['method_label']), escape(d['due_text']), escape(d['number']), escape(when),
               escape(d['name']), escape(d['email']), escape(d['phone'] or 'no phone'),
               ('<p style="margin:0 0 14px"><strong style="color:#182130">Send to</strong><br>%s</p>' % '<br>'.join(escape(x) for x in d['address']))
               if d['address'] and d['method'] != 'collect' else '', rows))
    import invoices
    html_body = invoices._email_html(None, base, 'New order to fulfil', lead, 'Open the Orders page', base + '/admin')
    return subject, text, html_body

def notify_owner(order):
    """Email the owner as soon as a website order is paid. Safe to call more than once: each order is emailed once."""
    if not order or order.get('status') != 'paid' or not order.get('checkout_session_id') or not shop.smtp_configured():
        return False
    try:
        _website_order(order['order_number'])
    except ValueError:
        return False
    if not _claim(order['order_number']):
        return False
    try:
        subject, text, html_body = owner_email(order)
        shop.send_mail(OWNER_EMAIL(), subject, text, html_body)
    except Exception as e:
        _set_flag(order['order_number'], 0)
        shop.log_email_error('owner new-order email for ' + order['order_number'], e)
        return False
    _set_flag(order['order_number'], 1)
    return True

def catch_up():
    """Email any recent paid order the owner has not heard about yet (e.g. if the email server was busy)."""
    since = int(time.time()) - CATCH_UP_HOURS * 3600
    with shop.db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT o.* FROM orders o WHERE " + WEBSITE + " AND o.status='paid' AND COALESCE(o.owner_notified,0)=0 "
                                              "AND o.paid_at >= ? ORDER BY o.paid_at", (since,))]
    return sum(1 for o in rows if notify_owner(o))

def start_catch_up_loop():
    def loop():
        time.sleep(20)
        while True:
            try:
                catch_up()
            except Exception as e:
                shop.log_email_error('owner new-order catch-up', e)
            time.sleep(600)
    threading.Thread(target=loop, daemon=True).start()

# ---------------------------------------------------------------- printable invoice / packing slip
def sheet(number):
    o = _website_order(str(number or '').strip().upper())
    d = describe(o)
    when = uk_time(o['paid_at'] or o['created_at']).strftime('%-d %B %Y')
    size = lambda l: '' if l['size'] == 'Standard' else l['size']
    rows = ''.join('<tr><td>%s</td><td>%s</td><td class="n">%d</td><td class="n">%s</td><td class="n">%s</td><td class="tick">☐</td></tr>'
                   % (escape(l['name']), escape(size(l)) or '—', l['qty'], shop.money(l['unit_pence']), shop.money(l['unit_pence'] * l['qty'])) for l in d['items'])
    tot = '<tr><td colspan="4">Items</td><td class="n">%s</td><td></td></tr>' % shop.money(d['subtotal'])
    if d['discount']:
        tot += '<tr><td colspan="4">Discount%s</td><td class="n">−%s</td><td></td></tr>' % (' (' + escape(d['coupon']) + ')' if d['coupon'] else '', shop.money(d['discount']))
    tot += '<tr><td colspan="4">%s</td><td class="n">%s</td><td></td></tr>' % (escape(d['method_label']), shop.money(d['shipping']))
    tot += '<tr class="big"><td colspan="4">Total paid (online, %s)</td><td class="n">%s</td><td></td></tr>' % (escape(when), shop.money(d['total']))
    to = '<br>'.join(escape(x) for x in d['address']) if d['address'] and d['method'] != 'collect' else escape(d['name']) + '<br><em>Click &amp; Collect (Cambridge area)</em>'
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex"><title>%s · DocNova</title><style>'
            'body{margin:0;background:#f3f1ec;font:14px/1.5 Arial,Helvetica,sans-serif;color:#182130}'
            '.page{max-width:780px;margin:24px auto;background:#fff;border:1px solid #e4e0d8;padding:36px 40px}'
            '.head{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:3px solid #182130;padding-bottom:16px}'
            '.brand{font:600 26px Georgia,serif;letter-spacing:.04em}.brand small{display:block;font:12px Arial;color:#6b7280;letter-spacing:0}'
            'h1{margin:0;font:400 22px Georgia,serif;text-align:right}h1 small{display:block;font:13px Arial;color:#6b7280}'
            '.cols{display:grid;grid-template-columns:1fr 1fr 1fr;gap:18px;margin:20px 0}.cols h3{margin:0 0 4px;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#004c9b}'
            'table{width:100%%;border-collapse:collapse}th{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#6b7280;text-align:left;border-bottom:1px solid #182130;padding:8px 6px}'
            'td{padding:9px 6px;border-bottom:1px solid #eee9e0}.n{text-align:right;white-space:nowrap}.tick{text-align:center;font-size:18px}th.tick{font-size:11px}'
            'tr.big td{font-weight:700;font-size:16px;border-top:2px solid #182130}.foot{margin-top:26px;font-size:12px;color:#6b7280;text-align:center}'
            '.bar{max-width:780px;margin:16px auto 0;display:flex;gap:10px}.bar button{font:600 14px Arial;background:#182130;color:#fff;border:0;padding:11px 18px;border-radius:8px;cursor:pointer}'
            '@media print{body{background:#fff}.page{border:0;margin:0;padding:0}.bar{display:none}}'
            '@media(max-width:640px){.page{padding:22px 16px}.cols{grid-template-columns:1fr}}'
            '</style></head><body><div class="bar"><button onclick="print()">Print / save as PDF</button></div><div class="page">'
            '<div class="head"><div class="brand">DOCNOVA<small>DocNova Ltd · Cambridge, United Kingdom · info@docnova.co.uk</small></div>'
            '<h1>Invoice &amp; packing slip<small>Order %s · %s</small></h1></div>'
            '<div class="cols"><div><h3>Customer</h3>%s<br>%s<br>%s</div><div><h3>Deliver to</h3>%s</div>'
            '<div><h3>Delivery</h3>%s<br><span style="color:#6b7280">%s</span><br>Status: %s%s</div></div>'
            '<table><thead><tr><th>Item</th><th>Size</th><th class="n">Qty</th><th class="n">Price</th><th class="n">Total</th><th class="tick">Packed</th></tr></thead>'
            '<tbody>%s%s</tbody></table>'
            '<p class="foot">Thank you for shopping with DocNova. Unworn, unwashed scrubs with tags attached can be returned within 30 days of delivery — contact info@docnova.co.uk.<br>'
            'DocNova Ltd · Registered in England &amp; Wales No. 16502835</p></div></body></html>'
            % (escape(d['number']), escape(d['number']), escape(when), escape(d['name']), escape(d['email']), escape(d['phone'] or ''), to,
               escape(d['method_label']), escape(d['due_text']) if d['stage'] in ('to_post', 'prepare_collect') else '', escape(d['stage_label']),
               ('<br>' + escape(d['carrier']) + ' ' + escape(d['tracking_number'])) if d['tracking_number'] else '', rows, tot))
