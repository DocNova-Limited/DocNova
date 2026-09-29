#!/bin/bash
# Connects the DocNova website to the info@docnova.co.uk mailbox (ZeenHost / cPanel).
# Your password is typed here, stored only in the private .env file on this Mac, and never shown or uploaded.
cd "$(dirname "$0")" || exit 1
PY='/Users/drusmanmacbookpro/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
[ -x "$PY" ] || PY=python3
echo "DocNova email setup"
echo "-------------------"
FROM=info@docnova.co.uk
HOST=mail.docnova.co.uk
echo "Please type the password for info@docnova.co.uk"
echo "(the letters will NOT appear on screen - that is normal)"
echo "then press the Enter key on your keyboard (the big key on the right, may be marked return or ⏎):"
read -r -s PASS
echo
"$PY" - "$FROM" "$HOST" "$PASS" <<'PYEOF'
import sys, re, os, smtplib, ssl
from pathlib import Path
frm, host, pw = sys.argv[1], sys.argv[2], sys.argv[3]
import socket
print('Checking the connection to', host, '...')
for port in (443, 993):
    try:
        socket.create_connection((host, port), timeout=8).close(); print('  web/mail server reachable on port', port)
    except Exception as e:
        print('  port', port, 'not reachable:', e.__class__.__name__)
ok = None
for port in (465, 587, 2525, 25):
    try:
        ctx = ssl.create_default_context()
        s = smtplib.SMTP_SSL(host, port, context=ctx, timeout=12) if port == 465 else smtplib.SMTP(host, port, timeout=12)
        if port != 465:
            try: s.starttls(context=ctx)
            except smtplib.SMTPNotSupportedError: pass
        s.login(frm, pw); s.quit(); ok = port; break
    except smtplib.SMTPAuthenticationError:
        print('The mail server rejected that password. Please run this again and check it.'); sys.exit(1)
    except ssl.SSLCertVerificationError:
        print('Port %d: certificate name mismatch' % port)
    except Exception as e:
        print('Port %d: %s' % (port, e.__class__.__name__))
if not ok:
    print('Could not connect to send email. Please send a screenshot of this window to Claude.'); sys.exit(1)
env = Path('.env'); lines = env.read_text().splitlines() if env.exists() else []
vals = {'DOCNOVA_SMTP_HOST': host, 'DOCNOVA_SMTP_PORT': str(ok), 'DOCNOVA_SMTP_USER': frm, 'DOCNOVA_SMTP_PASSWORD': pw,
        'DOCNOVA_FROM_EMAIL': frm}
if not any(l.startswith('DOCNOVA_PUBLIC_URL=') and l.split('=',1)[1].strip() for l in lines):
    vals['DOCNOVA_PUBLIC_URL'] = 'http://127.0.0.1:4180'
keep = [l for l in lines if l.split('=',1)[0].strip() not in vals]
keep += ['%s=%s' % (k, v) for k, v in vals.items()]
env.write_text('\n'.join(keep) + '\n'); os.chmod(env, 0o600)
import shop
shop.send_mail(frm, 'DocNova website email is connected',
    'Good news: the DocNova website can now send welcome emails and order confirmations from ' + frm + '.')
print('Connected on port %d. A test email has been sent to %s.' % (ok, frm))
PYEOF
if [ $? -eq 0 ]; then
  echo
  echo "Done. Restarting the website so it starts sending emails…"
  open "Start DocNova.command"
fi
read -r -p "All done. You can close this window." _
