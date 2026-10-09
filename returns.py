"""Returns and refunds for any paid sale (website order or invoice).

The owner picks the items coming back and the amount refunded. Each return:
  * removes the DocNova Rounds those items earned (a 'return' line on the customer's card),
  * marks the order Partially refunded / Refunded (and shows it on the invoice page),
  * can refund a card payment through Stripe straight away (only when the owner ticks it), and
  * can email the customer a refund confirmation (credit note) with their updated Rounds balance.
Bank-transfer and cash refunds are paid back by the owner as usual; this records them.
"""
import json, os, secrets, time
import shop, loyalty

def init_db():
    with shop._lock, shop.db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS returns (id TEXT PRIMARY KEY, order_number TEXT NOT NULL, created_at INTEGER NOT NULL,
            lines_json TEXT NOT NULL, amount INTEGER NOT NULL, method TEXT NOT NULL, reason TEXT, halves INTEGER NOT NULL DEFAULT 0,
            stripe_refund TEXT, emailed INTEGER DEFAULT 0)''')

def for_order(number):
    with shop.db() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM returns WHERE order_number=? ORDER BY created_at', (number,))]

def refunded_total(number):
    return sum(r['amount'] for r in for_order(number))

def _key(l):
    return l['id'] + '|' + l['size']

def summary(number):
    """What was bought, what has already come back, and what can still be returned."""
    order = shop.get_order(number)
    if not order:
        return None
    back = {}
    for r in for_order(number):
        for l in json.loads(r['lines_json']):
            back[_key(l)] = back.get(_key(l), 0) + l['qty']
    lines = json.loads(order['items_json'])
    goods = sum(l['unit_pence'] * l['qty'] for l in lines) or 1
    paid_goods = (order['total'] or 0) - (order['shipping'] or 0)     # what the customer actually paid for the items
    out = []
    for l in lines:
        # the price actually paid per item, after any order discount
        paid_each = round(l['unit_pence'] * paid_goods / goods) if goods else l['unit_pence']
        out.append({**l, 'name': shop.line_name(l), 'returned': back.get(_key(l), 0), 'returnable': l['qty'] - back.get(_key(l), 0), 'paid_each': max(paid_each, 0)})
    method = 'card' if order.get('payment_intent_id') else ('cash' if 'cash' in (order.get('note') or '') else 'bank')
    return {'order_number': number, 'email': order['email'], 'name': order['customer_name'], 'status': order['status'],
            'total': order['total'] or 0, 'shipping': order['shipping'] or 0, 'refunded': refunded_total(number), 'lines': out,
            'returns': for_order(number), 'default_method': method, 'can_card_refund': bool(order.get('payment_intent_id')) and shop.stripe_ready()}

def create(number, items, amount, method, reason='', stripe_refund=False, notify=True):
    """items: [{id, size, qty}] coming back. amount in pounds (what is paid back)."""
    order = shop.get_order(number)
    if not order or order['status'] not in ('paid', 'dispatched', 'delivered', 'partially_refunded'):
        raise ValueError('Only a paid order can be refunded.')
    info = summary(number)
    by_key = {_key(l): l for l in info['lines']}
    lines = []
    for it in items or []:
        try:
            qty = int(it.get('qty') or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        l = by_key.get(str(it.get('id')) + '|' + str(it.get('size')))
        if not l:
            raise ValueError('That item is not on this order.')
        if qty > l['returnable']:
            raise ValueError('Only %d × %s can still be returned.' % (l['returnable'], l['name']))
        lines.append({'id': l['id'], 'name': l['name'], 'size': l['size'], 'qty': qty, 'unit_pence': l['unit_pence']})
    try:
        pence = round(float(amount or 0) * 100)
    except (TypeError, ValueError):
        raise ValueError('Refund amount must be in pounds, e.g. 35.99.')
    left = info['total'] - info['refunded']
    if pence < 0 or pence > left:
        raise ValueError('The refund can be at most %s (the amount paid that has not been refunded yet).' % shop.money(left))
    if not lines and not pence:
        raise ValueError('Choose the items coming back and/or enter an amount to refund.')
    if method not in ('card', 'bank', 'cash', 'none'):
        raise ValueError('Unknown refund method.')
    rid = 'RF-' + time.strftime('%y%m%d') + '-' + secrets.token_hex(3).upper()
    stripe_id = None
    if stripe_refund and pence:
        if not order.get('payment_intent_id'):
            raise ValueError('This sale was not paid by card on the website, so it cannot be refunded through Stripe.')
        r = shop.stripe_request('POST', '/v1/refunds', {'payment_intent': order['payment_intent_id'], 'amount': pence,
                                                         'metadata': {'order_number': number, 'docnova_return': rid}},
                                idempotency_key='refund-' + rid)
        stripe_id = r.get('id')
        method = 'card'
    # Rounds: the items coming back lose what they earned (free items and reward sets earned nothing).
    halves = 0
    if order['email']:
        cat = shop.catalogue()
        halves = sum(loyalty.EARN.get((cat.get(l['id']) or {}).get('category'), 0) * l['qty'] for l in lines if l['unit_pence'] > 0)
        earned = 0
        with shop.db() as conn:
            row = conn.execute("SELECT COALESCE(SUM(halves),0) FROM rounds_ledger WHERE email=? AND reason='order' AND ref=?",
                               (loyalty.norm(order['email']), number)).fetchone()
            earned = row[0] if row else 0
            already = conn.execute("SELECT COALESCE(SUM(halves),0) FROM rounds_ledger WHERE email=? AND reason IN ('return','refund') AND ref LIKE ?",
                                   (loyalty.norm(order['email']), number + '%')).fetchone()[0]
        halves = min(halves, max(earned + already, 0))            # never take back more than this order earned
    now = int(time.time())
    with shop._lock, shop.db() as conn:
        conn.execute('INSERT INTO returns (id, order_number, created_at, lines_json, amount, method, reason, halves, stripe_refund) VALUES (?,?,?,?,?,?,?,?,?)',
                     (rid, number, now, json.dumps(lines), pence, method, str(reason or '').strip()[:300] or None, halves, stripe_id))
        if halves:
            conn.execute('INSERT INTO rounds_ledger (email, halves, reason, ref, created_at) VALUES (?,?,?,?,?)',
                         (loyalty.norm(order['email']), -halves, 'return', number + ' ' + rid, now))
    all_back = all(l['returnable'] - sum(x['qty'] for x in lines if _key(x) == _key(l)) <= 0 for l in info['lines'])
    fully_refunded = info['refunded'] + pence >= info['total']
    new_status = 'refunded' if (all_back and fully_refunded) or (fully_refunded and not lines) else 'partially_refunded'
    shop.update_order(number, status=new_status)
    if new_status == 'refunded':
        import guard
        guard.undo_referral_for_refund(number)
    if notify and order['email']:
        send_confirmation(rid)
    return {'id': rid, 'rounds_removed': loyalty.fmt(halves), 'refunded': shop.money(pence), 'stripe_refund': stripe_id,
            'balance': loyalty.fmt(loyalty.balance(loyalty.norm(order['email']))) if order['email'] else ''}

def send_confirmation(rid):
    with shop.db() as conn:
        r = conn.execute('SELECT * FROM returns WHERE id=?', (rid,)).fetchone()
    if not r or not shop.smtp_configured():
        return False
    r = dict(r)
    order = shop.get_order(r['order_number'])
    if not order or not order['email']:
        return False
    import invoices
    base = os.environ['DOCNOVA_PUBLIC_URL'].rstrip('/')
    lines = json.loads(r['lines_json'])
    items = ''.join('<li>%d × %s%s</li>' % (l['qty'], invoices._esc(shop.line_name(l)), '' if l['size'] == 'Standard' else ' (Size %s)' % invoices._esc(l['size'])) for l in lines)
    how = {'card': 'to the card you paid with (usually 5–10 working days)', 'bank': 'by bank transfer', 'cash': 'in cash', 'none': ''}.get(r['method'], '')
    ref = order['note'].split(' · ')[0] if (order.get('note') or '').startswith('Invoice ') else 'order ' + order['order_number']
    bal = loyalty.balance(loyalty.norm(order['email']))
    lead = ('<p style="margin:0 0 14px">We’ve processed your return for %s.</p>%s%s%s' % (
        invoices._esc(ref), ('<p style="margin:0 0 6px"><strong style="color:#182130">Items returned</strong></p><ul style="margin:0 0 14px;padding-left:20px">%s</ul>' % items) if items else '',
        ('<p style="margin:0 0 14px"><strong style="color:#182130">Refund: %s</strong> %s.</p>' % (shop.money(r['amount']), how)) if r['amount'] else '',
        ('<p style="margin:0">Your DocNova Rounds card has been updated (−%s for the returned items). You now have <strong style="color:#182130">%s of 10 rounds</strong>.</p>'
         % (loyalty.fmt(r['halves']), loyalty.fmt(max(bal, 0))) + '<div style="margin-top:16px">' + loyalty.card_img(bal) + '</div>') if r['halves'] else ''))
    text = 'We have processed your return for %s.\n\n%s\n%s\n%s\nReference: %s\n\nDocNova Ltd · Cambridge, UK\n' % (
        ref, '\n'.join('  %d × %s' % (l['qty'], shop.line_name(l)) for l in lines),
        ('Refund: %s %s.' % (shop.money(r['amount']), how)) if r['amount'] else '',
        ('Your DocNova Rounds card has been updated: you now have %s of 10 rounds.' % loyalty.fmt(max(bal, 0))) if r['halves'] else '', rid)
    try:
        shop.send_mail(order['email'], 'Your DocNova return and refund — ' + rid, text,
                       invoices._email_html(None, base, 'Return & refund confirmation', lead, 'Shop DocNova', base,
                                            '<tr><td style="padding:0 30px 20px;font:12px Arial;color:#8a909a;text-align:center">Reference %s</td></tr>' % invoices._esc(rid)))
    except Exception as e:
        shop.log_email_error('refund email ' + rid, e)
        return False
    with shop._lock, shop.db() as conn:
        conn.execute('UPDATE returns SET emailed=1 WHERE id=?', (rid,))
    return True

def sales(limit=500):
    """Every paid sale in one list: website orders and invoices."""
    with shop.db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT o.order_number, o.paid_at, o.email, o.customer_name, o.total, o.status, o.note, o.items_json, "
                                              "o.payment_intent_id, i.number AS invoice, i.token FROM orders o LEFT JOIN invoices i ON i.order_number=o.order_number "
                                              "WHERE o.paid_at IS NOT NULL ORDER BY o.paid_at DESC LIMIT ?", (limit,))]
        refunds = {r[0]: r[1] for r in conn.execute('SELECT order_number, SUM(amount) FROM returns GROUP BY order_number')}
    for r in rows:
        r['items'] = sum(l['qty'] for l in json.loads(r.pop('items_json') or '[]'))
        r['refunded'] = refunds.get(r['order_number'], 0)
        r['source'] = 'Invoice' if r['invoice'] else 'Website'
        r['label'] = shop.STATUS_LABELS.get(r['status'], r['status'])
        r['card'] = bool(r.pop('payment_intent_id'))
    return rows
