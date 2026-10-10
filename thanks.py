"""Video thank-you codes.

A customer who sends a short video of themselves in DocNova scrubs (by email reply or WhatsApp) can be given a
personal, single-use discount code for their next order:
  * On the Orders page the owner presses "Video received" on that order -> it waits under Approvals.
  * On approval a THANKS-XXXXXX code is made and emailed (the owner sees the email first).
  * The code is the customer's usual discount PLUS 10% (e.g. a Blue Light member with 15% gets a 25% code), so the
    extra 10% is truly additional while the checkout keeps its simple one-code box.
  * Valid 60 days, one order. It counts as used once that order is paid.
No reward is ever offered for Google reviews (Google's rules and the DMCC Act 2024).
"""
import json, os, re, secrets, time
from html import escape
import shop

EXTRA = 10
VALID_DAYS = 60
CODE_RE = re.compile(r'THANKS-[A-Z2-9]{6}')

def init_db():
    with shop._lock, shop.db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS video_rewards (id TEXT PRIMARY KEY, order_number TEXT, email TEXT, name TEXT, '
                     'percent INTEGER, base_percent INTEGER, status TEXT, note TEXT, created_at INTEGER, decided_at INTEGER, '
                     'code TEXT UNIQUE, expires_at INTEGER, redeemed_order TEXT, redeemed_at INTEGER, emailed INTEGER DEFAULT 0)')

def usual_percent(order):
    """The percentage discount this customer normally gets, from the code on this order (0 if none or a special offer)."""
    code = str(order.get('coupon') or '').strip().upper()
    if not code or code.startswith('ROUNDS-') or code.startswith('THANKS-') or code in shop.SCOPED:
        return 0
    if code in shop.COUPONS:
        return int(shop.COUPONS[code]['percent_off'])
    m = re.match(r'[A-Z]+(\d{1,2})-', code)          # partner codes look like SB15-XXXXXXXX
    return int(m.group(1)) if m else 0

def _row(r):
    d = dict(r)
    d['expired'] = bool(d.get('expires_at') and d['expires_at'] < time.time())
    return d

def request(number, note=''):
    o = shop.get_order(str(number or '').strip().upper())
    if not o or o['status'] not in ('paid', 'dispatched', 'delivered', 'partially_refunded'):
        raise ValueError('Order not found.')
    if not o.get('email'):
        raise ValueError('This order has no email address, so a code cannot be emailed.')
    with shop._lock, shop.db() as conn:
        if conn.execute("SELECT 1 FROM video_rewards WHERE order_number=? AND status IN ('pending','approved')", (o['order_number'],)).fetchone():
            raise ValueError('A video thank-you for this order is already waiting or approved.')
        base = usual_percent(o)
        rid = 'V' + time.strftime('%y%m%d') + '-' + ''.join(secrets.choice(shop.ORDER_ALPHABET) for _ in range(4))
        conn.execute('INSERT INTO video_rewards (id, order_number, email, name, percent, base_percent, status, note, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                     (rid, o['order_number'], o['email'], o['customer_name'] or '', min(base + EXTRA, 50), base, 'pending',
                      str(note or '').strip()[:300] or None, int(time.time())))
    return {'id': rid}

def listing():
    with shop.db() as conn:
        rows = [_row(r) for r in conn.execute('SELECT * FROM video_rewards ORDER BY created_at DESC LIMIT 100')]
    return {'pending': [r for r in rows if r['status'] == 'pending'], 'recent': [r for r in rows if r['status'] != 'pending'][:30]}

def for_order(number):
    with shop.db() as conn:
        r = conn.execute('SELECT * FROM video_rewards WHERE order_number=? ORDER BY created_at DESC LIMIT 1', (number,)).fetchone()
    return _row(r) if r else None

def _get(rid):
    with shop.db() as conn:
        r = conn.execute('SELECT * FROM video_rewards WHERE id=?', (str(rid or ''),)).fetchone()
    if not r:
        raise ValueError('Not found.')
    return dict(r)

def email(r, code=None):
    base = (os.getenv('DOCNOVA_PUBLIC_URL') or 'https://docnova.co.uk').rstrip('/')
    code = code or r.get('code') or 'THANKS-XXXXXX'
    until = time.strftime('%-d %B %Y', time.localtime(r.get('expires_at') or time.time() + VALID_DAYS * 86400))
    hello = 'Hello %s,' % r['name'] if r.get('name') else 'Hello,'
    extra = ('It already includes your usual %d%% discount plus an extra %d%%, so there is no need to add another code.'
             % (r['base_percent'], EXTRA)) if r.get('base_percent') else ''
    subject = 'Thank you for your video — here is %d%% off your next order' % r['percent']
    lines = ['Thank you so much for sending us your video — it really made our day, and it helps other doctors and nurses see DocNova in real life.',
             'As a thank-you, here is your personal code for %d%% off your next order:' % r['percent']]
    tail = [x for x in [extra, 'Use it at checkout on docnova.co.uk before %s. It works once, on one order.' % until] if x]
    text = '\n\n'.join([hello] + lines + ['    ' + code] + tail + ['Shop now: ' + base, 'With thanks,\nThe DocNova team'])
    lead = ('<p style="margin:0 0 12px">%s</p>%s'
            '<p style="margin:16px 0;text-align:center"><span style="display:inline-block;border:2px dashed #d9b97f;background:#fffaf0;color:#182130;'
            'font:700 26px/1 Arial;letter-spacing:.12em;padding:16px 26px">%s</span><br><span style="font:600 15px Arial;color:#8a6100">%d%% off your next order</span></p>%s'
            % (escape(hello), ''.join('<p style="margin:0 0 12px">%s</p>' % escape(x) for x in lines), escape(code), r['percent'],
               ''.join('<p style="margin:0 0 12px">%s</p>' % escape(x) for x in tail)))
    import invoices
    return subject, text, invoices._email_html(None, base, 'Thank you for your video', lead, 'Shop the collection', base + '/#/shop')

def preview(rid):
    r = _get(rid)
    subject, text, html_body = email(r)
    return {'to': r['email'], 'subject': subject, 'html': html_body, 'percent': r['percent'], 'base_percent': r['base_percent'],
            'can_send': shop.smtp_configured()}

def decide(rid, approve, percent=None, send=True):
    r = _get(rid)
    if r['status'] != 'pending':
        raise ValueError('This has already been decided.')
    now = int(time.time())
    if not approve:
        with shop._lock, shop.db() as conn:
            conn.execute("UPDATE video_rewards SET status='declined', decided_at=? WHERE id=?", (now, r['id']))
        return {'status': 'declined'}
    if percent is not None:
        try:
            percent = int(percent)
        except (TypeError, ValueError):
            raise ValueError('Please enter a whole number.')
        if not 5 <= percent <= 50:
            raise ValueError('The code must be between 5% and 50%.')
        r['percent'] = percent
    with shop._lock, shop.db() as conn:
        for _ in range(10):
            code = 'THANKS-' + ''.join(secrets.choice(shop.ORDER_ALPHABET) for _ in range(6))
            if not conn.execute('SELECT 1 FROM video_rewards WHERE code=?', (code,)).fetchone():
                break
        conn.execute("UPDATE video_rewards SET status='approved', decided_at=?, code=?, percent=?, expires_at=? WHERE id=?",
                     (now, code, r['percent'], now + VALID_DAYS * 86400, r['id']))
    r = _get(r['id'])
    emailed = False
    if send and shop.smtp_configured():
        subject, text, html_body = email(r)
        try:
            shop.send_mail(r['email'], subject, text, html_body)
            emailed = True
            with shop._lock, shop.db() as conn:
                conn.execute('UPDATE video_rewards SET emailed=1 WHERE id=?', (r['id'],))
            import fulfil
            fulfil.log_email(r['order_number'], 'thanks', r['email'], subject)
        except Exception as e:
            shop.log_email_error('video thank-you code ' + r['id'], e)
    return {'status': 'approved', 'code': code, 'percent': r['percent'], 'emailed': emailed}

# ---------------------------------------------------------------- at checkout
def info(code):
    code = str(code or '').strip().upper()
    if not CODE_RE.fullmatch(code):
        return None
    with shop.db() as conn:
        r = conn.execute("SELECT * FROM video_rewards WHERE code=? AND status='approved'", (code,)).fetchone()
    if not r or r['redeemed_order'] or (r['expires_at'] and r['expires_at'] < time.time()):
        return None
    return {'code': code, 'percent_off': r['percent']}

def stripe_coupon_spec(code):
    i = info(code)
    return {'stripe_id': 'docnova-thanks-%d' % i['percent_off'], 'percent_off': i['percent_off'],
            'name': 'Thank-you code · %d%% off' % i['percent_off']} if i else None

def redeem(order):
    code = str(order.get('coupon') or '').upper()
    if not code.startswith('THANKS-'):
        return
    with shop._lock, shop.db() as conn:
        conn.execute('UPDATE video_rewards SET redeemed_order=?, redeemed_at=? WHERE code=? AND redeemed_order IS NULL',
                     (order['order_number'], int(time.time()), code))
