"""DocNova owner dashboard at /admin — customers, subscribers, purchases and DocNova Rounds.

Switched on only when DOCNOVA_ADMIN_PASSWORD (12+ characters) is set in the hosting environment.
Sign-in makes a random session cookie (HttpOnly, Secure, SameSite=Strict, 12 hours). Restarting the
server signs everyone out. Ten wrong passwords in 15 minutes pauses all sign-ins for 15 minutes (the host's proxy hides real
visitor addresses, so the limit is site-wide).
"""
import hmac, json, os, secrets, time
import shop, loyalty

SESSION_SECONDS = 12 * 3600
SESSIONS = {}          # token -> expiry
FAILS = {}             # ip -> [timestamps]
PAID = ('paid', 'dispatched', 'delivered', 'partially_refunded')
REASONS = {'join': 'Joining bonus', 'order': 'Order', 'referral': 'Referral bonus', 'reward': 'Free set issued',
           'refund': 'Refund', 'return': 'Returned items', 'adjust': 'Added by DocNova'}

def init_db():
    with shop._lock, shop.db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS customer_notes (email TEXT PRIMARY KEY, name TEXT, note TEXT, updated_at INTEGER)')

def enabled():
    return len(os.getenv('DOCNOVA_ADMIN_PASSWORD', '')) >= 12

# ------------------------------------------------------------------ sign-in
def locked(ip=None):
    now = time.time()
    FAILS['all'] = [t for t in FAILS.get('all', []) if now - t < 900]
    return len(FAILS['all']) >= 10

def login(ip, password):
    if not enabled() or locked(ip):
        return None
    if hmac.compare_digest(str(password).encode(), os.environ['DOCNOVA_ADMIN_PASSWORD'].encode()):
        token = secrets.token_urlsafe(32)
        now = time.time()
        for t in [t for t, exp in SESSIONS.items() if exp < now]:
            SESSIONS.pop(t, None)
        SESSIONS[token] = now + SESSION_SECONDS
        return token
    FAILS.setdefault('all', []).append(time.time())
    return None

def valid(cookie_header):
    if not enabled():
        return False
    for part in (cookie_header or '').split(';'):
        k, _, v = part.strip().partition('=')
        if k == 'dn_admin' and SESSIONS.get(v, 0) > time.time():
            return True
    return False

def logout(cookie_header):
    for part in (cookie_header or '').split(';'):
        k, _, v = part.strip().partition('=')
        if k == 'dn_admin':
            SESSIONS.pop(v, None)

# ------------------------------------------------------------------ data
def _items(order, cat):
    out = {'sets': 0, 'tops': 0, 'trousers': 0, 'devices': 0}
    for l in json.loads(order['items_json'] or '[]'):
        c = (cat.get(l.get('id')) or {}).get('category')
        key = {'Sets': 'sets', 'Tops': 'tops', 'Pants': 'trousers'}.get(c, 'devices')
        out[key] += int(l.get('qty') or 0)
    return out

def people():
    cat = shop.catalogue()
    rows = {}
    def person(email):
        return rows.setdefault(email, {'email': email, 'name': '', 'subscriber': '', 'member': False, 'joined_at': None,
                                       'orders': 0, 'spent_pence': 0, 'sets': 0, 'tops': 0, 'trousers': 0, 'devices': 0,
                                       'halves': 0, 'rewards': 0, 'rewards_unused': 0, 'referrals': 0, 'last_activity': 0})
    with shop.db() as conn:
        for r in conn.execute('SELECT email, status, consent_at FROM subscribers'):
            p = person(r['email']); p['subscriber'] = r['status'] or ''; p['last_activity'] = max(p['last_activity'], r['consent_at'] or 0)
        for r in conn.execute('SELECT email, joined_at, created_at FROM rounds_members'):
            p = person(r['email']); p['member'] = bool(r['joined_at']); p['joined_at'] = r['joined_at']
            p['last_activity'] = max(p['last_activity'], r['created_at'] or 0)
        for r in conn.execute('SELECT * FROM orders WHERE email IS NOT NULL AND paid_at IS NOT NULL ORDER BY paid_at'):
            e = loyalty.norm(r['email'])
            if not e:
                continue
            p = person(e)
            p['name'] = r['customer_name'] or p['name']
            p['last_activity'] = max(p['last_activity'], r['paid_at'] or 0)
            if r['status'] in PAID:
                p['orders'] += 1; p['spent_pence'] += r['total'] or 0
                for k, v in _items(dict(r), cat).items():
                    p[k] += v
        for r in conn.execute('SELECT email, SUM(halves) h FROM rounds_ledger GROUP BY email'):
            person(r['email'])['halves'] = r['h'] or 0
        for r in conn.execute('SELECT email, COUNT(*) n, SUM(redeemed_order IS NULL AND expires_at > ?) u FROM rounds_rewards GROUP BY email', (int(time.time()),)):
            p = person(r['email']); p['rewards'] = r['n']; p['rewards_unused'] = r['u'] or 0
        for r in conn.execute('SELECT referred_by, COUNT(*) n FROM rounds_members WHERE referred_by IS NOT NULL GROUP BY referred_by'):
            person(r['referred_by'])['referrals'] = r['n']
        for r in conn.execute('SELECT email, name FROM customer_notes'):
            if r['email'] in rows and r['name']:
                rows[r['email']]['name'] = r['name']
    out = list(rows.values())
    for p in out:
        p['rounds'] = loyalty.fmt(p['halves'])
        p['to_go'] = loyalty.fmt(max(loyalty.REWARD_AT - p['halves'], 0))
    out.sort(key=lambda p: -p['last_activity'])
    return out

def person_detail(email):
    email = loyalty.norm(email)
    if not email:
        return None
    cat = shop.catalogue()
    summary = next((p for p in people() if p['email'] == email), None)
    with shop.db() as conn:
        orders = []
        for r in conn.execute('SELECT * FROM orders WHERE lower(email)=? AND paid_at IS NOT NULL ORDER BY paid_at DESC', (email,)):
            r = dict(r)
            lines = [('%d × %s' % (l.get('qty', 1), (cat.get(l.get('id')) or {}).get('name') or l.get('name') or l.get('id')))
                     for l in json.loads(r['items_json'] or '[]')]
            orders.append({'number': r['order_number'], 'paid_at': r['paid_at'], 'status': shop.STATUS_LABELS.get(r['status'], r['status']),
                           'total_pence': r['total'] or 0, 'items': lines, 'coupon': r['coupon'] or '',
                           'refunded_pence': conn.execute('SELECT COALESCE(SUM(amount),0) FROM returns WHERE order_number=?', (r['order_number'],)).fetchone()[0]
                           if conn.execute("SELECT 1 FROM sqlite_master WHERE name='returns'").fetchone() else 0})
        history = [{'at': r['created_at'], 'rounds': loyalty.fmt(r['halves']), 'halves': r['halves'],
                    'what': REASONS.get(r['reason'], r['reason']) + (' · ' + r['ref'].split(' #')[0] if r['ref'] else '')}
                   for r in conn.execute('SELECT * FROM rounds_ledger WHERE email=? ORDER BY id DESC', (email,))]
        rewards = [dict(r) for r in conn.execute('SELECT code, issued_at, expires_at, redeemed_order FROM rounds_rewards WHERE email=? ORDER BY issued_at DESC', (email,))]
        note = conn.execute('SELECT name, note FROM customer_notes WHERE email=?', (email,)).fetchone()
        m = loyalty.member(email)
        referred = [r[0] for r in conn.execute('SELECT email FROM rounds_members WHERE referred_by=?', (email,))]
    return {'summary': summary, 'orders': orders, 'history': history, 'rewards': rewards, 'referred': referred,
            'ref_code': m['ref_code'] if m else '', 'referred_by': (m or {}).get('referred_by') or '',
            'name': note['name'] if note else '', 'note': note['note'] if note else ''}

def adjust(email, rounds, reason, joining, notify):
    """Add (or remove) rounds by hand, e.g. for purchases made before DocNova Rounds existed."""
    email = loyalty.norm(email)
    if not email:
        raise ValueError('Please enter a valid email address.')
    try:
        halves = round(float(rounds) * 2)
    except (TypeError, ValueError):
        raise ValueError('Rounds must be a number such as 1, 2.5 or -1.')
    if abs(halves) > 200:
        raise ValueError('That is a lot of rounds — please enter 100 or fewer at a time.')
    reason = ' '.join(str(reason or '').split())[:80] or 'Added by DocNova'
    if not halves and not joining:
        raise ValueError('Enter a number of rounds, or tick the joining bonus.')
    loyalty.ensure_member(email, joined=bool(joining))
    if halves:
        if loyalty.balance(email) + halves < 0:
            raise ValueError('That would take the card below zero.')
        with shop._lock, shop.db() as conn:
            conn.execute('INSERT INTO rounds_ledger (email, halves, reason, ref, created_at) VALUES (?,?,?,?,?)',
                         (email, halves, 'adjust', reason + ' #' + secrets.token_hex(4), int(time.time())))
    codes = loyalty.issue_rewards(email)          # 10 rounds → a free set waits for the owner's approval
    sent = loyalty.send_link(email) if notify else False
    return {'balance': loyalty.fmt(loyalty.balance(email)), 'rewards_pending': codes, 'card_emailed': bool(sent)}

def save_note(email, name, note):
    email = loyalty.norm(email)
    if not email:
        raise ValueError('Invalid email.')
    with shop._lock, shop.db() as conn:
        conn.execute('INSERT INTO customer_notes (email, name, note, updated_at) VALUES (?,?,?,?) '
                     'ON CONFLICT(email) DO UPDATE SET name=excluded.name, note=excluded.note, updated_at=excluded.updated_at',
                     (email, str(name or '').strip()[:80], str(note or '').strip()[:1000], int(time.time())))
    return True

def csv_export():
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Email', 'Name', 'Newsletter', 'Rounds member', 'Paid orders', 'Spent (GBP)', 'Sets', 'Tops', 'Trousers', 'Devices',
                'Rounds', 'Rounds to free set', 'Free sets issued', 'Free sets unused', 'Referrals'])
    for p in people():
        name = p['name']
        if name[:1] in ('=', '+', '-', '@'):
            name = "'" + name                      # stop spreadsheet formulas
        w.writerow([p['email'], name, p['subscriber'], 'yes' if p['member'] else 'no', p['orders'], '%.2f' % (p['spent_pence'] / 100),
                    p['sets'], p['tops'], p['trousers'], p['devices'], p['rounds'], p['to_go'], p['rewards'], p['rewards_unused'], p['referrals']])
    return buf.getvalue()
