"""DocNova shop back office: catalogue prices, orders, Stripe Checkout and email.

Standard library only, so the site still runs with nothing but Python 3.10+.
Shared by server.py (the website) and orders.py (the order admin tool).
"""
from pathlib import Path
from email.message import EmailMessage
from urllib.parse import quote, urlencode
import hashlib, hmac, json, os, re, secrets, smtplib, sqlite3, ssl, threading, time
import http.client, urllib.error, urllib.request

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'private' / 'subscriptions.sqlite3'
STRIPE_API_VERSION = '2026-08-26.dahlia'
# Tags every Checkout Session so this flow can be found and compared in the Stripe Dashboard.
INTEGRATION_ID = 'docnova-storefront-checkout-qmvhtrkw'
SIZES = ('S', 'M', 'L', 'XL')
MAX_QTY_PER_LINE, MAX_LINES = 20, 30
DELIVERY_PENCE, FREE_DELIVERY_FROM_PENCE = 495, 10000
COUPONS = {'WELCOME10': {'stripe_id': 'docnova-welcome10', 'percent_off': 10, 'name': 'WELCOME10 · 10% off products'}}
ORDER_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'  # no 0/O, 1/I/L: easy to read over the phone

# ---------------------------------------------------------------- settings
def load_env_file():
    """Read KEY=VALUE lines from .env (git-ignored). Real environment variables win."""
    path = ROOT / '.env'
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

load_env_file()

def stripe_key():
    return os.getenv('STRIPE_SECRET_KEY', '').strip()

def stripe_mode():
    """'test', 'live', 'blocked-live' or 'off'. Live keys need DOCNOVA_STRIPE_LIVE=1 as a deliberate switch."""
    key = stripe_key()
    if not key:
        return 'off'
    if '_live_' in key:
        return 'live' if os.getenv('DOCNOVA_STRIPE_LIVE') == '1' else 'blocked-live'
    return 'test'

def stripe_ready():
    return stripe_mode() in ('test', 'live')

def smtp_configured():
    return all(os.getenv(k) for k in ('DOCNOVA_SMTP_HOST', 'DOCNOVA_FROM_EMAIL', 'DOCNOVA_PUBLIC_URL'))

# ---------------------------------------------------------------- catalogue
_catalogue_cache = {'mtime': None, 'items': {}}

def _field(obj, name):
    m = re.search(name + r":'((?:[^'\\]|\\.)*)'", obj)
    return m.group(1) if m else None

def catalogue():
    """Prices come from dist/app.js, the same file the shop page uses, so they can never drift apart.

    Mirrors the catalogue code at the top of app.js: the listed products, plus a separate top and
    trousers for every photographed scrub colour, plus the medical devices.
    """
    path = ROOT / 'dist' / 'app.js'
    mtime = path.stat().st_mtime
    if _catalogue_cache['mtime'] == mtime:
        return _catalogue_cache['items']
    src = path.read_text(encoding='utf-8')
    start = src.index('const products=[')
    listed = src[start:src.index('];', start)]
    pushed_start = re.search(r"products\.push\(\s*\{id:'", src[start:]).start() + start  # the device list, not the loop's push
    pushed = src[pushed_start:src.index(');', pushed_start)]

    def parse(block):
        out = []
        for obj in re.findall(r"\{id:'[^\n]*?\}(?=\s*[,\]\)]|$)", block, flags=re.M):
            price = re.search(r'price:([0-9]+(?:\.[0-9]{1,2})?)', obj)
            out.append({'id': _field(obj, 'id'), 'name': _field(obj, 'name'), 'category': _field(obj, 'category'),
                        'fit': _field(obj, 'fit'), 'color': _field(obj, 'color'), 'device': 'device:true' in obj,
                        'pence': round(float(price.group(1)) * 100)})
        return out

    products = parse(listed)
    for look in list(products):
        for category, label, price in (('Tops', 'Top', 4499), ('Pants', 'Trousers', 4999)):
            if not any(p['fit'] == look['fit'] and p['color'] == look['color'] and p['category'] == category for p in products):
                pid = f"{look['fit'].lower()}-{look['color'].lower().replace(' ', '-')}-{'top' if category == 'Tops' else 'trousers'}"
                products.append({**look, 'id': pid, 'name': f"DocNova Premium Scrub {label} – {look['color']}",
                                 'category': category, 'pence': price})
    products += parse(pushed)
    items = {p['id']: p for p in products if p['id'] and p['pence'] > 0}
    if len(items) < 10:
        raise RuntimeError('Could not read the product catalogue from dist/app.js')
    _catalogue_cache.update(mtime=mtime, items=items)
    return items

def price_cart(raw_items, coupon=''):
    """Turn the browser's bag into trusted order lines. Only ids, sizes and quantities are taken from the browser."""
    if not isinstance(raw_items, list) or not raw_items or len(raw_items) > MAX_LINES:
        raise ValueError('Your bag is empty or too large.')
    cat, lines = catalogue(), {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError('Invalid bag item.')
        pid, size, qty = str(raw.get('id', '')), str(raw.get('size', '')), raw.get('qty')
        product = cat.get(pid)
        if not product:
            raise ValueError('One of the products in your bag is no longer available.')
        if product['device'] and size != 'Standard' or not product['device'] and size not in SIZES:
            raise ValueError('Please choose a valid size for ' + product['name'] + '.')
        if not isinstance(qty, int) or isinstance(qty, bool) or not 1 <= qty <= MAX_QTY_PER_LINE:
            raise ValueError('Quantities must be between 1 and %d.' % MAX_QTY_PER_LINE)
        key = (pid, size)
        if key in lines:
            lines[key]['qty'] = min(MAX_QTY_PER_LINE, lines[key]['qty'] + qty)
        else:
            lines[key] = {'id': pid, 'name': product['name'], 'size': size, 'qty': qty, 'unit_pence': product['pence']}
    lines = list(lines.values())
    subtotal = sum(l['unit_pence'] * l['qty'] for l in lines)
    code = str(coupon or '').strip().upper()
    code = code if code in COUPONS else ''
    discount = (subtotal * COUPONS[code]['percent_off'] + 50) // 100 if code else 0  # round half up, like the site
    shipping = 0 if subtotal >= FREE_DELIVERY_FROM_PENCE else DELIVERY_PENCE  # threshold uses pre-discount subtotal, as on the site
    return {'lines': lines, 'subtotal': subtotal, 'discount': discount, 'shipping': shipping,
            'total': subtotal - discount + shipping, 'coupon': code}

# ---------------------------------------------------------------- database
_lock = threading.RLock()

def db():
    DB.parent.mkdir(exist_ok=True, mode=0o700)
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with _lock, db() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS subscribers (email TEXT PRIMARY KEY, token TEXT UNIQUE, consent_at INTEGER, status TEXT, attempted_at INTEGER);
        CREATE TABLE IF NOT EXISTS orders (
            order_number TEXT PRIMARY KEY, status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            items_json TEXT NOT NULL, coupon TEXT, subtotal INTEGER, discount INTEGER, shipping INTEGER, total INTEGER, currency TEXT DEFAULT 'gbp',
            email TEXT, customer_name TEXT, phone TEXT, shipping_json TEXT,
            checkout_session_id TEXT UNIQUE, payment_intent_id TEXT, livemode INTEGER,
            paid_at INTEGER, carrier TEXT, tracking_number TEXT, tracking_url TEXT, dispatched_at INTEGER, delivered_at INTEGER,
            confirmation_sent INTEGER DEFAULT 0, dispatch_sent INTEGER DEFAULT 0, note TEXT);
        CREATE INDEX IF NOT EXISTS orders_payment_intent ON orders(payment_intent_id);
        CREATE TABLE IF NOT EXISTS stripe_events (id TEXT PRIMARY KEY, type TEXT, received_at INTEGER);
        ''')
    os.chmod(DB, 0o600)

def new_order_number():
    return 'DN-' + time.strftime('%y%m%d') + '-' + ''.join(secrets.choice(ORDER_ALPHABET) for _ in range(5))

def create_order(priced):
    now = int(time.time())
    with _lock, db() as conn:
        for _ in range(10):
            number = new_order_number()
            if not conn.execute('SELECT 1 FROM orders WHERE order_number=?', (number,)).fetchone():
                break
        conn.execute('INSERT INTO orders (order_number,status,created_at,updated_at,items_json,coupon,subtotal,discount,shipping,total) VALUES (?,?,?,?,?,?,?,?,?,?)',
                     (number, 'awaiting_payment', now, now, json.dumps(priced['lines']), priced['coupon'],
                      priced['subtotal'], priced['discount'], priced['shipping'], priced['total']))
    return number

def get_order(number=None, session_id=None, payment_intent=None):
    with db() as conn:
        if number:
            row = conn.execute('SELECT * FROM orders WHERE order_number=?', (number,)).fetchone()
        elif session_id:
            row = conn.execute('SELECT * FROM orders WHERE checkout_session_id=?', (session_id,)).fetchone()
        else:
            row = conn.execute('SELECT * FROM orders WHERE payment_intent_id=?', (payment_intent,)).fetchone()
    return dict(row) if row else None

def update_order(number, **fields):
    fields['updated_at'] = int(time.time())
    cols = ', '.join(k + '=?' for k in fields)
    with _lock, db() as conn:
        conn.execute('UPDATE orders SET ' + cols + ' WHERE order_number=?', (*fields.values(), number))

STATUS_LABELS = {
    'awaiting_payment': 'Awaiting payment', 'processing': 'Payment processing', 'paid': 'Order confirmed',
    'dispatched': 'Dispatched', 'delivered': 'Delivered', 'payment_failed': 'Payment failed',
    'expired': 'Checkout not completed', 'cancelled': 'Cancelled', 'refunded': 'Refunded', 'partially_refunded': 'Partially refunded'}
# Later stages are never pulled back by a late or repeated Stripe event.
RANK = {'awaiting_payment': 0, 'expired': 1, 'payment_failed': 1, 'processing': 2, 'paid': 3, 'dispatched': 4, 'delivered': 5,
        'partially_refunded': 6, 'cancelled': 7, 'refunded': 7}

def public_order(order):
    """What a customer may see about their own order."""
    ship = json.loads(order['shipping_json']) if order.get('shipping_json') else None
    address = None
    if ship and ship.get('address'):
        a = ship['address']
        address = ', '.join(x for x in (a.get('line1'), a.get('line2'), a.get('city'), a.get('postal_code')) if x)
    return {'order_number': order['order_number'], 'status': order['status'], 'status_label': STATUS_LABELS.get(order['status'], order['status']),
            'created_at': order['created_at'], 'paid_at': order['paid_at'], 'dispatched_at': order['dispatched_at'], 'delivered_at': order['delivered_at'],
            'items': [{k: l[k] for k in ('name', 'size', 'qty', 'unit_pence')} for l in json.loads(order['items_json'])],
            'subtotal': order['subtotal'], 'discount': order['discount'], 'shipping': order['shipping'], 'total': order['total'],
            'coupon': order['coupon'], 'carrier': order['carrier'], 'tracking_number': order['tracking_number'],
            'tracking_url': order['tracking_url'], 'ship_to': address, 'name': order['customer_name']}

# ---------------------------------------------------------------- Stripe API (plain HTTPS, no SDK)
class StripeError(Exception):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status, self.code = status, code

def _flatten(value, prefix=''):
    """Stripe's form encoding: {'a': {'b': [{'c': 1}]}} -> a[b][0][c]=1"""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _flatten(v, f'{prefix}[{k}]' if prefix else k)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _flatten(v, f'{prefix}[{i}]')
    elif isinstance(value, bool):
        yield prefix, 'true' if value else 'false'
    elif value is not None:
        yield prefix, str(value)

def stripe_request(method, path, params=None, idempotency_key=None):
    if not stripe_ready():
        raise StripeError('Stripe is not configured.', 503)
    base = os.getenv('STRIPE_API_BASE', 'https://api.stripe.com').rstrip('/')  # override only for automated tests
    body = urlencode(list(_flatten(params or {}))).encode() if params and method == 'POST' else None
    url = base + path + ('?' + urlencode(list(_flatten(params))) if params and method == 'GET' else '')
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header('Authorization', 'Bearer ' + stripe_key())
    req.add_header('Stripe-Version', STRIPE_API_VERSION)
    req.add_header('User-Agent', 'DocNova-Storefront/1.0 (python-stdlib)')
    if body is not None:
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    if idempotency_key:
        req.add_header('Idempotency-Key', idempotency_key)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read()).get('error', {})
        except ValueError:
            err = {}
        raise StripeError(err.get('message') or 'Stripe request failed', e.code, err.get('code')) from None
    except urllib.error.URLError as e:
        raise StripeError('Could not reach Stripe: ' + str(e.reason), 502) from None
    except (OSError, http.client.HTTPException, ValueError) as e:  # dropped connection, timeout, bad reply
        raise StripeError('Stripe connection problem: ' + e.__class__.__name__, 502) from None

def ensure_coupon(code):
    """Create the Stripe coupon behind a site discount code the first time it is used."""
    spec = COUPONS[code]
    try:
        return stripe_request('GET', '/v1/coupons/' + spec['stripe_id'])['id']
    except StripeError as e:
        if e.status != 404:
            raise
    try:
        return stripe_request('POST', '/v1/coupons', {'id': spec['stripe_id'], 'percent_off': spec['percent_off'],
                                                      'duration': 'once', 'name': spec['name']},
                              idempotency_key='coupon-' + spec['stripe_id'])['id']
    except StripeError as e:
        if e.code == 'resource_already_exists':
            return spec['stripe_id']
        raise

def create_checkout_session(order_number, priced, base_url):
    params = {
        'mode': 'payment',
        'line_items': [{'quantity': l['qty'], 'price_data': {
            'currency': 'gbp', 'unit_amount': l['unit_pence'],
            'product_data': {'name': l['name'] + ('' if l['size'] == 'Standard' else ' · Size ' + l['size']),
                             'metadata': {'docnova_id': l['id'], 'size': l['size']}}}} for l in priced['lines']],
        # payment_method_types is deliberately omitted: Stripe shows every method switched on in the
        # Dashboard that suits this customer's device, country and basket (dynamic payment methods).
        'shipping_address_collection': {'allowed_countries': ['GB']},
        'shipping_options': [{'shipping_rate_data': {
            'type': 'fixed_amount', 'display_name': 'Free UK delivery' if priced['shipping'] == 0 else 'Standard UK delivery',
            'fixed_amount': {'amount': priced['shipping'], 'currency': 'gbp'},
            'delivery_estimate': {'minimum': {'unit': 'business_day', 'value': 3}, 'maximum': {'unit': 'business_day', 'value': 5}}}}],
        'phone_number_collection': {'enabled': True},
        'client_reference_id': order_number,
        'metadata': {'order_number': order_number},
        'payment_intent_data': {'description': 'DocNova order ' + order_number, 'metadata': {'order_number': order_number}},
        'custom_text': {'submit': {'message': 'Your DocNova order number is ' + order_number + '. Keep it to track your order.'}},
        'success_url': base_url + '/#/order-confirmed?session_id={CHECKOUT_SESSION_ID}',
        'cancel_url': base_url + '/#/checkout',
        'integration_identifier': INTEGRATION_ID,
    }
    if priced['coupon']:
        params['discounts'] = [{'coupon': ensure_coupon(priced['coupon'])}]
    session = stripe_request('POST', '/v1/checkout/sessions', params, idempotency_key='checkout-' + order_number)
    fields = {'checkout_session_id': session['id'], 'livemode': int(bool(session.get('livemode')))}
    if session.get('amount_total') is not None:
        # Stripe's own figure is what the customer is charged, so the order record follows it to the penny.
        fields.update(total=session['amount_total'], discount=(session.get('total_details') or {}).get('amount_discount', priced['discount']))
    update_order(order_number, **fields)
    return session

def sync_from_session(session, event_type=None):
    """Bring our order in line with a Checkout Session from a verified webhook or a direct Stripe lookup."""
    number = (session.get('metadata') or {}).get('order_number') or session.get('client_reference_id')
    with _lock:
        order = get_order(number) if number else None
        if not order or (order['checkout_session_id'] and order['checkout_session_id'] != session.get('id')):
            return None
        pay = session.get('payment_status')
        if event_type == 'checkout.session.async_payment_failed':
            target = 'payment_failed'
        elif event_type == 'checkout.session.expired' or session.get('status') == 'expired':
            target = 'expired'
        elif pay in ('paid', 'no_payment_required'):
            target = 'paid'
        elif session.get('status') == 'complete':
            target = 'processing'  # e.g. a bank payment that confirms in a few days
        else:
            return order
        moved = RANK.get(target, 0) > RANK.get(order['status'], 0) or (target == 'payment_failed' and order['status'] == 'processing')
        if not moved and order['status'] not in ('awaiting_payment', 'processing'):
            return order  # a late or repeated event never rewrites a confirmed, dispatched or refunded order
        details = session.get('customer_details') or {}
        ship = (session.get('collected_information') or {}).get('shipping_details') or session.get('shipping_details')
        totals = session.get('total_details') or {}
        fields = {}
        if details.get('email'): fields['email'] = details['email'].lower()
        if details.get('phone'): fields['phone'] = details['phone']
        name = (ship or {}).get('name') or details.get('name')
        if name: fields['customer_name'] = name
        if ship: fields['shipping_json'] = json.dumps(ship)
        if session.get('payment_intent'):
            pi = session['payment_intent']
            fields['payment_intent_id'] = pi['id'] if isinstance(pi, dict) else pi
        if session.get('amount_total') is not None and target in ('paid', 'processing'):
            fields.update(total=session['amount_total'], discount=totals.get('amount_discount', order['discount']),
                          shipping=totals.get('amount_shipping', order['shipping']))
        if moved:
            fields['status'] = target
            if target == 'paid' and not order['paid_at']:
                fields['paid_at'] = int(time.time())
        if fields:
            update_order(number, **fields)
        order = get_order(number)
    if order['status'] == 'paid' and not order['confirmation_sent']:
        send_order_email(order, 'confirmation')
    return order

def refresh_order_from_stripe(order):
    """Used by the confirmation page when the webhook has not arrived yet."""
    if order and order['status'] in ('awaiting_payment', 'processing') and order['checkout_session_id'] and stripe_ready():
        try:
            session = stripe_request('GET', '/v1/checkout/sessions/' + order['checkout_session_id'])
            return sync_from_session(session) or order
        except StripeError:
            pass
    return order

def handle_refund(charge):
    pi = charge.get('payment_intent')
    order = get_order(payment_intent=pi['id'] if isinstance(pi, dict) else pi) if pi else None
    if not order:
        return None
    full = charge.get('refunded') or charge.get('amount_refunded', 0) >= charge.get('amount', 1)
    update_order(order['order_number'], status='refunded' if full else 'partially_refunded')
    return get_order(order['order_number'])

def verify_webhook(payload: bytes, header: str, secret: str, tolerance=300):
    """Stripe-Signature check: HMAC-SHA256 over '<timestamp>.<raw body>' with the endpoint's signing secret."""
    if not secret or not header:
        return False
    parts = [p.split('=', 1) for p in header.split(',') if '=' in p]
    stamps = [v for k, v in parts if k == 't']
    sigs = [v for k, v in parts if k == 'v1']
    if not stamps or not sigs or not stamps[0].isdigit() or abs(time.time() - int(stamps[0])) > tolerance:
        return False
    expected = hmac.new(secret.encode(), stamps[0].encode() + b'.' + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in sigs)

def process_event(event):
    """Returns True when handled or safely ignored. Each Stripe event is processed once."""
    with _lock, db() as conn:
        if conn.execute('SELECT 1 FROM stripe_events WHERE id=?', (event['id'],)).fetchone():
            return True
    obj = event['data']['object']
    etype = event['type']
    if etype in ('checkout.session.completed', 'checkout.session.async_payment_succeeded',
                 'checkout.session.async_payment_failed', 'checkout.session.expired'):
        sync_from_session(obj, etype)
    elif etype == 'charge.refunded':
        handle_refund(obj)
    with _lock, db() as conn:
        conn.execute('INSERT OR IGNORE INTO stripe_events VALUES (?,?,?)', (event['id'], etype, int(time.time())))
    return True

# ---------------------------------------------------------------- payment methods shown on the site
PAYMENT_METHOD_LABELS = {  # methods Stripe Checkout can offer to UK customers paying in GBP
    'card': 'Visa · Mastercard · Amex', 'apple_pay': 'Apple Pay', 'google_pay': 'Google Pay', 'link': 'Link',
    'paypal': 'PayPal', 'klarna': 'Klarna', 'afterpay_clearpay': 'Clearpay', 'revolut_pay': 'Revolut Pay',
    'amazon_pay': 'Amazon Pay', 'pay_by_bank': 'Pay by bank'}
_pm_cache = {'at': 0, 'data': None}

def payment_methods():
    if _pm_cache['data'] and time.time() - _pm_cache['at'] < 600:
        return _pm_cache['data']
    methods, source = ['card', 'apple_pay', 'google_pay', 'link'], 'default'
    if stripe_ready():
        try:
            configs = stripe_request('GET', '/v1/payment_method_configurations', {'limit': 50})['data']
            mine = next((c for c in configs if c.get('is_default') and not c.get('application')), None) or \
                   next((c for c in configs if c.get('is_default')), None)
            if mine:
                methods = [k for k in PAYMENT_METHOD_LABELS if isinstance(mine.get(k), dict) and mine[k].get('available')
                           and (mine[k].get('display_preference') or {}).get('value') == 'on']
                source = 'stripe'
        except (StripeError, KeyError, TypeError):
            pass
    data = {'source': source, 'methods': [{'type': m, 'label': PAYMENT_METHOD_LABELS[m]} for m in methods]}
    _pm_cache.update(at=time.time(), data=data)
    return data

# ---------------------------------------------------------------- email
def send_mail(to, subject, text, html=None):
    sender = os.environ['DOCNOVA_FROM_EMAIL']
    msg = EmailMessage()
    msg['Subject'], msg['From'], msg['To'] = subject, 'DocNova <' + sender + '>', to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype='html')
    port = int(os.getenv('DOCNOVA_SMTP_PORT', '587'))
    host = os.environ['DOCNOVA_SMTP_HOST']
    ctx = ssl.create_default_context()
    conn = smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) if port == 465 else smtplib.SMTP(host, port, timeout=20)
    with conn as smtp:
        if port != 465:
            smtp.starttls(context=ctx)
        if os.getenv('DOCNOVA_SMTP_USER'):
            smtp.login(os.environ['DOCNOVA_SMTP_USER'], os.environ.get('DOCNOVA_SMTP_PASSWORD', ''))
        smtp.send_message(msg)

def money(pence):
    return '£%.2f' % (pence / 100)

def send_order_email(order, kind):
    """Order confirmation / dispatch notice. Skipped quietly when SMTP is not set up (Stripe still sends its receipt)."""
    flag = 'confirmation_sent' if kind == 'confirmation' else 'dispatch_sent'
    if not smtp_configured() or not order.get('email') or order.get(flag):
        return False
    base = os.environ['DOCNOVA_PUBLIC_URL'].rstrip('/')
    track = base + '/#/track-order?order=' + quote(order['order_number'])
    items = '\n'.join('  %d × %s%s  %s' % (l['qty'], l['name'], '' if l['size'] == 'Standard' else ' (' + l['size'] + ')',
                                           money(l['unit_pence'] * l['qty'])) for l in json.loads(order['items_json']))
    if kind == 'confirmation':
        subject = 'Your DocNova order ' + order['order_number'] + ' is confirmed'
        text = ('Thank you for your order.\n\nOrder number: %s\n\n%s\n\nDelivery: %s\nTotal paid: %s\n\n'
                'We will email you again with a tracking number as soon as it is dispatched.\nTrack your order: %s\n'
                % (order['order_number'], items, money(order['shipping'] or 0), money(order['total']), track))
    else:
        subject = 'Your DocNova order ' + order['order_number'] + ' is on its way'
        text = ('Good news: your order has been dispatched.\n\nOrder number: %s\nCarrier: %s\nTracking number: %s\n%s\n'
                'Track your order: %s\n' % (order['order_number'], order['carrier'] or '-', order['tracking_number'] or '-',
                                            ('Carrier tracking: ' + order['tracking_url'] + '\n') if order['tracking_url'] else '', track))
    try:
        send_mail(order['email'], subject, text)
    except Exception:
        return False
    update_order(order['order_number'], **{flag: 1})
    return True

CARRIER_TRACKING = {'royal mail': 'https://www.royalmail.com/track-your-item#/tracking-results/{n}'}

def tracking_url_for(carrier, number):
    template = CARRIER_TRACKING.get((carrier or '').strip().lower())
    return template.format(n=quote(number)) if template and number else None

# ---------------------------------------------------------------- Google reviews (Places API, New)
# DocNova's Google Business Profile. Confirmed from the business's Google share link (feature id 0x658367b6603e87b1:0xe74d21f50632db40).
DEFAULT_PLACE_ID = 'ChIJsYc-YLZng2URQNsyBvUhTec'
_reviews_cache = {'at': 0, 'data': None}
REVIEWS_TTL = 3600  # one Google call per hour at most: ~720 a month, inside Google's 1,000 free calls

def google_links(place_id):
    return {'maps': 'https://www.google.com/maps/place/?q=place_id:' + place_id,
            'write': 'https://search.google.com/local/writereview?placeid=' + place_id}

def featured_reviews():
    """Real Google reviews copied into data/featured-reviews.json, shown until the live connection is set up."""
    try:
        data = json.loads((ROOT / 'data' / 'featured-reviews.json').read_text(encoding='utf-8'))
        reviews = [r for r in data.get('reviews', []) if r.get('author') and r.get('text')][:4]
        return {'rating': data.get('rating'), 'count': data.get('count'), 'reviews': reviews} if reviews else None
    except (OSError, ValueError):
        return None

def google_reviews():
    """Live rating and reviews from Google. Held in memory for an hour only; never written to disk."""
    place_id = os.getenv('GOOGLE_PLACE_ID', DEFAULT_PLACE_ID).strip()
    key = os.getenv('GOOGLE_PLACES_API_KEY', '').strip()
    base = {'links': google_links(place_id), 'source': 'google'}
    base['featured'] = featured_reviews()
    if not key:
        return {**base, 'live': False}
    now = time.time()
    if _reviews_cache['data'] and now - _reviews_cache['at'] < REVIEWS_TTL:
        return _reviews_cache['data']
    req = urllib.request.Request(os.getenv('GOOGLE_PLACES_API_BASE', 'https://places.googleapis.com').rstrip('/') + '/v1/places/' + quote(place_id) + '?languageCode=en&regionCode=GB')
    req.add_header('X-Goog-Api-Key', key)
    req.add_header('X-Goog-FieldMask', 'displayName,rating,userRatingCount,googleMapsUri,reviews')
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            raw = json.loads(res.read())
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError):
        # Google unreachable: keep showing the last answer for up to 6 hours, then fall back to links only.
        if _reviews_cache['data'] and now - _reviews_cache['at'] < 6 * 3600:
            return _reviews_cache['data']
        return {**base, 'live': False}
    reviews = []
    for r in raw.get('reviews') or []:
        author = r.get('authorAttribution') or {}
        text = (r.get('text') or r.get('originalText') or {}).get('text', '')
        reviews.append({'author': author.get('displayName') or 'Google user', 'author_url': author.get('uri'),
                        'photo': author.get('photoUri'), 'rating': r.get('rating'), 'text': text,
                        'when': r.get('relativePublishTimeDescription'), 'published': r.get('publishTime'),
                        'url': r.get('googleMapsUri')})
    data = {**base, 'live': True, 'name': (raw.get('displayName') or {}).get('text', 'DocNova'),
            'rating': raw.get('rating'), 'count': raw.get('userRatingCount'),
            'maps_url': raw.get('googleMapsUri') or base['links']['maps'], 'reviews': reviews}
    _reviews_cache.update(at=now, data=data)
    return data
