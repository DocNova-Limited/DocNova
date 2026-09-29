"""DocNova website server: storefront, Stripe Checkout, order tracking and newsletter. No third-party packages."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs
import json, os, re, secrets, sqlite3, time
import shop, loyalty
from shop import ROOT, DB

shop.init_db()
RATE = {}
ORDER_RE = re.compile(r'DN-\d{6}-[A-Z0-9]{5}')

def configured():
    return shop.smtp_configured()

def deliver(email, token):
    base = os.environ['DOCNOVA_PUBLIC_URL'].rstrip('/')
    link = base + '/unsubscribe?token=' + token
    template = (ROOT / 'email' / 'welcome.html').read_text()
    shop.send_mail(email, 'Welcome to DocNova — your first shift is on us (10% off inside)',
                   'Thank you for joining DocNova.\n\nAs a welcome gift, enjoy 10% off your first order with code FIRSTSHIFT10 at checkout '
                   '(products only, delivery excluded; one discount code per order).\n\nShop the collection: ' + base + '\n\n'
                   'DocNova Ltd · Registered in England & Wales No. 16502835 · Cambridge, UK\nUnsubscribe: ' + link,
                   template.replace('{{SHOP_URL}}', base).replace('{{UNSUBSCRIBE_URL}}', link))

def limited(ip, bucket, per_minute):
    now = int(time.time())
    key = (ip, bucket)
    recent = [t for t in RATE.get(key, []) if now - t < 60]
    if len(recent) >= per_minute:
        return True
    RATE[key] = recent + [now]
    return False

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'dist'), **kwargs)

    def log_message(self, *args):
        pass  # Never log addresses, tokens or order lookups.

    def reply(self, status, data):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def html(self, content, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(('<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DocNova email preferences</title><body style="font:18px/1.6 Arial;max-width:580px;margin:70px auto;padding:24px;color:#182130">' + content + '</body></html>').encode())

    def base_url(self):
        if os.getenv('DOCNOVA_PUBLIC_URL'):
            return os.environ['DOCNOVA_PUBLIC_URL'].rstrip('/')
        host = self.headers.get('Host', '')
        return 'http://' + (host if re.fullmatch(r'[A-Za-z0-9.\-]+(:\d+)?', host) else '127.0.0.1:4173')

    def read_body(self, limit):
        length = int(self.headers.get('Content-Length', '0'))
        if length < 1 or length > limit:
            raise ValueError('size')
        return self.rfile.read(length)

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == '/unsubscribe':
            token = parse_qs(url.query).get('token', [''])[0]
            if not re.fullmatch(r'[A-Za-z0-9_-]{30,100}', token):
                return self.html('<h1>Invalid link</h1><p>Please use the link in your welcome email.</p>', 400)
            return self.html('<h1>Email preferences</h1><p>Stop receiving DocNova marketing emails.</p><form method="post" action="/api/unsubscribe"><input type="hidden" name="token" value="' + token + '"><button style="padding:14px">Unsubscribe</button></form>')
        if url.path == '/api/payment-methods':
            data = shop.payment_methods()
            return self.reply(200, {**data, 'checkout_enabled': shop.stripe_ready(), 'mode': shop.stripe_mode()})
        if url.path == '/api/rounds/me':
            data = loyalty.dashboard(parse_qs(url.query).get('t', [''])[0])
            if not data:
                return self.reply(404, {'message': 'This link has expired. Enter your email below and we’ll send a fresh one.'})
            return self.reply(200, data)
        if url.path == '/api/reviews':
            return self.reply(200, shop.google_reviews())
        if url.path == '/api/order':
            sid = parse_qs(url.query).get('session_id', [''])[0]
            if not re.fullmatch(r'cs_[A-Za-z0-9_]{10,300}', sid):
                return self.reply(400, {'message': 'Invalid order link.'})
            order = shop.refresh_order_from_stripe(shop.get_order(session_id=sid))
            if not order:
                return self.reply(404, {'message': 'We could not find this order.'})
            return self.reply(200, shop.public_order(order))
        if url.path.startswith('/api/'):
            return self.reply(404, {'message': 'Not found'})
        return super().do_GET()

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        path = urlsplit(self.path).path
        if path == '/api/stripe/webhook':
            return self.stripe_webhook()
        if path not in ('/api/subscribe', '/api/unsubscribe', '/api/checkout', '/api/track', '/api/coupon', '/api/rounds/join', '/api/rounds/link'):
            return self.reply(404, {'message': 'Not found'})
        origin = self.headers.get('Origin')
        if origin and urlsplit(origin).netloc != self.headers.get('Host'):
            return self.reply(403, {'message': 'Please submit from the DocNova website.'})
        try:
            raw = self.read_body(16384 if path == '/api/checkout' else 2048).decode()
            if path == '/api/checkout':
                return self.checkout(json.loads(raw))
            if path == '/api/track':
                return self.track(json.loads(raw))
            if path == '/api/coupon':
                return self.coupon(json.loads(raw))
            if path in ('/api/rounds/join', '/api/rounds/link'):
                return self.rounds(path, json.loads(raw))
            if path == '/api/unsubscribe':
                token = parse_qs(raw).get('token', [''])[0]
                with sqlite3.connect(DB) as db:
                    db.execute("UPDATE subscribers SET status='unsubscribed' WHERE token=?", (token,))
                return self.html('<h1>You’re unsubscribed.</h1><p>You will no longer receive DocNova marketing emails.</p>')
            return self.subscribe(json.loads(raw))
        except (ValueError, UnicodeError, AttributeError):
            return self.reply(400, {'message': 'Invalid request.'})

    def checkout(self, data):
        if not shop.stripe_ready():
            msg = ('Live Stripe keys are blocked until DOCNOVA_STRIPE_LIVE=1 is set.' if shop.stripe_mode() == 'blocked-live'
                   else 'Online payment is not switched on yet. Please try again soon.')
            return self.reply(503, {'message': msg})
        if limited(self.client_address[0], 'checkout', 10):
            return self.reply(429, {'message': 'Please wait a minute before trying again.'})
        try:
            priced = shop.price_cart(data.get('items'), data.get('coupon'), str(data.get('delivery') or ''))
        except ValueError as e:
            return self.reply(400, {'message': str(e)})
        number = shop.create_order(priced)
        try:
            session = shop.create_checkout_session(number, priced, self.base_url())
        except shop.StripeError as e:
            shop.update_order(number, status='cancelled', note='Checkout could not start: ' + str(e)[:200])
            return self.reply(502, {'message': 'We could not start secure checkout. Please try again in a moment.'})
        return self.reply(200, {'url': session['url'], 'order_number': number})

    def rounds(self, path, data):
        if limited(self.client_address[0], 'rounds', 6):
            return self.reply(429, {'message': 'Please wait a minute before trying again.'})
        try:
            if path == '/api/rounds/join':
                if data.get('consent') is not True:
                    return self.reply(400, {'message': 'Please tick the box to agree to the DocNova Rounds terms.'})
                loyalty.join(data.get('email'), str(data.get('referrer') or ''), str(data.get('ref') or ''))
            else:
                loyalty.send_link(data.get('email'))
        except ValueError as e:
            return self.reply(400, {'message': str(e)})
        # Same reply whether or not the address is a member, so nobody can check who has joined.
        return self.reply(200, {'status': 'sent', 'email_enabled': configured()})

    def coupon(self, data):
        # Codes are checked here so private codes never appear in the website's code.
        if limited(self.client_address[0], 'coupon', 12):
            return self.reply(429, {'message': 'Too many attempts. Please wait a minute.'})
        info = shop.coupon_info(data.get('code'))
        if not info:
            return self.reply(404, {'message': 'That code is not recognised. Please check it and try again.'})
        return self.reply(200, info)

    def track(self, data):
        if limited(self.client_address[0], 'track', 10):
            return self.reply(429, {'message': 'Too many attempts. Please wait a minute.'})
        number = str(data.get('order_number', '')).strip().upper()
        email = str(data.get('email', '')).strip().lower()
        order = shop.get_order(number) if ORDER_RE.fullmatch(number) else None
        # Same answer for "no such order" and "wrong email", so order numbers can't be probed.
        if not order or not order['email'] or not secrets.compare_digest(order['email'].encode(), email.encode()):
            return self.reply(404, {'message': 'We couldn’t find an order with that number and email. Please check both and try again.'})
        return self.reply(200, shop.public_order(order))

    def stripe_webhook(self):
        try:
            payload = self.read_body(1024 * 1024)
        except ValueError:
            return self.reply(400, {'message': 'Invalid payload'})
        if not shop.verify_webhook(payload, self.headers.get('Stripe-Signature', ''), os.getenv('STRIPE_WEBHOOK_SECRET', '')):
            return self.reply(400, {'message': 'Invalid signature'})
        try:
            event = json.loads(payload)
            shop.process_event(event)
        except (ValueError, KeyError, TypeError):
            return self.reply(400, {'message': 'Malformed event'})
        except Exception:
            return self.reply(500, {'message': 'Temporary error'})  # Stripe retries automatically
        return self.reply(200, {'received': True})

    def subscribe(self, data):
        email = str(data.get('email', '')).strip().lower()
        if data.get('consent') is not True or len(email) > 254 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', email):
            return self.reply(400, {'message': 'Enter a valid email and confirm your subscription consent.'})
        now = int(time.time())
        if limited(self.client_address[0], 'subscribe', 5):
            return self.reply(429, {'message': 'Please wait a minute before trying again.'})
        with sqlite3.connect(DB) as db:
            row = db.execute('SELECT token,status,attempted_at FROM subscribers WHERE email=?', (email,)).fetchone()
            if row and row[1] == 'sent':
                return self.reply(200, {'status': 'sent'})
            if row and row[1] in ('sending', 'failed') and now - row[2] < 60:
                return self.reply(429, {'message': 'Please wait a minute before retrying.'})
            token = row[0] if row else secrets.token_urlsafe(32)
            status = 'sending' if configured() else 'pending'
            db.execute('INSERT INTO subscribers (email,token,consent_at,status,attempted_at) VALUES (?,?,?,?,?) ON CONFLICT(email) DO UPDATE SET consent_at=excluded.consent_at,status=excluded.status,attempted_at=excluded.attempted_at', (email, token, now, status, now))
        if status == 'pending':
            return self.reply(202, {'status': 'pending'})
        try:
            deliver(email, token)
        except Exception:
            with sqlite3.connect(DB) as db:
                db.execute("UPDATE subscribers SET status='failed' WHERE email=?", (email,))
            return self.reply(502, {'message': 'The welcome email could not be sent. Please try again later.'})
        with sqlite3.connect(DB) as db:
            db.execute("UPDATE subscribers SET status='sent' WHERE email=?", (email,))
        return self.reply(200, {'status': 'sent'})

def send_pending_welcomes():
    """Sign-ups saved while email wasn't set up (or that failed) get their welcome email once sending works."""
    import threading
    def loop():
        while True:
            try:
                with sqlite3.connect(DB) as conn:
                    rows = conn.execute("SELECT email, token FROM subscribers WHERE status IN ('pending','failed') AND attempted_at < ?",
                                        (int(time.time()) - 300,)).fetchall()
                for email, token in rows:
                    with sqlite3.connect(DB) as conn:
                        conn.execute("UPDATE subscribers SET status='sending', attempted_at=? WHERE email=?", (int(time.time()), email))
                    try:
                        deliver(email, token)
                        status = 'sent'
                    except Exception:
                        status = 'failed'
                    with sqlite3.connect(DB) as conn:
                        conn.execute('UPDATE subscribers SET status=? WHERE email=?', (status, email))
            except Exception:
                pass
            time.sleep(600)
    threading.Thread(target=loop, daemon=True).start()

def poll_pending_orders():
    """Without a webhook (e.g. testing on a laptop), ask Stripe every 30 seconds about recent unpaid orders."""
    import threading
    def loop():
        while True:
            time.sleep(30)
            try:
                with shop.db() as conn:
                    rows = [dict(r) for r in conn.execute(
                        "SELECT * FROM orders WHERE (status IN ('awaiting_payment','processing') OR (status='paid' AND confirmation_sent=0)) AND checkout_session_id IS NOT NULL AND created_at > ?",
                        (int(time.time()) - 86400,))]
            except Exception:
                continue
            for order in rows:  # one bad order never stops the others
                try:
                    shop.refresh_order_from_stripe(order)
                except Exception:
                    pass
    threading.Thread(target=loop, daemon=True).start()

if __name__ == '__main__':
    host, port = os.getenv('DOCNOVA_HOST', '127.0.0.1'), int(os.getenv('DOCNOVA_PORT', '4173'))
    print('DocNova preview: http://%s:%d/' % (host, port), flush=True)
    print('Email mode: ' + ('SMTP enabled' if configured() else 'local pending signups — email sending not configured'), flush=True)
    if configured():
        send_pending_welcomes()
    mode = shop.stripe_mode()
    print('Stripe: ' + {'off': 'not configured — checkout disabled (add STRIPE_SECRET_KEY to .env)',
                        'test': 'TEST mode — use Stripe test cards, no real money',
                        'live': 'LIVE mode — real payments',
                        'blocked-live': 'live key found but DOCNOVA_STRIPE_LIVE=1 is not set — checkout disabled'}[mode], flush=True)
    print('Google reviews: ' + ('live from Google (refreshed hourly)' if os.getenv('GOOGLE_PLACES_API_KEY') else 'not connected — showing links to your Google reviews (add GOOGLE_PLACES_API_KEY to .env)'), flush=True)
    if mode in ('test', 'live') and not os.getenv('STRIPE_WEBHOOK_SECRET'):
        print('No webhook secret set — checking Stripe every 30 seconds for new payments instead (fine for testing; set up the webhook before going live).', flush=True)
        poll_pending_orders()
    ThreadingHTTPServer((host, port), Handler).serve_forever()
