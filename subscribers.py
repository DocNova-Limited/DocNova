"""DocNova newsletter subscribers. Run from this folder:

  python3 subscribers.py list                 everyone who signed up, newest first
  python3 subscribers.py export               save subscribers.csv (active subscribers only) for your email tool
  python3 subscribers.py export --all         include people who unsubscribed

Status: sent = welcome email delivered · pending = saved, email sends once email is set up · unsubscribed.
"""
import argparse, csv, sqlite3, time
import shop

shop.init_db()
LABELS = {'sent': 'Welcome email sent', 'pending': 'Waiting to send', 'sending': 'Sending', 'failed': 'Will retry', 'unsubscribed': 'Unsubscribed'}

def rows(include_unsubscribed=True):
    with sqlite3.connect(shop.DB) as conn:
        q = 'SELECT email, consent_at, status FROM subscribers ' + ('' if include_unsubscribed else "WHERE status != 'unsubscribed' ") + 'ORDER BY consent_at DESC'
        return conn.execute(q).fetchall()

def when(ts):
    return time.strftime('%d %b %Y %H:%M', time.localtime(ts)) if ts else '-'

def cmd_list(args):
    data = rows()
    if not data:
        return print('No subscribers yet.')
    active = sum(1 for r in data if r[2] != 'unsubscribed')
    print('%d subscribers (%d active)\n' % (len(data), active))
    for email, at, status in data:
        print('%-40s %-18s %s' % (email, when(at), LABELS.get(status, status)))

def cmd_export(args):
    data = rows(args.all)
    with open(args.file, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['email', 'subscribed_at', 'status'])
        for email, at, status in data:
            w.writerow([email, when(at), LABELS.get(status, status)])
    print('Saved %d subscribers to %s' % (len(data), args.file))

p = argparse.ArgumentParser(description='DocNova newsletter subscribers')
sub = p.add_subparsers(dest='cmd', required=True)
s = sub.add_parser('list'); s.set_defaults(fn=cmd_list)
s = sub.add_parser('export'); s.add_argument('--all', action='store_true'); s.add_argument('--file', default='subscribers.csv'); s.set_defaults(fn=cmd_export)
args = p.parse_args()
args.fn(args)
