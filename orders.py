"""DocNova order admin. Run from this folder:

  python3 orders.py list                     recent orders (add --all to include unpaid/abandoned)
  python3 orders.py show DN-260926-7K4QP     full order with delivery address
  python3 orders.py ship DN-260926-7K4QP --carrier "Royal Mail" --tracking AB123456789GB [--url https://...]
  python3 orders.py delivered DN-260926-7K4QP
  python3 orders.py cancel DN-260926-7K4QP   (refund the payment in the Stripe Dashboard first)

Customers see every change straight away on the website's "Track your order" page.
"""
import argparse, json, sys, time
import shop

shop.init_db()

def when(ts):
    return time.strftime('%d %b %Y %H:%M', time.localtime(ts)) if ts else '-'

def find(number):
    order = shop.get_order(number.strip().upper())
    if not order:
        sys.exit('No order ' + number)
    return order

def cmd_list(args):
    with shop.db() as conn:
        q = 'SELECT * FROM orders ' + ('' if args.all else "WHERE status NOT IN ('awaiting_payment','expired','cancelled') ") + 'ORDER BY created_at DESC LIMIT ?'
        rows = [dict(r) for r in conn.execute(q, (args.limit,))]
    if not rows:
        return print('No orders yet.')
    print('%-18s %-20s %-17s %9s  %s' % ('ORDER', 'STATUS', 'PLACED', 'TOTAL', 'CUSTOMER'))
    for o in rows:
        print('%-18s %-20s %-17s %9s  %s' % (o['order_number'], shop.STATUS_LABELS.get(o['status'], o['status']),
                                             when(o['created_at']), shop.money(o['total'] or 0), o['customer_name'] or o['email'] or ''))

def cmd_show(args):
    o = find(args.order)
    print('Order     ', o['order_number'], ' [TEST MODE]' if o['livemode'] == 0 else '')
    print('Status    ', shop.STATUS_LABELS.get(o['status'], o['status']))
    print('Placed    ', when(o['created_at']), '   Paid:', when(o['paid_at']))
    print('Customer  ', o['customer_name'] or '-', '·', o['email'] or '-', '·', o['phone'] or '-')
    if o['shipping_json']:
        a = (json.loads(o['shipping_json']).get('address') or {})
        print('Ship to   ', ', '.join(x for x in (a.get('line1'), a.get('line2'), a.get('city'), a.get('state'), a.get('postal_code'), a.get('country')) if x))
    print('Items')
    for l in json.loads(o['items_json']):
        print('   %2d × %s%s   %s' % (l['qty'], l['name'], '' if l['size'] == 'Standard' else ' — size ' + l['size'], shop.money(l['unit_pence'] * l['qty'])))
    print('Subtotal  ', shop.money(o['subtotal']), '  Discount:', shop.money(o['discount'] or 0), (o['coupon'] or ''),
          '  Delivery:', shop.money(o['shipping'] or 0), '  TOTAL:', shop.money(o['total'] or 0))
    if o['tracking_number']:
        print('Tracking  ', o['carrier'], o['tracking_number'], o['tracking_url'] or '', '· dispatched', when(o['dispatched_at']))
    if o['payment_intent_id']:
        print('Stripe    ', o['payment_intent_id'], '(search this in the Stripe Dashboard)')
    if o['note']:
        print('Note      ', o['note'])

def cmd_ship(args):
    o = find(args.order)
    if o['status'] not in ('paid', 'dispatched'):
        sys.exit('Order is "%s" — only paid orders can be dispatched.' % shop.STATUS_LABELS.get(o['status'], o['status']))
    url = args.url or shop.tracking_url_for(args.carrier, args.tracking)
    shop.update_order(o['order_number'], status='dispatched', carrier=args.carrier, tracking_number=args.tracking,
                      tracking_url=url, dispatched_at=o['dispatched_at'] or int(time.time()))
    o = shop.get_order(o['order_number'])
    sent = shop.send_order_email(o, 'dispatch')
    print('Marked', o['order_number'], 'as dispatched with', args.carrier, args.tracking,
          '— dispatch email sent.' if sent else '— no email sent (SMTP not configured or already sent).')

def cmd_delivered(args):
    o = find(args.order)
    if o['status'] not in ('dispatched', 'paid'):
        sys.exit('Order is "%s".' % o['status'])
    shop.update_order(o['order_number'], status='delivered', delivered_at=int(time.time()))
    print('Marked', o['order_number'], 'as delivered.')

def cmd_cancel(args):
    o = find(args.order)
    shop.update_order(o['order_number'], status='cancelled', note=args.reason or o['note'])
    print('Cancelled', o['order_number'] + '. If it was paid, refund it in the Stripe Dashboard (Payments → the payment → Refund).')

p = argparse.ArgumentParser(description='DocNova order admin')
sub = p.add_subparsers(dest='cmd', required=True)
s = sub.add_parser('list'); s.add_argument('--all', action='store_true'); s.add_argument('--limit', type=int, default=50); s.set_defaults(fn=cmd_list)
s = sub.add_parser('show'); s.add_argument('order'); s.set_defaults(fn=cmd_show)
s = sub.add_parser('ship'); s.add_argument('order'); s.add_argument('--carrier', required=True); s.add_argument('--tracking', required=True); s.add_argument('--url'); s.set_defaults(fn=cmd_ship)
s = sub.add_parser('delivered'); s.add_argument('order'); s.set_defaults(fn=cmd_delivered)
s = sub.add_parser('cancel'); s.add_argument('order'); s.add_argument('--reason'); s.set_defaults(fn=cmd_cancel)
args = p.parse_args()
args.fn(args)
