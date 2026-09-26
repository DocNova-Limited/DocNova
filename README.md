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
| `server.py` | Local HTTP server, subscription storage, SMTP welcome emails and unsubscribe endpoint |
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

## Preview limitations

This is a local storefront demo, not a production commerce backend. Checkout does not charge money, send orders or fulfil products. Live payments, inventory, account authentication, production hosting and server-side validation of prices/coupons still need connecting before commercial use. Local coupon state is a preview convenience, not secure payment enforcement.

Product prices were checked against docnova.co.uk on 25 September 2026. Copy about fabric, fit measurements, delivery, returns and illustrative testimonials still needs business approval. The preview currently uses £4.95 delivery and free delivery from £100; these are sample terms, not verified shipping rates. Google reviews are not connected; the owner will provide the existing widget information later. Social profile placeholders remain.

## Validation performed

JavaScript syntax, local asset references, desktop/mobile layouts, colour filtering, matching products, size selection, bag additions/removals, demo checkout, and the subscription popup were checked. A £44.99 top minus the £4.50 WELCOME10 discount plus £4.95 preview delivery totals £45.44. Email formatting was verified with mocked SMTP; real inbox delivery remains unverified.
