"""DocNova stock: what came in, what has sold, what is left — by fit, colour, size, top and trousers.

  * "Stock received" is set by the owner in /admin -> Stock (starts from the September 2026 delivery sheet:
    1,100 sets = 600 women's + 500 men's; a set is one top + one pair of trousers).
  * Sold is worked out live from every paid sale (website orders and invoices). A set uses one top and one
    pair of trousers. Items handed back through Return / refund go back into stock.
  * When an item falls to 50%, 25% and 10% of what was received (and when it runs out), info@docnova.co.uk
    gets one email (once per level; changing the received amount starts the alerts again).
"""
import json, time
import shop

SIZES = ('S', 'M', 'L', 'XL', '2XL')
LEVELS = (50, 25, 10)
SOLD_STATUSES = ('paid', 'dispatched', 'delivered', 'partially_refunded', 'refunded')

# The delivery sheet (Model 1 = women's, Model 2 = men's). Per colour: sets per size.
_W100 = {'S': 15, 'M': 25, 'L': 25, 'XL': 25, '2XL': 10}
_W50 = {'S': 10, 'M': 10, 'L': 10, 'XL': 10, '2XL': 10}
_M100 = {'S': 10, 'M': 20, 'L': 25, 'XL': 25, '2XL': 20}
OPENING = {
    'Women': {'Black': _W100, 'Navy Blue': _W100, 'Royal Blue': _W100, 'Pewter Grey': _W100, 'Burgundy': _W100, 'Pink': _W50, 'Green': _W50},
    'Men': {'Black': _M100, 'Navy Blue': _M100, 'Royal Blue': _M100, 'Pewter Grey': _M100, 'Green': _M100},
}

def key(fit, color, size, piece):
    return '|'.join((fit, color, size, piece))

def init_db():
    with shop._lock, shop.db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS stock_levels (key TEXT PRIMARY KEY, qty INTEGER NOT NULL, updated_at INTEGER)')
        if not conn.execute('SELECT 1 FROM stock_levels LIMIT 1').fetchone():
            now = int(time.time())
            for fit, colours in OPENING.items():
                for color, sizes in colours.items():
                    for size, n in sizes.items():
                        for piece in ('top', 'trousers'):
                            conn.execute('INSERT INTO stock_levels VALUES (?,?,?)', (key(fit, color, size, piece), n, now))

def _pieces(line, cat):
    """What leaves the shelf for one order line: [(key, qty)]."""
    p = cat.get(line.get('id')) or {}
    qty = int(line.get('qty') or 0)
    if p.get('device'):
        return [(key('Device', p.get('name') or line.get('id'), 'Standard', 'device'), qty)]
    fit, color, size, c = p.get('fit'), p.get('color'), line.get('size'), p.get('category')
    if not fit or not color or size not in SIZES:
        return []
    out = []
    if c in ('Sets', 'Tops'):
        out.append((key(fit, color, size, 'top'), qty))
    if c in ('Sets', 'Pants'):
        out.append((key(fit, color, size, 'trousers'), qty))
    return out

def sold_and_returned():
    cat = shop.catalogue()
    sold, back = {}, {}
    with shop.db() as conn:
        for r in conn.execute('SELECT items_json FROM orders WHERE paid_at IS NOT NULL AND status IN (%s)' % ','.join('?' * len(SOLD_STATUSES)), SOLD_STATUSES):
            for l in json.loads(r['items_json'] or '[]'):
                for k, q in _pieces(l, cat):
                    sold[k] = sold.get(k, 0) + q
        has_returns = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='returns'").fetchone()
        for r in (conn.execute('SELECT lines_json FROM returns') if has_returns else []):
            for l in json.loads(r['lines_json'] or '[]'):
                for k, q in _pieces(l, cat):
                    back[k] = back.get(k, 0) + q
    return sold, back

def _level(left, received):
    if received <= 0:
        return 'none'
    if left <= 0:
        return 'out'
    pct = 100.0 * left / received
    for lv in sorted(LEVELS):
        if pct <= lv:
            return str(lv)
    return 'ok'

def summary():
    with shop.db() as conn:
        received = {r['key']: r['qty'] for r in conn.execute('SELECT key, qty FROM stock_levels')}
    sold, back = sold_and_returned()
    rows = []
    for k in sorted(set(received) | set(sold) | set(back)):
        fit, color, size, piece = k.split('|')
        rec, s, b = received.get(k, 0), sold.get(k, 0), back.get(k, 0)
        left = rec - s + b
        rows.append({'key': k, 'fit': fit, 'color': color, 'size': size, 'piece': piece, 'received': rec, 'sold': s,
                     'returned': b, 'left': left, 'pct': round(100.0 * left / rec) if rec else None, 'level': _level(left, rec)})
    return rows

def set_received(changes):
    """changes: {key: qty}. Used when the owner updates stock (e.g. a new delivery or a stock count)."""
    now = int(time.time())
    valid = 0
    with shop._lock, shop.db() as conn:
        for k, q in (changes or {}).items():
            parts = str(k).split('|')
            try:
                q = int(q)
            except (TypeError, ValueError):
                raise ValueError('Stock numbers must be whole numbers.')
            if len(parts) != 4 or q < 0 or q > 100000:
                raise ValueError('Invalid stock line.')
            conn.execute('INSERT INTO stock_levels VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET qty=excluded.qty, updated_at=excluded.updated_at', (k, q, now))
            valid += 1
    return valid

def check_alerts():
    """Email info@ once when an item reaches 50%, 25%, 10% or runs out."""
    import guard
    hits = []
    for r in summary():
        if r['received'] <= 0 or r['level'] in ('ok', 'none'):
            continue
        name = ('%s %s %s — size %s' % (r['fit'] + '’s' if r['fit'] in ('Women', 'Men') else r['fit'], r['color'], r['piece'], r['size'])
                if r['piece'] != 'device' else r['color'])
        crossed = [x for x in LEVELS if r['left'] <= r['received'] * x / 100.0] + ([0] if r['level'] == 'out' else [])
        new = [lv for lv in crossed if guard._once('stock:%s:%d:%d' % (r['key'], r['received'], lv))]
        if new:
            hits.append((min(new), name, r))   # one line per item, at its lowest new level
    # overall totals too: all women's tops, all women's trousers, all men's tops, all men's trousers
    groups = {}
    for r in summary():
        if r['fit'] in ('Women', 'Men') and r['received'] > 0:
            g = groups.setdefault((r['fit'], r['piece']), {'received': 0, 'left': 0})
            g['received'] += r['received']; g['left'] += r['left']
    for (fit, piece), g in groups.items():
        crossed = [x for x in LEVELS if g['left'] <= g['received'] * x / 100.0] + ([0] if g['left'] <= 0 else [])
        new = [lv for lv in crossed if guard._once('stock-total:%s:%s:%d:%d' % (fit, piece, g['received'], lv))]
        if new:
            hits.append((min(new), 'ALL %s’s %s (overall)' % (fit.upper(), 'tops' if piece == 'top' else 'trousers'), g))
    if not hits:
        return 0
    lines = []
    for lv, name, r in sorted(hits, key=lambda h: h[0]):
        label = 'SOLD OUT' if lv == 0 else 'down to %d%%' % lv
        lines.append('- %s: %s (%d left of %d)' % (name, label, max(r['left'], 0), r['received']))
    guard.alert('Stock running low (%d item%s)' % (len(hits), '' if len(hits) == 1 else 's'),
                'These items have reached a low-stock level:\n\n%s\n\nSee the full stock list in your dashboard: %s/admin → Stock.'
                % ('\n'.join(lines), guard._base()), tag='DocNova Stock')
    return len(hits)

def short_name(pid, cat, fallback=''):
    p = cat.get(pid) or {}
    if p.get('device'):
        return (p.get('name') or fallback).replace('DocNova ', '')
    kind = {'Sets': 'Set', 'Tops': 'Top', 'Pants': 'Trousers'}.get(p.get('category'), '')
    return ('%s’s %s · %s' % (p.get('fit'), kind, p.get('color'))) if p.get('fit') and kind else (fallback or pid)

def movements(limit=300):
    """Stock history: every sale (out) and return (back in), newest first."""
    cat = shop.catalogue()
    out = []
    with shop.db() as conn:
        rows = conn.execute('SELECT o.order_number, o.paid_at, o.customer_name, o.email, o.items_json, i.number AS invoice FROM orders o '
                            'LEFT JOIN invoices i ON i.order_number=o.order_number WHERE o.paid_at IS NOT NULL AND o.status IN (%s) '
                            'ORDER BY o.paid_at DESC LIMIT ?' % ','.join('?' * len(SOLD_STATUSES)), (*SOLD_STATUSES, limit)).fetchall()
        for r in rows:
            items = [(l['qty'], short_name(l['id'], cat, l.get('name', '')), l.get('size'))
                     for l in json.loads(r['items_json'] or '[]')]
            out.append({'at': r['paid_at'], 'kind': 'sale', 'ref': r['invoice'] or r['order_number'], 'who': r['customer_name'] or r['email'] or '',
                        'items': ['%d × %s%s' % (q, n, '' if s == 'Standard' else ' · ' + s) for q, n, s in items],
                        'pieces': -sum(q for l in json.loads(r['items_json'] or '[]') for _, q in _pieces(l, cat))})
        has_returns = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='returns'").fetchone()
        for r in (conn.execute('SELECT r.id, r.created_at, r.order_number, r.lines_json, o.customer_name, o.email FROM returns r '
                               'LEFT JOIN orders o ON o.order_number=r.order_number ORDER BY r.created_at DESC LIMIT ?', (limit,)) if has_returns else []):
            lines = json.loads(r['lines_json'] or '[]')
            if not lines:
                continue
            out.append({'at': r['created_at'], 'kind': 'return', 'ref': r['id'] + ' (' + r['order_number'] + ')', 'who': r['customer_name'] or r['email'] or '',
                        'items': ['%d × %s%s' % (l['qty'], short_name(l['id'], cat, l['name']), '' if l['size'] == 'Standard' else ' · ' + l['size']) for l in lines],
                        'pieces': sum(q for l in lines for _, q in _pieces(l, cat))})
    out.sort(key=lambda m: -(m['at'] or 0))
    return out[:limit]
