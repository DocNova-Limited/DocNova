#!/bin/bash
# Connects the DocNova website to Stripe. The key is pasted here, stored only in the private .env file on this Mac.
cd "$(dirname "$0")" || exit 1
PY='/Users/drusmanmacbookpro/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
[ -x "$PY" ] || PY=python3
echo "DocNova Stripe setup (TEST MODE - no real money)"
echo "------------------------------------------------"
echo "In Stripe, click the Secret key (starts with sk_test_) to copy it."
echo "Then click inside this window, paste it with Command + V (it will NOT show on screen), and press Enter:"
read -r -s KEY
echo
"$PY" - "$KEY" <<'PYEOF'
import sys, os, json, base64, urllib.request, urllib.error
from pathlib import Path
key = sys.argv[1].strip()
if not (key.startswith('sk_test_') or key.startswith('rk_test_')):
    print('That does not look like a TEST secret key (it should start with sk_test_). Nothing was saved.'); sys.exit(1)
req = urllib.request.Request('https://api.stripe.com/v1/account', headers={'Authorization': 'Bearer ' + key})
try:
    acct = json.load(urllib.request.urlopen(req, timeout=20))
except urllib.error.HTTPError as e:
    print('Stripe did not accept that key (%s). Nothing was saved.' % e.code); sys.exit(1)
except Exception as e:
    print('Could not reach Stripe: %s' % e.__class__.__name__); sys.exit(1)
env = Path('.env'); lines = env.read_text().splitlines() if env.exists() else []
lines = [l for l in lines if not l.startswith('STRIPE_SECRET_KEY=')] + ['STRIPE_SECRET_KEY=' + key]
env.write_text('\n'.join(lines) + '\n'); os.chmod(env, 0o600)
name = (acct.get('settings') or {}).get('dashboard', {}).get('display_name') or acct.get('business_profile', {}).get('name') or acct.get('id')
print('Connected to Stripe account: %s (test mode).' % name)
PYEOF
if [ $? -eq 0 ]; then
  echo "Restarting the website with payments switched on…"
  open "Start DocNova.command"
fi
read -r -p "All done. You can close this window." _
