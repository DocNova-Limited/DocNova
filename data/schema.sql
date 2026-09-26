-- server.py creates this automatically in private/subscriptions.sqlite3.
CREATE TABLE IF NOT EXISTS subscribers (
    email TEXT PRIMARY KEY,
    token TEXT UNIQUE,
    consent_at INTEGER,
    status TEXT,
    attempted_at INTEGER
);
