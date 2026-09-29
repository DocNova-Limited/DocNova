#!/bin/bash
# Connects the DocNova website to the info@docnova.co.uk mailbox (ZeenHost / cPanel).
# Your password is typed here, stored only in the private .env file on this Mac, and never shown or uploaded.
cd "$(dirname "$0")" || exit 1
PY='/Users/drusmanmacbookpro/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
[ -x "$PY" ] || PY=python3
echo "DocNova email setup"
echo "-------------------"
read -r -p "Email address to send from [info@docnova.co.uk]: " FROM
FROM=${FROM:-info@docnova.co.uk}
read -r -p "Mail server [mail.docnova.co.uk]: " HOST
HOST=${HOST:-mail.docnova.co.uk}
echo "Type the password for $FROM (nothing will appear as you type), then press Return:"
read -r -s PASS
echo
"$PY" - "$FROM" "$HOST" "$PASS" <<'PYEOF'
import sys, re, os, smtplib, ssl
from pathlib import Path
frm, host, pw = sys.argv[1], sys.argv[2], sys.argv[3]
ok = None
for port in (465, 587):
    try:
        ctx = ssl.create_default_context()
        s = smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) if port == 465 else smtplib.SMTP(host, port, timeout=20)
        if port == 587: s.starttls(context=ctx)
        s.login(frm, pw); s.quit(); ok = port; break
    except smtplib.SMTPAuthenticationError:
        print('The mail server rejected that password. Please run this again and check it.'); sys.exit(1)
    except Exception as e:
        print('Port %d: %s' % (port, e.__class__.__name__))
if not ok:
    print('Could not reach %s. Check the mail server name with ZeenHost (cPanel > Email Accounts > Connect Devices).' % host); sys.exit(1)
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
read -r -p "Press Return to close this window." _
