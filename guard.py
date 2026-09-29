"""DocNova Rounds protection: strict referrals, owner-approved free sets, and alerts to the DocNova team.

Rules
  * A referral can only be attached when someone first registers (a brand-new member with no previous orders).
  * The referral round is only considered on the referred person's FIRST paid order, and only once per person.
  * Before a referral round is given, the new customer is compared with the referrer and with every existing
    customer: full name (titles ignored), phone number, delivery address and email aliases (Gmail dots,
    "+tags", googlemail.com). Any match -> the referral is HELD for the owner to approve or decline, and
    info@docnova.co.uk gets an alert.
  * info@docnova.co.uk is alerted when one member passes 4 referrals, and again when they pass 8.
  * Free sets are never issued automatically: at 10 rounds a request is created, the customer is told their
    free set is being prepared, and the owner approves (code emailed) or declines it in /admin -> Approvals.
  * If the referred person's first order is fully refunded, the referral round is removed again.
"""
import json, os, re, secrets, time
import shop

TITLES = {'dr', 'mr', 'mrs', 'ms', 'miss', 'mx', 'prof', 'professor', 'sir', 'dame', 'rev', 'mstr'}
ALERT_AT = (4, 8)

def init_db():
    with shop._lock, shop.db() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS rounds_review (id TEXT PRIMARY KEY, kind TEXT NOT NULL, email TEXT NOT NULL, referrer TEXT,
            order_number TEXT, reasons TEXT, status TEXT NOT NULL DEFAULT 'pending', created_at INTEGER NOT NULL,
            decided_at INTEGER, note TEXT, code TEXT);
        CREATE INDEX IF NOT EXISTS rounds_review_status ON rounds_review(status);
        CREATE TABLE IF NOT EXISTS rounds_alerts (key TEXT PRIMARY KEY, created_at INTEGER);
        ''')

# ------------------------------------------------------------------ identity matching
def canon_email(email):
    local, _, dom = str(email or '').strip().lower().partition('@')
    local = local.split('+', 1)[0]
    if dom in ('gmail.com', 'googlemail.com'):
        local, dom = local.replace('.', ''), 'gmail.com'
    return local + '@' + dom if dom else ''

def canon_name(name):
    words = [w for w in re.sub(r"[^a-z\s-]", ' ', str(name or '').lower()).replace('-', ' ').split() if w not in TITLES]
    return (words[0] + ' ' + words[-1]) if len(words) >= 2 else ''

def canon_phone(phone):
    digits = re.sub(r'\D', '', str(phone or ''))
    return digits[-10:] if len(digits) >= 9 else ''

def canon_address(ship_json=None, text=None):
    line1 = postcode = ''
    if ship_json:
        try:
            a = (json.loads(ship_json) or {}).get('address') or {}
            line1, postcode = a.get('line1') or '', a.get('postal_code') or ''
        except (ValueError, AttributeError):
            pass
    elif text:
        m = re.search(r'([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})', str(text).upper())
        postcode, line1 = (m.group(1) if m else ''), str(text).split(',')[0].split('\n')[0]
    postcode = re.sub(r'\s', '', postcode.upper())
    line1 = re.sub(r'[^a-z0-9]', '', line1.lower())
    return (postcode + '|' + line1) if postcode and line1 else ''

def alias_of_member(email):
    """True when this email is another spelling of an existing member's email (Gmail dots, +tags, googlemail)."""
    c = canon_email(email)
    with shop.db() as conn:
        return any(canon_email(r[0]) == c for r in conn.execute('SELECT email FROM rounds_members WHERE email<>?', (str(email).lower(),)))

def identity(email, exclude_order=None):
    """Everything we know about the person behind one email: names, phones, addresses."""
    email = str(email or '').lower()
    names, phones, addrs = set(), set(), set()
    with shop.db() as conn:
        for r in conn.execute('SELECT order_number, customer_name, phone, shipping_json FROM orders WHERE lower(email)=? AND paid_at IS NOT NULL', (email,)):
            if r['order_number'] == exclude_order:
                continue
            names.add(canon_name(r['customer_name'])); phones.add(canon_phone(r['phone'])); addrs.add(canon_address(r['shipping_json']))
        for r in conn.execute('SELECT customer_name, phone, address FROM invoices WHERE lower(email)=?', (email,)):
            names.add(canon_name(r['customer_name'])); phones.add(canon_phone(r['phone'])); addrs.add(canon_address(text=r['address']))
        row = conn.execute('SELECT name FROM customer_notes WHERE email=?', (email,)).fetchone() if _has(conn, 'customer_notes') else None
        if row:
            names.add(canon_name(row['name']))
    return {k: {x for x in v if x} for k, v in (('names', names), ('phones', phones), ('addrs', addrs))}

def _has(conn, table):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())

def order_identity(order):
    return {'names': {canon_name(order.get('customer_name'))} - {''}, 'phones': {canon_phone(order.get('phone'))} - {''},
            'addrs': {canon_address(order.get('shipping_json'))} - {''}}

def referral_problems(order, referee, referrer):
    """Reasons why this referral should not be paid automatically (empty list = clean)."""
    problems = []
    if canon_email(referee) == canon_email(referrer):
        problems.append('The new customer’s email is the same address as the referrer’s (e.g. dots or +tags).')
    new = order_identity(order)
    for k, v in identity(referee, exclude_order=order['order_number']).items():
        new[k] |= v
    ref = identity(referrer)
    labels = {'names': 'same first and last name', 'phones': 'same phone number', 'addrs': 'same delivery address'}
    for k, label in labels.items():
        if new[k] & ref[k]:
            problems.append('The new customer has the %s as the referrer (%s).' % (label, referrer))
    # Not a new customer? Compare with every other customer who bought before.
    with shop.db() as conn:
        others = [dict(r) for r in conn.execute('SELECT email, customer_name, phone, shipping_json FROM orders WHERE paid_at IS NOT NULL '
                                                'AND lower(email)<>? AND lower(email)<>? AND order_number<>? AND paid_at<=?',
                                                (referee.lower(), referrer.lower(), order['order_number'], order.get('paid_at') or int(time.time())))]
        members = [r[0] for r in conn.execute('SELECT email FROM rounds_members WHERE email<>?', (referee,))]
    seen = set()
    for o in others:
        hit = [labels[k] for k, v in order_identity(o).items() if v & new[k]]
        if hit and o['email'] not in seen:
            seen.add(o['email'])
            problems.append('The new customer may already be a customer as %s (%s).' % (o['email'], ', '.join(hit)))
    c = canon_email(referee)
    for e in members:
        if e != referrer and canon_email(e) == c:
            problems.append('The new customer’s email is an alias of an existing member (%s).' % e)
    return problems[:8]

# ------------------------------------------------------------------ referrals
def on_referral(order, referee, referrer):
    """Called on the referred person's first paid order. Pays the referral round only if everything checks out."""
    import loyalty
    with shop.db() as conn:
        if conn.execute("SELECT 1 FROM rounds_ledger WHERE reason='referral' AND ref=?", (order['order_number'],)).fetchone() \
                or conn.execute("SELECT 1 FROM rounds_review WHERE kind='referral' AND email=?", (referee,)).fetchone():
            return False
        # one referral per person, ever: a referee that already earned someone a round can't do it again
        if conn.execute("SELECT 1 FROM rounds_ledger l JOIN orders o ON o.order_number=l.ref WHERE l.reason='referral' AND lower(o.email)=?",
                        (referee.lower(),)).fetchone():
            return False
    problems = referral_problems(order, referee, referrer)
    if problems:
        rid = _review('referral', referee, referrer, order['order_number'], problems)
        alert('Referral held for review — ' + referee,
              'A referral was held and no round has been given yet.\n\nNew customer: %s (order %s, %s)\nReferred by: %s\n\nWhy it was held:\n- %s\n\n'
              'Approve or decline it in your dashboard: %s/admin → Approvals (%s).' % (
                  referee, order['order_number'], order.get('customer_name') or 'no name', referrer, '\n- '.join(problems), _base(), rid))
        return False
    _pay_referral(referrer, order['order_number'])
    return True

def _pay_referral(referrer, order_number):
    import loyalty
    with shop._lock, shop.db() as conn:
        conn.execute('INSERT OR IGNORE INTO rounds_ledger (email, halves, reason, ref, created_at) VALUES (?,?,?,?,?)',
                     (referrer, loyalty.REFERRAL_BONUS, 'referral', order_number, int(time.time())))
    _threshold_alerts(referrer)
    request_rewards(referrer)

def referral_count(referrer):
    with shop.db() as conn:
        return conn.execute("SELECT COUNT(*) FROM rounds_ledger WHERE email=? AND reason='referral' AND halves>0", (referrer,)).fetchone()[0]

def _threshold_alerts(referrer):
    n = referral_count(referrer)
    for limit in ALERT_AT:
        if n > limit and _once('referrals>%d:%s' % (limit, referrer)):
            alert('Referral check — %s has referred %d customers' % (referrer, n),
                  '%s has now had %d referral rounds (more than %d).\n\nPlease check that these are genuine, different colleagues. '
                  'You can see every referral in your dashboard: %s/admin (search for %s).' % (referrer, n, limit, _base(), referrer))

def undo_referral_for_refund(order_number):
    """The referred person's first order was fully refunded: take the referral round back."""
    with shop._lock, shop.db() as conn:
        r = conn.execute("SELECT email, halves FROM rounds_ledger WHERE reason='referral' AND ref=? AND halves>0", (order_number,)).fetchone()
        if r:
            conn.execute('INSERT OR IGNORE INTO rounds_ledger (email, halves, reason, ref, created_at) VALUES (?,?,?,?,?)',
                         (r['email'], -r['halves'], 'refund', order_number + ' referral', int(time.time())))
        conn.execute("UPDATE rounds_review SET status='declined', decided_at=?, note='Order refunded' WHERE kind='referral' AND order_number=? AND status='pending'",
                     (int(time.time()), order_number))

# ------------------------------------------------------------------ free sets (owner approval)
def request_rewards(email):
    """At 10 rounds: ask the owner to approve a free set. Nothing is issued automatically."""
    import loyalty
    made = []
    with shop.db() as conn:
        pending = conn.execute("SELECT COUNT(*) FROM rounds_review WHERE kind='reward' AND email=? AND status='pending'", (email,)).fetchone()[0]
    available = loyalty.balance(email) - pending * loyalty.REWARD_AT
    while available >= loyalty.REWARD_AT:
        made.append(_review('reward', email, None, None, ['Completed 10 rounds.']))
        available -= loyalty.REWARD_AT
    for rid in made:
        _tell_customer_pending(email)
        alert('Free set to approve — ' + email,
              '%s has completed 10 DocNova Rounds.\n\nPlease review their card and approve or decline the free set in your dashboard: '
              '%s/admin → Approvals (%s). The customer has been told their free set is being prepared; their code is only sent when you approve.'
              % (email, _base(), rid))
    return made

def _tell_customer_pending(email):
    import loyalty
    if not shop.smtp_configured():
        return
    base = _base()
    lead = ('<span style="font:26px/1.3 Georgia,serif;color:#182130">Congratulations — you’ve completed 10 rounds.</span><br><br>'
            'Your free DocNova scrub set is being prepared. Our team is checking your card now, and we’ll email your personal free-set code '
            'as soon as it’s confirmed — usually within 2 working days.')
    text = ('Congratulations — you have completed 10 DocNova Rounds!\n\nYour free scrub set is being prepared. Our team is checking your card, and we will '
            'email your personal free-set code as soon as it is confirmed (usually within 2 working days).\n\nDocNova')
    try:
        shop.send_mail(email, 'You’ve completed 10 DocNova Rounds 🎉', text,
                       loyalty.card_html(lead, loyalty.REWARD_AT, base + '/#/rounds', 'View my Rounds card'))
    except Exception as e:
        shop.log_email_error('reward pending email', e)

def pending():
    with shop.db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM rounds_review WHERE status='pending' ORDER BY created_at")]
        recent = [dict(r) for r in conn.execute("SELECT * FROM rounds_review WHERE status<>'pending' ORDER BY decided_at DESC LIMIT 30")]
    import loyalty
    for r in rows + recent:
        r['reasons'] = json.loads(r['reasons'] or '[]')
        r['balance'] = loyalty.fmt(loyalty.balance(r['email']))
        r['referrals'] = referral_count(r['referrer'] or r['email'])
    return {'pending': rows, 'recent': recent}

def decide(rid, approve, note=''):
    import loyalty
    with shop.db() as conn:
        r = conn.execute("SELECT * FROM rounds_review WHERE id=?", (rid,)).fetchone()
    if not r or r['status'] != 'pending':
        raise ValueError('This request has already been decided.')
    r = dict(r)
    code = None
    if approve and r['kind'] == 'referral':
        _pay_referral(r['referrer'], r['order_number'])
    if approve and r['kind'] == 'reward':
        if loyalty.balance(r['email']) < loyalty.REWARD_AT:
            raise ValueError('This card no longer has 10 rounds (for example after a return), so the free set can’t be approved.')
        code = loyalty.create_reward_code(r['email'])
    with shop._lock, shop.db() as conn:
        conn.execute("UPDATE rounds_review SET status=?, decided_at=?, note=?, code=? WHERE id=?",
                     ('approved' if approve else 'declined', int(time.time()), str(note or '')[:300] or None, code, rid))
    return {'status': 'approved' if approve else 'declined', 'code': code}

def _review(kind, email, referrer, order_number, reasons):
    rid = ('RV-' if kind == 'reward' else 'RF-CHK-') + secrets.token_hex(3).upper()
    with shop._lock, shop.db() as conn:
        conn.execute('INSERT INTO rounds_review (id, kind, email, referrer, order_number, reasons, created_at) VALUES (?,?,?,?,?,?,?)',
                     (rid, kind, email, referrer, order_number, json.dumps(reasons), int(time.time())))
    return rid

# ------------------------------------------------------------------ alerts
def _once(key):
    with shop._lock, shop.db() as conn:
        if conn.execute('SELECT 1 FROM rounds_alerts WHERE key=?', (key,)).fetchone():
            return False
        conn.execute('INSERT INTO rounds_alerts VALUES (?,?)', (key, int(time.time())))
    return True

def _base():
    return os.environ.get('DOCNOVA_PUBLIC_URL', 'https://docnova.co.uk').rstrip('/')

def alert(subject, body):
    to = os.getenv('DOCNOVA_ADMIN_EMAIL') or os.getenv('DOCNOVA_FROM_EMAIL') or 'info@docnova.co.uk'
    if not shop.smtp_configured():
        shop.log_email_error('admin alert (email off)', Exception(subject))
        return False
    try:
        shop.send_mail(to, '[DocNova Rounds] ' + subject, body + '\n\n— DocNova website')
        return True
    except Exception as e:
        shop.log_email_error('admin alert', e)
        return False
