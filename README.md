# DocNova website

Complete local website for DocNova premium medical scrubs and essential medical devices. All photographs and the official logo are included in this repository; no product imagery depends on the old website.

## Run the complete website

Requires Python 3.10 or newer. No external Python packages, Node installation, or build step is required.

```sh
python3 server.py
```

Open http://127.0.0.1:4173/ in a browser. On this Mac, double-click `Start DocNova.command` instead: it runs the site at http://127.0.0.1:4180/ and opens it for you. Keep the server running while viewing it. If port 4173 is already in use, stop the previous preview server first.

For Windows, use `py server.py` if `python3` is unavailable.

The `dist` folder can be served by static hosting for the shopping demo, but the Python server is required for subscription capture and email delivery. GitHub storing these files does not by itself host the Python service or activate payments.

## Included files

| Path | Purpose |
| --- | --- |
| `dist/index.html` | Website shell, official logo and homepage hero |
| `dist/app.js` | Full product catalogue, copy, routes, shopping interactions, offers and newsletter UI |
| `dist/style.css` | Desktop and mobile design |
| `dist/photos.js` | Scrub photograph manifest |
| `dist/assets/` | All supplied scrub photographs and official company logo |
| `dist/assets/devices/` | All 10 official medical-device photographs |
| `server.py` | Web server: storefront, Stripe checkout and webhook, order tracking, subscriptions and unsubscribe |
| `shop.py` | Server-side prices, orders database, Stripe API calls and order emails |
| `orders.py` | Order admin: list, view, mark dispatched with tracking number, delivered, cancelled |
| `.env.example` | Template for Stripe keys and email settings; copy to `.env` (never committed) |
| `email/welcome.html` | Branded WELCOME10 email template |
| `email-settings.example` | SMTP configuration variable names; contains no credentials |
| `data/schema.sql` | Subscriber database schema, automatically created by the server |
| `data/catalogue-sources.json` | Verified prices, approved sizes/colours and source notes |
| `Start DocNova.command` | Mac launcher |

## Shopping experience

- Homepage featuring scrubs and Essential Medical Devices, with a separate shop.
- Men’s scrubs in 5 colours and women’s in 7; every colour as a top (£44.99), trousers (£49.99) and set (£94.98).
- Sizes S, M, L, XL and 2XL, with women’s and men’s size charts on the size guide and every product page.
- Blood pressure monitor £44.99; infrared thermometer £34.99.
- Category, colour, fit, price, size, collection and fabric controls, sorting and pagination.
- Product image galleries, colour switching, quick view, wishlist, search and local cart.
- About, FAQ, size guide, contact (email + WhatsApp) and policy pages. A WhatsApp chat button sits on every page.

### Delivery options (chosen at checkout)

| Option | Price | Stripe asks for |
|---|---|---|
| UK delivery | £4.95, free from £70 (after any discount) | a UK address |
| Republic of Ireland | £9.95 | an Irish address |
| Click & Collect (Cambridge area) | free | phone number only — call the customer the same day |

Prices are set in `shop.py` (`DELIVERY`); the website only sends which option was picked. `python3 orders.py show <order>` shows the option; mark a collected order with `python3 orders.py delivered <order>`.

### Discount codes — one per order

- **WELCOME10** — 10% off products, offered in the welcome popup and welcome email.
- **Private codes** — not shown anywhere on the website. They are set only in the private `.env` file as
  `DOCNOVA_PRIVATE_CODES="CODE:15,OTHERCODE:20"` (code and percentage), so they never appear in the site’s code or in Git.

Codes never cover delivery and are checked by the server (`/api/coupon`). A new code replaces the previous one, and Stripe’s own promotion-code box stays off, so codes can’t be combined. Restart the website after changing `.env`.

## Email sending and subscriber data

Every sign-up from the welcome popup or footer is saved in `private/subscriptions.sqlite3` (never committed to Git).

- `python3 subscribers.py list` — everyone who signed up and whether their welcome email went out.
- `python3 subscribers.py export` — `subscribers.csv` of active subscribers for an email tool (keep it private).

**Welcome email:** when email sending is set up, each new subscriber automatically receives the branded welcome email (`email/welcome.html`) with WELCOME10 and an unsubscribe link. Sign-ups saved before email was set up are sent automatically (checked every 10 minutes) once it is.

**To switch email on**, fill in the `DOCNOVA_SMTP_*`, `DOCNOVA_FROM_EMAIL` and `DOCNOVA_PUBLIC_URL` settings in `.env` using your email provider’s SMTP details (for example Google Workspace, Microsoft 365, Zoho or a sending service such as Brevo). `DOCNOVA_PUBLIC_URL` must be the live website address so links in emails work.

## DocNova Rounds (loyalty & referrals)

Page: `#/rounds`. Members join with their email (no password) and get a private card link by email.

- 1 round for joining · 1 per scrub set · ½ per top or trousers · 1 when a referred colleague places their first paid order.
- Complete 10 rounds, get a set free: at 10 rounds a one-time `ROUNDS-XXXXXX` code is emailed (valid 12 months). At checkout it makes the most expensive set in the bag free; it can't be combined with other codes and the free set doesn't earn rounds.
- Rounds are added automatically when an order is paid (matched by the email used at checkout); a full refund removes them.
- Admin: `python3 rounds.py list` · `python3 rounds.py show someone@nhs.net` · `python3 rounds.py add someone@nhs.net 1 "Goodwill"` (use -1 or 0.5 as needed) · `python3 rounds.py link someone@nhs.net` (re-send their card link).

## Live Google reviews

The bottom of the home page (and each product page) shows DocNova’s live Google rating and reviews in the site’s own design. The server asks Google’s Places API at most once an hour and keeps the answer in memory only. That’s about 720 requests a month, inside Google’s free allowance of 1,000 for this request type.

- **Set up once:** in Google Cloud Console, create a project, enable **Places API (New)**, create an API key restricted to that API, and put it in `.env` as `GOOGLE_PLACES_API_KEY`. Google requires a billing account on the project, even when usage stays inside the free allowance. The DocNova Place ID (`ChIJsYc-YLZng2URQNsyBvUhTec`) is already built in.
- **Without a key**, the section shows the four real reviews saved in `data/featured-reviews.json` (copied word for word from Google on 28 Sept 2026), with the rating and links to your Google profile. Update that file if you want different reviews shown before the key is set up. With a key, live reviews replace them automatically (highest-rated first, up to four).
- **Google’s rules, followed here:** reviews are shown unedited, with the reviewer’s name and a link to each review on Google Maps, and with a note on how they’re chosen. Google returns up to 5 reviews, the ones it ranks most relevant.

## Payments with Stripe

Checkout uses **Stripe Checkout** (Stripe’s hosted payment page). The customer reviews their bag on the site, presses *Continue to secure payment*, pays on Stripe, and returns to an order confirmation page with their order number.

- **Payment methods:** the site never hard-codes a list. Stripe shows every method switched on in *Dashboard → Settings → Payment methods* that suits the customer’s device and country (cards, Apple Pay, Google Pay, Link, Klarna, Revolut Pay, PayPal and others). The checkout page and footer show the same list, read from your Stripe account.
- **Prices are enforced on the server.** `shop.py` reads prices from `dist/app.js`, so a customer cannot change what they pay. WELCOME10 becomes a Stripe coupon (10% off products, delivery excluded). UK delivery is £4.95, or free from £70 after any discount; Ireland £9.95; Click & Collect free.
- **Order numbers:** every checkout gets an ID such as `DN-260926-7K4QP`. It is shown on Stripe’s payment page, on the confirmation page, in the Stripe payment description (searchable in the Dashboard) and in order emails.
- **Order confirmation comes from Stripe’s webhook** (`/api/stripe/webhook`, signature-verified), so an order is recorded even if the customer closes the tab after paying. Bank payments that take a few days to confirm are handled too.
- **Order tracking:** customers open *Track your order* (footer), then enter their order number and email to see *Order placed → Payment confirmed → Dispatched → Delivered*, the carrier, and the tracking number with a link.

### Test mode setup (no real money)

1. In the Stripe Dashboard, switch on **Test mode**. Go to *Developers → API keys* and create a **restricted key** with *Checkout Sessions: Write*, *Coupons: Write* and *Payment Method Configurations: Read*. A test secret key also works.
2. Copy `.env.example` to `.env` and paste the key into `STRIPE_SECRET_KEY`.
3. Install the Stripe CLI (`brew install stripe/stripe-cli/stripe`), run `stripe login`, then keep this running in a second Terminal window:
   `stripe listen --forward-to localhost:4173/api/stripe/webhook`
   Paste the `whsec_...` value it prints into `STRIPE_WEBHOOK_SECRET` in `.env`.
4. Start the site (`python3 server.py` or `Start DocNova.command`). The first lines of output say which Stripe mode is active.
5. Buy something with test card `4242 4242 4242 4242`, any future expiry date and any CVC.

### Fulfilling orders

```sh
python3 orders.py list                                   # paid orders waiting to be sent
python3 orders.py show DN-260926-7K4QP                   # items, sizes, delivery address
python3 orders.py ship DN-260926-7K4QP --carrier "Royal Mail" --tracking AB123456789GB
python3 orders.py delivered DN-260926-7K4QP
```

The customer sees updates immediately on the tracking page. If SMTP is configured, they also get an order confirmation email and a dispatch email with the tracking number. Stripe separately emails a payment receipt (switch this on in *Settings → Customer emails*). Refunds are made in the Stripe Dashboard, and the order then shows *Refunded*.

### Going live checklist

- Host the site on a server that runs Python over **https** (the old docnova.co.uk shop pages are a separate system). Set `DOCNOVA_PUBLIC_URL`.
- In the **live** Dashboard, add a webhook endpoint `https://<your-domain>/api/stripe/webhook` with the events `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `checkout.session.expired` and `charge.refunded`. Put its signing secret in `STRIPE_WEBHOOK_SECRET`.
- Put a live restricted key (same three permissions) in `STRIPE_SECRET_KEY` **and** set `DOCNOVA_STRIPE_LIVE=1`. Live keys are refused without it.
- Switch on the payment methods you want in *Settings → Payment methods*. Google Pay and PayPal were off when this was set up.
- Replace the draft Delivery & returns, Privacy and Terms text with approved policies, and confirm how VAT is handled.
- Back up `private/subscriptions.sqlite3`: it now holds customer orders and addresses.

## Validation performed

JavaScript syntax, local asset references, desktop/mobile layouts, colour filtering, matching products, size selection, bag additions/removals, demo checkout, and the subscription popup were checked. A £44.99 top minus the £4.50 WELCOME10 discount plus £4.95 delivery totals £45.44. The Stripe flow was tested end-to-end against a simulated Stripe API: price tampering, invalid sizes and quantities, signed and forged webhooks, duplicate and late events, bank-transfer style delayed payments, failures, expiry, refunds, tracking lookups and dispatch, on desktop and mobile layouts. Run a real test-mode purchase with the Stripe CLI before going live. Email formatting was verified with mocked SMTP; real inbox delivery remains unverified.
