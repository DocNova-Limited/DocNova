"""One friendly Google review request per order, sent when the customer is most likely to be happy:
after they have had the scrubs for a few days — not at the moment they pay.

  * Delivery orders: 6 days after the order is marked dispatched (arrives in 3–5 days, then worn on a shift or two).
  * Click & Collect and sales recorded in the dashboard (invoices): 5 days after payment.
  * Never for refunded or cancelled orders, never twice for the same order, and a customer who was asked in the
    last 120 days is not asked again (repeat customers are not pestered).
  * Emails go out between 10:00 and 19:00 UK time only. Orders from before this feature was switched on are not included.
"""
import json, os, threading, time
from datetime import datetime
from html import escape
import shop

LAUNCH_AT = 1790743000          # 30 Sep 2026: earlier orders are never emailed
COLLECT_DAYS, DISPATCH_DAYS, QUIET_DAYS = 5, 6, 120
SKIP = ('refunded', 'partially_refunded', 'cancelled', 'awaiting_payment', 'processing', 'expired', 'failed')

def init_db():
    with shop._lock, shop.db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS review_requests (order_number TEXT PRIMARY KEY, email TEXT, sent_at INTEGER, status TEXT)')

def _uk_hour(now):
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(now, ZoneInfo('Europe/London')).hour
    except Exception:
        return datetime.utcfromtimestamp(now).hour

def due_orders(now=None):
    now = now or time.time()
    out = []
    with shop.db() as conn:
        rows = [dict(r) for r in conn.execute(
            'SELECT o.* FROM orders o LEFT JOIN review_requests r ON r.order_number=o.order_number '
            "WHERE r.order_number IS NULL AND o.paid_at IS NOT NULL AND o.paid_at >= ? AND o.email IS NOT NULL AND o.email != ''", (LAUNCH_AT,))]
        for o in rows:
            if o['status'] in SKIP:
                continue
            if (o.get('delivery_method') or 'uk') == 'collect':
                ready = o['paid_at'] + COLLECT_DAYS * 86400
            elif o.get('dispatched_at'):
                ready = o['dispatched_at'] + DISPATCH_DAYS * 86400
            else:
                continue                                  # not sent yet: wait
            if now < ready:
                continue
            email = o['email'].strip().lower()
            recent = conn.execute("SELECT 1 FROM review_requests WHERE lower(email)=? AND status='sent' AND sent_at > ?",
                                  (email, now - QUIET_DAYS * 86400)).fetchone()
            if recent:
                with shop._lock:
                    conn.execute('INSERT OR IGNORE INTO review_requests VALUES (?,?,?,?)', (o['order_number'], email, int(now), 'skipped-recent'))
                continue
            out.append(o)
    return out

def build(order):
    import invoices
    base = os.environ['DOCNOVA_PUBLIC_URL'].rstrip('/')
    link = shop.review_link()
    name = invoices._greet(order.get('customer_name') or '')
    items = json.loads(order['items_json'] or '[]')
    cat = shop.catalogue()
    devices_only = items and all((cat.get(l['id']) or {}).get('device') for l in items)
    thing = 'your DocNova order' if devices_only else 'your DocNova scrubs'
    lead = ('<p style="margin:0 0 14px">Hi %s,</p>'
            '<p style="margin:0 0 14px">We hope you’re enjoying %s. As a small, independent British brand, every honest review makes a real difference — '
            'it helps us improve, and helps fellow healthcare professionals choose with confidence.</p>'
            '<p style="margin:0 0 14px">If you have a minute, we’d be truly grateful if you shared your experience on Google.</p>'
            '<p style="margin:0;font-size:14px;color:#6b7280">If anything isn’t quite right, simply reply to this email and we’ll put it right. '
            'We’ll only ask once — thank you for being part of DocNova.</p>' % (escape(name), thing))
    html = invoices._email_html(None, base, 'How are you getting on?', lead, 'Leave a Google review', link)
    text = ('Hi %s,\n\nWe hope you’re enjoying %s. As a small, independent British brand, every honest review makes a real difference — it helps us improve, '
            'and helps fellow healthcare professionals choose with confidence.\n\nIf you have a minute, we’d be truly grateful if you shared your '
            'experience on Google:\n%s\n\nIf anything isn’t quite right, simply reply to this email and we’ll put it right. We’ll only ask once.\n\n'
            'With thanks,\nThe DocNova team\ninfo@docnova.co.uk\n' % (name, thing, link))
    subject = 'How are you getting on with %s?' % thing
    return subject, text, html

def run_once(now=None):
    now = now or time.time()
    if not shop.smtp_configured() or not 10 <= _uk_hour(now) < 19:
        return 0
    sent, seen = 0, set()
    for o in due_orders(now)[:20]:                        # a gentle trickle, never a blast
        email = o['email'].strip().lower()
        if email in seen:                                 # two orders from one person: one email only
            with shop._lock, shop.db() as conn:
                conn.execute('INSERT OR IGNORE INTO review_requests VALUES (?,?,?,?)', (o['order_number'], email, int(now), 'skipped-recent'))
            continue
        seen.add(email)
        subject, text, html = build(o)
        try:
            shop.send_mail(o['email'], subject, text, html)
            status = 'sent'
            sent += 1
        except Exception as e:
            shop.log_email_error('review request ' + o['order_number'], e)
            status = 'failed'
        with shop._lock, shop.db() as conn:
            conn.execute('INSERT OR IGNORE INTO review_requests VALUES (?,?,?,?)', (o['order_number'], o['email'].strip().lower(), int(now), status))
    return sent

def start():
    def loop():
        time.sleep(120)
        while True:
            try:
                run_once()
            except Exception as e:
                shop.log_email_error('review requests', e)
            time.sleep(1800)
    threading.Thread(target=loop, daemon=True).start()
