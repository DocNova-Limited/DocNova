"""DocNova Rounds admin. Run from this folder:

  python3 rounds.py list                       every member, their rounds and referrals
  python3 rounds.py show someone@nhs.net       one member's card, history and reward codes
  python3 rounds.py add someone@nhs.net 1 "Goodwill"      add (or with -1, remove) rounds; halves allowed, e.g. 0.5
  python3 rounds.py link someone@nhs.net       email the member a fresh link to their card
"""
import argparse, sys, time
import shop, loyalty

shop.init_db()

def when(ts):
    return time.strftime('%d %b %Y', time.localtime(ts)) if ts else '-'

def cmd_list(a):
    with shop.db() as conn:
        rows = conn.execute('''SELECT m.email, m.joined_at, m.referred_by, COALESCE(SUM(l.halves),0) AS h,
                               (SELECT COUNT(*) FROM rounds_members r WHERE r.referred_by=m.email) AS refs,
                               (SELECT COUNT(*) FROM rounds_rewards w WHERE w.email=m.email) AS rewards
                               FROM rounds_members m LEFT JOIN rounds_ledger l ON l.email=m.email
                               GROUP BY m.email ORDER BY h DESC''').fetchall()
    if not rows:
        return print('No DocNova Rounds members yet.')
    print('%-36s %-12s %7s %5s %8s  %s' % ('MEMBER', 'JOINED', 'ROUNDS', 'REFS', 'REWARDS', 'REFERRED BY'))
    for r in rows:
        print('%-36s %-12s %7s %5d %8d  %s' % (r['email'], when(r['joined_at']), loyalty.fmt(r['h']), r['refs'], r['rewards'], r['referred_by'] or ''))

def cmd_show(a):
    email = loyalty.norm(a.email)
    m = loyalty.member(email)
    if not m:
        sys.exit('Not a member: ' + a.email)
    print('Member     ', email, '· joined', when(m['joined_at']), '· referral code', m['ref_code'])
    print('Rounds     ', loyalty.fmt(loyalty.balance(email)), 'of 10', '· referred by', m['referred_by'] or '-')
    with shop.db() as conn:
        for r in conn.execute('SELECT * FROM rounds_ledger WHERE email=? ORDER BY id', (email,)):
            print('   %s  %6s  %-9s %s' % (when(r['created_at']), loyalty.fmt(r['halves']), r['reason'], r['ref']))
        for r in conn.execute('SELECT * FROM rounds_rewards WHERE email=?', (email,)):
            print('Reward     ', r['code'], '· issued', when(r['issued_at']), '· expires', when(r['expires_at']),
                  '· used on ' + r['redeemed_order'] if r['redeemed_order'] else '· not used yet')

def cmd_add(a):
    email = loyalty.norm(a.email)
    halves = round(float(a.rounds) * 2)
    if not email or not halves:
        sys.exit('Give an email and a number of rounds, e.g. 1 or -0.5')
    loyalty.ensure_member(email)
    with shop.db() as conn:
        conn.execute('INSERT INTO rounds_ledger (email, halves, reason, ref, created_at) VALUES (?,?,?,?,?)',
                     (email, halves, 'adjust', (a.reason or 'Manual adjustment')[:80] + ' #' + str(int(time.time())), int(time.time())))
    codes = loyalty.issue_rewards(email)
    print('Done. %s now has %s rounds.%s' % (email, loyalty.fmt(loyalty.balance(email)), (' Reward sent: ' + ', '.join(codes)) if codes else ''))

def cmd_link(a):
    print('Sent.' if loyalty.send_link(a.email) else 'Could not send (not a member, or email not set up).')

p = argparse.ArgumentParser(description='DocNova Rounds admin')
sub = p.add_subparsers(dest='cmd', required=True)
s = sub.add_parser('list'); s.set_defaults(fn=cmd_list)
s = sub.add_parser('show'); s.add_argument('email'); s.set_defaults(fn=cmd_show)
s = sub.add_parser('add'); s.add_argument('email'); s.add_argument('rounds'); s.add_argument('reason', nargs='?'); s.set_defaults(fn=cmd_add)
s = sub.add_parser('link'); s.add_argument('email'); s.set_defaults(fn=cmd_link)
a = p.parse_args()
a.fn(a)
