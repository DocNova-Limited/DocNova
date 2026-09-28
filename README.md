# DocNova website

Complete local website for DocNova premium medical scrubs and essential medical devices. All photographs and the official logo are included in this repository; no product imagery depends on the old website.

## Run the complete website

Requires Python 3.10 or newer. No external Python packages, Node installation, or build step is required.

```sh
python3 server.py
```

Open http://127.0.0.1:4173/ in a browser. On this Mac, `Start DocNova.command` also launches the site. Keep the server running while viewing it. If port 4173 is already in use, stop the previous preview server first.

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
- Men’s and women’s scrub tops, trousers and matching sets in the photographed colours.
- S, M, L and XL sizing as confirmed by the owner.
- Tops £44.99; trousers £49.99; sets £94.98 (top plus trousers).
- Blood pressure monitor £44.99; infrared thermometer £34.99.
- Category, colour, fit, price, size, collection and fabric controls, sorting and pagination.
- Product image galleries, colour switching, quick view, wishlist, search and local cart.
- “Complete the set” recommendations match garment colour and men’s/women’s fit, with independent sizes.
- About, FAQ, size guide, support and policy pages.
- Welcome subscription popup and WELCOME10 coupon: 10% off product subtotal, rounded to pennies, excluding delivery. Repeated application does not stack discounts.

## Email sending and subscriber data

Without email configuration, subscriptions are saved locally with a pending status. The UI explicitly says no email was sent. Automatic real email delivery is **not active**.

To enable delivery, configure the server environment using the variable names in `email-settings.example`. Use the chosen email service’s verified sender and SMTP credentials. `DOCNOVA_PUBLIC_URL` must be the public website URL so email and unsubscribe links work for customers. Keep secrets out of `dist`, version control and shared archives.

The server creates `private/subscriptions.sqlite3` automatically. This repository includes the full schema rather than a live customer database. The local database had zero customer records when this repository was prepared. Private databases and secrets are deliberately ignored by Git. Back up future customer records separately with appropriate access controls; never commit them to this public repository.

The welcome email includes an unsubscribe link. Its confirmation form marks the address unsubscribed. Any future marketing tool must honour that status. Pending signups are not automatically sent when SMTP is later configured; they must be deliberately processed or the customer can subscribe again. Browser preference resets do not delete server-side records.

## Live Google reviews

The bottom of the home page (and each product page) shows DocNova’s live Google rating and reviews in the site’s own design. The server asks Google’s Places API at most once an hour and keeps the answer in memory only. That’s about 720 requests a month, inside Google’s free allowance of 1,000 for this request type.

- **Set up once:** in Google Cloud Console, create a project, enable **Places API (New)**, create an API key restricted to that API, and put it in `.env` as `GOOGLE_PLACES_API_KEY`. Google requires a billing account on the project, even when usage stays inside the free allowance. The DocNova Place ID (`ChIJsYc-YLZng2URQNsyBvUhTec`) is already built in.
- **Without a key**, the section still shows, with “Read our Google reviews” and “Write a review” buttons linking to your Google profile.
- **Google’s rules, followed here:** reviews are shown unedited, with the reviewer’s name and a link to each review on Google Maps, and with a note on how they’re chosen. Google returns up to 5 reviews, the ones it ranks most relevant.

## Payments with Stripe

Checkout uses **Stripe Checkout** (Stripe’s hosted payment page). The customer reviews their bag on the site, presses *Continue to secure payment*, pays on Stripe, and returns to an order confirmation page with their order number.

- **Payment methods:** the site never hard-codes a list. Stripe shows every method switched on in *Dashboard → Settings → Payment methods* that suits the customer’s device and country (cards, Apple Pay, Google Pay, Link, Klarna, Revolut Pay, PayPal and others). The checkout page and footer show the same list, read from your Stripe account.
- **Prices are enforced on the server.** `shop.py` reads prices from `dist/app.js`, so a customer cannot change what they pay. WELCOME10 becomes a Stripe coupon (10% off products, delivery excluded). Delivery is £4.95, or free from £100.
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
