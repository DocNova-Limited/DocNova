-- server.py creates these automatically in private/subscriptions.sqlite3 (see shop.init_db).
CREATE TABLE IF NOT EXISTS subscribers (
    email TEXT PRIMARY KEY, token TEXT UNIQUE, consent_at INTEGER, status TEXT, attempted_at INTEGER
);
-- One row per checkout attempt. order_number (DN-YYMMDD-XXXXX) is what customers see and track.
-- status: awaiting_payment -> paid (or processing -> paid) -> dispatched -> delivered;
--         also expired, payment_failed, cancelled, refunded, partially_refunded.
CREATE TABLE IF NOT EXISTS orders (
    order_number TEXT PRIMARY KEY, status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    items_json TEXT NOT NULL, coupon TEXT, subtotal INTEGER, discount INTEGER, shipping INTEGER, total INTEGER, currency TEXT DEFAULT 'gbp',
    email TEXT, customer_name TEXT, phone TEXT, shipping_json TEXT,
    checkout_session_id TEXT UNIQUE, payment_intent_id TEXT, livemode INTEGER,
    paid_at INTEGER, carrier TEXT, tracking_number TEXT, tracking_url TEXT, dispatched_at INTEGER, delivered_at INTEGER,
    confirmation_sent INTEGER DEFAULT 0, dispatch_sent INTEGER DEFAULT 0, note TEXT
);
-- Stripe webhook event ids already processed (each event is applied once).
CREATE TABLE IF NOT EXISTS stripe_events (id TEXT PRIMARY KEY, type TEXT, received_at INTEGER);
