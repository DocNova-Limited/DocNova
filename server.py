"""DocNova local preview and optional SMTP welcome-email service. No third-party packages."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from email.message import EmailMessage
import json, os, re, secrets, smtplib, sqlite3, ssl, time
from urllib.parse import urlsplit, parse_qs
ROOT=Path(__file__).resolve().parent
DB=ROOT/'private'/'subscriptions.sqlite3'
DB.parent.mkdir(exist_ok=True,mode=0o700)
with sqlite3.connect(DB) as db:
    db.execute('CREATE TABLE IF NOT EXISTS subscribers (email TEXT PRIMARY KEY, token TEXT UNIQUE, consent_at INTEGER, status TEXT, attempted_at INTEGER)')
os.chmod(DB,0o600)
RATE={}
def configured():
    return all(os.getenv(k) for k in ('DOCNOVA_SMTP_HOST','DOCNOVA_FROM_EMAIL','DOCNOVA_PUBLIC_URL'))
def deliver(email,token):
    sender=os.environ['DOCNOVA_FROM_EMAIL']
    base=os.environ['DOCNOVA_PUBLIC_URL'].rstrip('/')
    link=base+'/unsubscribe?token='+token
    msg=EmailMessage();msg['Subject']='Welcome to DocNova — enjoy 10% off';msg['From']='DocNova <'+sender+'>';msg['To']=email
    msg.set_content('Welcome to DocNova!\n\nThank you for subscribing. Enjoy 10% off your products with code WELCOME10 at checkout. Delivery is excluded. One code per order.\n\nShop: '+base+'\n\nManage your subscription: '+link)
    template=(ROOT/'email'/'welcome.html').read_text()
    msg.add_alternative(template.replace('{{SHOP_URL}}',base).replace('{{UNSUBSCRIBE_URL}}',link),subtype='html')
    port=int(os.getenv('DOCNOVA_SMTP_PORT','587'))
    conn=smtplib.SMTP_SSL(os.environ['DOCNOVA_SMTP_HOST'],port,context=ssl.create_default_context(),timeout=20) if port==465 else smtplib.SMTP(os.environ['DOCNOVA_SMTP_HOST'],port,timeout=20)
    with conn as smtp:
        if port!=465:smtp.starttls(context=ssl.create_default_context())
        if os.getenv('DOCNOVA_SMTP_USER'):smtp.login(os.environ['DOCNOVA_SMTP_USER'],os.environ.get('DOCNOVA_SMTP_PASSWORD',''))
        smtp.send_message(msg)
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT/'dist'),**kwargs)
    def log_message(self,*args):pass # Never log addresses or unsubscribe tokens.
    def reply(self,status,data):
        raw=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)
    def html(self,content,status=200):
        self.send_response(status);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(('<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DocNova email preferences</title><body style="font:18px/1.6 Arial;max-width:580px;margin:70px auto;padding:24px;color:#182130">'+content+'</body></html>').encode())
    def do_GET(self):
        if urlsplit(self.path).path=='/unsubscribe':
            token=parse_qs(urlsplit(self.path).query).get('token',[''])[0]
            if not re.fullmatch(r'[A-Za-z0-9_-]{30,100}',token):return self.html('<h1>Invalid link</h1><p>Please use the link in your welcome email.</p>',400)
            return self.html('<h1>Email preferences</h1><p>Stop receiving DocNova marketing emails.</p><form method="post" action="/api/unsubscribe"><input type="hidden" name="token" value="'+token+'"><button style="padding:14px">Unsubscribe</button></form>')
        return super().do_GET()
    def do_POST(self):
        path=urlsplit(self.path).path
        if path not in ('/api/subscribe','/api/unsubscribe'):return self.reply(404,{'message':'Not found'})
        origin=self.headers.get('Origin')
        if origin and urlsplit(origin).netloc!=self.headers.get('Host'):return self.reply(403,{'message':'Please submit from the DocNova website.'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<1 or length>2048:return self.reply(400,{'message':'Invalid request.'})
            raw=self.rfile.read(length).decode()
            if path=='/api/unsubscribe':
                token=parse_qs(raw).get('token',[''])[0]
                with sqlite3.connect(DB) as db:db.execute("UPDATE subscribers SET status='unsubscribed' WHERE token=?",(token,))
                return self.html('<h1>You’re unsubscribed.</h1><p>You will no longer receive DocNova marketing emails.</p>')
            data=json.loads(raw);email=str(data.get('email','')).strip().lower()
            if data.get('consent') is not True or len(email)>254 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',email):return self.reply(400,{'message':'Enter a valid email and confirm your subscription consent.'})
            now=int(time.time());ip=self.client_address[0];recent=[t for t in RATE.get(ip,[]) if now-t<60]
            if len(recent)>=5:return self.reply(429,{'message':'Please wait a minute before trying again.'})
            RATE[ip]=recent+[now]
            with sqlite3.connect(DB) as db:
                row=db.execute('SELECT token,status,attempted_at FROM subscribers WHERE email=?',(email,)).fetchone()
                if row and row[1]=='sent':return self.reply(200,{'status':'sent'})
                if row and row[1] in ('sending','failed') and now-row[2]<60:return self.reply(429,{'message':'Please wait a minute before retrying.'})
                token=row[0] if row else secrets.token_urlsafe(32)
                status='sending' if configured() else 'pending'
                db.execute('INSERT INTO subscribers VALUES (?,?,?,?,?) ON CONFLICT(email) DO UPDATE SET consent_at=excluded.consent_at,status=excluded.status,attempted_at=excluded.attempted_at',(email,token,now,status,now))
            if status=='pending':return self.reply(202,{'status':'pending'})
            try:deliver(email,token)
            except Exception:
                with sqlite3.connect(DB) as db:db.execute("UPDATE subscribers SET status='failed' WHERE email=?",(email,))
                return self.reply(502,{'message':'The welcome email could not be sent. Please try again later.'})
            with sqlite3.connect(DB) as db:db.execute("UPDATE subscribers SET status='sent' WHERE email=?",(email,))
            return self.reply(200,{'status':'sent'})
        except (ValueError,UnicodeError):return self.reply(400,{'message':'Invalid request.'})
if __name__=='__main__':
    print('DocNova preview: http://127.0.0.1:4173/',flush=True)
    print('Email mode: '+('SMTP enabled' if configured() else 'local pending signups — email sending not configured'),flush=True)
    ThreadingHTTPServer(('127.0.0.1',4173),Handler).serve_forever()
