"""Discount partners such as Student Beans: batches of single-use codes.

How it works:
  * In /admin -> Student discount, the owner makes a batch of codes (e.g. 500 codes, 15% off, for Student Beans)
    and downloads them as a CSV file, which is uploaded to the partner's portal.
  * The partner checks the customer is a student and hands them one code. Each code works once, for one order.
  * A code counts as used only once the order is paid; the dashboard shows how many were used and the sales they brought in.
  * Batches can be switched off at any time (e.g. if the partnership ends); switched-off codes stop working at once.
"""
import csv, io, json, secrets, time
import shop

PARTNERS = ('Student Beans',)
PREFIX = {'Student Beans': 'SB'}
MAX_BATCH = 5000

def init_db():
    with shop._lock, shop.db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS partner_batches (id TEXT PRIMARY KEY, partner TEXT NOT NULL, percent INTEGER NOT NULL, '
                     'size INTEGER NOT NULL, created_at INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1, note TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS partner_codes (code TEXT PRIMARY KEY, batch TEXT NOT NULL, redeemed_order TEXT, redeemed_at INTEGER)')
        conn.execute('CREATE INDEX IF NOT EXISTS partner_codes_batch ON partner_codes(batch)')

def _code(prefix, percent):
    return '%s%d-%s' % (prefix, percent, ''.join(secrets.choice(shop.ORDER_ALPHABET) for _ in range(8)))

def create_batch(partner, percent, size, note=''):
    if partner not in PARTNERS:
        raise ValueError('Unknown partner.')
    try:
        percent, size = int(percent), int(size)
    except (TypeError, ValueError):
        raise ValueError('Please enter whole numbers.')
    if not 5 <= percent <= 50:
        raise ValueError('The discount must be between 5% and 50%.')
    if not 1 <= size <= MAX_BATCH:
        raise ValueError('A batch can have 1 to %d codes.' % MAX_BATCH)
    now = int(time.time())
    bid = 'B' + time.strftime('%y%m%d', time.gmtime(now)) + '-' + ''.join(secrets.choice(shop.ORDER_ALPHABET) for _ in range(4))
    with shop._lock, shop.db() as conn:
        conn.execute('INSERT INTO partner_batches (id, partner, percent, size, created_at, active, note) VALUES (?,?,?,?,?,1,?)',
                     (bid, partner, percent, size, now, str(note or '')[:200]))
        made = 0
        while made < size:
            cur = conn.execute('INSERT OR IGNORE INTO partner_codes (code, batch) VALUES (?,?)', (_code(PREFIX[partner], percent), bid))
            made += cur.rowcount
    return {'id': bid, 'size': size}

def set_active(bid, active):
    with shop._lock, shop.db() as conn:
        if not conn.execute('UPDATE partner_batches SET active=? WHERE id=?', (1 if active else 0, str(bid))).rowcount:
            raise ValueError('Batch not found.')
    return {'id': bid, 'active': bool(active)}

def info(code):
    """A valid, unused code from an active batch -> {'code', 'percent_off', 'partner'}; otherwise None."""
    code = str(code or '').strip().upper()
    if not (4 <= len(code) <= 20) or '-' not in code:
        return None
    with shop.db() as conn:
        r = conn.execute('SELECT c.code, c.redeemed_order, b.percent, b.partner, b.active FROM partner_codes c '
                         'JOIN partner_batches b ON b.id=c.batch WHERE c.code=?', (code,)).fetchone()
    if not r or r['redeemed_order'] or not r['active']:
        return None
    return {'code': r['code'], 'percent_off': r['percent'], 'partner': r['partner']}

def stripe_coupon_spec(code):
    i = info(code)
    if not i:
        return None
    return {'stripe_id': 'docnova-%s-%d' % (i['partner'].lower().replace(' ', ''), i['percent_off']), 'percent_off': i['percent_off'],
            'name': '%s · %d%% student discount' % (i['partner'], i['percent_off'])}

def redeem(order):
    """Mark the code used once its order is paid (safe to repeat)."""
    code = str(order.get('coupon') or '').upper()
    if not code or '-' not in code or code.startswith('ROUNDS-'):
        return
    with shop._lock, shop.db() as conn:
        conn.execute('UPDATE partner_codes SET redeemed_order=?, redeemed_at=? WHERE code=? AND redeemed_order IS NULL',
                     (order['order_number'], int(time.time()), code))

def summary():
    with shop.db() as conn:
        batches = [dict(r) for r in conn.execute('SELECT * FROM partner_batches ORDER BY created_at DESC')]
        for b in batches:
            b['used'] = conn.execute('SELECT COUNT(*) FROM partner_codes WHERE batch=? AND redeemed_order IS NOT NULL', (b['id'],)).fetchone()[0]
        used = [dict(r) for r in conn.execute(
            'SELECT c.code, c.redeemed_order, c.redeemed_at, b.partner, o.customer_name, o.email, o.subtotal, o.discount, o.total, o.status '
            'FROM partner_codes c JOIN partner_batches b ON b.id=c.batch LEFT JOIN orders o ON o.order_number=c.redeemed_order '
            'WHERE c.redeemed_order IS NOT NULL ORDER BY c.redeemed_at DESC LIMIT 300')]
    return {'batches': batches, 'used': used, 'partners': list(PARTNERS)}

def batch_csv(bid):
    with shop.db() as conn:
        b = conn.execute('SELECT * FROM partner_batches WHERE id=?', (str(bid),)).fetchone()
        if not b:
            raise ValueError('Batch not found.')
        rows = conn.execute('SELECT code FROM partner_codes WHERE batch=? AND redeemed_order IS NULL ORDER BY rowid', (b['id'],)).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['code'])
    for r in rows:
        w.writerow([r['code']])
    return b, out.getvalue()
