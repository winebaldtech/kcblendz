# KCBlendz — Premium Smoothie and Wellness E-Commerce Platform

*Nourishing lives, inspiring wellness.*

A production-ready, full-stack e-commerce website built for **KCBlendz**, a student-led wellness brand founded inside the **African Leadership College of Higher Education**, operating from Kitchen 2 of the Kongo residence in Pamplemousses, Mauritius. KCBlendz sells fresh-blended smoothies, juices, wellness shots, sorbets, fruit salads, popsicles, probiotics, dried fruits, fruit powders, party packs and kiddies packs across three regions — Mauritius, Nigeria, and Global (shelf-stable products shipped via DHL).

This repository delivers a complete, demo-ready website covering every part of the buyer's journey and every back-office workflow an owner needs.

## v3.3 — Strict separation of orders and subscriptions (latest)

Orders and subscriptions are completely different products and are now strictly separated across the entire app:

- **`/admin/orders` only shows product orders** — subscription orders are filtered out (`WHERE is_subscription=0`). They live on `/admin/subscriptions` where they belong.
- **Customer `/account/orders` only shows product orders** too. The new **`/account/subscriptions`** page surfaces the customer's plans, with a "Complete payment" CTA for pending-payment ones and a Cancel button for active ones.
- **Account sidebar** has a new "My Subscriptions" link between Orders and Favorites.
- **Admin user-detail page** shows orders and subscriptions in separate panels.
- **Payment success messaging is subscription-aware** — subscriptions get "Subscription payment received" notifications linking to `/account` or `/admin/subscriptions` instead of "Payment received for order X" linking to order details.
- **Kitchen pipeline skipped for subscriptions** — no more "Sent to kitchen" timeline event polluting subscription orders. They go directly to `order_status='delivered'` once paid, matching the 3-step subscription flow (Plan selected → Payment confirmed → Subscription active).
- **Thank-you page is subscription-aware** — "You're in!" + "Go to my account" for subscribers; "Thank you!" + "Track this order" for product buyers.
- **CSV exports respect separation** — orders.csv has product orders only; subscriptions.csv has the subscription records.

## v3.2 — Unified exports, smart timelines, robust print

- Unified Export CSV button everywhere (Customers / Orders / Products / Subscriptions / Reports).
- New Subscriptions admin page with stat cards and cancel action.
- Products + Subscriptions CSV exports.
- Subscriptions use a clean 3-step timeline; regular orders fill skipped steps cleanly.
- Receipt logo print bug fixed (waits for `img.decode()` + `load` before printing).
- Payment page subscription-aware ("Step 2 of 2" / "Back to plans").

## v3.1 — Payment-gated subscriptions

- Subscriptions require payment before activation (card, PayPal, bank transfer).
- Home subscription section matches `/subscribe` exactly.
- Wellness dropdown removed; Contact map uses exact pin.
- "Stripe" wiped everywhere.
- Products full CRUD; Promo full CRUD.
- Dashboard chart uses separate Y axes per region.

## v3 — Production hardening

- End-to-end payment processing.
- Reliable receipt print.
- Subscription plans + promos seeded.
- Full CRUD for categories and FAQs.
- MFA (TOTP) for admins.
- Comprehensive report exports.


## Table of contents

1. [What's inside](#whats-inside)
2. [Quick start](#quick-start)
3. [Default admin credentials](#default-admin-credentials)
4. [Sandbox payment test cards](#sandbox-payment-test-cards)
5. [Running the tests](#running-the-tests)
6. [Project structure](#project-structure)
7. [Feature tour](#feature-tour)
8. [Brand system](#brand-system)
9. [Tech stack and architecture](#tech-stack-and-architecture)
10. [Security model](#security-model)
11. [Deployment](#deployment)
12. [Going live with real payments](#going-live-with-real-payments)

## What's inside

- A public storefront with three regions and full currency switching
- A custom smoothie builder with a live video preview of ingredients being prepared
- A complete cart, secure card-entry payment flow, and bank-transfer fallback
- Product reviews with verified-buyer badges
- Favorites / wishlist for logged-in customers
- A Wellness Hub blog with five long-form, original articles
- Customer dashboards (orders, reorder, saved blends, addresses, favorites, profile)
- A full administrator panel (products, orders, customers, reports, blog CMS, builder config, profile, messages, notifications)
- A 53-test unit-test suite covering business logic, security and seed integrity
- A complete sprint board plan and a 125-commit GitHub history plan in the `docs/` folder

## Quick start

```bash
# 1. Unpack and enter the project
unzip kcblendz.zip && cd kcblendz

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Run
python app.py
```

Open <http://127.0.0.1:5000>. The database is created on first import (so gunicorn, Railway and other WSGI hosts all boot cleanly without a separate migration step). Seed data is inserted only if the database is empty.

On first boot the seed creates:

- 1 admin user
- 12 product categories
- 57 real products from the KCBlendz catalog
- 37 builder options (cup sizes, fruits, bases, sweeteners, add-ons, boosters)
- 5 long-form Wellness Hub articles
- 54 sample customer reviews so the UI is never empty

You will be redirected to the region picker — choose **Mauritius**, **Nigeria**, or **Global** to begin shopping.

## Default admin credentials

```
URL:       http://127.0.0.1:5000/admin
Email:     admin@kcblendz.com
Password:  KCBlendz@2026
```

## Sandbox payment test cards

These cards are only revealed on the live payment page to admin users; ordinary customers never see them. Use them to test the secure-card-entry flow without touching real money.

| Brand        | Number                 | Expiry       | CVV         |
| ------------ | ---------------------- | ------------ | ----------- |
| Visa         | `4242 4242 4242 4242`  | any future   | any 3-digit |
| Mastercard   | `5555 5555 5555 4444`  | any future   | any 3-digit |
| Amex         | `3782 822463 10005`    | any future   | `1234`      |

All numbers are real Luhn-valid test PANs that the live brand-detection JavaScript recognises as you type.

## Running the tests

The project ships with a 53-test unit-test suite (100% passing) covering business logic, route security, role separation, payment validation, custom-blend imagery, and seed data integrity.

```bash
# All tests, verbose
python -m unittest tests.py -v

# A specific suite
python -m unittest tests.CardValidationTests -v
python -m unittest tests.AuthTests -v
python -m unittest tests.AuthorizationTests -v
```

Test coverage summary:

| Suite                    | What it verifies                                                                                       |
| ------------------------ | ------------------------------------------------------------------------------------------------------ |
| `CardValidationTests`    | Luhn checksum, brand detection, expiry/CVV/format validation                                            |
| `PublicRouteTests`       | Every public page returns 200; guest redirects work; sitemap and robots render                          |
| `AuthTests`              | Signup with one password, signup with matching/mismatched confirm, duplicate-email rejection, 2026 admin password works |
| `AuthorizationTests`     | Guests blocked from account and admin; customers blocked from admin; admin can reach every admin page  |
| `FavoritesAndReviewsTests` | Favorite toggle adds and removes; requires login; 404 on missing product; reviews submit and render    |
| `CustomBlendImageTests`  | Real-image mapper picks the dominant fruit; falls back safely                                          |
| `RegionHelperTests`      | Price-field mapping, currency mapping, formatted money output                                          |
| `CartTests`              | Add product, render cart, handle empty state                                                            |
| `SandboxVisibilityTests` | Admin sees sandbox test cards on the payment page; guests and customers do not                          |
| `SeedDataTests`          | Admin user, products, blog posts, reviews and builder options all seed correctly; every product has a real http image URL |

## Project structure

```
kcblendz/
├── app.py                  # Monolithic Flask app — routes, schema, seed data, business logic
├── tests.py                # 53 unit tests
├── requirements.txt        # Flask + Werkzeug
├── kcblendz.db             # SQLite, created on first import
├── README.md
├── docs/
│   ├── TRELLO_BOARD.md     # Sprint board template (lists, cards, subtasks)
│   └── GITHUB_COMMITS.md   # 125 commit messages across 5 team members
├── static/
│   ├── img/
│   │   ├── logo.png                 # The round glassy KCBlendz logo
│   │   ├── kcblendz-video.mp4       # The brand video on hero, region picker, builder preview
│   │   ├── kcblendz-catalog.jpeg    # Printed menu used as the pricing source
│   │   ├── kcblendz-product.png
│   │   ├── kcblendz-products.png
│   │   └── custom-cup.svg
│   ├── uploads/                     # Admin uploads + customer payment proofs
│   ├── css/  &  js/                 # Reserved for future custom assets
└── templates/
    ├── base.html                    # Brand palette, performance hints, WhatsApp button, flash UI
    ├── partials/
    │   ├── nav.html                 # Role-aware navigation (admin vs customer)
    │   └── footer.html              # Newsletter, address, payment badges
    ├── public/                      # All customer-facing pages
    │   ├── store_select.html        # Region picker with video background
    │   ├── home.html                # Hero with KCBlendz video
    │   ├── shop.html                # Filterable product grid
    │   ├── _product_card.html       # Shared product card with onerror fallback
    │   ├── product.html             # Detail + reviews + favorites + related
    │   ├── builder.html             # Custom smoothie builder with live video preview
    │   ├── cart.html, checkout.html
    │   ├── payment.html             # Real card-entry flow with live brand detection
    │   ├── order_thanks.html
    │   ├── wellness.html, wellness_post.html  # Long-form blog with markdown rendering
    │   ├── about.html, contact.html, faq.html
    │   └── privacy.html, terms.html, refund.html, shipping.html
    ├── auth/                        # login.html, register.html, forgot.html
    ├── account/                     # dashboard, orders, favorites, saved_smoothies, addresses, profile, notifications
    └── admin/                       # dashboard, products, orders, users, categories, blogs, builder_config, reports, profile, messages, notifications
```

## Feature tour

### Multi-region storefront

Three regions with currency, delivery and product-availability differences:

| Region | Currency | Delivery | What ships |
|--------|----------|----------|------------|
| **Mauritius (HQ)** | MUR · Rs | Island-wide, 24 h | Full menu — fresh and shelf-stable |
| **Nigeria** | NGN · ₦ | Lagos same-day for orders before 1pm, 1–3 days outside | Full menu — fresh and shelf-stable |
| **Global** | USD · $ | DHL Express, 5–10 days | Shelf-stable only (dried fruits, fruit powders, freeze-dried, teas) |

The region is forced on first visit through `/store` and can be changed any time from the navigation. Fresh products are hidden from the Global store automatically.

### Custom smoothie builder with live video preview

The builder displays the real KCBlendz video of ingredients being prepared as the customer makes choices. The four mandatory steps are:

1. Pick a cup size (Regular 400 ml, Large 600 ml, Family 1 L)
2. Pick 1 to 3 fruits from 12 options (banana, mango, pineapple, strawberry, blueberry, kiwi, watermelon, orange, apple, papaya, avocado, peach)
3. Choose a base (water, coconut water, almond milk, oat milk, cow's milk, yoghurt)
4. Optional sweeteners, add-ons (chia, flax, granola, peanut butter, oats, honey) and boosters (whey, plant protein, collagen, spirulina, ginger, turmeric)

Live pricing recalculates on every change via `/api/builder/price`. The cart shows a unique, realistic photo per blend based on the dominant fruit — never the same SVG placeholder for every order.

### Real card-entry payment flow

The payment page collects card details with live brand detection and full validation:

- Live brand recognition as the user types: Visa, Mastercard, Amex, Discover, JCB
- Luhn checksum on the card number
- MM/YY format check and expiry-in-the-future check
- 3 or 4 digit CVV check (4 for Amex)
- Only the brand and last 4 digits are ever stored — never the full PAN
- Card number is auto-formatted as the user types (groups of 4, Amex 4-6-5)

Bank transfer is the second option, with mandatory proof upload and region-aware bank details. Proof is verified by an admin within 10 minutes.

### Reviews and ratings

- Star ratings on every product card and detail page
- Inline submit form on the product page for both logged-in customers and guests
- A "verified buyer" badge is awarded automatically when the reviewer has a paid order containing the product
- Sample reviews are seeded across the first dozen products so the UI is never empty

### Favorites and wishlist

- Heart button on every product card and the product detail page
- Count badge in the navigation
- Dedicated `/account/favorites` page with one-click add-to-cart
- The toggle works via both regular POST and XHR (the heart on the product page swaps state without a full page reload)

### Wellness Hub

Five long-form, original articles (7–10 minute reads each, 8–15 sections):

1. Why turmeric belongs in your morning blend
2. West African superfoods your gut absolutely loves
3. Building the ultimate post-workout recovery smoothie
4. Five smoothies that kill your afternoon energy slump
5. The truth about detox drinks (and what actually works)

Articles are markdown-rendered server-side — `**bold**` becomes `<strong>`, `## Heading` becomes an `<h2>`, `- item` becomes a list. Admin-editable through the blog CMS at `/admin/blogs`.

### Customer dashboard

- Dashboard overview with recent orders, saved blends and address book
- Order history and detail with reorder button (re-adds every item to the cart)
- Saved smoothies — custom builds named and ready for repeat orders
- Multi-address book
- Profile with password change
- Favorites with count badges and quick add-to-cart
- Notifications, auto-cleared on visit

### Admin panel

Strictly separated from the customer experience. Admins never see customer-only nav items (favorites heart, cart, "My Orders", saved blends, addresses) — they get an admin-focused dropdown with Admin Dashboard, Manage Orders, Manage Products, Customers, Reports and My profile. The admin sidebar has its own dedicated profile section where the admin can update name, phone and password (with current-password confirmation).

| Section | Capabilities |
|---------|--------------|
| Dashboard | Today and month revenue, order counts, customer total, 7-day region revenue chart in a fixed-height container so the chart renders instantly at the right size |
| Products | Full CRUD with image upload or URL, per-region availability, bestseller / new / featured flags, soft delete |
| Categories | Add and list, with auto-slug |
| Orders | Filter by status / region / search, detail view with line items, payment proof preview, status workflow (pending → processing → ready → delivered → cancelled) with customer notification |
| Customers | Search and filter, CSV export, profile view, suspend / activate / delete / promote-to-admin |
| Wellness Hub | Markdown-friendly editor, cover image upload, draft/publish toggle |
| Builder Config | Manage cup sizes, fruits, bases, sweeteners, add-ons and boosters per region |
| Reports | 30-day daily revenue, 12-month monthly, top 10 products, customer growth chart, region revenue comparison — every chart in a fixed-height container with quick animations |
| Profile | Admin updates own name, phone and password (with stats card showing total products / orders / customers / actions) |
| Messages | Contact-form inbox with handled flag |
| Notifications | System events and admin-targeted alerts |

## Brand system

Every colour comes from the KCBlendz logo:

```css
--kc-green:       #2E8B57   /* The mint/sea green of the KCBlendz wordmark */
--kc-green-deep:  #1F6E43   /* Hover and dark accents */
--kc-orange:      #F7941D   /* The warm orange of the cup illustration */
--kc-orange-deep: #D97706
--kc-yellow:      #FBC02D   /* The outer ring + phone number on the logo */
--kc-cream:       #FFF8E7   /* Warm background for callouts */
--kc-bg:          #F5F9F2   /* Page background */
--kc-dark:        #1B3A2F   /* Primary text — no plain black */
```

Older CSS tokens (`--fuchsia`, `--lime`, `--sky`, `--purple`) are aliased to the brand palette so older template fragments still render in the correct colours without modification.

**Typography**: Plus Jakarta Sans (body, weights 400–800) + Righteous (display, `.brand` class).
**Iconography**: 100 percent inline SVG. No emojis anywhere. No icon font dependency.
**Visual identity**: 2 px brand-dark borders, sharp drop-shadows (`shadow-[4px_4px_0_var(--kc-dark)]`), rounded-2xl cards, generous whitespace.

## Tech stack and architecture

| Layer | Choice | Why |
|-------|--------|-----|
| Backend | Flask | Minimal, fast to read, perfect for a monolithic app of this size |
| Database | SQLite | Zero-config, single-file, easy to inspect for a student project; trivial to swap for PostgreSQL in production (see Deployment) |
| Frontend | Server-rendered Jinja2 + Tailwind CDN | No build step required — facilitators can run the project with a single `python app.py` |
| Charts | Chart.js (via CDN) | Lightweight, dependency-free in the admin, all charts wrapped in fixed-height containers for instant render |
| Auth | bcrypt via Werkzeug | Industry-standard password hashing |
| Sessions | Flask signed cookies, HttpOnly, SameSite=Lax | Hardened against XSS; CSRF tokens layered on top, rotated on login |
| Tests | Standard-library `unittest` | No extra install required |
| Deployment | Gunicorn behind any modern PaaS | Project bootstraps its own database on import, so Railway, Render and Fly.io start cleanly |

The entire backend lives in **one file** (`app.py`) by design: routes, schema, seeds and business logic in one place make it easy to onboard a new contributor in 20 minutes and easy to grade in one read-through.

## Security model

- **CSRF** — every POST form carries a hashed token; the token rotates on login (`session.clear()` then re-issue).
- **Password storage** — bcrypt via `werkzeug.security.generate_password_hash`. The default admin password is hashed at seed time.
- **Card data** — only the brand and last 4 digits are persisted. The PAN is validated server-side via Luhn and then discarded.
- **Role decorators** — `@login_required`, `@admin_required`, `@region_required` on every protected route.
- **Soft deletes** — products and customers are deactivated, never hard-removed.
- **Audit log** — every admin mutation is written to the `audit_logs` table.
- **Security headers** — X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy on every response.
- **File uploads** — extension allowlist (`png, jpg, jpeg, gif, webp`), 8 MB cap, secure filename.
- **Admin separation** — admins see an admin-only dropdown (no favorites heart, no cart icon, no customer "My Orders"); the sandbox test cards on the payment page are gated behind `current_user().role == 'admin'`.

## Deployment

The project is designed for one-command deployment to any modern PaaS.

### Local production preview with Gunicorn

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:8000 app:app
```

### Railway / Render / Fly.io

- **Start command:** `gunicorn -w 2 -b 0.0.0.0:$PORT app:app`
- **Environment variables:** set `KCB_SECRET` to a long random string.
- **Persistent volume:** mount one for `static/uploads/` and (if you want the SQLite DB to survive redeploys) `kcblendz.db`. Otherwise the database self-heals on first request — but customer-created data is lost on each redeploy.
- **Database bootstrap is automatic** — `init_db()` runs on module import and again as a `before_request` safety check, so there is no separate migration step.

### Performance tuning for slow free tiers

The project includes a few touches that make a noticeable difference on free PaaS tiers:

- `<link rel="preconnect">` for Tailwind, Google Fonts and Unsplash so the browser warms up TCP+TLS in parallel with the HTML download.
- `Cache-Control: public, max-age=604800, immutable` on every response under `/static/` so the 8.5 MB brand video is downloaded once per week per visitor, not on every page load.
- `preload="metadata"` on every `<video>` tag so the first paint is not blocked by the full video download.
- Idempotent `init_db()` — safe to call repeatedly under multiple gunicorn workers without conflict.

### Switching to PostgreSQL

Swap `sqlite3.connect()` and the `Row` factory in `get_db()` for `psycopg2` + `RealDictCursor`. The SQL is standard except:

- `datetime('now')` → `now()`
- `date(...)` → `::date`
- `strftime('%Y-%m', ...)` → `to_char(..., 'YYYY-MM')`

## Going live with real payments

The card-payment branch currently runs a sandbox flow — it validates the card properly (Luhn, expiry, CVV, brand) but does not actually charge. To go live, replace the `card` branch of `/payment/<id>/process`:

- **Nigeria — Paystack**: initialise on the server, redirect to Paystack, verify via webhook before marking paid.
- **Mauritius / Global — PayPal Smart Buttons** (client-side, capture on success), or **Stripe Elements** if you want one global processor.
- **Bank transfer** — already production-ready: customer uploads proof, admin verifies at `/admin/orders/<id>`.

The order schema already has `payment_reference`, `payment_status` and a `payments` table with `raw_payload`, so no migration is needed.