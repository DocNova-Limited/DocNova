"""DocNova Rounds — the loyalty programme.

Rules (kept in one place so the website, emails and admin tool always agree):
  * Joining bonus: 1 round, once per email address.
  * Every scrub set = 1 round; every top or pair of trousers = ½ round. Medical devices don't earn.
  * Referral: when someone who named you as their referrer places their first paid order, you get 1 round.
  * complete 10 rounds = the customer's next scrub set free, their choice, sent as a one-time ROUNDS-XXXXXX code (valid 12 months).
  * The free set in a reward order doesn't earn a round; a fully refunded order has its rounds removed.
Rounds are stored in halves (whole numbers) so there is never any rounding.
"""
import re, secrets, time, os
from urllib.parse import quote
import shop

JOIN_BONUS, REFERRAL_BONUS, REWARD_AT = 2, 2, 20          # in half-rounds: 1, 1 and 10 rounds
REWARD_DAYS, LINK_DAYS = 365, 7
EARN = {'Sets': 2, 'Tops': 1, 'Pants': 1}                 # half-rounds per item
CODE_RE = re.compile(r'ROUNDS-[A-Z2-9]{6}')
EMAIL_RE = re.compile(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+')

def init_db():
    with shop._lock, shop.db() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS rounds_members (email TEXT PRIMARY KEY, ref_code TEXT UNIQUE, joined_at INTEGER,
            referred_by TEXT, created_at INTEGER);
        CREATE TABLE IF NOT EXISTS rounds_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, halves INTEGER NOT NULL,
            reason TEXT NOT NULL, ref TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, UNIQUE(email, reason, ref));
        CREATE TABLE IF NOT EXISTS rounds_rewards (code TEXT PRIMARY KEY, email TEXT NOT NULL, issued_at INTEGER, expires_at INTEGER,
            redeemed_order TEXT, redeemed_at INTEGER);
        CREATE TABLE IF NOT EXISTS rounds_tokens (token TEXT PRIMARY KEY, email TEXT NOT NULL, expires_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS rounds_ledger_email ON rounds_ledger(email);
        ''')

def norm(email):
    email = str(email or '').strip().lower()
    return email if len(email) <= 254 and EMAIL_RE.fullmatch(email) else ''

def fmt(halves):
    if halves < 0:
        return '-' + fmt(-halves)
    whole = '%d' % (halves // 2) if halves >= 2 or not halves % 2 else ''
    return whole + ('½' if halves % 2 else '')

def _code(prefix, n):
    return prefix + ''.join(secrets.choice('ABCDEFGHJKMNPQRSTUVWXYZ23456789') for _ in range(n))

def member(email):
    with shop.db() as conn:
        r = conn.execute('SELECT * FROM rounds_members WHERE email=?', (email,)).fetchone()
        return dict(r) if r else None

def ensure_member(email, joined=False):
    """Create the member record if needed. `joined` also grants the one-off joining bonus."""
    now = int(time.time())
    with shop._lock, shop.db() as conn:
        if not conn.execute('SELECT 1 FROM rounds_members WHERE email=?', (email,)).fetchone():
            for _ in range(10):
                ref = _code('', 6)
                if not conn.execute('SELECT 1 FROM rounds_members WHERE ref_code=?', (ref,)).fetchone():
                    break
            conn.execute('INSERT INTO rounds_members (email, ref_code, joined_at, created_at) VALUES (?,?,?,?)',
                         (email, ref, now if joined else None, now))
        if joined:
            conn.execute('UPDATE rounds_members SET joined_at=COALESCE(joined_at, ?) WHERE email=?', (now, email))
            conn.execute('INSERT OR IGNORE INTO rounds_ledger (email, halves, reason, ref, created_at) VALUES (?,?,?,?,?)',
                         (email, JOIN_BONUS, 'join', '', now))
    return member(email)

def balance(email):
    with shop.db() as conn:
        return conn.execute('SELECT COALESCE(SUM(halves),0) FROM rounds_ledger WHERE email=?', (email,)).fetchone()[0]

def has_paid_order(email):
    with shop.db() as conn:
        return bool(conn.execute("SELECT 1 FROM orders WHERE email=? AND status IN ('paid','dispatched','delivered','partially_refunded')",
                                 (email,)).fetchone())

def join(email, referrer='', ref_code=''):
    """Join (or re-request a link). Always behaves the same from outside so membership can't be probed."""
    email = norm(email)
    if not email:
        raise ValueError('Please enter a valid email address.')
    ref_email = ''
    if ref_code:
        with shop.db() as conn:
            r = conn.execute('SELECT email FROM rounds_members WHERE ref_code=?', (str(ref_code).strip().upper()[:12],)).fetchone()
            ref_email = r[0] if r else ''
    if not ref_email and referrer:
        ref_email = norm(referrer)
        if referrer and not ref_email:
            raise ValueError('Please check the email address of the person who referred you.')
    existing = member(email)
    new_joiner = not existing or not existing['joined_at']
    m = ensure_member(email, joined=True)
    # A referral only counts for someone new who hasn't ordered yet, and never for yourself.
    if ref_email and ref_email != email and not m['referred_by'] and not has_paid_order(email):
        ensure_member(ref_email)
        with shop.db() as conn:
            conn.execute('UPDATE rounds_members SET referred_by=? WHERE email=?', (ref_email, email))
    send_link(email, welcome=new_joiner)
    return True

def new_token(email):
    """A private card link token, valid for LINK_DAYS."""
    token = secrets.token_urlsafe(24)
    with shop.db() as conn:
        conn.execute('DELETE FROM rounds_tokens WHERE expires_at < ?', (int(time.time()),))
        conn.execute('INSERT INTO rounds_tokens VALUES (?,?,?)', (token, email, int(time.time()) + LINK_DAYS * 86400))
    return token

def enrol_subscriber(email):
    """Newsletter sign-ups join Rounds automatically (joining bonus granted once, never twice).
    Returns what the welcome email needs; no separate email is sent."""
    email = norm(email)
    if not email:
        return None
    m = ensure_member(email, joined=True)
    base = os.environ['DOCNOVA_PUBLIC_URL'].rstrip('/')
    return {'balance': fmt(balance(email)), 'card': base + '/#/rounds?t=' + new_token(email),
            'share': base + '/#/rounds?ref=' + m['ref_code']}

def send_link(email, welcome=False):
    email = norm(email)
    if not email or not member(email):
        return False
    token = new_token(email)
    if not shop.smtp_configured():
        return False
    base = os.environ['DOCNOVA_PUBLIC_URL'].rstrip('/')
    link = base + '/#/rounds?t=' + token
    bal = balance(email)
    if welcome:
        subject = 'Welcome to DocNova Rounds — your first round is in'
        lead = 'Welcome to DocNova Rounds. Your joining bonus is already on your card: you have %s of 10 rounds.' % fmt(bal)
    else:
        subject = 'Your DocNova Rounds card'
        lead = 'Here is your secure link to your DocNova Rounds card. You have %s of 10 rounds.' % fmt(bal)
    m = member(email)
    share = base + '/#/rounds?ref=' + m['ref_code']
    text = ('%s\n\nView your card: %s\n(This private link works for %d days.)\n\nHow it works: every scrub set earns 1 round, '
            'every top or pair of trousers ½ round. Collect 10 rounds and we send you a code for your next scrub set free.\n\n'
            'Refer a colleague: share %s — when they place their first order, you earn a bonus round.\n\nDocNova'
            % (lead, link, LINK_DAYS, share))
    try:
        shop.send_mail(email, subject, text, card_html(lead, bal, link, 'View my Rounds card', share))
    except Exception as e:
        shop.log_email_error('rounds email', e)
        return False
    return True

def card_html(lead, bal, link, button, share='', code=''):
    """Branded email with the 10-round card plus the free set (matches the Rounds page)."""
    base = os.environ.get('DOCNOVA_PUBLIC_URL', '').rstrip('/')
    done = min(bal, REWARD_AT)
    cells = ''
    for i in range(10):
        full = done >= (i + 1) * 2
        half = not full and done == i * 2 + 1
        gift = False
        if full:
            bg, fg, border = ('#d9b97f' if gift else '#ffffff'), '#101826', ('#d9b97f' if gift else '#ffffff')
        elif half:
            bg, fg, border = '#8f9bb0', '#101826', '#ffffff'
        else:
            bg, fg, border = 'transparent', ('#d9b97f' if gift else '#6f7b8f'), ('#d9b97f' if gift else '#46536a')
        label = '&#127873;' if gift else str(i + 1)
        cells += ('<td align="center" style="padding:5px"><div style="width:44px;height:44px;line-height:44px;border-radius:50%%;'
                  'border:1.5px solid %s;background:%s;color:%s;font:600 14px/44px Arial,sans-serif;text-align:center">%s</div></td>' % (border, bg, fg, label))
        if i == 4:
            cells += '</tr><tr>'
    code_box = ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="margin:6px 0 4px"><tr><td align="center" '
                'style="background:#d9b97f;border-radius:10px;padding:18px"><p style="margin:0 0 6px;font:600 10px Arial;letter-spacing:.18em;color:#3b2e17">'
                'YOUR FREE SET CODE</p><p style="margin:0;font:700 26px Arial;letter-spacing:.12em;color:#101826">%s</p></td></tr></table>' % code) if code else ''
    ref = ('<tr><td style="padding:0 32px 28px"><table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="background:#f3f1ec;border-radius:10px">'
           '<tr><td style="padding:18px 20px;font:14px/1.6 Arial;color:#4a5160"><strong style="color:#182130">Refer a colleague, earn a bonus round.</strong><br>'
           'Share your link — when they join and place their first order, a round lands on your card.<br>'
           '<a href="%s" style="color:#89515c;word-break:break-all">%s</a></td></tr></table></td></tr>' % (share, share)) if share else ''
    return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>'
            '<body style="margin:0;background:#f3f1ec"><table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="background:#f3f1ec">'
            '<tr><td align="center" style="padding:28px 14px"><table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
            'style="max-width:560px;background:#ffffff;border:1px solid #e4e0d8">'
            '<tr><td style="background:#101826;padding:22px 32px"><table role="presentation" width="100%%" cellpadding="0" cellspacing="0"><tr>'
            '<td><img src="%s/assets/docnova-logo-white.png" width="120" alt="DocNova" style="display:block;border:0;width:120px;height:auto"></td>'
            '<td align="right" style="font:600 11px Arial;letter-spacing:.22em;color:#d9b97f">DOCNOVA ROUNDS</td></tr></table></td></tr>'
            '<tr><td><img src="%s/assets/rounds-email.jpg" width="560" alt="The DocNova team in navy, royal blue and burgundy scrubs" '
            'style="display:block;border:0;width:100%%;height:auto"></td></tr>'
            '<tr><td style="padding:32px 32px 8px"><p style="font:16px/1.65 Arial;color:#182130;margin:0 0 22px">%s</p>%s</td></tr>'
            '<tr><td style="padding:10px 32px 6px"><table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="background:#101826;border-radius:14px">'
            '<tr><td style="padding:18px 18px 6px;font:600 10px Arial;letter-spacing:.2em;color:#c9d0db">YOUR ROUNDS CARD</td>'
            '<td align="right" style="padding:18px 18px 6px;font:11px Arial;color:#ffffff">%s of 10</td></tr>'
            '<tr><td colspan="2" align="center" style="padding:6px 10px 18px"><table role="presentation" cellpadding="0" cellspacing="0"><tr>%s</tr></table>'
            '<p style="margin:12px 0 0"><span style="display:inline-block;border:1.5px solid #d9b97f;border-radius:30px;padding:8px 18px;font:600 12px Arial;letter-spacing:.16em;%s">&#127873; 10 ROUNDS = 1 FREE SET</span></p></td></tr></table></td></tr>'
            '<tr><td align="center" style="padding:24px 32px 28px"><a href="%s" style="display:inline-block;background:#182130;color:#ffffff;'
            'text-decoration:none;font:600 15px Arial;padding:16px 30px">%s</a></td></tr>%s'
            '<tr><td style="border-top:1px solid #ece9e3;padding:18px 32px;font:12px/1.6 Arial;color:#8a909a;text-align:center">'
            '1 round when you join · 1 per scrub set · ½ per top or trousers · 1 per colleague referred · complete 10 rounds for a free set<br>'
            'DocNova Ltd · Registered in England &amp; Wales No. 16502835 · Cambridge, UK</td></tr>'
            '</table></td></tr></table></body></html>' % (base, base, lead, code_box, fmt(done), cells, 'background:#d9b97f;color:#101826' if done >= REWARD_AT else 'color:#d9b97f', link, button, ref))

def order_halves(order):
    cat = shop.catalogue()
    halves = 0
    for l in shop.json.loads(order['items_json']):
        p = cat.get(l['id']) or {}
        halves += EARN.get(p.get('category'), 0) * l['qty']
    if order.get('coupon') and CODE_RE.fullmatch(order['coupon']):
        halves -= EARN['Sets']        # the free set itself doesn't earn
    return max(halves, 0)

def earn_from_order(order):
    """Called once an order is paid. Safe to call repeatedly: every ledger row is unique per order."""
    email = norm(order.get('email'))
    if not email:
        return None
    first_order = not has_paid_order_before(email, order['order_number'])
    m = ensure_member(email, joined=True)          # every customer is a member automatically
    now = int(time.time())
    halves = order_halves(order)
    with shop._lock, shop.db() as conn:
        if halves:
            conn.execute('INSERT OR IGNORE INTO rounds_ledger (email, halves, reason, ref, created_at) VALUES (?,?,?,?,?)',
                         (email, halves, 'order', order['order_number'], now))
        if first_order and m['referred_by']:
            conn.execute('INSERT OR IGNORE INTO rounds_ledger (email, halves, reason, ref, created_at) VALUES (?,?,?,?,?)',
                         (m['referred_by'], REFERRAL_BONUS, 'referral', order['order_number'], now))
        code = order.get('coupon') or ''
        if CODE_RE.fullmatch(code):
            conn.execute('UPDATE rounds_rewards SET redeemed_order=?, redeemed_at=? WHERE code=? AND redeemed_order IS NULL',
                         (order['order_number'], now, code))
    issue_rewards(email)
    if first_order and m['referred_by']:
        issue_rewards(m['referred_by'])
    return halves

def has_paid_order_before(email, number):
    with shop.db() as conn:
        return bool(conn.execute("SELECT 1 FROM orders WHERE email=? AND order_number<>? AND paid_at IS NOT NULL", (email, number)).fetchone())

def issue_rewards(email):
    issued = []
    while balance(email) >= REWARD_AT:
        now = int(time.time())
        with shop._lock, shop.db() as conn:
            for _ in range(10):
                code = _code('ROUNDS-', 6)
                if not conn.execute('SELECT 1 FROM rounds_rewards WHERE code=?', (code,)).fetchone():
                    break
            conn.execute('INSERT INTO rounds_rewards (code, email, issued_at, expires_at) VALUES (?,?,?,?)',
                         (code, email, now, now + REWARD_DAYS * 86400))
            conn.execute('INSERT INTO rounds_ledger (email, halves, reason, ref, created_at) VALUES (?,?,?,?,?)',
                         (email, -REWARD_AT, 'reward', code, now))
        issued.append(code)
        send_reward(email, code)
    return issued

def send_reward(email, code):
    if not shop.smtp_configured():
        return False
    base = os.environ['DOCNOVA_PUBLIC_URL'].rstrip('/')
    lead = ('<span style="font:26px/1.3 Georgia,serif;color:#182130">Congratulations — you’ve completed 10 rounds.</span><br><br>'
            'Your free DocNova scrub set is ready. Choose any set, any colour, any size, add it to your bag and enter your code at checkout. '
            'Your code is valid for 12 months.')
    text = ('Congratulations — you have completed 10 DocNova Rounds, so your next set is free!\n\nYour free scrub set code: %s\n\nChoose any scrub set, any colour, '
            'any size, and enter the code at checkout. It covers one set and is valid for 12 months.\n\nShop: %s\n\nDocNova' % (code, base))
    try:
        shop.send_mail(email, 'Your free DocNova scrub set is ready 🎁', text, card_html(lead, REWARD_AT, base + '/#/shop?category=Sets', 'Choose my free set ↗', code=code))
    except Exception as e:
        shop.log_email_error('rounds reward email', e)
        return False
    return True

def reverse_order(order):
    """A fully refunded order loses the rounds it earned."""
    email = norm(order.get('email'))
    if not email:
        return
    with shop._lock, shop.db() as conn:
        r = conn.execute("SELECT halves FROM rounds_ledger WHERE email=? AND reason='order' AND ref=?", (email, order['order_number'])).fetchone()
        if r:
            conn.execute('INSERT OR IGNORE INTO rounds_ledger (email, halves, reason, ref, created_at) VALUES (?,?,?,?,?)',
                         (email, -r[0], 'refund', order['order_number'], int(time.time())))

def reward_info(code):
    code = str(code or '').strip().upper()
    if not CODE_RE.fullmatch(code):
        return None
    with shop.db() as conn:
        r = conn.execute('SELECT * FROM rounds_rewards WHERE code=?', (code,)).fetchone()
    if not r or r['redeemed_order'] or r['expires_at'] < time.time():
        return None
    return {'code': code, 'reward': True, 'label': 'DocNova Rounds · free scrub set'}

def reward_discount(lines):
    """Value of the free set: the highest-priced scrub set in the bag (one set)."""
    cat = shop.catalogue()
    sets = [l['unit_pence'] for l in lines if (cat.get(l['id']) or {}).get('category') == 'Sets']
    if not sets:
        raise ValueError('Your DocNova Rounds code is for one free scrub set — please add a set to your bag.')
    return max(sets)

def progress_line(email):
    email = norm(email)
    if not email or not member(email):
        return ''
    b = balance(email)
    left = REWARD_AT - b
    return ('DocNova Rounds: you now have %s of 10 rounds — %s to go until your next set is free.' % (fmt(b), fmt(left))
            if left > 0 else 'DocNova Rounds: you have %s rounds.' % fmt(b))

def dashboard(token):
    with shop.db() as conn:
        r = conn.execute('SELECT email FROM rounds_tokens WHERE token=? AND expires_at>?', (str(token)[:80], int(time.time()))).fetchone()
        if not r:
            return None
        email = r[0]
        hist = [dict(x) for x in conn.execute('SELECT halves, reason, ref, created_at FROM rounds_ledger WHERE email=? ORDER BY id DESC LIMIT 30', (email,))]
        rewards = [dict(x) for x in conn.execute('SELECT code, issued_at, expires_at, redeemed_order FROM rounds_rewards WHERE email=? ORDER BY issued_at DESC', (email,))]
        referrals = conn.execute('SELECT COUNT(*) FROM rounds_members WHERE referred_by=?', (email,)).fetchone()[0]
    m = member(email)
    name, _, dom = email.partition('@')
    return {'email': name[:2] + '•••@' + dom, 'halves': balance(email), 'history': hist, 'rewards': rewards,
            'ref_code': m['ref_code'], 'referrals': referrals, 'reward_at': REWARD_AT}
