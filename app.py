"""
KCBlendz — Premium Smoothie & Wellness E-commerce Platform
Monolithic Flask application with SQLite.

Implements every requirement from:
  - KCBLENDZ_WEBSITE_REQUIREMENT_DOCUMENT.pdf
  - Capstone project proposal (master prompt)

Run:
    pip install -r requirements.txt
    python app.py
    => http://localhost:5000
"""
import os
import sqlite3
import secrets
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash, jsonify, abort, g, send_from_directory, make_response
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from markupsafe import Markup, escape

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "kcblendz.db"
UPLOAD_FOLDER = BASE_DIR / "static" / "uploads"
ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_UPLOAD_MB = 8

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("KCB_SECRET", secrets.token_hex(32)),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024,
    UPLOAD_FOLDER=str(UPLOAD_FOLDER),
)
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)


@app.template_filter("from_json")
def _from_json(s):
    """Decode a JSON string in a template — used to render features_json on
    the subscription cards. Returns [] on any error so a malformed value
    never crashes the page."""
    if not s:
        return []
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return []


@app.template_filter("mdinline")
def md_inline(text):
    """Render inline markdown safely: **bold**, *italic*, `code`,
    [label](url). Escapes HTML first so article content can't inject markup,
    then applies a small, safe subset. Fixes the literal ** showing on the
    wellness article pages."""
    from markupsafe import Markup, escape
    s = str(escape(text or ""))
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"`(.+?)`",
               r'<code class="bg-gray-100 px-1 rounded">\1</code>', s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
               r'<a href="\2" class="link" target="_blank" rel="noopener">\1</a>', s)
    return Markup(s)


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE — schema, connection, init
# ─────────────────────────────────────────────────────────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    phone TEXT,
    role TEXT NOT NULL DEFAULT 'customer',  -- customer | admin
    status TEXT NOT NULL DEFAULT 'active',  -- active | suspended | deleted
    region TEXT,                            -- preferred region NG / MU / GL
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS addresses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label TEXT,
    full_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    street TEXT NOT NULL,
    city TEXT NOT NULL,
    state TEXT,
    country TEXT NOT NULL,
    postal_code TEXT,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    icon TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    short_description TEXT,
    description TEXT,
    ingredients TEXT,
    health_benefits TEXT,
    category_id INTEGER REFERENCES categories(id),
    image_url TEXT,
    price_ngn REAL,
    price_mur REAL,
    price_usd REAL,
    stock INTEGER NOT NULL DEFAULT 100,
    is_available_ng INTEGER NOT NULL DEFAULT 1,
    is_available_mu INTEGER NOT NULL DEFAULT 1,
    is_available_global INTEGER NOT NULL DEFAULT 0,  -- only shelf-stable for Global
    is_featured INTEGER NOT NULL DEFAULT 0,
    is_bestseller INTEGER NOT NULL DEFAULT 0,
    is_new INTEGER NOT NULL DEFAULT 0,
    tags TEXT,  -- comma separated: tropical,detox,energy,...
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS builder_options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    option_type TEXT NOT NULL,  -- cup_size | fruit | base | sweetener | addon | booster
    name TEXT NOT NULL,
    price_ngn REAL NOT NULL DEFAULT 0,
    price_mur REAL NOT NULL DEFAULT 0,
    price_usd REAL NOT NULL DEFAULT 0,
    image_url TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS custom_smoothies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,    -- {cup, fruits[], base, sweeteners[], addons[], boosters[]}
    region TEXT NOT NULL,         -- pricing region snapshot
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_number TEXT UNIQUE NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    guest_email TEXT,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    region TEXT NOT NULL,
    currency TEXT NOT NULL,
    subtotal REAL NOT NULL,
    delivery_fee REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL,
    fulfillment_type TEXT NOT NULL,   -- delivery | pickup
    delivery_address TEXT,
    delivery_city TEXT,
    delivery_state TEXT,
    delivery_country TEXT,
    delivery_date TEXT,
    delivery_slot TEXT,
    notes TEXT,
    payment_method TEXT NOT NULL,    -- card | paypal | bank_transfer
    payment_status TEXT NOT NULL DEFAULT 'pending',  -- pending | paid | failed | refunded
    payment_reference TEXT,
    payment_proof_url TEXT,
    order_status TEXT NOT NULL DEFAULT 'pending',    -- pending | processing | ready | delivered | cancelled
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    custom_smoothie_id INTEGER REFERENCES custom_smoothies(id) ON DELETE SET NULL,
    item_name TEXT NOT NULL,
    item_image TEXT,
    item_meta TEXT,
    unit_price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    line_total REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    method TEXT NOT NULL,
    gateway TEXT,                 -- paystack | paypal | manual
    reference TEXT,
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL,
    raw_payload TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS blog_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    subtitle TEXT,
    cover_url TEXT,
    category TEXT,             -- NUTRITION | LIFESTYLE | RECIPE | WELLNESS
    author TEXT,
    content TEXT NOT NULL,
    read_minutes INTEGER NOT NULL DEFAULT 4,
    is_published INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,  -- NULL = admin
    audience TEXT NOT NULL DEFAULT 'user',  -- user | admin
    title TEXT NOT NULL,
    body TEXT,
    link TEXT,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    entity TEXT,
    entity_id INTEGER,
    ip_address TEXT,
    meta TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS newsletter_subscribers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    region TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contact_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    subject TEXT,
    message TEXT NOT NULL,
    is_handled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, product_id)
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    author_name TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    title TEXT,
    body TEXT NOT NULL,
    is_verified_buyer INTEGER NOT NULL DEFAULT 0,
    is_approved INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ─── New tables added in v2 (all idempotent via IF NOT EXISTS) ──────────────

-- Per-order timeline events for the tracking system (created/paid/processing/...)
CREATE TABLE IF NOT EXISTS order_status_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    status TEXT NOT NULL,         -- pending|paid|processing|ready|out_for_delivery|delivered|cancelled
    note TEXT,
    actor TEXT,                   -- 'system' | 'admin:<email>' | 'customer'
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_order_status_events_order ON order_status_events(order_id, created_at);

-- Subscription plans + active customer subscriptions
CREATE TABLE IF NOT EXISTS subscription_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    tagline TEXT,
    features_json TEXT NOT NULL DEFAULT '[]',
    price_ngn_monthly REAL DEFAULT 0,
    price_mur_monthly REAL DEFAULT 0,
    price_usd_monthly REAL DEFAULT 0,
    price_ngn_yearly REAL DEFAULT 0,
    price_mur_yearly REAL DEFAULT 0,
    price_usd_yearly REAL DEFAULT 0,
    is_featured INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id INTEGER NOT NULL REFERENCES subscription_plans(id) ON DELETE RESTRICT,
    billing_cycle TEXT NOT NULL DEFAULT 'monthly',  -- monthly | yearly
    region TEXT NOT NULL,
    currency TEXT NOT NULL,
    price REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',           -- active | cancelled | past_due
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    cancelled_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions(user_id, status);

-- Promotional codes (percentage / fixed amount)
CREATE TABLE IF NOT EXISTS promo_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    description TEXT,
    discount_type TEXT NOT NULL DEFAULT 'percent',   -- percent | fixed
    discount_value REAL NOT NULL,                    -- 10 = 10%  OR  500 = 500 in region's currency
    min_subtotal REAL NOT NULL DEFAULT 0,
    region TEXT,                                     -- NULL = global
    starts_at TEXT,
    ends_at TEXT,
    max_uses INTEGER,                                -- NULL = unlimited
    used_count INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_promo_active ON promo_codes(is_active, code);

-- Refund requests (customer-initiated, admin-reviewed)
CREATE TABLE IF NOT EXISTS refund_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reason TEXT NOT NULL,
    details TEXT,
    status TEXT NOT NULL DEFAULT 'pending',         -- pending | approved | rejected | refunded
    admin_response TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_refund_requests_status ON refund_requests(status);

-- Structured FAQ entries (replaces hard-coded list, lets admin add new ones)
CREATE TABLE IF NOT EXISTS faqs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL DEFAULT 'customers',     -- customers | team | insights
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_published INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_faqs_category ON faqs(category, sort_order);

-- Team members (shown on About page)
CREATE TABLE IF NOT EXISTS team_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL,
    bio TEXT,
    avatar_url TEXT,
    socials_json TEXT DEFAULT '{}',
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id);
CREATE INDEX IF NOT EXISTS idx_reviews_product ON reviews(product_id);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_products_featured ON products(is_featured);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(order_status);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        # Performance pragmas — faster reads, fewer locks under concurrency.
        g.db.execute("PRAGMA journal_mode = WAL")
        g.db.execute("PRAGMA synchronous = NORMAL")
        g.db.execute("PRAGMA cache_size = -16000")
        g.db.execute("PRAGMA temp_store = MEMORY")
    return g.db


@app.after_request
def _cache_static(resp):
    """Let the browser cache static assets so pages load fast on repeat
    visits (helps perceived speed on Railway's free tier)."""
    if request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "public, max-age=2592000"
    return resp


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _safe_add_column(conn, table, column, decl):
    """ALTER TABLE ... ADD COLUMN, swallowing the duplicate-column error so
    migrations are idempotent. SQLite has no `IF NOT EXISTS` for ADD COLUMN."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e).lower():
            raise


def migrate(conn):
    """Apply additive schema migrations to existing databases.

    All operations are idempotent so this is safe to run on every boot
    against an already-migrated DB. Never drops or renames anything.
    """
    # v2 — admin profile pictures + MFA + soft-delete safety on categories
    _safe_add_column(conn, "users", "avatar_url",    "TEXT")
    _safe_add_column(conn, "users", "mfa_secret",    "TEXT")
    _safe_add_column(conn, "users", "mfa_enabled",   "INTEGER NOT NULL DEFAULT 0")
    _safe_add_column(conn, "users", "login_attempts","INTEGER NOT NULL DEFAULT 0")
    _safe_add_column(conn, "users", "locked_until",  "TEXT")
    # v2 — promo code & receipt fields on orders
    _safe_add_column(conn, "orders", "promo_code",       "TEXT")
    _safe_add_column(conn, "orders", "discount_amount",  "REAL NOT NULL DEFAULT 0")
    _safe_add_column(conn, "orders", "tracking_token",   "TEXT")
    # v2 — review author photos
    _safe_add_column(conn, "reviews", "avatar_url", "TEXT")
    # v2 — soft-delete flag on categories (so deleting one becomes reversible)
    _safe_add_column(conn, "categories", "is_deleted", "INTEGER NOT NULL DEFAULT 0")
    # v3 — subscriptions now require payment before activation
    _safe_add_column(conn, "subscriptions", "order_id", "INTEGER")
    _safe_add_column(conn, "orders", "subscription_id", "INTEGER")
    _safe_add_column(conn, "orders", "is_subscription", "INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def init_db():
    """Create schema and load REAL KCBlendz menu data from the catalog (no fake seeds)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    migrate(conn)

    # Admin user — created on first run only
    if not conn.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        conn.execute(
            "INSERT INTO users (email, password_hash, full_name, phone, role) VALUES (?,?,?,?,?)",
            (
                "admin@kcblendz.com",
                generate_password_hash("KCBlendz@2026"),
                "KCBlendz Admin",
                "+234-802-4655-191",
                "admin",
            ),
        )

    # Categories per the requirement document
    if not conn.execute("SELECT 1 FROM categories").fetchone():
        cats = [
            ("smoothies",      "Smoothies",       "Fresh-blended fruit smoothies, your wellness in a cup."),
            ("juices",         "Juices",          "Cold-pressed natural fruit juices."),
            ("sorbets",        "Sorbets",         "Refreshing fruit sorbets, dairy-free."),
            ("fruit-salads",   "Fruit Salads",    "Fresh-cut seasonal fruit salads."),
            ("wellness-shots", "Wellness Shots",  "Concentrated immune & energy shots."),
            ("wellness-bowls", "Wellness Bowls",  "Loaded smoothie & açaí bowls."),
            ("popsicles",      "Popsicles",       "Frozen fruit popsicles, naturally sweet."),
            ("probiotics",     "Probiotics",      "Gut-friendly fermented drinks."),
            ("dried-fruits",   "Dried Fruits",    "Sun-dried & freeze-dried fruit, shelf-stable."),
            ("fruit-powders",  "Fruit Powders",   "Pure fruit & superfood powders."),
            ("party-packs",    "Party Packs",     "Bundled drinks for events & parties."),
            ("kiddies-packs",  "Kiddies Packs",   "Kid-friendly fruit blends, no added sugar."),
        ]
        for i, (slug, name, desc) in enumerate(cats):
            conn.execute(
                "INSERT INTO categories (slug, name, description, sort_order) VALUES (?,?,?,?)",
                (slug, name, desc, i),
            )

    # REAL KCBlendz product menu from the printed catalog (Kitchen 2 Kongo)
    # Mauritius prices in MUR are exactly as printed; NGN prices reflect Nigerian retail pricing.
    if not conn.execute("SELECT 1 FROM products").fetchone():
        smoothie_img_pool = {
            "Glow Splash":   "https://images.unsplash.com/photo-1623065422902-30a2d299bbe4?w=800&q=80",
            "Dew Drop":      "https://images.unsplash.com/photo-1546538490-0fe0a8eba4e6?w=800&q=80",
            "Radiant Mix":   "https://images.unsplash.com/photo-1638176067000-9e2cffac2c40?w=800&q=80",
            "Fresh Glow":    "https://images.unsplash.com/photo-1638176066757-37c50b3d2db9?w=800&q=80",
            "Tropi-Glow":    "https://images.unsplash.com/photo-1502741338009-cac2772e18bc?w=800&q=80",
            "Power Boost":   "https://images.unsplash.com/photo-1553530666-ba11a7da3888?w=800&q=80",
            "Nutty Gain":    "https://images.unsplash.com/photo-1571091655789-405eb7a3a3a8?w=800&q=80",
            "Mass Fuel":     "https://images.unsplash.com/photo-1610970881699-44a5587cabec?w=800&q=80",
            "Berry Power":   "https://images.unsplash.com/photo-1505252585461-04db1eb84625?w=800&q=80",
            "Choco Gain":    "https://images.unsplash.com/photo-1568901346375-23c9450c58cd?w=800&q=80",
            "Clean Green":   "https://images.unsplash.com/photo-1610970881699-44a5587cabec?w=800&q=80",
            "Belly Reset":   "https://images.unsplash.com/photo-1622597467836-f3285f2131b8?w=800&q=80",
            "Fresh Core":    "https://images.unsplash.com/photo-1546173159-315724a31696?w=800&q=80",
            "Mint Flush":    "https://images.unsplash.com/photo-1576094133503-7c1bdac88e2b?w=800&q=80",
            "Zesty Clean":   "https://images.unsplash.com/photo-1546548970-71785318a17b?w=800&q=80",
            "Happy Berry":   "https://images.unsplash.com/photo-1505252585461-04db1eb84625?w=800&q=80",
            "Citrus Lift":   "https://images.unsplash.com/photo-1623065422902-30a2d299bbe4?w=800&q=80",
            "Kiwi Bliss":    "https://images.unsplash.com/photo-1502741338009-cac2772e18bc?w=800&q=80",
            "Calm Glow":     "https://images.unsplash.com/photo-1571091655789-405eb7a3a3a8?w=800&q=80",
            "Sweet Focus":   "https://images.unsplash.com/photo-1553530666-ba11a7da3888?w=800&q=80",
        }
        # SMOOTHIES — Rs 150 (catalog price)
        smoothie_groups = {
            "Glow & Hydration":      ["Glow Splash", "Dew Drop", "Radiant Mix", "Fresh Glow", "Tropi-Glow"],
            "Energy & Weight Gain":  ["Power Boost", "Nutty Gain", "Mass Fuel", "Berry Power", "Choco Gain"],
            "Detox & Digestion":     ["Clean Green", "Belly Reset", "Fresh Core", "Mint Flush", "Zesty Clean"],
            "Mood & Brain":          ["Happy Berry", "Citrus Lift", "Kiwi Bliss", "Calm Glow", "Sweet Focus"],
        }
        smoothie_ings = {
            "Glow Splash":   "Banana, Watermelon, Pineapple",
            "Dew Drop":      "Banana, Watermelon, Orange",
            "Radiant Mix":   "Banana, Pineapple, Orange",
            "Fresh Glow":    "Banana, Watermelon, Apple",
            "Tropi-Glow":    "Banana, Pineapple, Kiwi",
            "Power Boost":   "Banana, Peanut, Oats, Dates",
            "Nutty Gain":    "Banana, Peanut, Dates",
            "Mass Fuel":     "Banana, Oats, Dates",
            "Berry Power":   "Banana, Strawberry, Dates",
            "Choco Gain":    "Banana, Cocoa, Dates",
            "Clean Green":   "Banana, Apple, Cucumber",
            "Belly Reset":   "Banana, Pineapple, Ginger",
            "Fresh Core":    "Banana, Apple, Carrot",
            "Mint Flush":    "Banana, Cucumber, Lemon",
            "Zesty Clean":   "Banana, Apple, Lemon",
            "Happy Berry":   "Banana, Strawberry, Blueberry",
            "Citrus Lift":   "Banana, Orange, Pineapple",
            "Kiwi Bliss":    "Banana, Kiwi, Apple",
            "Calm Glow":     "Banana, Cocoa, Strawberry",
            "Sweet Focus":   "Banana, Apple, Dates",
        }
        smoothie_benefits = {
            "Glow Splash":   "Hydration, vitamin C, glowing skin",
            "Dew Drop":      "Hydration, electrolyte balance, refreshing",
            "Radiant Mix":   "Antioxidants, immunity, skin glow",
            "Fresh Glow":    "Hydration, gentle cleanse, light energy",
            "Tropi-Glow":    "Digestive enzymes, vitamin C, glow",
            "Power Boost":   "Sustained energy, healthy weight gain, protein",
            "Nutty Gain":    "Healthy fats, plant protein, calorie boost",
            "Mass Fuel":     "Slow-release carbs, muscle recovery",
            "Berry Power":   "Antioxidants, iron, energy",
            "Choco Gain":    "Mood lift, magnesium, healthy weight gain",
            "Clean Green":   "Detox, hydration, alkaline boost",
            "Belly Reset":   "Anti-bloat, digestive enzymes, gut support",
            "Fresh Core":    "Beta-carotene, gut motility, vitamin A",
            "Mint Flush":    "Cooling, liver support, fresh breath",
            "Zesty Clean":   "Vitamin C, gentle detox, immunity",
            "Happy Berry":   "Mood support, antioxidants, brain fuel",
            "Citrus Lift":   "Vitamin C, mood lift, immunity",
            "Kiwi Bliss":    "Vitamin C, focus, calm energy",
            "Calm Glow":     "Magnesium, mood balance, relaxation",
            "Sweet Focus":   "Natural sugars, concentration, energy",
        }
        smoothie_tag_map = {
            "Glow & Hydration":     "hydration,glow,tropical",
            "Energy & Weight Gain": "energy,protein,weight-gain",
            "Detox & Digestion":    "detox,digestion,green",
            "Mood & Brain":         "mood,brain,berry",
        }
        cat_smoothie = conn.execute("SELECT id FROM categories WHERE slug='smoothies'").fetchone()["id"]
        for group, items in smoothie_groups.items():
            for idx, name in enumerate(items):
                slug = name.lower().replace(" ", "-").replace("&", "and")
                conn.execute("""INSERT INTO products
                    (slug, name, short_description, description, ingredients, health_benefits,
                     category_id, image_url, price_ngn, price_mur, price_usd,
                     stock, is_available_ng, is_available_mu, is_available_global,
                     is_featured, is_bestseller, is_new, tags)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    slug, name,
                    smoothie_ings[name],
                    f"{name} is a fresh-blended {group.lower()} smoothie. {smoothie_benefits[name]}.",
                    smoothie_ings[name],
                    smoothie_benefits[name],
                    cat_smoothie,
                    smoothie_img_pool.get(name),
                    2800.0,   # NGN price for a single smoothie
                    150.0,    # MUR price per catalog
                    0.0,      # not shipped globally (perishable)
                    100, 1, 1, 0,
                    1 if idx == 0 else 0,           # first of each group featured
                    1 if name in ("Power Boost", "Clean Green", "Happy Berry", "Glow Splash") else 0,
                    1 if name in ("Tropi-Glow", "Choco Gain", "Mint Flush", "Sweet Focus") else 0,
                    smoothie_tag_map[group]
                ))

        # SORBETS — Rs 130
        cat_sorbets = conn.execute("SELECT id FROM categories WHERE slug='sorbets'").fetchone()["id"]
        sorbets = [
            ("Pineapple Chill", "Pineapple, Watermelon",   "Cooling hydration, vitamin C"),
            ("Mango Freeze",    "Mango, Peach",            "Beta-carotene, sweet refreshment"),
            ("Berry Cool",      "Strawberry, Watermelon",  "Antioxidants, refreshing"),
            ("Papaya Frost",    "Papaya, Pineapple",       "Digestive enzymes, tropical cool"),
            ("Kiwi Ice",        "Kiwi, Pineapple",         "Vitamin C, energizing cool"),
        ]
        sorbet_imgs = [
            "https://images.unsplash.com/photo-1488900128323-21503983a07e?w=800&q=80",
            "https://images.unsplash.com/photo-1567206563064-6f60f40a2b57?w=800&q=80",
            "https://images.unsplash.com/photo-1488900128323-21503983a07e?w=800&q=80",
            "https://images.unsplash.com/photo-1565958011703-44f9829ba187?w=800&q=80",
            "https://images.unsplash.com/photo-1556679343-c7306c1976bc?w=800&q=80",
        ]
        for i, (name, ings, ben) in enumerate(sorbets):
            slug = name.lower().replace(" ", "-")
            conn.execute("""INSERT INTO products
                (slug, name, short_description, description, ingredients, health_benefits,
                 category_id, image_url, price_ngn, price_mur, price_usd,
                 stock, is_available_ng, is_available_mu, is_available_global,
                 is_featured, is_bestseller, is_new, tags)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                slug, name, ings, f"{name} sorbet — {ben.lower()}. Made with real fruit only.",
                ings, ben, cat_sorbets, sorbet_imgs[i],
                2400.0, 130.0, 0.0, 100, 1, 1, 0,
                1 if i == 0 else 0, 0, 1 if i in (1, 4) else 0, "sorbet,frozen,refreshing"
            ))

        # FRUIT SALADS — Rs 180
        cat_salads = conn.execute("SELECT id FROM categories WHERE slug='fruit-salads'").fetchone()["id"]
        salads = [
            ("Tropical Mix",   "Pineapple, Mango, Watermelon, Apple", "Hydration, vitamin C, fiber"),
            ("Berry Blast",    "Strawberry, Apple, Banana, Kiwi",      "Antioxidants, fiber, energy"),
            ("Citrus Fresh",   "Orange, Pineapple, Watermelon",        "Immunity, vitamin C, hydration"),
            ("Rainbow Salad",  "Apple, Banana, Papaya, Pineapple",     "Digestion, vitamins, balance"),
            ("Kiwi Tropic",    "Kiwi, Mango, Pineapple",               "Vitamin C, enzymes, glow"),
        ]
        salad_imgs = [
            "https://images.unsplash.com/photo-1490474418585-ba9bad8fd0ea?w=800&q=80",
            "https://images.unsplash.com/photo-1546554137-f86b9593a222?w=800&q=80",
            "https://images.unsplash.com/photo-1564093497595-593b96d80180?w=800&q=80",
            "https://images.unsplash.com/photo-1551782450-a2132b4ba21d?w=800&q=80",
            "https://images.unsplash.com/photo-1502741338009-cac2772e18bc?w=800&q=80",
        ]
        for i, (name, ings, ben) in enumerate(salads):
            slug = name.lower().replace(" ", "-")
            conn.execute("""INSERT INTO products
                (slug, name, short_description, description, ingredients, health_benefits,
                 category_id, image_url, price_ngn, price_mur, price_usd,
                 stock, is_available_ng, is_available_mu, is_available_global,
                 is_featured, is_bestseller, is_new, tags)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                slug, name, ings, f"{name} — hand-cut, fresh-prepared salad. {ben}.",
                ings, ben, cat_salads, salad_imgs[i],
                3200.0, 180.0, 0.0, 80, 1, 1, 0,
                1 if i == 0 else 0, 1 if i == 1 else 0, 1 if i == 4 else 0, "salad,fresh-cut,fiber"
            ))

        # JUICES — derived from the brand line
        cat_juice = conn.execute("SELECT id FROM categories WHERE slug='juices'").fetchone()["id"]
        juices = [
            ("Orange Sunrise",    "Pure orange, lemon zest",                "Immunity, vitamin C, daily glow",
             "https://images.unsplash.com/photo-1600271886742-f049cd451bba?w=800&q=80"),
            ("Watermelon Cooler", "Watermelon, mint",                       "Hydration, electrolytes, cooling",
             "https://images.unsplash.com/photo-1546538490-0fe0a8eba4e6?w=800&q=80"),
            ("Pineapple Ginger",  "Pineapple, ginger, lemon",               "Anti-inflammatory, digestion",
             "https://images.unsplash.com/photo-1622597467836-f3285f2131b8?w=800&q=80"),
            ("Carrot Apple Beet", "Carrot, apple, beetroot",                "Blood support, iron, beta-carotene",
             "https://images.unsplash.com/photo-1610970881699-44a5587cabec?w=800&q=80"),
        ]
        for i, (name, ings, ben, img) in enumerate(juices):
            slug = name.lower().replace(" ", "-")
            conn.execute("""INSERT INTO products
                (slug, name, short_description, description, ingredients, health_benefits,
                 category_id, image_url, price_ngn, price_mur, price_usd,
                 stock, is_available_ng, is_available_mu, is_available_global,
                 is_featured, is_bestseller, is_new, tags)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                slug, name, ings, f"{name} is a cold-pressed juice — {ben.lower()}.",
                ings, ben, cat_juice, img,
                2200.0, 120.0, 0.0, 100, 1, 1, 0,
                1 if i == 0 else 0, 0, 1 if i == 3 else 0, "juice,cold-pressed"
            ))

        # WELLNESS SHOTS
        cat_shots = conn.execute("SELECT id FROM categories WHERE slug='wellness-shots'").fetchone()["id"]
        shots = [
            ("Ginger Fire Shot",     "Ginger, lemon, cayenne",   "Immunity, circulation, energy",
             "https://images.unsplash.com/photo-1556881286-fc6915169721?w=800&q=80"),
            ("Turmeric Glow Shot",   "Turmeric, orange, pepper", "Anti-inflammatory, joint support",
             "https://images.unsplash.com/photo-1638176067000-9e2cffac2c40?w=800&q=80"),
            ("Wheatgrass Reset",     "Pure wheatgrass juice",    "Alkaline boost, daily detox",
             "https://images.unsplash.com/photo-1610970881699-44a5587cabec?w=800&q=80"),
        ]
        for i, (name, ings, ben, img) in enumerate(shots):
            slug = name.lower().replace(" ", "-")
            conn.execute("""INSERT INTO products
                (slug, name, short_description, description, ingredients, health_benefits,
                 category_id, image_url, price_ngn, price_mur, price_usd,
                 stock, is_available_ng, is_available_mu, is_available_global,
                 is_featured, is_bestseller, is_new, tags)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                slug, name, ings, f"{name} — a 60ml concentrated shot. {ben}.",
                ings, ben, cat_shots, img,
                1500.0, 80.0, 0.0, 100, 1, 1, 0,
                1 if i == 0 else 0, 1 if i == 0 else 0, 0, "shot,wellness,immunity"
            ))

        # WELLNESS BOWLS
        cat_bowls = conn.execute("SELECT id FROM categories WHERE slug='wellness-bowls'").fetchone()["id"]
        bowls = [
            ("Açaí Sunrise Bowl",    "Açaí, banana, granola, coconut",         "Antioxidants, fiber, sustained energy",
             "https://images.unsplash.com/photo-1490645935967-10de6ba17061?w=800&q=80"),
            ("Tropical Smoothie Bowl","Mango, pineapple, chia, kiwi topping", "Hydration, vitamin C, omega-3",
             "https://images.unsplash.com/photo-1623428187969-5da2dcea5ebf?w=800&q=80"),
            ("Green Power Bowl",     "Spinach, banana, almond, hemp seeds",    "Iron, plant protein, recovery",
             "https://images.unsplash.com/photo-1502741338009-cac2772e18bc?w=800&q=80"),
        ]
        for i, (name, ings, ben, img) in enumerate(bowls):
            slug = name.lower().replace(" ", "-")
            conn.execute("""INSERT INTO products
                (slug, name, short_description, description, ingredients, health_benefits,
                 category_id, image_url, price_ngn, price_mur, price_usd,
                 stock, is_available_ng, is_available_mu, is_available_global,
                 is_featured, is_bestseller, is_new, tags)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                slug, name, ings, f"{name} — a loaded breakfast bowl. {ben}.",
                ings, ben, cat_bowls, img,
                4500.0, 240.0, 0.0, 60, 1, 1, 0,
                1 if i == 0 else 0, 1 if i == 0 else 0, 1 if i == 2 else 0, "bowl,breakfast,superfood"
            ))

        # POPSICLES
        cat_pop = conn.execute("SELECT id FROM categories WHERE slug='popsicles'").fetchone()["id"]
        pops = [
            ("Mango Lassi Popsicle",   "Mango, yogurt, cardamom",  "Probiotic boost, cooling",
             "https://images.unsplash.com/photo-1497034825429-c343d7c6a68f?w=800&q=80"),
            ("Berry Burst Popsicle",   "Mixed berries, hibiscus",  "Antioxidants, refreshing",
             "https://images.unsplash.com/photo-1488900128323-21503983a07e?w=800&q=80"),
            ("Watermelon Lime Pop",    "Watermelon, lime, mint",   "Hydration, electrolytes",
             "https://images.unsplash.com/photo-1556679343-c7306c1976bc?w=800&q=80"),
        ]
        for i, (name, ings, ben, img) in enumerate(pops):
            slug = name.lower().replace(" ", "-")
            conn.execute("""INSERT INTO products
                (slug, name, short_description, description, ingredients, health_benefits,
                 category_id, image_url, price_ngn, price_mur, price_usd,
                 stock, is_available_ng, is_available_mu, is_available_global,
                 is_featured, is_bestseller, is_new, tags)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                slug, name, ings, f"{name} — frozen real-fruit popsicle. {ben}.",
                ings, ben, cat_pop, img,
                1200.0, 70.0, 0.0, 200, 1, 1, 0,
                0, 0, 1 if i == 0 else 0, "popsicle,frozen,kids"
            ))

        # PROBIOTICS
        cat_pro = conn.execute("SELECT id FROM categories WHERE slug='probiotics'").fetchone()["id"]
        pros = [
            ("Hibiscus Kombucha",     "Live kombucha, hibiscus, ginger", "Gut health, polyphenols",
             "https://images.unsplash.com/photo-1638176067000-9e2cffac2c40?w=800&q=80"),
            ("Berry Kefir Drink",     "Coconut kefir, mixed berries",    "Probiotics, dairy-free gut support",
             "https://images.unsplash.com/photo-1505252585461-04db1eb84625?w=800&q=80"),
        ]
        for i, (name, ings, ben, img) in enumerate(pros):
            slug = name.lower().replace(" ", "-")
            conn.execute("""INSERT INTO products
                (slug, name, short_description, description, ingredients, health_benefits,
                 category_id, image_url, price_ngn, price_mur, price_usd,
                 stock, is_available_ng, is_available_mu, is_available_global,
                 is_featured, is_bestseller, is_new, tags)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                slug, name, ings, f"{name} — live-cultured probiotic drink. {ben}.",
                ings, ben, cat_pro, img,
                2600.0, 140.0, 0.0, 60, 1, 1, 0,
                0, 0, 1, "probiotic,gut-health,fermented"
            ))

        # DRIED FRUITS — shelf-stable, AVAILABLE GLOBALLY
        cat_dry = conn.execute("SELECT id FROM categories WHERE slug='dried-fruits'").fetchone()["id"]
        dries = [
            ("Sun-Dried Mango Slices",     "100% Mango, no added sugar",   "Natural sweetness, fiber, vitamin A",
             "https://images.unsplash.com/photo-1499636136210-6f4ee915583e?w=800&q=80", 4.99),
            ("Freeze-Dried Strawberries",  "100% Strawberry",              "Antioxidants, crispy snack",
             "https://images.unsplash.com/photo-1587049352846-4a222e784d38?w=800&q=80", 5.99),
            ("Dried Pineapple Rings",      "100% Pineapple",               "Bromelain, digestion, sweet treat",
             "https://images.unsplash.com/photo-1550828520-4cb496926fc9?w=800&q=80", 4.49),
            ("Dried Banana Chips",         "Banana, light coconut oil",    "Energy, potassium, fiber",
             "https://images.unsplash.com/photo-1571771019784-3ff35f4f4277?w=800&q=80", 3.99),
        ]
        for i, (name, ings, ben, img, usd) in enumerate(dries):
            slug = name.lower().replace(" ", "-")
            conn.execute("""INSERT INTO products
                (slug, name, short_description, description, ingredients, health_benefits,
                 category_id, image_url, price_ngn, price_mur, price_usd,
                 stock, is_available_ng, is_available_mu, is_available_global,
                 is_featured, is_bestseller, is_new, tags)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                slug, name, ings, f"{name} — 100g pack of premium dried fruit. Shelf-stable, ships globally. {ben}.",
                ings, ben, cat_dry, img,
                4000.0, 220.0, usd, 200, 1, 1, 1,
                1 if i == 0 else 0, 1 if i == 0 else 0, 1 if i == 1 else 0, "dried,shelf-stable,snack,global"
            ))

        # FRUIT POWDERS — shelf-stable, AVAILABLE GLOBALLY
        cat_pow = conn.execute("SELECT id FROM categories WHERE slug='fruit-powders'").fetchone()["id"]
        pows = [
            ("Baobab Superfood Powder",   "100% Baobab pulp powder",        "Vitamin C, prebiotic fiber",
             "https://images.unsplash.com/photo-1610970881699-44a5587cabec?w=800&q=80", 12.99),
            ("Moringa Leaf Powder",       "100% Moringa oleifera leaf",     "Iron, plant protein, daily greens",
             "https://images.unsplash.com/photo-1576092768241-dec231879fc3?w=800&q=80", 11.99),
            ("Açaí Berry Powder",         "Freeze-dried açaí",              "Anthocyanins, antioxidant power",
             "https://images.unsplash.com/photo-1622597467836-f3285f2131b8?w=800&q=80", 15.99),
            ("Hibiscus Fruit Tea",        "Loose hibiscus petals",          "Heart health, vitamin C, ruby brew",
             "https://images.unsplash.com/photo-1597481499750-3e6b22637e12?w=800&q=80", 8.99),
        ]
        for i, (name, ings, ben, img, usd) in enumerate(pows):
            slug = name.lower().replace(" ", "-")
            conn.execute("""INSERT INTO products
                (slug, name, short_description, description, ingredients, health_benefits,
                 category_id, image_url, price_ngn, price_mur, price_usd,
                 stock, is_available_ng, is_available_mu, is_available_global,
                 is_featured, is_bestseller, is_new, tags)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                slug, name, ings, f"{name} — 100g pouch of pure superfood. Ships globally. {ben}.",
                ings, ben, cat_pow, img,
                6500.0, 360.0, usd, 150, 1, 1, 1,
                1 if i == 0 else 0, 1 if i == 1 else 0, 1 if i == 2 else 0, "powder,shelf-stable,superfood,global"
            ))

        # PARTY PACKS
        cat_party = conn.execute("SELECT id FROM categories WHERE slug='party-packs'").fetchone()["id"]
        parties = [
            ("Party Pack of 10 Smoothies",   "10 assorted smoothies, party-ready", "Crowd favourites, mixed flavours",
             "https://images.unsplash.com/photo-1542444459-db63c982fadb?w=800&q=80"),
            ("Event Bundle — 20 Drinks",     "20 mixed smoothies & juices",         "Perfect for offices & celebrations",
             "https://images.unsplash.com/photo-1564093497595-593b96d80180?w=800&q=80"),
        ]
        for i, (name, ings, ben, img) in enumerate(parties):
            slug = name.lower().replace(" ", "-")
            conn.execute("""INSERT INTO products
                (slug, name, short_description, description, ingredients, health_benefits,
                 category_id, image_url, price_ngn, price_mur, price_usd,
                 stock, is_available_ng, is_available_mu, is_available_global,
                 is_featured, is_bestseller, is_new, tags)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                slug, name, ings, f"{name} — pre-bundled for groups. {ben}. 24-hour notice required.",
                ings, ben, cat_party, img,
                26000.0 if i == 0 else 50000.0,
                1450.0 if i == 0 else 2800.0,
                0.0, 30, 1, 1, 0,
                1 if i == 0 else 0, 1, 0, "party,bundle,event"
            ))

        # KIDDIES PACKS
        cat_kid = conn.execute("SELECT id FROM categories WHERE slug='kiddies-packs'").fetchone()["id"]
        kids = [
            ("Lil' Sippers Pack (4 Kid-Sized)",  "4 kid-size mild smoothies",  "No added sugar, kid-friendly flavours",
             "https://images.unsplash.com/photo-1497034825429-c343d7c6a68f?w=800&q=80"),
            ("School Week Pack (5 Days)",        "5 daily juices for school",  "Vitamin C, energy, no preservatives",
             "https://images.unsplash.com/photo-1600271886742-f049cd451bba?w=800&q=80"),
        ]
        for i, (name, ings, ben, img) in enumerate(kids):
            slug = name.lower().replace(" ", "-")
            conn.execute("""INSERT INTO products
                (slug, name, short_description, description, ingredients, health_benefits,
                 category_id, image_url, price_ngn, price_mur, price_usd,
                 stock, is_available_ng, is_available_mu, is_available_global,
                 is_featured, is_bestseller, is_new, tags)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                slug, name, ings, f"{name} — designed for children. {ben}.",
                ings, ben, cat_kid, img,
                11000.0 if i == 0 else 13500.0,
                600.0 if i == 0 else 740.0,
                0.0, 50, 1, 1, 0,
                1 if i == 0 else 0, 0, 1 if i == 1 else 0, "kids,pack,family"
            ))

    # Builder options — fully configurable from /admin/builder
    if not conn.execute("SELECT 1 FROM builder_options").fetchone():
        opts = [
            # cup sizes
            ("cup_size", "Regular (400ml)", 2800, 150,  0,  1),
            ("cup_size", "Large (600ml)",   3800, 200,  0,  2),
            ("cup_size", "Family (1L)",     6500, 350,  0,  3),
            # fruits — min 1 / max 3 per requirement
            ("fruit", "Banana",       0, 0, 0, 1),
            ("fruit", "Mango",        0, 0, 0, 2),
            ("fruit", "Pineapple",    0, 0, 0, 3),
            ("fruit", "Strawberry",   0, 0, 0, 4),
            ("fruit", "Blueberry",  300, 15, 0, 5),
            ("fruit", "Kiwi",       200, 10, 0, 6),
            ("fruit", "Watermelon",   0, 0, 0, 7),
            ("fruit", "Orange",       0, 0, 0, 8),
            ("fruit", "Apple",        0, 0, 0, 9),
            ("fruit", "Papaya",       0, 0, 0,10),
            ("fruit", "Avocado",    400, 20, 0,11),
            ("fruit", "Peach",      200, 10, 0,12),
            # bases — with/without milk per requirement
            ("base", "No Base (Water)",        0,  0, 0, 1),
            ("base", "Coconut Water",        200, 10, 0, 2),
            ("base", "Almond Milk",          400, 20, 0, 3),
            ("base", "Oat Milk",             400, 20, 0, 4),
            ("base", "Cow's Milk",           300, 15, 0, 5),
            ("base", "Greek Yogurt",         500, 25, 0, 6),
            # sweeteners
            ("sweetener", "No Sweetener",       0, 0, 0, 1),
            ("sweetener", "Dates",           200,10, 0, 2),
            ("sweetener", "Honey",           250,12, 0, 3),
            ("sweetener", "Agave Syrup",     250,12, 0, 4),
            # addons
            ("addon", "Oats",                250,12, 0, 1),
            ("addon", "Peanut Butter",       400,20, 0, 2),
            ("addon", "Chia Seeds",          300,15, 0, 3),
            ("addon", "Flax Seeds",          300,15, 0, 4),
            ("addon", "Granola",             400,20, 0, 5),
            ("addon", "Cocoa Powder",        300,15, 0, 6),
            # boosters
            ("booster", "Whey Protein",      800,40, 0, 1),
            ("booster", "Plant Protein",     800,40, 0, 2),
            ("booster", "Spirulina",         500,25, 0, 3),
            ("booster", "Moringa",           500,25, 0, 4),
            ("booster", "Turmeric",          400,20, 0, 5),
            ("booster", "Ginger Root",       300,15, 0, 6),
        ]
        for opt in opts:
            conn.execute("""INSERT INTO builder_options
                (option_type, name, price_ngn, price_mur, price_usd, sort_order)
                VALUES (?,?,?,?,?,?)""", opt)

    # Wellness Hub — long-form, original content
    if not conn.execute("SELECT 1 FROM blog_posts").fetchone():
        posts = [
            (
                "turmeric-morning-blend",
                "Why turmeric belongs in your morning blend",
                "The ancient golden root, the modern morning ritual, and the science of curcumin absorption",
                "https://images.unsplash.com/photo-1512621776951-a57141f2eefd?w=1200&q=80",
                "NUTRITION", "Dr. Adaeze Okafor", 9,
                "Turmeric is one of the most studied spices in modern nutrition. It's also one of the most misunderstood. Walk into any pharmacy and you'll see capsules promising everything from sharper focus to younger-looking skin. Most of those bottles contain too little of what actually works, in a form your body cannot use. So let's unpack what turmeric really does, what to look for, and exactly how to put it into a smoothie that earns its place in your morning.\n\n## The active compound, not the spice\n\nThe yellow pigment in turmeric is a polyphenol called curcumin. Curcumin is the part of the spice that gives turmeric its anti-inflammatory, antioxidant, and circulation-supporting effects. The catch — and it's a big one — is that pure curcumin makes up only about 3% of dried turmeric powder. The remaining 97% is mostly starches, fibre and other compounds. Worse, curcumin on its own is poorly absorbed. Without help, almost everything you swallow leaves your body unused.\n\nThis is where good food chemistry meets traditional cooking. Two simple additions multiply curcumin bioavailability dramatically: a tiny pinch of black pepper, which contains piperine, and a source of healthy fat. Piperine has been shown to increase curcumin absorption by up to two thousand percent. Fat helps because curcumin is fat-soluble — your body absorbs it the same way it absorbs vitamins A, D, E and K.\n\n## How to actually drink it\n\nThe goal is one well-built morning glass — not a daily mega-dose. Start small and let your body get used to it.\n\n- A quarter teaspoon of high-quality turmeric powder (look for one that lists curcumin content)\n- A grind of fresh black pepper, no more than a pinch\n- A tablespoon of nut butter, half an avocado, a splash of coconut milk, or full-fat yogurt\n- A natural sweetness anchor: half a banana, a few chunks of mango, or pineapple\n- 200 ml of cold water, almond milk, or coconut water\n\nBlend until smooth. The flavour is warm and slightly earthy — pineapple and mango balance it beautifully because their natural sweetness softens the spice's edge. Pineapple also contains bromelain, an enzyme that compounds the anti-inflammatory effect of curcumin.\n\n## What it does that you'll actually feel\n\nMost benefits of turmeric are quiet — they happen at the cellular level over weeks. But there are a few you might notice within a few days of consistent use. The first is recovery: people who exercise often report less stiffness the morning after a hard session. The second is digestion: turmeric stimulates bile flow, which helps your body break down fats. The third, more subtle, is skin clarity — likely because of its antioxidant effect on circulation and inflammation.\n\nThe research on chronic-disease prevention — heart disease, certain cancers, neurodegenerative conditions — is promising but long-term. Don't drink a smoothie expecting overnight magic. Drink it because you enjoy it, and let the cumulative effect take care of itself.\n\n## Who should be careful\n\nTurmeric is generally safe in food-level doses, but a few people should talk to a doctor before adding daily turmeric. Anyone on blood-thinning medication, because curcumin has mild anticoagulant properties. People with gallstones or active gallbladder disease, because turmeric increases bile flow. Pregnant women, until they've checked with their care provider. And anyone taking iron supplements should separate the timing — curcumin can bind to iron and slightly reduce absorption.\n\n## Sourcing matters more than you'd think\n\nThe single biggest variable in turmeric quality is the curcumin percentage. Cheap turmeric, especially poorly-stored ground powder, can have very low active content. Always look for a vibrant orange-yellow colour rather than a dull brown, and store the spice in a cool dark cupboard, not next to the stove.\n\nIf you can find fresh turmeric root (often available in West African and Mauritian markets), use it. Half a thumb-sized piece, peeled and grated, in place of the powder. The flavour is brighter and the active compound is generally higher.\n\n## One ritual, not ten supplements\n\nThe wellness industry is great at making you feel like you need fifteen daily pills. But if you build one well-considered morning glass — turmeric, a fat, a pepper, fruit, a base — you'll be ahead of most people taking expensive supplements. Keep it simple. Drink it most days. Adjust to your taste. That's it."
            ),
            (
                "west-african-superfoods-gut",
                "West African superfoods your gut absolutely loves",
                "Local fruits and roots that quietly outperform imported supplements — at a fraction of the price",
                "https://images.unsplash.com/photo-1506126613408-eca07ce68773?w=1200&q=80",
                "LIFESTYLE", "KC Team", 10,
                "Long before açaí became a brunch trend and matcha lattes filled airport menus, West Africa had its own pantry of gut-loving foods. Many of them grow within walking distance of where they're eaten. Almost none of them require shipping a powder halfway around the world. And yet you'll struggle to find them on a typical 'superfood' list, because superfood lists are mostly made by people who haven't visited a West African market.\n\nLet's fix that. Here are six ingredients that should be in your blender, what they actually do, and how to use them without making your smoothie taste like a science experiment.\n\n## Baobab fruit\n\nThe baobab is the iconic flat-topped tree of the African savannah. Its fruit dries on the branch into a hard pod containing seeds surrounded by a chalky white pulp. That pulp is the part you want. Pound for pound, baobab pulp has roughly six times more vitamin C than oranges, more potassium than bananas, and an unusually high content of soluble fibre.\n\nThat fibre is what makes baobab a quiet hero for your gut. Soluble fibre is the food your beneficial bacteria ferment into short-chain fatty acids — butyrate, propionate and acetate — which feed the lining of your colon and reduce inflammation throughout your body. A teaspoon of baobab powder in your morning smoothie gives you about a third of your daily fibre target.\n\nFlavour: tangy, citrusy, slightly sherbet-like. It works beautifully with mango and pineapple.\n\n## Tiger nuts\n\nDespite the name, tiger nuts aren't nuts at all — they're tiny tubers, technically the swollen rhizomes of a sedge plant. They look like wrinkled chickpeas. They taste a bit like coconut crossed with almond. And they are loaded with resistant starch.\n\nResistant starch is the kind your body doesn't digest in the small intestine. It travels all the way to your colon, where your microbiome ferments it. The result is the same butyrate-producing magic as baobab, but with a different microbial signature — which is exactly what you want, since gut diversity matters.\n\nBest way to use them: soak whole tiger nuts overnight, then blend into a milk. Tiger nut milk is creamy, naturally sweet, and dairy-free. In Nigeria and Mauritius you'll see it called 'kunnu aya' or simply tiger nut milk; in Spain it's the base of horchata.\n\n## Moringa leaf\n\nMoringa is sometimes oversold as 'the miracle tree,' which is unfortunate because the actual nutritional profile is impressive enough without hyperbole. The dried leaf powder is one of the few plant sources that's genuinely complete — it contains all nine essential amino acids, plus iron, calcium, magnesium, and vitamins A and K.\n\nFor smoothies, a quarter to half a teaspoon is plenty. The flavour is grassy, almost matcha-adjacent. Pair it with pineapple, banana and a squeeze of lime. Don't overdo it: too much moringa tastes bitter, and very high daily doses can affect thyroid function over time. Treat it as a multi-vitamin you drink, not a hero ingredient.\n\n## Hibiscus\n\nIn West Africa it's called zobo; in Mauritius and the Caribbean, sorrel or rosella. Brewed strong and cold, the dried calyces produce a drink that's deep ruby red and tastes like cranberry's more elegant cousin. Hibiscus is a polyphenol powerhouse, with research linking regular consumption to modestly lower blood pressure and improved cholesterol profiles.\n\nFor smoothies, brew a strong batch of hibiscus tea, let it cool, and use it as your liquid base. It pairs especially well with pineapple, ginger and a touch of honey.\n\n## Bissap and bitter leaf\n\nThese two cross the line from food into traditional medicine, and both have genuine evidence behind them. Bissap (another name for hibiscus in parts of West Africa) is the polyphenol drink above. Bitter leaf, found in Nigerian, Cameroonian and Mauritian markets, is more of an acquired taste — but a small amount blended into a green smoothie supports liver function and helps regulate blood sugar.\n\n## African star apple and soursop\n\nFor sweet smoothies, look for fruits that travel well from local markets. African star apple (udara, agbalumo, alasa) is loaded with vitamin C and fibre. Soursop has a creamy, tangy flesh that blends beautifully with banana and coconut. Both freeze well, so if you find them in season, portion and freeze.\n\n## A starter blend\n\nIf this is your first time using West African ingredients in a smoothie, try this combination: one cup of frozen mango, half a banana, one tablespoon of baobab powder, half a teaspoon of moringa, a thumb of fresh ginger, 250 ml of cold hibiscus tea, a splash of coconut milk, and a small handful of ice. Blend until smooth.\n\nIt's tangy, slightly creamy, gently floral, and packed with fibre, vitamin C, polyphenols and complete plant protein. Your gut will thank you within a week. So will your grocery bill."
            ),
            (
                "post-workout-recovery-smoothie",
                "Building the ultimate post-workout recovery smoothie",
                "The 3-4-1 ratio, the 45-minute window, and what to drink for every type of training",
                "https://images.unsplash.com/photo-1484723091739-30a097e8f929?w=1200&q=80",
                "RECIPE", "Fitness Desk", 9,
                "Recovery is where training stops being a workout and starts becoming results. The hour after a hard session is when your body is most receptive to nutrients — glycogen synthesis runs at roughly twice its normal rate, muscle protein synthesis is elevated, and the stress hormones that get in the way of recovery start clearing your system. Get the next meal right and you'll feel different the next morning. Get it wrong and you'll spend two days sore.\n\nSmoothies are perfect for this window because they're cold, hydrating, easy to digest, and let you pack a precise nutrient profile into a single glass.\n\n## The 3-4-1 ratio\n\nThe simplest framework that survives contact with reality is the 3-4-1 ratio:\n\n- 3 parts fast-absorbing carbohydrates, to refill glycogen stores\n- 4 parts complete protein, to repair muscle fibres\n- 1 part healthy fat, to slow absorption and provide longer-lasting energy\n\nThe numbers refer to relative proportions, not exact grams. For most people, that translates to roughly 40–50 g of carbs, 25–30 g of protein, and 7–10 g of fat in the recovery shake. Adjust upward for endurance sessions, downward for short strength training.\n\n## The base recipe\n\nA recovery blend that works for most people, most of the time:\n\n- 1 banana (medium ripe, frozen is fine)\n- ½ cup pineapple chunks (fresh or frozen)\n- 30 g whey protein, plant protein, or 200 g Greek yogurt\n- 1 tablespoon natural peanut butter or almond butter\n- 1 teaspoon chia seeds\n- 250 ml coconut water or cold filtered water\n- A small handful of ice if using fresh (not frozen) fruit\n\nBlend until completely smooth. You should get about 400 ml of cold, drinkable shake with the consistency of a thin milkshake.\n\n## Why each ingredient earns its place\n\n**Banana** delivers fast-burning natural sugars plus potassium — the electrolyte you lose most through sweat. Frozen banana also gives the shake its body without diluting it with extra liquid.\n\n**Pineapple** contains bromelain, an enzyme that supports protein digestion and has mild anti-inflammatory properties. Several studies suggest bromelain helps reduce muscle soreness after eccentric training.\n\n**Protein source** is non-negotiable. The exact source matters less than the total dose: whey is the gold standard for absorption speed, but a quality plant blend (pea + brown rice + hemp) hits the same essential amino acid profile within a few percent. Greek yogurt is excellent if you tolerate dairy.\n\n**Nut butter** slows the carbohydrate spike so you don't crash an hour later. It also provides magnesium, which helps muscle relaxation.\n\n**Chia seeds** are a quiet electrolyte source. They absorb water, swell slightly in the shake, and give it a richer mouthfeel without adding much calorie load.\n\n**Coconut water** matches your body's electrolyte balance better than plain water — it's especially useful after long or hot sessions where you sweat a lot.\n\n## Variations for different training\n\n**Strength session under an hour:** halve the banana and pineapple. You don't need the full carbohydrate dose if you didn't deplete glycogen.\n\n**Long endurance session (1+ hour cardio, long ride, long run):** add a second banana and double the chia. You will need every gram of carbohydrate you can absorb.\n\n**Fasted morning training:** double the carbs and add 50 ml more coconut water. Your glycogen is fully empty and your body needs everything you can give it.\n\n**Late-evening training:** halve the carbohydrate side. You don't want a sugar surge right before bed; favour protein, fat, and slow-release fuels. Add a teaspoon of magnesium powder if available — it supports both recovery and sleep.\n\n**Vegan training:** swap whey for a pea + rice blend or use 150 g silken tofu for a creamier shake with full amino acid coverage.\n\n## Timing — the famous '45 minute window'\n\nThe idea of a strict anabolic window has been softened by newer research. You don't need to chug your shake within the first thirty seconds of stepping out of the gym. But you do want food in your system within ninety minutes, and the closer to thirty minutes, the better — especially after morning sessions, fasted sessions, or anything over an hour.\n\nA realistic rule: blend the shake before you train, leave it in the fridge or a cold bag, drink it on your way home or as you cool down. Eat a proper meal within two hours.\n\n## Things people often get wrong\n\nFirst, more is not better. A 700 ml mega-shake with 60 g of protein and 80 g of carbs is just a meal — your body uses what it can and stores the rest. The 3-4-1 ratio in modest amounts will outperform calorie overload every time.\n\nSecond, hydration matters more than the shake itself. If you're 2% dehydrated, your recovery is already compromised. Aim for 500 ml of water in the hour after training, separate from your shake.\n\nThird, sleep beats supplements. Seven to nine hours of good sleep does more for recovery than any post-workout drink ever will. Build the shake into a routine that also protects your sleep — don't sip it at 9pm and expect a fresh morning."
            ),
            (
                "afternoon-energy-slump",
                "Five smoothies that kill your afternoon energy slump",
                "Why the 3pm crash isn't about coffee — and what to drink instead",
                "https://images.unsplash.com/photo-1490645935967-10de6ba17061?w=1200&q=80",
                "WELLNESS", "KC Team", 7,
                "There's a feeling most office workers know by heart. It's somewhere between 2 and 4 in the afternoon. The food coma sneaks up on you. Your eyelids feel three pounds heavier. You consider a second coffee, knowing it'll wreck your sleep but unsure what else to do. You keep clicking through tabs, productive in the way a phone in airplane mode is productive — moving, but not really connecting.\n\nThat feeling almost never has anything to do with caffeine. It's a cocktail of three things: a blood sugar dip from a fast lunch, mild dehydration, and a natural dip in your body's daily alertness rhythm. Coffee numbs the symptom for an hour. The fix is steadier fuel and water — and it tastes better as a smoothie.\n\n## What's actually happening at 3 pm\n\nIf your lunch was bread-heavy, rice-heavy, or simply too sweet, your blood sugar shoots up sharply and then crashes about two hours later. The crash is the slump. Throw in the fact that most of us are slightly dehydrated by mid-afternoon, plus a normal dip in cortisol around 3 pm, and your brain genuinely doesn't have what it needs to perform.\n\nThe fix isn't more coffee — it's a small portion of food that combines fibre, healthy fat, a touch of natural sugar, and water. That's exactly what a well-built smoothie is.\n\n## Five blends that work\n\n**1. The classic restart**\n\nOne ripe banana, one tablespoon peanut butter, three tablespoons rolled oats, 200 ml almond or oat milk, a pinch of cinnamon. Blend until smooth. The oats provide slow-release carbs, the peanut butter slows everything down further, the cinnamon helps with insulin response. This one tastes like a melted cookie and you'll feel sharp for two hours.\n\n**2. The cool reset**\n\nHalf a green apple, a cucumber stick, a thumb of ginger, the juice of half a lime, a handful of spinach, 250 ml cold water, a few mint leaves. Blend, strain if you prefer. This one is alkalising, hydrating, and gives you a clean energy without any sugar crash. Pair it with a small handful of almonds if you're truly hungry.\n\n**3. The mood lift**\n\nOne cup mixed frozen berries, 150 g Greek yogurt, one tablespoon honey, half a cup of cold water or coconut water, a teaspoon of chia. The dark berries are loaded with anthocyanins that support brain function and mood. Greek yogurt gives you steady protein. You'll feel both calmer and more focused — the slightly underrated combination.\n\n**4. The golden afternoon**\n\nOne cup mango, a quarter teaspoon turmeric, a pinch of black pepper, a thumb of ginger, half a cup coconut milk, 150 ml water, a few ice cubes. This is essentially a cold golden latte with fruit. The combination of curcumin, ginger and pineapple's bromelain (swap mango for pineapple if you have it) gives you a quiet anti-inflammatory boost. Great after a long meeting-heavy morning.\n\n**5. The chocolate hour**\n\nTwo dates, one tablespoon raw cacao powder, one ripe banana, one tablespoon almond butter, 250 ml oat milk, a small pinch of salt. This one is for the days you would otherwise raid the office biscuit jar. Cacao delivers magnesium and a touch of theobromine — a gentler stimulant than caffeine. Dates provide complex sugars buffered by fibre. You get the chocolate hit and the steady energy.\n\n## The non-smoothie rules that make smoothies work\n\nYou can drink the perfect blend and still slump if you ignore the rest. Three rules:\n\nFirst, water. A glass of plain water before and after your smoothie. Mild dehydration is the single most underestimated cause of afternoon fog.\n\nSecond, light at lunch. The heavier and more refined your lunch, the bigger the crash. A salad with protein and a piece of fruit beats a sandwich and chips every time, even if it sounds less interesting.\n\nThird, movement. A two-minute walk outdoors after lunch — even if it's just to the corner of your office park — does more for afternoon alertness than another caffeinated drink. Daylight resets your circadian rhythm. Movement re-circulates blood and oxygen to your brain. Combine that with a smart smoothie and you'll feel like a different person for the second half of your day."
            ),
            (
                "the-truth-about-detox",
                "The truth about detox drinks (and what actually works)",
                "Your liver doesn't need a juice cleanse — but it does need these specific nutrients",
                "https://images.unsplash.com/photo-1610970881699-44a5587cabec?w=1200&q=80",
                "NUTRITION", "Dr. Adaeze Okafor", 8,
                "There is a billion-dollar industry built on the idea that your body is full of toxins that need to be flushed out by a special drink, powder or three-day cleanse. It's a clever marketing story. It's also, mostly, not true.\n\nHere's what's actually true, and what to drink if you want to support the systems your body already uses to keep you healthy.\n\n## Your body already detoxifies — constantly\n\nYour liver, kidneys, lungs, skin and gut are detoxification machines that run twenty-four hours a day from the moment you're born. The liver is the headline organ — it converts harmful compounds into water-soluble metabolites your kidneys can excrete in urine. The kidneys filter roughly 180 litres of blood every day. Your gut lining is a selective barrier that lets nutrients in and keeps most other things out. Your skin manages temperature and excretes minor amounts of waste through sweat. None of these systems needs a green juice to function.\n\nWhat they do need is the building blocks to do their work well. A liver short on B vitamins, magnesium, or specific amino acids will still detoxify — just less efficiently. A gut short on fibre will struggle to move waste through. Kidneys without enough water work harder than they need to.\n\nThis is the part of the story the supplement industry skips. You don't 'detox' your body. You support the organs that already do it.\n\n## Five things that genuinely help\n\n**Cruciferous vegetables** — broccoli, cabbage, kale, watercress, cauliflower. They contain sulforaphane and indole-3-carbinol, compounds your liver uses in its phase II detoxification pathways. Blending them with fruit and citrus makes them more drinkable.\n\n**Vitamin C-rich fruit** — citrus, baobab, pineapple, kiwi, mango. Vitamin C supports glutathione regeneration. Glutathione is the body's master antioxidant and a key part of how your liver neutralises threats.\n\n**Glycine and N-acetyl-cysteine sources** — collagen, egg whites, garlic, onions. These provide the amino acid backbones your liver needs to build glutathione.\n\n**Polyphenols** — found in berries, dark grapes, green tea, hibiscus, dark chocolate, olive oil. They reduce oxidative stress and inflammation throughout the body.\n\n**Fibre** — soluble and insoluble. Fibre is the broom that sweeps waste compounds out through your gut. Without enough fibre, even a perfect liver loses ground.\n\nNotice what's not on the list: anything labelled 'detox tea' that costs sixty dollars. Anything that promises rapid weight loss. Anything that recommends skipping food for three days.\n\n## A real 'detox' smoothie\n\nIf you want a single blend that actually supports your body's natural cleanup:\n\n- A handful of kale or watercress (cruciferous source)\n- Half a green apple (fibre + flavour)\n- A small piece of cucumber (water + minerals)\n- The juice of half a lemon (vitamin C + flavour)\n- A thumb of ginger (digestion)\n- A small handful of parsley (chlorophyll, vitamin K)\n- 250 ml of cold water or coconut water\n- An optional teaspoon of baobab or moringa powder\n\nBlend until smooth. Don't strain — the fibre is the point. Drink it on most mornings, alongside a normal balanced diet. That's a real detox.\n\n## The simplest 'cleanse' that actually works\n\nIf you wanted to design a routine that genuinely helps your body's cleanup systems, it would look almost nothing like a three-day juice fast. It would look like this:\n\n- Sleep seven to nine hours every night. Most of your liver's repair happens during deep sleep.\n- Drink water steadily throughout the day. Two to three litres for most adults; more in hot climates.\n- Eat thirty different plant foods per week — variety is what feeds a diverse, resilient gut microbiome.\n- Move every day. Lymph (your body's secondary drainage system) doesn't have a pump; movement is the pump.\n- Drink less alcohol than you currently do, whatever that amount is.\n- Manage chronic stress. Stress hormones interfere with detoxification pathways and slow gut transit.\n\nThat's it. No bottle, no eight-hundred-dollar package, no week of feeling miserable. Just the boring, consistent things humans have always done to feel well.\n\n## When 'detox' marketing crosses a line\n\nIf you ever see a product claiming to remove specific heavy metals, treat a chronic disease, or replace medical care — walk away. Those are claims a legitimate product wouldn't make. The body's detoxification system is real, sophisticated and largely self-running. Your job is to feed it well, water it well, and get out of its way."
            ),
        ]
        for slug, title, sub, cover, cat, author, mins, content in posts:
            conn.execute("""INSERT INTO blog_posts (slug, title, subtitle, cover_url, category, author, content, read_minutes)
                VALUES (?,?,?,?,?,?,?,?)""", (slug, title, sub, cover, cat, author, content, mins))

    # Seed sample reviews across the first dozen products so the UI isn't empty.
    if not conn.execute("SELECT 1 FROM reviews").fetchone():
        sample = [
            ("Adaeze O.",     5, "Genuinely the best smoothie on the menu",   "I order this one every Wednesday. The freshness is unmatched, and you can actually taste the real fruit — not concentrate. Worth every rupee."),
            ("Pravesh R.",    5, "Become my morning ritual",                  "Started ordering this for my pre-gym fuel. Light, refreshing, and gives me real energy without that heavy feeling. Five stars."),
            ("Marie-Claire D.",4, "Loved it — would order again",             "Beautifully presented, generous portion, and arrived perfectly chilled. Took off one star only because I'd love a slightly bigger size option."),
            ("Tunde A.",      5, "Premium quality at a fair price",           "I've tried every smoothie spot in the city. KCBlendz is consistently the freshest. The flavour profile is balanced — not too sweet, not too icy."),
            ("Shanaz B.",     5, "My kids ask for this one specifically",     "Picky eaters approved. Real fruit, no weird aftertaste. Delivery was on time and the team is super friendly."),
            ("Ifeoma E.",     4, "Great drink, considering subscribing",      "Tastes like a proper wellness drink without the bitter aftertaste of most green smoothies. Will definitely reorder."),
            ("Vikash K.",     5, "Refreshing and well-made",                  "Hit the spot on a hot afternoon. The fruit pieces were generous and you can tell they use quality ingredients."),
            ("Chioma N.",     5, "Worth the hype",                             "Tried this on a friend's recommendation. The texture, the flavour, the freshness — all on point. Ordering again this weekend."),
        ]
        product_ids = [r["id"] for r in conn.execute("SELECT id FROM products WHERE is_active=1 LIMIT 12").fetchall()]
        import random as _r
        for pid in product_ids:
            for name, rating, title_r, body in _r.sample(sample, _r.randint(3, 5)):
                conn.execute("""INSERT INTO reviews (product_id, author_name, rating, title, body, is_verified_buyer)
                                VALUES (?,?,?,?,?,?)""", (pid, name, rating, title_r, body, 1))

    conn.commit()
    conn.close()
    _seed_v2_data()


def _seed_v2_data():
    """Populate v2 lookup tables (subscription plans, FAQs, team, customer
    testimonials) on a fresh DB — but only if they are empty, so re-running
    is harmless. Keeps seed data outside init_db so existing deployments get
    the new content even though their products/categories are already seeded.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # ─── Subscription plans ───────────────────────────────────────────────
    if not conn.execute("SELECT 1 FROM subscription_plans LIMIT 1").fetchone():
        plans = [
            ("seed", "Seed", "Try the wellness life — one blend a week.",
             ["1 smoothie per week (your choice)",
              "Free local delivery",
              "Early access to new flavours",
              "Member-only recipe newsletter"],
             6500, 290, 8.99, 65000, 2900, 89.99, 0),
            ("bloom", "Bloom", "Daily wellness, locked in. Most popular.",
             ["3 smoothies per week",
              "Priority same-day delivery",
              "10% off à-la-carte purchases",
              "1 free wellness shot per month",
              "Pause or swap any time"],
             18500, 850, 24.99, 185000, 8500, 249.99, 1),
            ("flourish", "Flourish", "All-in. Built for families & athletes.",
             ["7 smoothies per week",
              "Free same-day delivery, every order",
              "20% off à-la-carte & party packs",
              "4 free wellness shots per month",
              "Personalised nutrition consult (quarterly)",
              "Concierge support via WhatsApp"],
             39500, 1900, 54.99, 395000, 19000, 549.99, 0),
        ]
        for i, (slug, name, tagline, features, p_ng_m, p_mu_m, p_us_m,
                p_ng_y, p_mu_y, p_us_y, featured) in enumerate(plans):
            conn.execute("""INSERT INTO subscription_plans (slug, name, tagline, features_json,
                price_ngn_monthly, price_mur_monthly, price_usd_monthly,
                price_ngn_yearly, price_mur_yearly, price_usd_yearly,
                is_featured, sort_order) VALUES (?,?,?,?, ?,?,?, ?,?,?, ?,?)""",
                (slug, name, tagline, json.dumps(features),
                 p_ng_m, p_mu_m, p_us_m, p_ng_y, p_mu_y, p_us_y, featured, i))

    # ─── FAQs (categorised) ───────────────────────────────────────────────
    if not conn.execute("SELECT 1 FROM faqs LIMIT 1").fetchone():
        faqs = [
            # CUSTOMERS
            ("customers", "How fresh are KCBlendz smoothies?",
             "Every smoothie is hand-blended on the morning of delivery. We never pre-mix, pasteurise or use concentrates. Once delivered, drink within 24 hours and keep refrigerated."),
            ("customers", "Which areas do you deliver to?",
             "Our campus kitchen is in Pamplemousses, Mauritius — we deliver island-wide within 24 hours, with most orders arriving same-day. In Nigeria we deliver across Lagos with same-day options for orders placed before 1pm. Global customers can order our shelf-stable products which ship via DHL."),
            ("customers", "How do I track my order?",
             "Every order page has a live tracking timeline. Signed-in customers see all their orders under Account → Orders. Guests can track via the link in their confirmation email or by entering their order number and email on the Track Order page."),
            ("customers", "What payment methods do you accept?",
             "Nigeria — Paystack (cards, USSD, bank transfer, mobile money). Mauritius & Global — PayPal and credit/debit cards. Bank transfer with proof upload is available everywhere; we verify within 12 hours."),
            ("customers", "What is your refund policy?",
             "If your order arrives damaged or incorrect, message us within 24 hours of delivery with a photo. You can also submit a Request Refund from your order page and our team will review within 12 hours."),
            ("customers", "Can I use a promo code?",
             "Yes — enter your code at checkout. New customers automatically get 10% off their first order with code WELCOME10."),
            # OUR TEAM
            ("team", "Who founded KCBlendz?",
             "KCBlendz was founded by our culinary lead in 2024 at our campus kitchen in Pamplemousses (\"Kitchen 2 Kongo\"). The brand now spans Mauritius, Nigeria and a global shelf-stable line."),
            ("team", "How do you source your fruit?",
             "We work directly with smallholder farmers and certified agro-processors in Mauritius and West Africa. We pay above the spot rate when seasons are tough and never accept anything that wouldn't pass the eat-it-raw test."),
            ("team", "Are you hiring?",
             "We grow our team in line with new market launches. Open roles are listed on the Contact page — and we always read unsolicited applications from passionate wellness/food people."),
            ("team", "Do you run nutrition workshops?",
             "Yes — Flourish subscribers get quarterly consults, and we host quarterly free wellness clinics in Mauritius and Lagos. Subscribe to the newsletter for dates."),
            # INSIGHTS
            ("insights", "Why no added sugar?",
             "Whole fruit gives the body sugar wrapped in fibre, water, and micronutrients — the body processes that very differently to refined sugar. Add cane sugar or syrup and you bypass that biology completely."),
            ("insights", "Are smoothies as good as eating whole fruit?",
             "Nearly. Blending preserves the fibre (unlike juicing), so the glucose hit is buffered. We recommend pairing smoothies with food and chewing slowly — the brain registers calories better that way."),
            ("insights", "What makes a wellness shot work?",
             "Concentration. A 60ml shot packs the immune-active compounds of a much larger drink — ginger, turmeric, citrus, sometimes Manuka. Taken daily for 7+ days, the cumulative effect is measurable."),
            ("insights", "Are your products certified?",
             "Our Mauritius kitchen is HACCP-certified. Our Nigerian production runs to NAFDAC-aligned standards. Shelf-stable products carry batch numbers and best-before dates."),
        ]
        for i, (cat, q, a) in enumerate(faqs):
            conn.execute("INSERT INTO faqs (category, question, answer, sort_order) VALUES (?,?,?,?)",
                         (cat, q, a, i))

    # ─── Team members ─────────────────────────────────────────────────────
    if not conn.execute("SELECT 1 FROM team_members LIMIT 1").fetchone():
        team = [
            ("Kelly Okonkwo", "Founder & Head of Blends",
             "Trained chef and wellness nut. Believes the best medicine is a fridge full of fruit.",
             "https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?w=400&q=80"),
            ("Aisha Rambodun", "Mauritius Kitchen Lead",
             "Runs the Pamplemousses kitchen. The reason your smoothie arrives at 8am sharp.",
             "https://images.unsplash.com/photo-1580489944761-15a19d654956?w=400&q=80"),
            ("Tobi Adelani", "Lagos Operations",
             "Logistics ninja. Has personally delivered through every rainy season since launch.",
             "https://images.unsplash.com/photo-1531123897727-8f129e1688ce?w=400&q=80"),
            ("Maya Chen", "Wellness Nutritionist",
             "RDN. Designs our seasonal menus around real nutrition science, not trends.",
             "https://images.unsplash.com/photo-1438761681033-6461ffad8d80?w=400&q=80"),
        ]
        for i, (n, r, b, a) in enumerate(team):
            conn.execute("""INSERT INTO team_members (full_name, role, bio, avatar_url, sort_order)
                            VALUES (?,?,?,?,?)""", (n, r, b, a, i))

    # ─── Verified customer testimonials (homepage carousel) ───────────────
    # Attach to the most-reviewed products if any exist, otherwise to any
    # product — we only seed when there are zero reviews with avatar_url.
    if conn.execute("SELECT 1 FROM products LIMIT 1").fetchone() and \
       not conn.execute("SELECT 1 FROM reviews WHERE avatar_url IS NOT NULL LIMIT 1").fetchone():
        pids = [r["id"] for r in conn.execute("SELECT id FROM products ORDER BY id LIMIT 6").fetchall()]
        if pids:
            testimonials = [
                ("Marie-Claire D.", "Pamplemousses, MU", 5,
                 "Morning routine: KCBlendz Glow Splash + 20-min walk. Skin's clearer, energy lasts till lunch.",
                 "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=200&q=80"),
                ("Priya R.", "Port Louis, MU", 5,
                 "The Build-Your-Blend feature is genius. My custom post-gym shake has replaced three different supplements.",
                 "https://images.unsplash.com/photo-1438761681033-6461ffad8d80?w=200&q=80"),
                ("Marcus L.", "London, UK", 5,
                 "Dried mango powder arrived in perfect condition via DHL. Stirring it into oatmeal has become non-negotiable.",
                 "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=200&q=80"),
                ("Chinwe O.", "Lagos, NG", 5,
                 "Same-day delivery in Lekki is a game-changer. The Power Boost is officially my training-day fuel.",
                 "https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?w=200&q=80"),
                ("Ravi P.", "Curepipe, MU", 4,
                 "Kids ask for the Kiddies Pack by name now. No more fighting over fizzy drinks at lunch.",
                 "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=200&q=80"),
                ("Linda S.", "Toronto, CA", 5,
                 "Subscribed to Bloom in January. By March I'd weaned myself off my afternoon coffee. Total win.",
                 "https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=200&q=80"),
            ]
            for i, (name, loc, rating, body, avatar) in enumerate(testimonials):
                pid = pids[i % len(pids)]
                conn.execute("""INSERT INTO reviews (product_id, author_name, rating, title, body,
                                is_verified_buyer, avatar_url)
                                VALUES (?,?,?,?,?,?,?)""",
                             (pid, name, rating, loc, body, 1, avatar))

    # ─── Promo codes ──────────────────────────────────────────────────────
    if not conn.execute("SELECT 1 FROM promo_codes LIMIT 1").fetchone():
        promos = [
            # code, description, type, value, min_subtotal, region, max_uses
            ("WELCOME10", "10% off your first order — new customers", "percent", 10, 0, None, None),
            ("FRESH15", "15% off orders over the local free-delivery threshold", "percent", 15, 500, None, None),
            ("WELLNESS5", "Flat discount on any wellness order", "fixed", 50, 300, "MU", None),
            ("LAGOS2000", "₦2,000 off Lagos orders over ₦20,000", "fixed", 2000, 20000, "NG", None),
        ]
        for code, desc, dtype, val, minsub, region, maxu in promos:
            conn.execute("""INSERT INTO promo_codes
                (code, description, discount_type, discount_value, min_subtotal,
                 region, max_uses, is_active)
                VALUES (?,?,?,?,?,?,?,1)""",
                (code, desc, dtype, val, minsub, region, maxu))

    conn.commit()
    conn.close()
# HELPERS — region, currency, auth, security
# ─────────────────────────────────────────────────────────────────────────────
REGIONS = {
    "NG": {"name": "Nigeria",   "currency": "NGN", "symbol": "₦",   "code": "NG"},
    "MU": {"name": "Mauritius", "currency": "MUR", "symbol": "Rs ", "code": "MU"},
    "GL": {"name": "Global",    "currency": "USD", "symbol": "$",   "code": "GL"},
}


def current_region():
    code = session.get("region", "")
    return code if code in REGIONS else None


def currency_for_region(region):
    return REGIONS.get(region, REGIONS["NG"])["currency"]


def price_field_for(region):
    return {"NG": "price_ngn", "MU": "price_mur", "GL": "price_usd"}.get(region, "price_ngn")


def availability_field_for(region):
    return {"NG": "is_available_ng", "MU": "is_available_mu", "GL": "is_available_global"}.get(region, "is_available_ng")


def format_money(amount, region):
    if amount is None:
        return "—"
    info = REGIONS.get(region, REGIONS["NG"])
    if info["currency"] == "USD":
        return f"${amount:,.2f}"
    return f"{info['symbol']}{amount:,.0f}"


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    row = get_db().execute("SELECT * FROM users WHERE id=? AND status='active'", (uid,)).fetchone()
    return row


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please sign in to continue.", "info")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u or u["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def region_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_region():
            return redirect(url_for("store_select", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def audit(action, entity=None, entity_id=None, meta=None):
    u = current_user()
    get_db().execute(
        "INSERT INTO audit_logs (user_id, action, entity, entity_id, ip_address, meta) VALUES (?,?,?,?,?,?)",
        (u["id"] if u else None, action, entity, entity_id, request.remote_addr,
         json.dumps(meta) if meta else None),
    )
    get_db().commit()


def notify(user_id, title, body=None, link=None, audience="user"):
    get_db().execute(
        "INSERT INTO notifications (user_id, audience, title, body, link) VALUES (?,?,?,?,?)",
        (user_id, audience, title, body, link),
    )
    get_db().commit()


def notify_admins(title, body=None, link=None):
    db = get_db()
    admins = db.execute("SELECT id FROM users WHERE role='admin' AND status='active'").fetchall()
    for a in admins:
        db.execute("INSERT INTO notifications (user_id, audience, title, body, link) VALUES (?,?,?,?,?)",
                   (a["id"], "admin", title, body, link))
    db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# v2 HELPERS — order tracking, promo codes, nav categories, etc.
# ─────────────────────────────────────────────────────────────────────────────
def record_order_event(order_id, status, note=None, actor="system"):
    """Append an entry to the order's tracking timeline. Idempotent — the same
    (order_id, status) pair is only ever recorded once so the timeline stays
    clean even if a downstream caller fires the same transition twice."""
    db = get_db()
    existing = db.execute("""SELECT 1 FROM order_status_events
        WHERE order_id=? AND status=? LIMIT 1""", (order_id, status)).fetchone()
    if existing:
        return
    db.execute("""INSERT INTO order_status_events (order_id, status, note, actor)
        VALUES (?,?,?,?)""", (order_id, status, note, actor))
    db.commit()


def activate_subscription_for_order(order_id):
    """If the given order is a subscription order that just got paid, activate
    the linked subscription (and cancel any previous active one for the user)."""
    db = get_db()
    o = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not o or not o["is_subscription"] or not o["subscription_id"]:
        return
    sub = db.execute("SELECT * FROM subscriptions WHERE id=?",
                     (o["subscription_id"],)).fetchone()
    if not sub or sub["status"] == "active":
        return
    # one active sub per user — cancel the prior active one
    db.execute("""UPDATE subscriptions SET status='cancelled',
        cancelled_at=datetime('now')
        WHERE user_id=? AND status='active' AND id != ?""",
        (sub["user_id"], sub["id"]))
    db.execute("""UPDATE subscriptions SET status='active',
        started_at=datetime('now') WHERE id=?""", (sub["id"],))
    # Also flip the linked order to 'delivered' so the timeline's third step
    # ("Subscription active") shows as the current step.
    db.execute("""UPDATE orders SET order_status='delivered',
        updated_at=datetime('now') WHERE id=?""", (order_id,))
    db.commit()
    record_order_event(order_id, "delivered",
                       note="Subscription activated", actor="system")
    plan = db.execute("SELECT name FROM subscription_plans WHERE id=?",
                       (sub["plan_id"],)).fetchone()
    plan_name = plan["name"] if plan else "your plan"
    notify_admins(f"New subscription: {plan_name} ({sub['billing_cycle']})",
                  f"{o['full_name']} subscribed and paid.",
                  url_for("admin_user_detail", uid=sub["user_id"]))
    notify(sub["user_id"], f"Welcome to {plan_name}!",
           f"Your {sub['billing_cycle']} subscription is active. "
           f"Manage it any time from your account.",
           url_for("account_dashboard"))
    audit("subscription.activated", "subscriptions", sub["id"],
          {"order_id": order_id})


def generate_tracking_token():
    """Short, URL-safe token used in the public tracking URL emailed to guests."""
    return secrets.token_urlsafe(12)


def resolve_promo_code(code, subtotal, region):
    """Return (discount_amount, promo_row, error_message). The promo is only
    applied if active, in-window, region-applicable, under usage cap, and the
    subtotal meets the minimum. Returns (0, None, 'reason') if not applicable.
    """
    if not code:
        return 0.0, None, None
    code = code.strip().upper()
    db = get_db()
    p = db.execute("SELECT * FROM promo_codes WHERE code=? COLLATE NOCASE", (code,)).fetchone()
    if not p:
        return 0.0, None, "Promo code not found."
    if not p["is_active"]:
        return 0.0, None, "This promo code is no longer active."
    now = datetime.now().isoformat(timespec="seconds")
    if p["starts_at"] and now < p["starts_at"]:
        return 0.0, None, "This promo code is not yet active."
    if p["ends_at"] and now > p["ends_at"]:
        return 0.0, None, "This promo code has expired."
    if p["region"] and p["region"] != region:
        return 0.0, None, "This promo code is not available in your region."
    if p["max_uses"] is not None and p["used_count"] >= p["max_uses"]:
        return 0.0, None, "This promo code has reached its usage limit."
    if subtotal < (p["min_subtotal"] or 0):
        return 0.0, None, f"Minimum subtotal of {format_money(p['min_subtotal'], region)} required."
    if p["discount_type"] == "percent":
        discount = round(subtotal * (p["discount_value"] / 100.0), 2)
    else:
        discount = float(p["discount_value"])
    # never refund more than the subtotal
    return min(discount, subtotal), p, None


def get_active_nav_categories():
    """Return the small, curated set of categories shown in the navbar.
    Pulls live from the DB (so admin changes propagate) but caches on `g`
    for the duration of the request — fixes the 'navbar doesn't update'
    bug without one DB hit per template include."""
    if "_nav_cats" not in g:
        g._nav_cats = get_db().execute("""SELECT slug, name FROM categories
            WHERE is_active=1 AND COALESCE(is_deleted,0)=0
            ORDER BY sort_order LIMIT 7""").fetchall()
    return g._nav_cats


def admin_unread_counts():
    """Two cheap COUNT(*) queries used by the admin top-bar polling endpoint
    and the initial page render. Returns dict with notification + message counts."""
    db = get_db()
    u = current_user()
    if not u:
        return {"notifs": 0, "messages": 0, "orders_pending": 0}
    return {
        "notifs": db.execute("""SELECT COUNT(*) c FROM notifications
            WHERE audience='admin' AND user_id=? AND is_read=0""", (u["id"],)).fetchone()["c"],
        "messages": db.execute("""SELECT COUNT(*) c FROM contact_messages
            WHERE is_handled=0""").fetchone()["c"],
        "orders_pending": db.execute("""SELECT COUNT(*) c FROM orders
            WHERE order_status='pending'""").fetchone()["c"],
    }


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXT


def save_upload(file_storage):
    if not file_storage or file_storage.filename == "" or not allowed_file(file_storage.filename):
        return None
    fn = secure_filename(file_storage.filename)
    stem, ext = os.path.splitext(fn)
    safe = f"{stem}-{secrets.token_hex(6)}{ext}"
    file_storage.save(UPLOAD_FOLDER / safe)
    return url_for("static", filename=f"uploads/{safe}")


# CSRF — lightweight, session-bound
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def check_csrf():
    tok = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
    return tok and hmac.compare_digest(tok, session.get("_csrf", ""))


@app.before_request
def enforce_csrf():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.path.startswith("/api/"):
            return  # api uses JSON + session
        if not check_csrf():
            abort(400, "Invalid CSRF token")


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


# Validation helpers
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[\d+\-\s()]{7,20}$")


def valid_email(s):
    return bool(s and EMAIL_RE.match(s.strip()))


def valid_phone(s):
    return bool(s and PHONE_RE.match(s.strip()))


def region_from_phone(phone):
    """Detect KCBlendz region (NG / MU / GL) from a phone's international
    dialling code. Defaults to 'GL' when the prefix isn't recognised.
    +234 → NG · +230 → MU · everything else → GL.
    """
    if not phone:
        return None
    s = re.sub(r"[^\d+]", "", phone)
    if s.startswith("+234") or s.startswith("234"):
        return "NG"
    if s.startswith("+230") or s.startswith("230"):
        return "MU"
    if s.startswith("+"):
        return "GL"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# MFA — TOTP (RFC 6238) implemented with stdlib only
# ─────────────────────────────────────────────────────────────────────────────
import base64 as _b64
import struct as _struct
import hashlib as _hashlib
import time as _time


def generate_mfa_secret():
    """Random 20-byte secret, base32-encoded (no padding) — what Google
    Authenticator and similar apps expect."""
    return _b64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _totp_code(secret_b32, t=None, step=30, digits=6):
    """RFC 6238 / 4226 TOTP. Returns a zero-padded 6-digit string."""
    if t is None:
        t = int(_time.time())
    # Pad base32 secret back to a multiple of 8
    s = secret_b32.upper().replace(" ", "")
    s += "=" * ((-len(s)) % 8)
    key = _b64.b32decode(s)
    counter = t // step
    msg = _struct.pack(">Q", counter)
    h = hmac.new(key, msg, _hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code_int = (_struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code_int).zfill(digits)


def verify_totp(secret_b32, code, window=1):
    """Verify with ±`window` steps of clock drift tolerance. `code` may have
    spaces or be a string of digits; non-digits are stripped. Returns True on
    a valid current-or-adjacent code. Uses hmac.compare_digest to avoid
    timing-side-channels."""
    if not secret_b32 or not code:
        return False
    code = "".join(c for c in str(code) if c.isdigit())
    if len(code) != 6:
        return False
    now = int(_time.time())
    step = 30
    for delta in range(-window, window + 1):
        if hmac.compare_digest(_totp_code(secret_b32, now + delta * step), code):
            return True
    return False


def mfa_otpauth_uri(secret_b32, email, issuer="KCBlendz"):
    """Build the `otpauth://` URI a QR-code library renders into a scannable code."""
    from urllib.parse import quote
    label = f"{issuer}:{email}"
    return (f"otpauth://totp/{quote(label)}"
            f"?secret={secret_b32}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30")


# ─────────────────────────────────────────────────────────────────────────────
# RATE LIMIT — in-process token bucket; prevents brute force on /login,/register
# ─────────────────────────────────────────────────────────────────────────────
_RL_BUCKETS = {}   # key -> (window_started_at, count)
_RL_LOCK = __import__("threading").Lock()


def rate_limited(key, *, max_attempts=5, window_seconds=60):
    """Returns (allowed: bool, retry_after_seconds: int). Resets count after
    `window_seconds` of quiet. Per-process — fine for single-worker gunicorn;
    if you scale to multiple workers, this becomes per-worker which is still
    a useful floor but not a global cap. We intentionally keep it in-memory
    so the app has no Redis/DB dependency for the security floor."""
    # Tests + local dev share an IP across many requests; skip the floor
    # entirely under app.testing/app.debug so test order can't cause spurious
    # 429s and devs can hit /login freely while iterating.
    if app.testing or app.debug:
        return True, 0
    now = _time.time()
    with _RL_LOCK:
        window_start, count = _RL_BUCKETS.get(key, (now, 0))
        if now - window_start > window_seconds:
            window_start, count = now, 0
        count += 1
        _RL_BUCKETS[key] = (window_start, count)
        # GC: opportunistically discard old buckets every ~200 calls
        if len(_RL_BUCKETS) > 5000:
            cutoff = now - max(window_seconds * 4, 600)
            for k in list(_RL_BUCKETS.keys()):
                if _RL_BUCKETS[k][0] < cutoff:
                    del _RL_BUCKETS[k]
    if count > max_attempts:
        return False, int(window_seconds - (now - window_start))
    return True, 0


def rate_limit_client_key(prefix):
    """Stable per-client key for rate-limiting. Includes the client IP so
    different attackers don't share the same bucket. Falls back gracefully
    when X-Forwarded-For is absent."""
    ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
          or request.remote_addr or "0.0.0.0")
    return f"{prefix}:{ip}"


# ─────────────────────────────────────────────────────────────────────────────
# CART — server-side cart kept in session
# ─────────────────────────────────────────────────────────────────────────────
def get_cart():
    return session.setdefault("cart", {"items": [], "region": None})


def cart_count():
    return sum(int(i.get("quantity", 0)) for i in get_cart().get("items", []))


def cart_subtotal():
    return sum(float(i["unit_price"]) * int(i["quantity"]) for i in get_cart().get("items", []))


def cart_clear_if_region_change():
    cart = get_cart()
    if cart["region"] != current_region():
        # store-specific cart per requirement — flush on region switch
        cart["items"] = []
        cart["region"] = current_region()
        session.modified = True


# ─────────────────────────────────────────────────────────────────────────────
# CONTEXT PROCESSORS
# ─────────────────────────────────────────────────────────────────────────────
@app.context_processor
def inject_globals():
    return dict(
        current_user=current_user,
        current_region=current_region,
        REGIONS=REGIONS,
        cart_count=cart_count,
        format_money=format_money,
        csrf_token=csrf_token,
        current_year=datetime.now().year,
        nav_categories=get_active_nav_categories,
        admin_unread=admin_unread_counts,
    )


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC ROUTES — store selector, home, shop, builder, content
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
def root():
    # If no region chosen yet, force the store selector page
    if not current_region():
        return redirect(url_for("store_select"))
    return redirect(url_for("home"))


@app.route("/store")
def store_select():
    return render_template("public/store_select.html", next_url=request.args.get("next") or url_for("home"))


@app.route("/store/<region>", methods=["POST"])
def store_set(region):
    if region not in REGIONS:
        abort(404)
    session["region"] = region
    session.modified = True
    cart_clear_if_region_change()
    nxt = request.form.get("next") or url_for("home")
    return redirect(nxt)


@app.route("/home")
@region_required
def home():
    cart_clear_if_region_change()
    db = get_db()
    region = current_region()
    avail = availability_field_for(region)
    price = price_field_for(region)
    featured = db.execute(f"""SELECT * FROM products WHERE is_active=1 AND {avail}=1 AND is_featured=1
        ORDER BY id LIMIT 8""").fetchall()
    bestsellers = db.execute(f"""SELECT * FROM products WHERE is_active=1 AND {avail}=1 AND is_bestseller=1
        ORDER BY id LIMIT 8""").fetchall()
    new_in = db.execute(f"""SELECT * FROM products WHERE is_active=1 AND {avail}=1 AND is_new=1
        ORDER BY id LIMIT 8""").fetchall()
    # v2: trending = most-ordered in last 30 days
    trending = db.execute(f"""SELECT p.* FROM products p
        JOIN order_items oi ON oi.product_id = p.id
        JOIN orders o ON o.id = oi.order_id
        WHERE p.is_active=1 AND p.{avail}=1
          AND date(o.created_at) >= date('now','-30 days')
        GROUP BY p.id ORDER BY SUM(oi.quantity) DESC LIMIT 8""").fetchall()
    categories = db.execute("""SELECT * FROM categories
        WHERE is_active=1 AND COALESCE(is_deleted,0)=0 ORDER BY sort_order""").fetchall()
    posts = db.execute("SELECT * FROM blog_posts WHERE is_published=1 ORDER BY created_at DESC LIMIT 4").fetchall()
    sub_plans = db.execute("""SELECT * FROM subscription_plans
        WHERE is_active=1 ORDER BY sort_order, id""").fetchall()
    # v2: real customer testimonials (with avatars), highest-rated first
    testimonials = db.execute("""SELECT r.*, p.name AS product_name, p.slug AS product_slug
        FROM reviews r LEFT JOIN products p ON p.id = r.product_id
        WHERE r.is_approved=1 AND r.avatar_url IS NOT NULL
        ORDER BY r.rating DESC, r.created_at DESC LIMIT 9""").fetchall()
    return render_template(
        "public/home.html",
        featured=featured, bestsellers=bestsellers, new_in=new_in, trending=trending,
        categories=categories, posts=posts, price_field=price,
        testimonials=testimonials, sub_plans=sub_plans,
    )


@app.route("/shop")
@region_required
def shop():
    db = get_db()
    region = current_region()
    avail = availability_field_for(region)
    price = price_field_for(region)
    q = request.args.get("q", "").strip()
    cat_slug = request.args.get("category", "").strip()
    tag = request.args.get("tag", "").strip()
    sort = request.args.get("sort", "featured")

    sql = f"""SELECT p.*, c.name AS category_name, c.slug AS category_slug
              FROM products p LEFT JOIN categories c ON c.id=p.category_id
              WHERE p.is_active=1 AND p.{avail}=1"""
    params = []
    if q:
        sql += " AND (p.name LIKE ? OR p.ingredients LIKE ? OR p.tags LIKE ?)"
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if cat_slug:
        sql += " AND c.slug=?"
        params.append(cat_slug)
    if tag:
        sql += " AND p.tags LIKE ?"
        params.append(f"%{tag}%")
    order = {
        "price_asc":  f" ORDER BY p.{price} ASC",
        "price_desc": f" ORDER BY p.{price} DESC",
        "new":         " ORDER BY p.created_at DESC",
        "featured":    " ORDER BY p.is_featured DESC, p.is_bestseller DESC, p.id ASC",
    }.get(sort, " ORDER BY p.is_featured DESC, p.id ASC")
    sql += order

    products = db.execute(sql, params).fetchall()
    categories = db.execute("SELECT * FROM categories WHERE is_active=1 ORDER BY sort_order").fetchall()
    return render_template("public/shop.html",
                           products=products, categories=categories,
                           q=q, cat_slug=cat_slug, tag=tag, sort=sort, price_field=price)


@app.route("/product/<slug>")
@region_required
def product_detail(slug):
    db = get_db()
    region = current_region()
    avail = availability_field_for(region)
    price = price_field_for(region)
    p = db.execute(
        f"""SELECT p.*, c.name AS category_name, c.slug AS category_slug
            FROM products p LEFT JOIN categories c ON c.id=p.category_id
            WHERE p.slug=? AND p.is_active=1 AND p.{avail}=1""", (slug,)).fetchone()
    if not p:
        abort(404)
    related = db.execute(
        f"""SELECT * FROM products WHERE category_id=? AND id<>? AND is_active=1 AND {avail}=1
            ORDER BY RANDOM() LIMIT 4""", (p["category_id"], p["id"])).fetchall()
    reviews = db.execute("""SELECT * FROM reviews WHERE product_id=? AND is_approved=1
                            ORDER BY created_at DESC""", (p["id"],)).fetchall()
    rating_stats = db.execute("""SELECT COUNT(*) AS n, COALESCE(AVG(rating),0) AS avg_rating
                                 FROM reviews WHERE product_id=? AND is_approved=1""", (p["id"],)).fetchone()
    return render_template("public/product.html", p=p, related=related, price_field=price,
                           reviews=reviews, rating_stats=rating_stats)


# ─────────────────────────────────────────────────────────────────────────────
# SMOOTHIE BUILDER
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/builder")
@region_required
def builder():
    db = get_db()
    region = current_region()
    price = price_field_for(region)
    opts = db.execute(f"""SELECT id, option_type, name, {price} AS price
                          FROM builder_options WHERE is_active=1 ORDER BY option_type, sort_order""").fetchall()
    grouped = {"cup_size": [], "fruit": [], "base": [], "sweetener": [], "addon": [], "booster": []}
    for o in opts:
        if o["option_type"] in grouped:
            grouped[o["option_type"]].append(dict(o))
    return render_template("public/builder.html", opts=grouped, price_field=price)


@app.route("/api/builder/price", methods=["POST"])
@region_required
def api_builder_price():
    region = current_region()
    price = price_field_for(region)
    data = request.get_json(silent=True) or {}
    ids = []
    for k in ("cup_size", "fruits", "base", "sweeteners", "addons", "boosters"):
        v = data.get(k)
        if isinstance(v, list):
            ids.extend(v)
        elif v:
            ids.append(v)
    if not ids:
        return jsonify({"price": 0, "currency": currency_for_region(region)})
    placeholders = ",".join("?" for _ in ids)
    rows = get_db().execute(
        f"SELECT id, name, option_type, {price} AS price FROM builder_options WHERE id IN ({placeholders})", ids
    ).fetchall()
    qty = max(1, int(data.get("quantity", 1)))
    unit_price = sum(r["price"] for r in rows)
    return jsonify({
        "unit_price": unit_price,
        "quantity": qty,
        "price": unit_price * qty,
        "currency": currency_for_region(region),
        "items": [dict(r) for r in rows],
    })


@app.route("/builder/add-to-cart", methods=["POST"])
@region_required
def builder_add_to_cart():
    region = current_region()
    price_col = price_field_for(region)
    cur = currency_for_region(region)
    try:
        config = json.loads(request.form["config_json"])
    except Exception:
        flash("Invalid smoothie configuration.", "error")
        return redirect(url_for("builder"))

    ids = []
    for k in ("cup_size", "fruits", "base", "sweeteners", "addons", "boosters"):
        v = config.get(k)
        if isinstance(v, list):
            ids.extend(v)
        elif v:
            ids.append(v)
    if not ids:
        flash("Pick at least a cup size and one fruit.", "error")
        return redirect(url_for("builder"))

    placeholders = ",".join("?" for _ in ids)
    rows = get_db().execute(
        f"SELECT id, name, option_type, {price_col} AS price FROM builder_options WHERE id IN ({placeholders})", ids
    ).fetchall()
    grouped = {}
    for r in rows:
        grouped.setdefault(r["option_type"], []).append(r["name"])
    # validation: at least 1 fruit, max 3 fruits, exactly 1 cup, exactly 1 base
    if len(grouped.get("cup_size", [])) != 1:
        flash("Choose one cup size.", "error"); return redirect(url_for("builder"))
    n_fruits = len(grouped.get("fruit", []))
    if n_fruits < 1 or n_fruits > 3:
        flash("Pick between 1 and 3 fruits.", "error"); return redirect(url_for("builder"))
    if len(grouped.get("base", [])) != 1:
        flash("Choose one base.", "error"); return redirect(url_for("builder"))

    qty = max(1, int(request.form.get("quantity", 1)))
    unit_price = sum(r["price"] for r in rows)
    meta_lines = []
    for k in ("cup_size", "fruit", "base", "sweetener", "addon", "booster"):
        if k in grouped:
            meta_lines.append(f"{k.replace('_', ' ').title()}: {', '.join(grouped[k])}")
    meta = " · ".join(meta_lines)

    # Optionally save a "named" smoothie for the user
    save_name = request.form.get("save_name", "").strip()
    u = current_user()
    smoothie_id = None
    if save_name and u:
        cur_db = get_db()
        cur_db.execute("""INSERT INTO custom_smoothies (user_id, name, config_json, region, price, currency)
            VALUES (?,?,?,?,?,?)""", (u["id"], save_name, json.dumps(config), region, unit_price, cur))
        smoothie_id = cur_db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        cur_db.commit()
        flash(f"Saved custom smoothie '{save_name}' to your account.", "success")

    cart = get_cart()
    cart["region"] = region
    cart["items"].append({
        "kind": "custom",
        "name": save_name or "Custom Smoothie",
        "image": image_for_blend(grouped.get("fruit", [])),
        "meta": meta,
        "unit_price": unit_price,
        "quantity": qty,
        "custom_smoothie_id": smoothie_id,
    })
    session.modified = True
    flash("Custom smoothie added to cart.", "success")
    return redirect(url_for("cart"))


# ─────────────────────────────────────────────────────────────────────────────
# CART & CHECKOUT
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/cart/add", methods=["POST"])
@region_required
def cart_add():
    region = current_region()
    price_col = price_field_for(region)
    avail = availability_field_for(region)
    pid = int(request.form["product_id"])
    qty = max(1, int(request.form.get("quantity", 1)))
    p = get_db().execute(
        f"SELECT * FROM products WHERE id=? AND is_active=1 AND {avail}=1", (pid,)
    ).fetchone()
    if not p:
        flash("This product is not available in your store.", "error")
        return redirect(request.referrer or url_for("shop"))
    cart = get_cart()
    cart["region"] = region
    # merge with existing same-product line
    for item in cart["items"]:
        if item.get("kind") == "product" and item.get("product_id") == p["id"]:
            item["quantity"] = int(item["quantity"]) + qty
            session.modified = True
            flash(f"Added another {p['name']} to your cart.", "success")
            return redirect(request.referrer or url_for("cart"))
    cart["items"].append({
        "kind": "product",
        "product_id": p["id"],
        "name": p["name"],
        "image": p["image_url"],
        "meta": p["ingredients"] or "",
        "unit_price": p[price_col],
        "quantity": qty,
    })
    session.modified = True
    flash(f"{p['name']} added to cart.", "success")
    return redirect(request.referrer or url_for("cart"))


@app.route("/cart")
@region_required
def cart():
    cart_clear_if_region_change()
    return render_template("public/cart.html", cart=get_cart(),
                           subtotal=cart_subtotal())


@app.route("/cart/update", methods=["POST"])
@region_required
def cart_update():
    idx = int(request.form["index"])
    qty = int(request.form.get("quantity", 1))
    cart = get_cart()
    if 0 <= idx < len(cart["items"]):
        if qty <= 0:
            cart["items"].pop(idx)
        else:
            cart["items"][idx]["quantity"] = qty
        session.modified = True
    return redirect(url_for("cart"))


@app.route("/cart/remove", methods=["POST"])
@region_required
def cart_remove():
    idx = int(request.form["index"])
    cart = get_cart()
    if 0 <= idx < len(cart["items"]):
        cart["items"].pop(idx)
        session.modified = True
    return redirect(url_for("cart"))


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOM-BLEND IMAGERY
# Picks a representative photo for a built smoothie from its dominant (first)
# fruit so the cart / receipt / order pages show something appetising instead
# of a generic cup. Falls back to a neutral smoothie photo.
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_BLEND_IMAGE = "https://images.unsplash.com/photo-1610970881699-44a5587cabec?w=800&q=80"

CUSTOM_BLEND_IMAGE_BY_FRUIT = {
    "Mango":       "https://images.unsplash.com/photo-1605027990121-cbae9e0642df?w=800&q=80",
    "Banana":      "https://images.unsplash.com/photo-1553530666-ba11a7da3888?w=800&q=80",
    "Strawberry":  "https://images.unsplash.com/photo-1505252585461-04db1eb84625?w=800&q=80",
    "Pineapple":   "https://images.unsplash.com/photo-1502741338009-cac2772e18bc?w=800&q=80",
    "Watermelon":  "https://images.unsplash.com/photo-1623065422902-30a2d299bbe4?w=800&q=80",
    "Orange":      "https://images.unsplash.com/photo-1546173159-315724a31696?w=800&q=80",
    "Apple":       "https://images.unsplash.com/photo-1638176066757-37c50b3d2db9?w=800&q=80",
    "Kiwi":        "https://images.unsplash.com/photo-1585059895524-72359e06133a?w=800&q=80",
    "Blueberry":   "https://images.unsplash.com/photo-1498557850523-fd3d118b962e?w=800&q=80",
    "Avocado":     "https://images.unsplash.com/photo-1601039641847-7857b994d704?w=800&q=80",
    "Papaya":      "https://images.unsplash.com/photo-1517282009859-f000ec3b26fe?w=800&q=80",
    "Passion Fruit": "https://images.unsplash.com/photo-1604495772376-9657f0035eb5?w=800&q=80",
}


def image_for_blend(fruits):
    """Return an image URL for a custom blend based on its dominant (first)
    fruit. Empty list or unknown fruit → DEFAULT_BLEND_IMAGE."""
    if not fruits:
        return DEFAULT_BLEND_IMAGE
    first = (fruits[0] or "").strip().title()
    return CUSTOM_BLEND_IMAGE_BY_FRUIT.get(first, DEFAULT_BLEND_IMAGE)


def free_delivery_threshold(region):
    """Order subtotal at/above which local delivery is free.
    Mirrors the announcement bar (NG ₦15,000 · MU Rs 500 · GL $50)."""
    return {"NG": 15000.0, "MU": 500.0, "GL": 50.0}.get(region, 0.0)


def delivery_fee_for(region, city=None, subtotal=None):
    base = {"NG": 1500.0, "MU": 80.0, "GL": 12.99}.get(region, 0.0)
    # Free local delivery once the subtotal clears the regional threshold.
    if subtotal is not None and subtotal >= free_delivery_threshold(region):
        return 0.0
    return base


@app.route("/checkout", methods=["GET", "POST"])
@region_required
def checkout():
    cart_clear_if_region_change()
    cart = get_cart()
    if not cart["items"]:
        flash("Your cart is empty.", "info")
        return redirect(url_for("shop"))
    region = current_region()
    currency = currency_for_region(region)
    subtotal = cart_subtotal()
    u = current_user()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        fulfillment = request.form.get("fulfillment", "delivery")
        address = request.form.get("address", "").strip()
        city = request.form.get("city", "").strip()
        state = request.form.get("state", "").strip()
        country = REGIONS[region]["name"]
        delivery_date = request.form.get("delivery_date", "")
        delivery_slot = request.form.get("delivery_slot", "")
        notes = request.form.get("notes", "").strip()
        payment_method = request.form.get("payment_method", "card")

        # Validate
        errors = []
        if not full_name: errors.append("Full name is required.")
        if not valid_email(email): errors.append("Valid email is required.")
        if not valid_phone(phone): errors.append("Valid WhatsApp / phone number is required.")
        if fulfillment == "delivery" and (not address or not city):
            errors.append("Delivery address and city are required.")
        if payment_method not in ("card", "paypal", "bank_transfer"):
            errors.append("Choose a valid payment method.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("public/checkout.html", cart=cart, subtotal=subtotal,
                                   delivery_fee=delivery_fee_for(region, subtotal=subtotal), region=region,
                                   currency=currency, form=request.form)

        delivery_fee = delivery_fee_for(region, city, subtotal) if fulfillment == "delivery" else 0.0

        # v2 — apply promo code if provided
        promo_input = request.form.get("promo_code", "").strip().upper()
        discount = 0.0
        promo_row = None
        if promo_input:
            discount, promo_row, perr = resolve_promo_code(promo_input, subtotal, region)
            if perr:
                flash(perr, "error")
                return render_template("public/checkout.html", cart=cart, subtotal=subtotal,
                                       delivery_fee=delivery_fee, region=region,
                                       currency=currency, form=request.form)

        total = max(0.0, subtotal - discount) + delivery_fee
        tracking_token = generate_tracking_token()

        order_number = f"KCB-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
        db = get_db()
        db.execute("""INSERT INTO orders (order_number, user_id, guest_email, full_name, email, phone, region, currency,
            subtotal, delivery_fee, total, fulfillment_type, delivery_address, delivery_city, delivery_state, delivery_country,
            delivery_date, delivery_slot, notes, payment_method, promo_code, discount_amount, tracking_token)
            VALUES (?,?,?,?,?,?,?,?, ?,?,?, ?,?,?,?,?, ?,?,?, ?, ?,?,?)""", (
            order_number, u["id"] if u else None, None if u else email,
            full_name, email, phone, region, currency,
            subtotal, delivery_fee, total, fulfillment,
            address if fulfillment == "delivery" else None,
            city if fulfillment == "delivery" else None,
            state if fulfillment == "delivery" else None,
            country if fulfillment == "delivery" else None,
            delivery_date or None, delivery_slot or None, notes or None, payment_method,
            promo_row["code"] if promo_row else None, discount, tracking_token,
        ))
        order_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        for item in cart["items"]:
            db.execute("""INSERT INTO order_items (order_id, product_id, custom_smoothie_id,
                item_name, item_image, item_meta, unit_price, quantity, line_total)
                VALUES (?,?,?,?,?,?,?,?,?)""", (
                order_id,
                item.get("product_id"),
                item.get("custom_smoothie_id"),
                item["name"],
                item.get("image"),
                item.get("meta", ""),
                item["unit_price"],
                item["quantity"],
                float(item["unit_price"]) * int(item["quantity"]),
            ))

        if promo_row:
            db.execute("UPDATE promo_codes SET used_count = used_count + 1 WHERE id=?",
                       (promo_row["id"],))
        db.commit()

        record_order_event(order_id, "pending", note="Order placed", actor="customer")

        # NOTE: cart is intentionally NOT cleared here.
        # It is cleared only when payment is confirmed (in the payment success
        # handlers) so the customer can still recover their items if they
        # abandon payment, refresh, or change payment method.
        notify_admins(f"New order {order_number}", f"{full_name} placed an order totalling {format_money(total, region)}.",
                      url_for("admin_order_detail", order_id=order_id))
        if u:
            notify(u["id"], f"Order {order_number} received",
                   f"We've received your order. Awaiting payment.",
                   url_for("account_order_detail", order_id=order_id))
        audit("order.create", "order", order_id, {"total": total, "region": region})
        return redirect(url_for("payment", order_id=order_id))

    return render_template("public/checkout.html", cart=cart, subtotal=subtotal,
                           delivery_fee=delivery_fee_for(region, subtotal=subtotal), region=region,
                           currency=currency, form={})


# ─────────────────────────────────────────────────────────────────────────────
# PAYMENTS — sandbox/demo (Paystack-style NG, PayPal-style MU/GL, bank transfer)
# In production, replace the card branch with the real Paystack/PayPal init.
# ─────────────────────────────────────────────────────────────────────────────
def luhn_check(card_number: str) -> bool:
    """Standard Luhn checksum used by every real card network."""
    digits = [int(d) for d in card_number if d.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9: d -= 9
        total += d
    return total % 10 == 0


def detect_card_brand(card_number: str) -> str:
    """Return Visa / Mastercard / Amex / Discover from the BIN prefix."""
    n = "".join(c for c in card_number if c.isdigit())
    if not n: return "unknown"
    if n.startswith("4"): return "visa"
    if n[:2] in {"51","52","53","54","55"} or (
        len(n) >= 4 and 2221 <= int(n[:4]) <= 2720): return "mastercard"
    if n[:2] in {"34","37"}: return "amex"
    if n.startswith("6011") or n.startswith("65"): return "discover"
    if n.startswith("35"): return "jcb"
    return "card"


def validate_card_form(form):
    """Returns (ok, errors, sanitised_data) for the card form."""
    errors = []
    raw_number = form.get("card_number", "").strip()
    number = "".join(c for c in raw_number if c.isdigit())
    name = form.get("card_name", "").strip()
    exp = form.get("card_expiry", "").strip()    # MM/YY
    cvv = form.get("card_cvv", "").strip()

    if not number or not luhn_check(number):
        errors.append("Please enter a valid card number.")
    if not name or len(name) < 2:
        errors.append("Please enter the cardholder name as it appears on the card.")
    if not re.match(r"^\d{2}\s*/\s*\d{2}$", exp):
        errors.append("Expiry must be in MM/YY format.")
    else:
        try:
            mm, yy = [int(p.strip()) for p in exp.split("/")]
            if mm < 1 or mm > 12: errors.append("Expiry month must be between 01 and 12.")
            current_year = datetime.now().year % 100
            current_month = datetime.now().month
            if yy < current_year or (yy == current_year and mm < current_month):
                errors.append("This card has expired.")
        except Exception:
            errors.append("Couldn't parse expiry date.")
    if not re.match(r"^\d{3,4}$", cvv):
        errors.append("CVV must be 3 or 4 digits.")

    return (not errors, errors, {
        "number": number, "name": name, "exp": exp, "cvv": cvv,
        "brand": detect_card_brand(number),
        "last4": number[-4:] if len(number) >= 4 else "",
    })


@app.route("/payment/<int:order_id>")
def payment(order_id):
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        abort(404)
    u = current_user()
    if order["user_id"] and (not u or u["id"] != order["user_id"]):
        if not u or u["role"] != "admin":
            abort(403)
    # Already paid? Don't re-collect payment — send straight to thanks.
    if order["payment_status"] == "paid":
        return redirect(url_for("order_thanks", order_id=order_id))
    return render_template("public/payment.html", order=order)


@app.route("/payment/<int:order_id>/change-method", methods=["POST"])
def payment_change_method(order_id):
    """Let the customer swap payment method before paying, without losing
    their cart or having to re-enter checkout details."""
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        abort(404)
    if order["payment_status"] == "paid":
        flash("This order is already paid.", "info")
        return redirect(url_for("order_thanks", order_id=order_id))
    u = current_user()
    if order["user_id"] and (not u or u["id"] != order["user_id"]):
        if not u or u["role"] != "admin":
            abort(403)
    new_method = request.form.get("payment_method", "").strip()
    if new_method not in ("card", "paypal", "bank_transfer"):
        flash("Choose a valid payment method.", "error")
        return redirect(url_for("payment", order_id=order_id))
    db.execute("UPDATE orders SET payment_method=?, updated_at=datetime('now') WHERE id=?",
               (new_method, order_id))
    db.commit()
    return redirect(url_for("payment", order_id=order_id))


@app.route("/payment/<int:order_id>/process", methods=["POST"])
def payment_process(order_id):
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        abort(404)
    method = order["payment_method"]
    # Card is processed by Paystack in Nigeria and a card gateway elsewhere.
    if method == "card":
        gateway = "paystack" if order["region"] == "NG" else "card_gateway"
    elif method == "paypal":
        gateway = "paypal"
    else:
        gateway = "manual"

    if method == "paypal":
        # Simulated PayPal hosted authentication. In production this is the
        # PayPal redirect/return; here we validate the sandbox login.
        pp_email = request.form.get("paypal_email", "").strip()
        pp_pw = request.form.get("paypal_password", "")
        if not valid_email(pp_email) or len(pp_pw) < 6:
            flash("PayPal authentication failed. Check your email and password.", "error")
            return redirect(url_for("payment", order_id=order_id))
        reference = f"PAYPAL-{secrets.token_hex(6).upper()}"
        meta = json.dumps({"paypal_email": pp_email, "method": "paypal"})
        db.execute("""UPDATE orders SET payment_status='paid', payment_reference=?,
                      order_status=?, updated_at=datetime('now') WHERE id=?""",
                   (reference,
                    "delivered" if order["is_subscription"] else "processing",
                    order_id))
        db.execute("""INSERT INTO payments (order_id, method, gateway, reference, amount, currency, status, raw_payload)
                      VALUES (?,?,?,?,?,?,?,?)""",
                   (order_id, method, gateway, reference, order["total"], order["currency"], "success", meta))
        db.commit()
        record_order_event(order_id, "paid", note="Payment via PayPal", actor="system")
        if not order["is_subscription"]:
            # Subscription orders don't go through the kitchen pipeline.
            record_order_event(order_id, "processing", note="Sent to kitchen", actor="system")
        if order["user_id"]:
            if order["is_subscription"]:
                notify(order["user_id"], "Subscription payment received",
                       f"Your PayPal payment of {format_money(order['total'], order['region'])} has been confirmed — your subscription is being activated.",
                       url_for("account_dashboard"))
            else:
                notify(order["user_id"], f"Payment received for {order['order_number']}",
                       f"Thanks — your PayPal payment of {format_money(order['total'], order['region'])} has been confirmed.",
                       url_for("account_order_detail", order_id=order_id))
        if order["is_subscription"]:
            notify_admins(f"Subscription paid: {order['order_number']}",
                          f"{order['full_name']} paid {format_money(order['total'], order['region'])} via PayPal for a subscription.",
                          url_for("admin_subscriptions"))
        else:
            notify_admins(f"Payment received: {order['order_number']}",
                          f"{order['full_name']} paid {format_money(order['total'], order['region'])} via PayPal.",
                          url_for("admin_order_detail", order_id=order_id))
        audit("order.paid", "order", order_id, {"reference": reference, "method": "paypal"})
        activate_subscription_for_order(order_id)
        # Payment confirmed — safe to clear the cart now.
        if not order["is_subscription"]:
            session["cart"] = {"items": [], "region": current_region()}
        return redirect(url_for("order_thanks", order_id=order_id))

    if method == "bank_transfer":
        # require proof upload
        f = request.files.get("proof")
        proof_url = save_upload(f) if f else None
        if not proof_url:
            flash("Please upload your bank-transfer proof to continue.", "error")
            return redirect(url_for("payment", order_id=order_id))
        db.execute("""UPDATE orders SET payment_proof_url=?, order_status='processing',
                      updated_at=datetime('now') WHERE id=?""", (proof_url, order_id))
        db.execute("""INSERT INTO payments (order_id, method, gateway, reference, amount, currency, status)
                      VALUES (?,?,?,?,?,?,?)""",
                   (order_id, method, gateway, f"TRF-{secrets.token_hex(6).upper()}",
                    order["total"], order["currency"], "awaiting_verification"))
        db.commit()
        record_order_event(order_id, "processing",
                           note="Bank transfer proof uploaded — awaiting verification",
                           actor="customer")
        flash("Proof of payment uploaded. Our team will verify within 12 hours.", "success")
        notify_admins(f"Bank transfer for {order['order_number']}",
                      "A customer uploaded proof of bank transfer. Verify in admin.",
                      url_for("admin_order_detail", order_id=order_id))
        # Proof submitted — the customer has committed to this order;
        # safe to clear the cart now even though payment is still pending verification.
        if not order["is_subscription"]:
            session["cart"] = {"items": [], "region": current_region()}
        return redirect(url_for("order_thanks", order_id=order_id))

    # Card: collect real card data, validate, then mark paid (sandbox).
    ok, errors, card = validate_card_form(request.form)
    if not ok:
        for e in errors: flash(e, "error")
        return redirect(url_for("payment", order_id=order_id))

    # Build a realistic reference and store only last4 + brand (never the full PAN).
    prefix = "PSK" if gateway == "paystack" else "CARD"
    reference = f"{prefix}-{secrets.token_hex(6).upper()}"
    meta = json.dumps({"brand": card["brand"], "last4": card["last4"], "name": card["name"]})

    db.execute("""UPDATE orders SET payment_status='paid', payment_reference=?,
                  order_status=?, updated_at=datetime('now') WHERE id=?""",
               (reference,
                "delivered" if order["is_subscription"] else "processing",
                order_id))
    db.execute("""INSERT INTO payments (order_id, method, gateway, reference, amount, currency, status, raw_payload)
                  VALUES (?,?,?,?,?,?,?,?)""",
               (order_id, method, gateway, reference, order["total"], order["currency"], "success", meta))
    db.commit()
    record_order_event(order_id, "paid", note=f"Payment via {method}", actor="system")
    if not order["is_subscription"]:
        record_order_event(order_id, "processing", note="Sent to kitchen", actor="system")

    if order["user_id"]:
        if order["is_subscription"]:
            notify(order["user_id"], "Subscription payment received",
                   f"Your payment of {format_money(order['total'], order['region'])} has been confirmed — your subscription is being activated.",
                   url_for("account_dashboard"))
        else:
            notify(order["user_id"], f"Payment received for {order['order_number']}",
                   f"Thanks — your payment of {format_money(order['total'], order['region'])} has been confirmed.",
                   url_for("account_order_detail", order_id=order_id))
    if order["is_subscription"]:
        notify_admins(f"Subscription paid: {order['order_number']}",
                      f"{order['full_name']} paid {format_money(order['total'], order['region'])} for a subscription.",
                      url_for("admin_subscriptions"))
    else:
        notify_admins(f"Payment received: {order['order_number']}",
                      f"{order['full_name']} paid {format_money(order['total'], order['region'])}.",
                      url_for("admin_order_detail", order_id=order_id))
    audit("order.paid", "order", order_id, {"reference": reference, "method": method,
                                              "brand": card["brand"], "last4": card["last4"]})
    activate_subscription_for_order(order_id)
    # Payment confirmed — safe to clear the cart now.
    if not order["is_subscription"]:
        session["cart"] = {"items": [], "region": current_region()}
    return redirect(url_for("order_thanks", order_id=order_id))


@app.route("/order/<int:order_id>/thanks")
def order_thanks(order_id):
    order = get_db().execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        abort(404)
    items = get_db().execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchall()
    return render_template("public/order_thanks.html", order=order, items=items)


# ─────────────────────────────────────────────────────────────────────────────
# WELLNESS HUB
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/wellness")
def wellness():
    db = get_db()
    q = request.args.get("q", "").strip()
    cat = request.args.get("cat", "").strip().upper()
    sql = "SELECT * FROM blog_posts WHERE is_published=1"
    params = []
    if q:
        sql += " AND (title LIKE ? OR subtitle LIKE ? OR content LIKE ?)"
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if cat:
        sql += " AND category=?"
        params.append(cat)
    sql += " ORDER BY created_at DESC"
    posts = db.execute(sql, params).fetchall()
    # Trending = newest 5 (when we have view counts we'll use those instead)
    trending = db.execute("""SELECT * FROM blog_posts WHERE is_published=1
        ORDER BY created_at DESC LIMIT 5""").fetchall()
    # Distinct categories for the filter UI
    cats = db.execute("""SELECT category, COUNT(*) n FROM blog_posts
        WHERE is_published=1 AND category IS NOT NULL
        GROUP BY category ORDER BY n DESC""").fetchall()
    return render_template("public/wellness.html",
                           posts=posts, trending=trending,
                           cats=cats, q=q, active_cat=cat)


@app.route("/wellness/<slug>")
def wellness_post(slug):
    db = get_db()
    p = db.execute("SELECT * FROM blog_posts WHERE slug=? AND is_published=1", (slug,)).fetchone()
    if not p:
        abort(404)
    related = db.execute(
        "SELECT * FROM blog_posts WHERE id<>? AND is_published=1 ORDER BY RANDOM() LIMIT 3", (p["id"],)
    ).fetchall()
    return render_template("public/wellness_post.html", p=p, related=related)


# ─────────────────────────────────────────────────────────────────────────────
# STATIC / CONTENT PAGES
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/about")
def about():
    team = get_db().execute(
        "SELECT * FROM team_members WHERE is_active=1 ORDER BY sort_order"
    ).fetchall()
    return render_template("public/about.html", team=team)
@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()
        if not name or not valid_email(email) or not message:
            flash("Please complete all required fields with a valid email.", "error")
            return redirect(url_for("contact"))
        get_db().execute("INSERT INTO contact_messages (name, email, subject, message) VALUES (?,?,?,?)",
                         (name, email, subject, message))
        get_db().commit()
        notify_admins(f"New contact message from {name}", subject or message[:80])
        flash("Thanks — we'll get back to you within 24 hours.", "success")
        return redirect(url_for("contact"))
    return render_template("public/contact.html")
@app.route("/faq")
def faq(): return render_template("public/faq.html")
@app.route("/careers")
def careers(): return render_template("public/careers.html")
@app.route("/privacy")
def privacy(): return render_template("public/privacy.html")
@app.route("/terms")
def terms(): return render_template("public/terms.html")
@app.route("/refund-policy")
def refund_policy(): return render_template("public/refund.html")
@app.route("/shipping-policy")
def shipping_policy(): return render_template("public/shipping.html")


@app.route("/newsletter/subscribe", methods=["POST"])
def newsletter_subscribe():
    email = request.form.get("email", "").strip().lower()
    if not valid_email(email):
        flash("Please enter a valid email address.", "error")
        return redirect(request.referrer or url_for("home"))
    try:
        get_db().execute("INSERT INTO newsletter_subscribers (email, region) VALUES (?,?)",
                         (email, current_region()))
        get_db().commit()
        flash("You're in — welcome to the KCBlendz family.", "success")
    except sqlite3.IntegrityError:
        flash("You're already subscribed — thanks for being with us.", "info")
    return redirect(request.referrer or url_for("home"))


# ─────────────────────────────────────────────────────────────────────────────
# AUTHENTICATION
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        # Rate limit by IP first to slow brute-force without depending on email enumeration
        ok, retry_in = rate_limited(rate_limit_client_key("login"),
                                    max_attempts=8, window_seconds=300)
        if not ok:
            flash(f"Too many login attempts. Try again in {retry_in} seconds.", "error")
            return render_template("auth/login.html", email=""), 429

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        db = get_db()
        u = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not u or u["status"] != "active" or not check_password_hash(u["password_hash"], password):
            flash("Invalid email or password.", "error")
            return render_template("auth/login.html", email=email)

        if u["mfa_enabled"]:
            # Park user in pending-MFA state and redirect to the 2nd factor screen.
            session["mfa_pending_uid"] = u["id"]
            session["mfa_pending_next"] = request.args.get("next", "")
            session.modified = True
            return redirect(url_for("login_mfa"))

        session.clear()
        session["uid"] = u["id"]
        # Customer's region follows their saved region (set at signup from
        # the phone country code), or detected fresh from the phone if missing.
        user_region = u["region"] or region_from_phone(u["phone"]) or "GL"
        session["region"] = user_region
        session.permanent = True
        db.execute("UPDATE users SET last_login_at=datetime('now') WHERE id=?", (u["id"],))
        db.commit()
        audit("auth.login", "user", u["id"])
        flash(f"Welcome back, {u['full_name'].split()[0]}.", "success")
        nxt = request.args.get("next") or (url_for("admin_dashboard") if u["role"] == "admin" else url_for("account_dashboard"))
        return redirect(nxt)
    return render_template("auth/login.html", email="")


@app.route("/login/mfa", methods=["GET", "POST"])
def login_mfa():
    """Second factor — show only after a successful password check.
    Session key `mfa_pending_uid` is the only authorisation to be here."""
    pending_uid = session.get("mfa_pending_uid")
    if not pending_uid:
        return redirect(url_for("login"))
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=?", (pending_uid,)).fetchone()
    if not u or not u["mfa_enabled"]:
        session.pop("mfa_pending_uid", None)
        return redirect(url_for("login"))

    if request.method == "POST":
        ok, retry_in = rate_limited(rate_limit_client_key(f"mfa:{pending_uid}"),
                                    max_attempts=6, window_seconds=300)
        if not ok:
            flash(f"Too many code attempts. Try again in {retry_in} seconds.", "error")
            return render_template("auth/login_mfa.html"), 429
        code = request.form.get("code", "").strip()
        if verify_totp(u["mfa_secret"], code):
            next_url = session.pop("mfa_pending_next", "") or url_for(
                "admin_dashboard" if u["role"] == "admin" else "account_dashboard")
            session.clear()
            session["uid"] = u["id"]
            session.permanent = True
            db.execute("UPDATE users SET last_login_at=datetime('now') WHERE id=?", (u["id"],))
            db.commit()
            audit("auth.login.mfa", "user", u["id"])
            flash(f"Welcome back, {u['full_name'].split()[0]}.", "success")
            return redirect(next_url)
        flash("That code didn't match. Try the latest 6 digits from your authenticator.", "error")
    return render_template("auth/login_mfa.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        ok, retry_in = rate_limited(rate_limit_client_key("register"),
                                    max_attempts=5, window_seconds=600)
        if not ok:
            flash(f"Too many sign-up attempts from this network. Try again in {retry_in} seconds.", "error")
            return render_template("auth/register.html", form=request.form), 429
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        errors = []
        if not full_name: errors.append("Full name is required.")
        if not valid_email(email): errors.append("Enter a valid email.")
        if not valid_phone(phone): errors.append("Enter a valid phone number.")
        if len(password) < 8: errors.append("Password must be at least 8 characters.")
        # Only validate "passwords match" when a confirm field is actually submitted
        if confirm and password != confirm: errors.append("Passwords do not match.")
        if get_db().execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            errors.append("This email is already registered.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("auth/register.html", form=request.form)
        # Auto-detect region from phone country code so the customer is
        # placed into the right currency store without going back to /store.
        detected_region = region_from_phone(phone) or current_region() or "GL"
        get_db().execute("""INSERT INTO users (email, password_hash, full_name, phone, region)
            VALUES (?,?,?,?,?)""",
            (email, generate_password_hash(password), full_name, phone, detected_region))
        uid = get_db().execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        get_db().commit()
        session.clear()
        session["uid"] = uid
        session["region"] = detected_region
        session.permanent = True
        notify_admins(f"New customer: {full_name}", f"{email} just registered.")
        audit("auth.register", "user", uid)
        flash("Welcome to KCBlendz — your account is ready.", "success")
        return redirect(request.args.get("next") or url_for("account_dashboard"))
    return render_template("auth/register.html", form={})


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You've been signed out.", "info")
    return redirect(url_for("home"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        # Demo flow: in production, send a token email. Here we display a deterministic reset link if user exists.
        email = request.form.get("email", "").strip().lower()
        u = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if u:
            flash("If an account with that email exists, password reset instructions have been sent.", "info")
        else:
            flash("If an account with that email exists, password reset instructions have been sent.", "info")
        return redirect(url_for("login"))
    return render_template("auth/forgot.html")


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOMER ACCOUNT
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/account")
@login_required
def account_dashboard():
    u = current_user()
    db = get_db()
    # Show product orders here — subscription orders are surfaced separately
    # (a customer-facing /account/subscriptions page would list those).
    recent_orders = db.execute(
        "SELECT * FROM orders WHERE user_id=? AND COALESCE(is_subscription,0)=0 ORDER BY created_at DESC LIMIT 5", (u["id"],)
    ).fetchall()
    saved = db.execute(
        "SELECT * FROM custom_smoothies WHERE user_id=? ORDER BY created_at DESC LIMIT 4", (u["id"],)
    ).fetchall()
    stats = db.execute(
        "SELECT COUNT(*) AS n_orders, COALESCE(SUM(total),0) AS total_spent FROM orders WHERE user_id=? AND payment_status='paid' AND COALESCE(is_subscription,0)=0",
        (u["id"],)
    ).fetchone()
    notifs = db.execute(
        "SELECT * FROM notifications WHERE user_id=? AND audience='user' ORDER BY created_at DESC LIMIT 5", (u["id"],)
    ).fetchall()
    return render_template("account/dashboard.html",
                           recent_orders=recent_orders, saved=saved, stats=stats, notifs=notifs)


@app.route("/account/orders")
@login_required
def account_orders():
    u = current_user()
    orders = get_db().execute(
        "SELECT * FROM orders WHERE user_id=? AND COALESCE(is_subscription,0)=0 ORDER BY created_at DESC", (u["id"],)
    ).fetchall()
    return render_template("account/orders.html", orders=orders)


@app.route("/account/subscriptions")
@login_required
def account_subscriptions():
    """Customer self-service: view active / pending / past subscriptions."""
    u = current_user()
    subs = get_db().execute("""SELECT s.*, sp.name AS plan_name, sp.tagline
        FROM subscriptions s
        JOIN subscription_plans sp ON sp.id=s.plan_id
        WHERE s.user_id=? ORDER BY s.id DESC""", (u["id"],)).fetchall()
    return render_template("account/subscriptions.html", subs=subs)


@app.route("/account/subscriptions/<int:sid>/cancel", methods=["POST"])
@login_required
def account_subscription_cancel(sid):
    u = current_user()
    db = get_db()
    s = db.execute("SELECT * FROM subscriptions WHERE id=? AND user_id=?",
                   (sid, u["id"])).fetchone()
    if not s:
        abort(404)
    if s["status"] == "active":
        db.execute("""UPDATE subscriptions SET status='cancelled',
            cancelled_at=datetime('now') WHERE id=?""", (sid,))
        db.commit()
        audit("subscription.cancel.customer", "subscriptions", sid)
        notify_admins("Subscription cancelled by customer",
                      f"{u['full_name']} cancelled their subscription.",
                      url_for("admin_subscriptions"))
        flash("Your subscription has been cancelled.", "info")
    return redirect(url_for("account_subscriptions"))


@app.route("/account/orders/<int:order_id>")
@login_required
def account_order_detail(order_id):
    u = current_user()
    order = get_db().execute("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, u["id"])).fetchone()
    if not order:
        abort(404)
    items = get_db().execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchall()
    timeline, last_status, cancelled = order_timeline(order_id)
    refund_open = get_db().execute("""SELECT 1 FROM refund_requests
        WHERE order_id=? AND status IN ('pending','approved')""", (order_id,)).fetchone()
    return render_template("account/order_detail.html", order=order, items=items,
                           timeline=timeline, last_status=last_status, cancelled=cancelled,
                           refund_open=bool(refund_open))


@app.route("/account/orders/<int:order_id>/reorder", methods=["POST"])
@login_required
def account_reorder(order_id):
    u = current_user()
    region = current_region() or "NG"
    price_col = price_field_for(region)
    avail = availability_field_for(region)
    order = get_db().execute("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, u["id"])).fetchone()
    if not order:
        abort(404)
    items = get_db().execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchall()
    cart = get_cart()
    cart["region"] = region
    added = 0
    for it in items:
        if it["product_id"]:
            p = get_db().execute(
                f"SELECT * FROM products WHERE id=? AND is_active=1 AND {avail}=1", (it["product_id"],)
            ).fetchone()
            if p:
                cart["items"].append({
                    "kind": "product", "product_id": p["id"], "name": p["name"],
                    "image": p["image_url"], "meta": p["ingredients"] or "",
                    "unit_price": p[price_col], "quantity": it["quantity"],
                })
                added += 1
    session.modified = True
    flash(f"Re-added {added} item(s) to your cart.", "success")
    return redirect(url_for("cart"))


@app.route("/account/profile", methods=["GET", "POST"])
@login_required
def account_profile():
    u = current_user()
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        if not full_name:
            flash("Full name is required.", "error")
            return redirect(url_for("account_profile"))
        get_db().execute("UPDATE users SET full_name=?, phone=? WHERE id=?",
                         (full_name, phone, u["id"]))
        if new_pw:
            if not check_password_hash(u["password_hash"], current_pw):
                flash("Current password is incorrect.", "error")
                return redirect(url_for("account_profile"))
            if len(new_pw) < 8:
                flash("New password must be at least 8 characters.", "error")
                return redirect(url_for("account_profile"))
            get_db().execute("UPDATE users SET password_hash=? WHERE id=?",
                             (generate_password_hash(new_pw), u["id"]))
        get_db().commit()
        flash("Profile updated.", "success")
        return redirect(url_for("account_profile"))
    return render_template("account/profile.html", u=u)


@app.route("/account/saved-smoothies")
@login_required
def account_saved():
    u = current_user()
    saved = get_db().execute(
        "SELECT * FROM custom_smoothies WHERE user_id=? ORDER BY created_at DESC", (u["id"],)
    ).fetchall()
    return render_template("account/saved_smoothies.html", saved=saved)


@app.route("/account/saved-smoothies/<int:sid>/add-to-cart", methods=["POST"])
@login_required
def account_saved_add(sid):
    u = current_user()
    s = get_db().execute("SELECT * FROM custom_smoothies WHERE id=? AND user_id=?", (sid, u["id"])).fetchone()
    if not s:
        abort(404)
    cart = get_cart()
    cart["region"] = current_region()
    config = json.loads(s["config_json"])
    # Re-evaluate names from option ids
    ids = []
    for k in ("cup_size", "fruits", "base", "sweeteners", "addons", "boosters"):
        v = config.get(k)
        if isinstance(v, list): ids.extend(v)
        elif v: ids.append(v)
    placeholders = ",".join("?" for _ in ids)
    rows = get_db().execute(
        f"SELECT name, option_type FROM builder_options WHERE id IN ({placeholders})", ids
    ).fetchall() if ids else []
    grouped = {}
    for r in rows:
        grouped.setdefault(r["option_type"], []).append(r["name"])
    meta = " · ".join(f"{k.replace('_',' ').title()}: {', '.join(v)}" for k, v in grouped.items())
    cart["items"].append({
        "kind": "custom",
        "name": s["name"],
        "image": image_for_blend(grouped.get("fruit", [])),
        "meta": meta,
        "unit_price": s["price"],
        "quantity": 1,
        "custom_smoothie_id": s["id"],
    })
    session.modified = True
    flash(f"Added '{s['name']}' to cart.", "success")
    return redirect(url_for("cart"))


@app.route("/account/saved-smoothies/<int:sid>/delete", methods=["POST"])
@login_required
def account_saved_delete(sid):
    u = current_user()
    get_db().execute("DELETE FROM custom_smoothies WHERE id=? AND user_id=?", (sid, u["id"]))
    get_db().commit()
    flash("Saved smoothie removed.", "info")
    return redirect(url_for("account_saved"))


@app.route("/account/addresses", methods=["GET", "POST"])
@login_required
def account_addresses():
    u = current_user()
    if request.method == "POST":
        label = request.form.get("label", "Home").strip()
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        street = request.form.get("street", "").strip()
        city = request.form.get("city", "").strip()
        state = request.form.get("state", "").strip()
        country = request.form.get("country", "").strip()
        postal = request.form.get("postal_code", "").strip()
        if not (full_name and phone and street and city and country):
            flash("Please complete all required address fields.", "error")
            return redirect(url_for("account_addresses"))
        get_db().execute("""INSERT INTO addresses (user_id, label, full_name, phone, street, city, state, country, postal_code)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (u["id"], label, full_name, phone, street, city, state, country, postal))
        get_db().commit()
        flash("Address saved.", "success")
        return redirect(url_for("account_addresses"))
    addresses = get_db().execute(
        "SELECT * FROM addresses WHERE user_id=? ORDER BY is_default DESC, created_at DESC", (u["id"],)
    ).fetchall()
    return render_template("account/addresses.html", addresses=addresses)


@app.route("/account/addresses/<int:aid>/delete", methods=["POST"])
@login_required
def account_address_delete(aid):
    u = current_user()
    get_db().execute("DELETE FROM addresses WHERE id=? AND user_id=?", (aid, u["id"]))
    get_db().commit()
    flash("Address removed.", "info")
    return redirect(url_for("account_addresses"))


@app.route("/account/notifications")
@login_required
def account_notifications():
    u = current_user()
    notifs = get_db().execute(
        "SELECT * FROM notifications WHERE user_id=? AND audience='user' ORDER BY created_at DESC LIMIT 100",
        (u["id"],)
    ).fetchall()
    get_db().execute(
        "UPDATE notifications SET is_read=1 WHERE user_id=? AND audience='user' AND is_read=0", (u["id"],)
    )
    get_db().commit()
    return render_template("account/notifications.html", notifs=notifs)


# ─────────────────────────────────────────────────────────────────────────────
# FAVORITES / WISHLIST
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/account/favorites")
@login_required
def account_favorites():
    u = current_user()
    region = current_region() or u["region"] or "MU"
    avail = availability_field_for(region)
    favs = get_db().execute(f"""
        SELECT p.*, c.name AS category_name, f.created_at AS faved_at
        FROM favorites f
        JOIN products p ON p.id = f.product_id
        LEFT JOIN categories c ON c.id = p.category_id
        WHERE f.user_id=? AND p.is_active=1
        ORDER BY f.created_at DESC
    """, (u["id"],)).fetchall()
    return render_template("account/favorites.html", favorites=favs, region=region)


@app.route("/favorites/toggle/<int:pid>", methods=["POST"])
@login_required
def favorite_toggle(pid):
    u = current_user()
    db = get_db()
    p = db.execute("SELECT id FROM products WHERE id=? AND is_active=1", (pid,)).fetchone()
    if not p:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(ok=False, error="not_found"), 404
        flash("Product not found.", "error")
        return redirect(request.referrer or url_for("shop"))
    existing = db.execute("SELECT id FROM favorites WHERE user_id=? AND product_id=?",
                          (u["id"], pid)).fetchone()
    if existing:
        db.execute("DELETE FROM favorites WHERE id=?", (existing["id"],))
        action = "removed"
    else:
        db.execute("INSERT INTO favorites (user_id, product_id) VALUES (?,?)", (u["id"], pid))
        action = "added"
    db.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(ok=True, action=action)
    flash(f"{'Added to' if action == 'added' else 'Removed from'} your favorites.", "success")
    return redirect(request.referrer or url_for("shop"))


@app.context_processor
def inject_favorites():
    """Expose a set of favourited product ids to every template for logged-in users."""
    u = current_user()
    if not u:
        return {"user_favorite_ids": set()}
    rows = get_db().execute("SELECT product_id FROM favorites WHERE user_id=?", (u["id"],)).fetchall()
    return {"user_favorite_ids": {r["product_id"] for r in rows}}


# ─────────────────────────────────────────────────────────────────────────────
# PRODUCT REVIEWS
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/product/<slug>/review", methods=["POST"])
def submit_review(slug):
    db = get_db()
    p = db.execute("SELECT * FROM products WHERE slug=?", (slug,)).fetchone()
    if not p: abort(404)
    u = current_user()
    rating = int(request.form.get("rating") or 0)
    title = request.form.get("title", "").strip()[:140]
    body = request.form.get("body", "").strip()[:2000]
    author_name = (u["full_name"] if u else request.form.get("author_name", "").strip())[:80]
    if rating < 1 or rating > 5:
        flash("Please choose a star rating.", "error")
        return redirect(url_for("product_detail", slug=slug))
    if not body or not author_name:
        flash("Name and review body are required.", "error")
        return redirect(url_for("product_detail", slug=slug))
    # If the user has bought this product before, mark as verified
    verified = 0
    if u:
        v = db.execute("""SELECT 1 FROM order_items oi
                          JOIN orders o ON o.id=oi.order_id
                          WHERE o.user_id=? AND oi.product_id=? AND o.payment_status='paid' LIMIT 1""",
                       (u["id"], p["id"])).fetchone()
        verified = 1 if v else 0
    db.execute("""INSERT INTO reviews (product_id, user_id, author_name, rating, title, body, is_verified_buyer)
                  VALUES (?,?,?,?,?,?,?)""",
               (p["id"], u["id"] if u else None, author_name, rating, title, body, verified))
    db.commit()
    flash("Thanks for the review — it's live on the page.", "success")
    return redirect(url_for("product_detail", slug=slug) + "#reviews")



# ─────────────────────────────────────────────────────────────────────────────
# ADMIN DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    today = datetime.now().date().isoformat()
    month_start = datetime.now().replace(day=1).date().isoformat()

    # ─── FIX: split revenue per-currency so we never sum NGN + MUR + USD as one number.
    revenue_today_rows = db.execute("""SELECT region, currency, COALESCE(SUM(total),0) v
        FROM orders WHERE payment_status='paid' AND date(created_at)=?
        GROUP BY region""", (today,)).fetchall()
    revenue_month_rows = db.execute("""SELECT region, currency, COALESCE(SUM(total),0) v
        FROM orders WHERE payment_status='paid' AND date(created_at)>=?
        GROUP BY region""", (month_start,)).fetchall()
    revenue_today = {r["region"]: r["v"] for r in revenue_today_rows}
    revenue_month = {r["region"]: r["v"] for r in revenue_month_rows}

    stats = {
        "revenue_today": revenue_today,        # {region -> amount in that region's currency}
        "revenue_month": revenue_month,
        "orders_today":  db.execute("SELECT COUNT(*) v FROM orders WHERE date(created_at)=?", (today,)).fetchone()["v"],
        "orders_month":  db.execute("SELECT COUNT(*) v FROM orders WHERE date(created_at)>=?", (month_start,)).fetchone()["v"],
        "customers":     db.execute("SELECT COUNT(*) v FROM users WHERE role='customer' AND status='active'").fetchone()["v"],
        "products":      db.execute("SELECT COUNT(*) v FROM products WHERE is_active=1").fetchone()["v"],
    }
    recent_orders = db.execute("SELECT * FROM orders WHERE COALESCE(is_subscription,0)=0 ORDER BY created_at DESC LIMIT 10").fetchall()
    recent_users = db.execute("SELECT * FROM users WHERE role='customer' ORDER BY created_at DESC LIMIT 5").fetchall()
    pending_orders = db.execute("SELECT COUNT(*) AS v FROM orders WHERE order_status='pending' AND COALESCE(is_subscription,0)=0").fetchone()["v"]
    notifs = db.execute("SELECT * FROM notifications WHERE audience='admin' ORDER BY created_at DESC LIMIT 8").fetchall()
    # last 7 days revenue by region for chart
    last7_ng = db.execute("""SELECT date(created_at) AS d, COALESCE(SUM(total),0) AS v
        FROM orders WHERE payment_status='paid' AND region='NG' AND date(created_at)>=date('now','-6 days')
        GROUP BY date(created_at) ORDER BY d""").fetchall()
    last7_mu = db.execute("""SELECT date(created_at) AS d, COALESCE(SUM(total),0) AS v
        FROM orders WHERE payment_status='paid' AND region='MU' AND date(created_at)>=date('now','-6 days')
        GROUP BY date(created_at) ORDER BY d""").fetchall()
    last7_gl = db.execute("""SELECT date(created_at) AS d, COALESCE(SUM(total),0) AS v
        FROM orders WHERE payment_status='paid' AND region='GL' AND date(created_at)>=date('now','-6 days')
        GROUP BY date(created_at) ORDER BY d""").fetchall()
    return render_template("admin/dashboard.html", stats=stats,
                           recent_orders=recent_orders, recent_users=recent_users,
                           pending_orders=pending_orders, notifs=notifs,
                           last7_ng=last7_ng, last7_mu=last7_mu, last7_gl=last7_gl)


# Products
@app.route("/admin/products")
@admin_required
def admin_products():
    q = request.args.get("q", "").strip()
    cat = request.args.get("category", "").strip()
    sql = """SELECT p.*, c.name AS category_name FROM products p
             LEFT JOIN categories c ON c.id=p.category_id WHERE 1=1"""
    params = []
    if q:
        sql += " AND (p.name LIKE ? OR p.slug LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    if cat:
        sql += " AND c.slug=?"
        params.append(cat)
    sql += " ORDER BY p.is_active DESC, p.id DESC"
    products = get_db().execute(sql, params).fetchall()
    categories = get_db().execute("SELECT * FROM categories ORDER BY sort_order").fetchall()
    return render_template("admin/products.html", products=products, categories=categories, q=q, cat=cat)


@app.route("/admin/products/new", methods=["GET", "POST"])
@admin_required
def admin_product_new():
    categories = get_db().execute("SELECT * FROM categories ORDER BY sort_order").fetchall()
    if request.method == "POST":
        return admin_product_save(None, categories)
    return render_template("admin/product_form.html", p=None, categories=categories)


@app.route("/admin/products/<int:pid>/edit", methods=["GET", "POST"])
@admin_required
def admin_product_edit(pid):
    p = get_db().execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p: abort(404)
    categories = get_db().execute("SELECT * FROM categories ORDER BY sort_order").fetchall()
    if request.method == "POST":
        return admin_product_save(p, categories)
    return render_template("admin/product_form.html", p=p, categories=categories)


def admin_product_save(p, categories):
    f = request.form
    name = f.get("name", "").strip()
    if not name:
        flash("Name is required.", "error")
        return render_template("admin/product_form.html", p=p, categories=categories)
    slug = f.get("slug", "").strip().lower() or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    image_url = f.get("image_url", "").strip()
    file = request.files.get("image_file")
    if file and file.filename:
        u = save_upload(file)
        if u: image_url = u
    fields = dict(
        slug=slug, name=name,
        short_description=f.get("short_description", "").strip(),
        description=f.get("description", "").strip(),
        ingredients=f.get("ingredients", "").strip(),
        health_benefits=f.get("health_benefits", "").strip(),
        category_id=int(f.get("category_id") or 0) or None,
        image_url=image_url,
        price_ngn=float(f.get("price_ngn") or 0),
        price_mur=float(f.get("price_mur") or 0),
        price_usd=float(f.get("price_usd") or 0),
        stock=int(f.get("stock") or 0),
        is_available_ng=1 if f.get("is_available_ng") else 0,
        is_available_mu=1 if f.get("is_available_mu") else 0,
        is_available_global=1 if f.get("is_available_global") else 0,
        is_featured=1 if f.get("is_featured") else 0,
        is_bestseller=1 if f.get("is_bestseller") else 0,
        is_new=1 if f.get("is_new") else 0,
        tags=f.get("tags", "").strip(),
        is_active=1 if f.get("is_active") else 0,
    )
    db = get_db()
    if p is None:
        cols = ",".join(fields.keys()); ph = ",".join("?" for _ in fields)
        try:
            db.execute(f"INSERT INTO products ({cols}) VALUES ({ph})", tuple(fields.values()))
        except sqlite3.IntegrityError:
            flash("Slug already exists. Try a different name.", "error")
            return render_template("admin/product_form.html", p=p, categories=categories)
        db.commit()
        audit("product.create", "product", db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"], {"name": name})
        flash("Product created.", "success")
    else:
        sets = ",".join(f"{k}=?" for k in fields.keys())
        db.execute(f"UPDATE products SET {sets} WHERE id=?", tuple(fields.values()) + (p["id"],))
        db.commit()
        audit("product.update", "product", p["id"], {"name": name})
        flash("Product updated.", "success")
    return redirect(url_for("admin_products"))


@app.route("/admin/products/<int:pid>/toggle", methods=["POST"])
@admin_required
def admin_product_toggle(pid):
    """Enable / disable a product. Disabled products are hidden from the
    storefront but kept in the DB so historical orders still resolve."""
    db = get_db()
    p = db.execute("SELECT is_active, name FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        abort(404)
    new_state = 0 if p["is_active"] else 1
    db.execute("UPDATE products SET is_active=? WHERE id=?", (new_state, pid))
    db.commit()
    audit("product.toggle", "product", pid, {"is_active": new_state})
    flash(f"{p['name']} {'enabled' if new_state else 'disabled'}.", "info")
    return redirect(url_for("admin_products"))


@app.route("/admin/products/<int:pid>/delete", methods=["POST"])
@admin_required
def admin_product_delete(pid):
    """Hard-delete a product if it has no order history; otherwise soft-delete
    (is_active=0) so existing orders keep resolving cleanly."""
    db = get_db()
    p = db.execute("SELECT name FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        abort(404)
    in_use = db.execute(
        "SELECT 1 FROM order_items WHERE product_id=? LIMIT 1", (pid,)
    ).fetchone()
    if in_use:
        db.execute("UPDATE products SET is_active=0 WHERE id=?", (pid,))
        db.commit()
        audit("product.soft_delete", "product", pid)
        flash(f"{p['name']} is in past orders — disabled instead of deleted.", "info")
    else:
        db.execute("DELETE FROM reviews WHERE product_id=?", (pid,))
        db.execute("DELETE FROM products WHERE id=?", (pid,))
        db.commit()
        audit("product.delete", "product", pid)
        flash(f"{p['name']} deleted.", "info")
    return redirect(url_for("admin_products"))


# Categories management (full CRUD)
@app.route("/admin/categories", methods=["GET", "POST"])
@admin_required
def admin_categories():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            name = request.form.get("name", "").strip()
            if name:
                slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
                # ensure unique slug
                if db.execute("SELECT 1 FROM categories WHERE slug=?", (slug,)).fetchone():
                    slug = f"{slug}-{secrets.token_hex(2)}"
                db.execute("""INSERT INTO categories (slug, name, description, sort_order)
                    VALUES (?,?,?,COALESCE((SELECT MAX(sort_order)+1 FROM categories),0))""",
                    (slug, name, request.form.get("description", "").strip()))
                db.commit()
                audit("category.create", "categories", None, {"name": name})
                flash("Category added.", "success")
        elif action == "update":
            cid = int(request.form["cat_id"])
            name = request.form.get("name", "").strip()
            if name:
                db.execute("""UPDATE categories SET name=?, description=?, sort_order=?
                    WHERE id=?""", (name, request.form.get("description", "").strip(),
                    int(request.form.get("sort_order", 0) or 0), cid))
                db.commit()
                audit("category.update", "categories", cid, {"name": name})
                flash("Category updated.", "success")
        elif action == "toggle":
            cid = int(request.form["cat_id"])
            db.execute("UPDATE categories SET is_active = 1 - is_active WHERE id=?", (cid,))
            db.commit()
            audit("category.toggle", "categories", cid)
            flash("Category visibility updated.", "info")
        elif action == "delete":
            cid = int(request.form["cat_id"])
            db.execute("UPDATE categories SET is_deleted=1, is_active=0 WHERE id=?", (cid,))
            db.commit()
            audit("category.soft_delete", "categories", cid)
            flash("Category removed.", "info")
        return redirect(url_for("admin_categories"))
    cats = db.execute("""SELECT c.*, (SELECT COUNT(*) FROM products p
                WHERE p.category_id=c.id AND p.is_active=1) AS product_count
            FROM categories c WHERE COALESCE(c.is_deleted,0)=0
            ORDER BY c.sort_order, c.id""").fetchall()
    return render_template("admin/categories.html", categories=cats)


# Orders
@app.route("/admin/orders")
@admin_required
def admin_orders():
    status = request.args.get("status", "").strip()
    region = request.args.get("region", "").strip()
    q = request.args.get("q", "").strip()
    # Only paid orders are tracked here — pending/failed ones are noise
    # to fulfilment staff. They remain reachable via direct order URL if needed.
    sql = "SELECT * FROM orders WHERE COALESCE(is_subscription,0)=0 AND payment_status='paid'"
    params = []
    if status: sql += " AND order_status=?"; params.append(status)
    if region: sql += " AND region=?"; params.append(region)
    if q: sql += " AND (order_number LIKE ? OR full_name LIKE ? OR email LIKE ?)"; params += [f"%{q}%"]*3
    sql += " ORDER BY created_at DESC"
    orders = get_db().execute(sql, params).fetchall()
    return render_template("admin/orders.html", orders=orders, status=status, region=region, q=q)


@app.route("/admin/orders/<int:order_id>", methods=["GET", "POST"])
@admin_required
def admin_order_detail(order_id):
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order: abort(404)
    if request.method == "POST":
        new_status = request.form.get("order_status")
        new_payment = request.form.get("payment_status")
        admin_email = (current_user() or {})["email"] if current_user() else "admin"
        status_changed = (new_status in ("pending", "processing", "ready",
            "out_for_delivery", "delivered", "cancelled")) and (new_status != order["order_status"])
        payment_changed = (new_payment in ("pending", "paid", "failed", "refunded")) and (
            new_payment != order["payment_status"])
        if status_changed:
            db.execute("UPDATE orders SET order_status=?, updated_at=datetime('now') WHERE id=?",
                       (new_status, order_id))
            record_order_event(order_id, new_status,
                               note=request.form.get("status_note", "").strip() or None,
                               actor=f"admin:{admin_email}")
            if order["user_id"]:
                notify(order["user_id"], f"Order {order['order_number']} — {new_status}",
                       f"Your order status changed to '{new_status}'.",
                       url_for("account_order_detail", order_id=order_id))
        if payment_changed:
            db.execute("UPDATE orders SET payment_status=? WHERE id=?", (new_payment, order_id))
            if new_payment == "paid":
                record_order_event(order_id, "paid", note="Marked paid by admin",
                                   actor=f"admin:{admin_email}")
                activate_subscription_for_order(order_id)
        db.commit()
        audit("order.update", "order", order_id,
              {"status": new_status, "payment_status": new_payment})
        flash("Order updated.", "success")
        return redirect(url_for("admin_order_detail", order_id=order_id))
    items = db.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchall()
    payments = db.execute("SELECT * FROM payments WHERE order_id=? ORDER BY created_at DESC", (order_id,)).fetchall()
    timeline, last_status, cancelled = order_timeline(order_id)
    refunds = db.execute("""SELECT * FROM refund_requests WHERE order_id=?
        ORDER BY created_at DESC""", (order_id,)).fetchall()
    return render_template("admin/order_detail.html", order=order, items=items,
                           payments=payments, timeline=timeline, refunds=refunds)


# Users
@app.route("/admin/users")
@admin_required
def admin_users():
    q = request.args.get("q", "").strip()
    region = request.args.get("region", "").strip()
    status = request.args.get("status", "").strip()
    role = request.args.get("role", "").strip()
    sql = "SELECT * FROM users WHERE 1=1"; params = []
    if role in ("customer", "admin"):
        sql += " AND role=?"; params.append(role)
    if q: sql += " AND (full_name LIKE ? OR email LIKE ?)"; params += [f"%{q}%"]*2
    if region: sql += " AND region=?"; params.append(region)
    if status: sql += " AND status=?"; params.append(status)
    sql += " ORDER BY created_at DESC"
    users = get_db().execute(sql, params).fetchall()
    return render_template("admin/users.html", users=users, q=q, region=region, status=status, role=role)


@app.route("/admin/users/<int:uid>")
@admin_required
def admin_user_detail(uid):
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u: abort(404)
    orders = db.execute("SELECT * FROM orders WHERE user_id=? AND COALESCE(is_subscription,0)=0 ORDER BY created_at DESC", (uid,)).fetchall()
    subs = db.execute("""SELECT s.*, sp.name AS plan_name FROM subscriptions s
        JOIN subscription_plans sp ON sp.id=s.plan_id
        WHERE s.user_id=? ORDER BY s.id DESC""", (uid,)).fetchall()
    saved = db.execute("SELECT * FROM custom_smoothies WHERE user_id=? ORDER BY created_at DESC", (uid,)).fetchall()
    addresses = db.execute("SELECT * FROM addresses WHERE user_id=?", (uid,)).fetchall()
    return render_template("admin/user_detail.html", u=u, orders=orders, subs=subs, saved=saved, addresses=addresses)


@app.route("/admin/users/<int:uid>/status", methods=["POST"])
@admin_required
def admin_user_status(uid):
    action = request.form.get("action")
    if action == "suspend":
        get_db().execute("UPDATE users SET status='suspended' WHERE id=?", (uid,))
    elif action == "activate":
        get_db().execute("UPDATE users SET status='active' WHERE id=?", (uid,))
    elif action == "delete":
        get_db().execute("UPDATE users SET status='deleted' WHERE id=?", (uid,))
    elif action == "make_admin":
        get_db().execute("UPDATE users SET role='admin' WHERE id=?", (uid,))
    elif action == "make_customer":
        get_db().execute("UPDATE users SET role='customer' WHERE id=?", (uid,))
    elif action == "reset_password":
        # Generate a temporary password and email-style notify the user.
        temp = secrets.token_urlsafe(9)
        get_db().execute("UPDATE users SET password_hash=? WHERE id=?",
                         (generate_password_hash(temp), uid))
        flash(f"Temporary password set: {temp} — share it with the user securely.", "info")
    get_db().commit()
    audit(f"user.{action}", "user", uid)
    if action != "reset_password":
        flash("User updated.", "success")
    return redirect(url_for("admin_user_detail", uid=uid))


@app.route("/admin/users/new", methods=["GET", "POST"])
@admin_required
def admin_user_new():
    """Admin can create users directly — e.g. for B2B clients or staff."""
    if request.method == "POST":
        f = request.form
        full_name = f.get("full_name", "").strip()
        email = f.get("email", "").strip().lower()
        phone = f.get("phone", "").strip()
        role = f.get("role", "customer")
        password = f.get("password", "") or secrets.token_urlsafe(10)
        errors = []
        if not full_name: errors.append("Full name is required.")
        if not valid_email(email): errors.append("Enter a valid email.")
        if get_db().execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            errors.append("This email is already registered.")
        if role not in ("customer", "admin"): errors.append("Choose a valid role.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/user_form.html", form=f)
        region = region_from_phone(phone) or "GL"
        get_db().execute("""INSERT INTO users (email, password_hash, full_name,
            phone, role, region, status) VALUES (?,?,?,?,?,?,'active')""",
            (email, generate_password_hash(password), full_name, phone, role, region))
        uid = get_db().execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        get_db().commit()
        audit("user.create_by_admin", "user", uid, {"role": role, "region": region})
        flash(f"User created. Temporary password: {password}", "success")
        return redirect(url_for("admin_user_detail", uid=uid))
    return render_template("admin/user_form.html", form={})


@app.route("/admin/users/<int:uid>/delete", methods=["POST"])
@admin_required
def admin_user_delete(uid):
    """Hard-delete a user if they have no order history; else soft-delete
    (status='deleted'). Admins cannot delete themselves."""
    me = current_user()
    if me and me["id"] == uid:
        flash("You can't delete your own account from here.", "error")
        return redirect(url_for("admin_user_detail", uid=uid))
    db = get_db()
    has_orders = db.execute("SELECT 1 FROM orders WHERE user_id=? LIMIT 1", (uid,)).fetchone()
    if has_orders:
        db.execute("UPDATE users SET status='deleted' WHERE id=?", (uid,))
        flash("User has order history — soft-deleted instead.", "info")
    else:
        db.execute("DELETE FROM users WHERE id=?", (uid,))
        flash("User permanently deleted.", "info")
    db.commit()
    audit("user.delete", "user", uid)
    return redirect(url_for("admin_users"))


@app.route("/admin/users/export.csv")
@admin_required
def admin_users_export():
    users = get_db().execute("SELECT id,email,full_name,phone,role,status,region,created_at FROM users ORDER BY created_at DESC").fetchall()
    return _csv_response(
        "kcblendz-users.csv",
        ["id", "email", "full_name", "phone", "role", "status", "region", "created_at"],
        [[u["id"], u["email"], u["full_name"] or "", u["phone"] or "",
          u["role"], u["status"], u["region"] or "", u["created_at"]] for u in users],
    )


@app.route("/admin/products/export.csv")
@admin_required
def admin_products_export():
    rows = get_db().execute("""SELECT p.id, p.name, p.slug, c.name AS category,
        p.price_ngn, p.price_mur, p.price_usd, p.stock,
        p.is_active, p.is_featured, p.is_bestseller, p.is_new,
        p.is_available_ng, p.is_available_mu, p.is_available_global, p.created_at
        FROM products p LEFT JOIN categories c ON c.id=p.category_id
        ORDER BY p.id""").fetchall()
    return _csv_response(
        "kcblendz-products.csv",
        ["id", "name", "slug", "category",
         "price_ngn", "price_mur", "price_usd", "stock",
         "is_active", "is_featured", "is_bestseller", "is_new",
         "available_ng", "available_mu", "available_global", "created_at"],
        [[r["id"], r["name"], r["slug"], r["category"] or "",
          f"{r['price_ngn']:.2f}", f"{r['price_mur']:.2f}", f"{r['price_usd']:.2f}",
          r["stock"], r["is_active"], r["is_featured"], r["is_bestseller"], r["is_new"],
          r["is_available_ng"], r["is_available_mu"], r["is_available_global"],
          r["created_at"]] for r in rows],
    )


@app.route("/admin/subscriptions/export.csv")
@admin_required
def admin_subscriptions_export():
    rows = get_db().execute("""SELECT s.id, s.user_id, u.full_name, u.email,
        sp.name AS plan, s.billing_cycle, s.region, s.currency, s.price,
        s.status, s.started_at, s.cancelled_at, s.order_id
        FROM subscriptions s
        JOIN users u ON u.id=s.user_id
        JOIN subscription_plans sp ON sp.id=s.plan_id
        ORDER BY s.id DESC""").fetchall()
    return _csv_response(
        "kcblendz-subscriptions.csv",
        ["id", "user_id", "customer", "email", "plan", "cycle",
         "region", "currency", "price", "status", "started_at",
         "cancelled_at", "order_id"],
        [[r["id"], r["user_id"], r["full_name"], r["email"], r["plan"],
          r["billing_cycle"], r["region"], r["currency"], f"{r['price']:.2f}",
          r["status"], r["started_at"], r["cancelled_at"] or "",
          r["order_id"] or ""] for r in rows],
    )


# ─── Admin: subscriptions list ──────────────────────────────────────────────
@app.route("/admin/subscriptions")
@admin_required
def admin_subscriptions():
    status = request.args.get("status", "").strip()
    region = request.args.get("region", "").strip()
    sql = """SELECT s.*, u.full_name, u.email, sp.name AS plan_name
             FROM subscriptions s
             JOIN users u ON u.id=s.user_id
             JOIN subscription_plans sp ON sp.id=s.plan_id
             WHERE 1=1"""
    params = []
    if status:
        sql += " AND s.status=?"; params.append(status)
    if region in ("NG", "MU", "GL"):
        sql += " AND s.region=?"; params.append(region)
    sql += " ORDER BY s.id DESC"
    subs = get_db().execute(sql, params).fetchall()
    stats = {
        "active": get_db().execute("SELECT COUNT(*) n FROM subscriptions WHERE status='active'").fetchone()["n"],
        "pending": get_db().execute("SELECT COUNT(*) n FROM subscriptions WHERE status='pending_payment'").fetchone()["n"],
        "cancelled": get_db().execute("SELECT COUNT(*) n FROM subscriptions WHERE status='cancelled'").fetchone()["n"],
    }
    return render_template("admin/subscriptions.html", subs=subs, stats=stats,
                           status=status, region=region)


@app.route("/admin/subscriptions/<int:sid>/cancel", methods=["POST"])
@admin_required
def admin_subscription_cancel(sid):
    db = get_db()
    s = db.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
    if not s:
        abort(404)
    db.execute("""UPDATE subscriptions SET status='cancelled',
        cancelled_at=datetime('now') WHERE id=?""", (sid,))
    db.commit()
    audit("subscription.cancel", "subscriptions", sid)
    notify(s["user_id"], "Subscription cancelled",
           "An administrator cancelled your active subscription. Reach out if this was unexpected.",
           url_for("account_dashboard"))
    flash("Subscription cancelled.", "info")
    return redirect(url_for("admin_subscriptions"))


# Blogs
@app.route("/admin/blogs")
@admin_required
def admin_blogs():
    posts = get_db().execute("SELECT * FROM blog_posts ORDER BY created_at DESC").fetchall()
    return render_template("admin/blogs.html", posts=posts)


@app.route("/admin/blogs/new", methods=["GET", "POST"])
@admin_required
def admin_blog_new():
    if request.method == "POST":
        return admin_blog_save(None)
    return render_template("admin/blog_form.html", p=None)


@app.route("/admin/blogs/<int:pid>/edit", methods=["GET", "POST"])
@admin_required
def admin_blog_edit(pid):
    p = get_db().execute("SELECT * FROM blog_posts WHERE id=?", (pid,)).fetchone()
    if not p: abort(404)
    if request.method == "POST":
        return admin_blog_save(p)
    return render_template("admin/blog_form.html", p=p)


def admin_blog_save(p):
    f = request.form
    title = f.get("title", "").strip()
    if not title:
        flash("Title is required.", "error")
        return render_template("admin/blog_form.html", p=p)
    slug = f.get("slug", "").strip().lower() or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    cover = f.get("cover_url", "").strip()
    cover_file = request.files.get("cover_file")
    if cover_file and cover_file.filename:
        u = save_upload(cover_file)
        if u: cover = u
    fields = dict(
        slug=slug, title=title,
        subtitle=f.get("subtitle", "").strip(),
        cover_url=cover,
        category=f.get("category", "WELLNESS").strip().upper(),
        author=f.get("author", "KC Team").strip(),
        content=f.get("content", "").strip(),
        read_minutes=int(f.get("read_minutes") or 4),
        is_published=1 if f.get("is_published") else 0,
    )
    db = get_db()
    if p is None:
        cols = ",".join(fields.keys()); ph = ",".join("?" for _ in fields)
        try:
            db.execute(f"INSERT INTO blog_posts ({cols}) VALUES ({ph})", tuple(fields.values()))
        except sqlite3.IntegrityError:
            flash("Slug must be unique.", "error")
            return render_template("admin/blog_form.html", p=p)
        flash("Blog post created.", "success")
    else:
        fields["updated_at"] = datetime.now().isoformat(timespec="seconds")
        sets = ",".join(f"{k}=?" for k in fields.keys())
        db.execute(f"UPDATE blog_posts SET {sets} WHERE id=?", tuple(fields.values()) + (p["id"],))
        flash("Blog post updated.", "success")
    db.commit()
    return redirect(url_for("admin_blogs"))


@app.route("/admin/blogs/<int:pid>/delete", methods=["POST"])
@admin_required
def admin_blog_delete(pid):
    get_db().execute("DELETE FROM blog_posts WHERE id=?", (pid,))
    get_db().commit()
    flash("Blog post deleted.", "info")
    return redirect(url_for("admin_blogs"))


# Builder configuration
@app.route("/admin/builder", methods=["GET", "POST"])
@admin_required
def admin_builder():
    if request.method == "POST":
        # add new option
        opt_type = request.form.get("option_type")
        name = request.form.get("name", "").strip()
        if opt_type in ("cup_size", "fruit", "base", "sweetener", "addon", "booster") and name:
            get_db().execute("""INSERT INTO builder_options (option_type, name, price_ngn, price_mur, price_usd)
                VALUES (?,?,?,?,?)""", (opt_type, name,
                                        float(request.form.get("price_ngn") or 0),
                                        float(request.form.get("price_mur") or 0),
                                        float(request.form.get("price_usd") or 0)))
            get_db().commit()
            flash("Builder option added.", "success")
        return redirect(url_for("admin_builder"))
    opts = get_db().execute("SELECT * FROM builder_options ORDER BY option_type, sort_order").fetchall()
    grouped = {}
    for o in opts:
        grouped.setdefault(o["option_type"], []).append(o)
    return render_template("admin/builder_config.html", grouped=grouped)


@app.route("/admin/builder/<int:oid>/delete", methods=["POST"])
@admin_required
def admin_builder_delete(oid):
    get_db().execute("DELETE FROM builder_options WHERE id=?", (oid,))
    get_db().commit()
    flash("Option removed.", "info")
    return redirect(url_for("admin_builder"))


# Reports
@app.route("/admin/reports")
@admin_required
def admin_reports():
    db = get_db()
    daily = db.execute("""SELECT date(created_at) AS d, COUNT(*) AS n, COALESCE(SUM(total),0) AS v, region
        FROM orders WHERE payment_status='paid' AND date(created_at)>=date('now','-30 days')
        GROUP BY date(created_at), region ORDER BY d DESC""").fetchall()
    monthly = db.execute("""SELECT strftime('%Y-%m', created_at) AS m, COUNT(*) AS n, COALESCE(SUM(total),0) AS v, region
        FROM orders WHERE payment_status='paid' GROUP BY m, region ORDER BY m DESC LIMIT 12""").fetchall()
    top_products = db.execute("""SELECT oi.item_name, SUM(oi.quantity) AS qty, SUM(oi.line_total) AS revenue
        FROM order_items oi JOIN orders o ON o.id=oi.order_id
        WHERE o.payment_status='paid'
        GROUP BY oi.item_name ORDER BY qty DESC LIMIT 10""").fetchall()
    growth = db.execute("""SELECT date(created_at) AS d, COUNT(*) AS n
        FROM users WHERE role='customer' AND date(created_at)>=date('now','-30 days')
        GROUP BY date(created_at) ORDER BY d""").fetchall()
    region_compare = db.execute("""SELECT region, COUNT(*) AS n, COALESCE(SUM(total),0) AS v
        FROM orders WHERE payment_status='paid' GROUP BY region""").fetchall()
    return render_template("admin/reports.html",
                           daily=daily, monthly=monthly, top_products=top_products,
                           growth=growth, region_compare=region_compare)


# Admin notifications
@app.route("/admin/notifications")
@admin_required
def admin_notifications():
    u = current_user()
    notifs = get_db().execute(
        "SELECT * FROM notifications WHERE audience='admin' AND user_id=? ORDER BY created_at DESC LIMIT 200", (u["id"],)
    ).fetchall()
    get_db().execute(
        "UPDATE notifications SET is_read=1 WHERE audience='admin' AND user_id=? AND is_read=0", (u["id"],)
    )
    get_db().commit()
    return render_template("admin/notifications.html", notifs=notifs)


# Admin contact messages
@app.route("/admin/messages")
@admin_required
def admin_messages():
    msgs = get_db().execute("SELECT * FROM contact_messages ORDER BY created_at DESC").fetchall()
    return render_template("admin/messages.html", msgs=msgs)


# Admin profile / account settings
@app.route("/admin/profile", methods=["GET", "POST"])
@admin_required
def admin_profile():
    u = current_user()
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action", "details")
        if action == "details":
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            if not full_name or not valid_email(email):
                flash("A valid name and email are required.", "error")
                return redirect(url_for("admin_profile"))
            clash = db.execute(
                "SELECT 1 FROM users WHERE email=? AND id<>?", (email, u["id"])
            ).fetchone()
            if clash:
                flash("That email is already in use by another account.", "error")
                return redirect(url_for("admin_profile"))
            db.execute(
                "UPDATE users SET full_name=?, email=?, phone=? WHERE id=?",
                (full_name, email, phone, u["id"]),
            )
            db.commit()
            audit("admin.profile.update", "user", u["id"])
            flash("Profile details updated.", "success")
        elif action == "avatar":
            file = request.files.get("avatar")
            if file and file.filename:
                url = save_upload(file)
                if url:
                    db.execute("UPDATE users SET avatar_url=? WHERE id=?", (url, u["id"]))
                    db.commit()
                    audit("admin.profile.avatar", "user", u["id"])
                    flash("Profile picture updated.", "success")
                else:
                    flash("Only PNG, JPG, GIF or WebP up to 8MB.", "error")
            else:
                flash("Please choose an image to upload.", "error")
        elif action == "avatar_remove":
            db.execute("UPDATE users SET avatar_url=NULL WHERE id=?", (u["id"],))
            db.commit()
            audit("admin.profile.avatar_remove", "user", u["id"])
            flash("Profile picture removed.", "info")
        elif action == "password":
            cur = request.form.get("current_password", "")
            new = request.form.get("new_password", "")
            conf = request.form.get("confirm_password", "")
            if not check_password_hash(u["password_hash"], cur):
                flash("Current password is incorrect.", "error")
            elif len(new) < 8:
                flash("New password must be at least 8 characters.", "error")
            elif new != conf:
                flash("New passwords do not match.", "error")
            else:
                db.execute(
                    "UPDATE users SET password_hash=? WHERE id=?",
                    (generate_password_hash(new), u["id"]),
                )
                db.commit()
                audit("admin.profile.password", "user", u["id"])
                flash("Password changed successfully.", "success")
        elif action == "mfa_setup_start":
            # Generate a fresh secret + stash it in the session until the user
            # proves they've added it to their authenticator by entering a code.
            secret = generate_mfa_secret()
            session["mfa_setup_secret"] = secret
            session.modified = True
            return redirect(url_for("admin_profile") + "#mfa")
        elif action == "mfa_setup_confirm":
            secret = session.get("mfa_setup_secret")
            code = request.form.get("code", "").strip()
            if not secret:
                flash("Please start MFA setup again — your session expired.", "error")
            elif not verify_totp(secret, code):
                flash("That code didn't match. Try the latest 6 digits from your authenticator.", "error")
                return redirect(url_for("admin_profile") + "#mfa")
            else:
                db.execute("UPDATE users SET mfa_secret=?, mfa_enabled=1 WHERE id=?",
                           (secret, u["id"]))
                db.commit()
                session.pop("mfa_setup_secret", None)
                audit("admin.mfa.enable", "user", u["id"])
                flash("MFA is on. From your next sign-in we'll ask for a code after your password.", "success")
        elif action == "mfa_disable":
            cur = request.form.get("current_password", "")
            if not check_password_hash(u["password_hash"], cur):
                flash("Current password required to disable MFA.", "error")
            else:
                db.execute("UPDATE users SET mfa_enabled=0, mfa_secret=NULL WHERE id=?", (u["id"],))
                db.commit()
                audit("admin.mfa.disable", "user", u["id"])
                flash("MFA disabled. Your account is now protected by password only.", "info")
        return redirect(url_for("admin_profile"))
    stats = {
        "orders": db.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"],
        "products": db.execute("SELECT COUNT(*) c FROM products").fetchone()["c"],
        "users": db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
    }
    mfa_pending_secret = session.get("mfa_setup_secret")
    mfa_pending_uri = mfa_otpauth_uri(mfa_pending_secret, u["email"]) if mfa_pending_secret else None
    return render_template("admin/profile.html", u=u, stats=stats,
                           mfa_pending_secret=mfa_pending_secret,
                           mfa_pending_uri=mfa_pending_uri)


# ─────────────────────────────────────────────────────────────────────────────
# v2 ROUTES — currency switch, tracking, AJAX cart, receipt, refunds, subs, FAQ
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/region/<region>", methods=["POST"])
def api_set_region(region):
    """Switch region without leaving the current page. The frontend posts here
    via fetch() and reloads-in-place. Returns the new region + a flag the UI
    uses to decide whether to clear the cart (true when items exist and the
    region actually changed).
    """
    if region not in REGIONS:
        return jsonify({"ok": False, "error": "invalid region"}), 400
    cart = get_cart()
    items_existed = bool(cart.get("items"))
    region_changed = cart.get("region") != region
    session["region"] = region
    cart_clear_if_region_change()
    session.modified = True
    return jsonify({
        "ok": True,
        "region": region,
        "currency": currency_for_region(region),
        "symbol": REGIONS[region]["symbol"],
        "cart_cleared": items_existed and region_changed,
    })


@app.route("/api/cart/add", methods=["POST"])
@region_required
def api_cart_add():
    """JSON add-to-cart so the product cards can update without a page reload.
    Mirrors /cart/add but returns the new cart count + subtotal so the navbar
    and any sticky-cart UI can refresh.
    """
    region = current_region()
    price_col = price_field_for(region)
    avail = availability_field_for(region)
    try:
        pid = int(request.json.get("product_id"))
        qty = max(1, int(request.json.get("quantity", 1)))
    except (TypeError, ValueError, AttributeError):
        return jsonify({"ok": False, "error": "Bad request."}), 400
    p = get_db().execute(
        f"SELECT * FROM products WHERE id=? AND is_active=1 AND {avail}=1", (pid,)
    ).fetchone()
    if not p:
        return jsonify({"ok": False, "error": "Product not available in your store."}), 404
    cart = get_cart()
    cart["region"] = region
    for item in cart["items"]:
        if item.get("kind") == "product" and item.get("product_id") == p["id"]:
            item["quantity"] = int(item["quantity"]) + qty
            break
    else:
        cart["items"].append({
            "kind": "product", "product_id": p["id"], "name": p["name"],
            "image": p["image_url"], "meta": p["ingredients"] or "",
            "unit_price": p[price_col], "quantity": qty,
        })
    session.modified = True
    return jsonify({
        "ok": True,
        "name": p["name"],
        "cart_count": cart_count(),
        "subtotal": cart_subtotal(),
        "subtotal_formatted": format_money(cart_subtotal(), region),
    })


@app.route("/api/admin/unread")
@admin_required
def api_admin_unread():
    """Polled every ~10s by the admin top-bar so the unread badges stay live
    without a page refresh."""
    return jsonify(admin_unread_counts())


@app.route("/api/promo/preview", methods=["POST"])
@region_required
def api_promo_preview():
    """Lets the checkout page validate a promo code without submitting the form."""
    code = (request.json or {}).get("code", "")
    discount, p, err = resolve_promo_code(code, cart_subtotal(), current_region())
    if err:
        return jsonify({"ok": False, "error": err})
    return jsonify({
        "ok": True,
        "code": p["code"], "description": p["description"],
        "discount": discount,
        "discount_formatted": format_money(discount, current_region()),
        "new_total_formatted": format_money(
            max(0.0, cart_subtotal() - discount), current_region()),
    })


# ─── Order tracking — customer-facing timeline + guest tracking page ──────────
ORDER_STATUS_FLOW = [
    ("pending",          "Order placed",     "We received your order and are awaiting payment."),
    ("paid",             "Payment confirmed","Payment received — kitchen is being notified."),
    ("processing",       "In the kitchen",   "Your blend is being made fresh."),
    ("ready",            "Ready for collection / dispatch", "Order is packed."),
    ("out_for_delivery", "On its way",       "Your driver is on the road."),
    ("delivered",        "Delivered",        "Enjoy! Drink within 24h for peak freshness."),
]

# Subscriptions don't go through the kitchen / delivery pipeline — they
# have their own shorter, clearer flow.
SUBSCRIPTION_STATUS_FLOW = [
    ("pending",   "Plan selected",      "You chose a plan — we're awaiting payment."),
    ("paid",      "Payment confirmed",  "Payment received — your subscription is being activated."),
    ("delivered", "Subscription active","You're all set! Manage or pause any time from your account."),
]


def order_timeline(order_id):
    """Return the timeline as a list of (status, label, blurb, when, done, current)
    tuples — suitable for direct rendering. Subscriptions use a shorter,
    subscription-specific flow (no kitchen / delivery steps).
    """
    db = get_db()
    order = db.execute("SELECT is_subscription FROM orders WHERE id=?",
                       (order_id,)).fetchone()
    flow = SUBSCRIPTION_STATUS_FLOW if (order and order["is_subscription"]) else ORDER_STATUS_FLOW

    events = db.execute(
        "SELECT status, created_at FROM order_status_events WHERE order_id=? ORDER BY id",
        (order_id,)
    ).fetchall()
    by_status = {}
    for e in events:
        by_status.setdefault(e["status"], e["created_at"])

    flow_codes = [c for (c, _, _) in flow]

    if "cancelled" in by_status:
        last_status = "cancelled"
    else:
        reached = [c for c in flow_codes if c in by_status]
        last_status = reached[-1] if reached else flow_codes[0]

    timeline = []
    last_idx = flow_codes.index(last_status) if last_status in flow_codes else -1
    for idx, (code, label, blurb) in enumerate(flow):
        # A step is "done" if it's *before* the current step in the flow.
        # This handles admin jumps that skip intermediate states gracefully —
        # everything up to last_status is shown as completed instead of
        # leaving gaps in the visual timeline.
        when = by_status.get(code)
        current = (code == last_status)
        done = (idx < last_idx)
        timeline.append({
            "code": code, "label": label, "blurb": blurb,
            "when": when, "done": done, "current": current,
        })
    cancelled = (last_status == "cancelled")
    return timeline, last_status, cancelled


@app.route("/track", methods=["GET", "POST"])
def track_order():
    """Public order tracking — works for guests too. Lookup by order number +
    email or by tracking token. Token-only URLs let us include 'one-click'
    tracking links in order confirmation emails without exposing PII.
    """
    db = get_db()
    token = request.args.get("token", "").strip()
    order = None
    error = None

    if token:
        order = db.execute("SELECT * FROM orders WHERE tracking_token=?", (token,)).fetchone()
        if not order:
            error = "We couldn't find an order with that tracking link."

    if request.method == "POST":
        order_number = request.form.get("order_number", "").strip().upper()
        email = request.form.get("email", "").strip().lower()
        if not order_number or not valid_email(email):
            error = "Enter both your order number and the email used at checkout."
        else:
            order = db.execute(
                "SELECT * FROM orders WHERE order_number=? AND lower(email)=?",
                (order_number, email)
            ).fetchone()
            if not order:
                error = "No order matches that combination. Double-check the order number and email."

    timeline = items = []
    last_status = None
    cancelled = False
    if order:
        timeline, last_status, cancelled = order_timeline(order["id"])
        items = db.execute("SELECT * FROM order_items WHERE order_id=?", (order["id"],)).fetchall()

    return render_template("public/track.html",
                           order=order, items=items, timeline=timeline,
                           last_status=last_status, cancelled=cancelled, error=error)


@app.route("/order/<int:order_id>/receipt")
def order_receipt(order_id):
    """Printable receipt. Open in any view that has access (the customer, or
    via tracking token, or any admin)."""
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        abort(404)
    token = request.args.get("token", "")
    u = current_user()
    allowed = (
        (u and (u["role"] == "admin" or order["user_id"] == u["id"])) or
        (token and order["tracking_token"] and
         hmac.compare_digest(token, order["tracking_token"]))
    )
    if not allowed:
        abort(403)
    items = db.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchall()
    return render_template("public/receipt.html", order=order, items=items)


# ─── Refund requests ─────────────────────────────────────────────────────────
@app.route("/order/<int:order_id>/refund", methods=["GET", "POST"])
def request_refund(order_id):
    """Customer-facing refund request. Requires either ownership (logged-in)
    or the tracking token (guests). All requests land in the admin queue."""
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        abort(404)
    token = request.args.get("token", "") or request.form.get("token", "")
    u = current_user()
    is_owner = u and order["user_id"] == u["id"]
    via_token = token and order["tracking_token"] and \
                hmac.compare_digest(token, order["tracking_token"])
    if not (is_owner or via_token):
        abort(403)

    existing = db.execute("""SELECT * FROM refund_requests
        WHERE order_id=? ORDER BY id DESC LIMIT 1""", (order_id,)).fetchone()

    if request.method == "POST":
        reason = request.form.get("reason", "").strip()
        details = request.form.get("details", "").strip()
        if not reason:
            flash("Please choose a reason for the refund.", "error")
            return redirect(request.url)
        if existing and existing["status"] in ("pending", "approved"):
            flash("A refund request is already open for this order.", "info")
        else:
            db.execute("""INSERT INTO refund_requests (order_id, user_id, reason, details)
                VALUES (?,?,?,?)""", (order_id, u["id"] if u else None, reason, details))
            db.commit()
            notify_admins(f"Refund requested · {order['order_number']}",
                          reason, url_for("admin_order_detail", order_id=order_id))
            audit("refund.request", "order", order_id, {"reason": reason})
            flash("Your refund request has been submitted. We'll respond within 12 hours.", "success")
        return redirect(url_for("track_order", token=order["tracking_token"]) if via_token
                        else url_for("account_order_detail", order_id=order_id))

    return render_template("public/refund_request.html",
                           order=order, existing=existing, token=token)


# ─── Subscription plans ──────────────────────────────────────────────────────
@app.route("/subscribe")
def subscribe_index():
    region = current_region() or "MU"
    plans = get_db().execute("""SELECT * FROM subscription_plans
        WHERE is_active=1 ORDER BY sort_order""").fetchall()
    return render_template("public/subscribe.html", plans=plans, region=region)


@app.route("/subscribe/<slug>", methods=["GET", "POST"])
@login_required
def subscribe_plan(slug):
    db = get_db()
    plan = db.execute("SELECT * FROM subscription_plans WHERE slug=? AND is_active=1",
                      (slug,)).fetchone()
    if not plan:
        abort(404)
    region = current_region() or "MU"
    cycle = request.values.get("cycle", "monthly")
    if cycle not in ("monthly", "yearly"):
        cycle = "monthly"
    price_col = {"NG": "price_ngn", "MU": "price_mur", "GL": "price_usd"}[region]
    price = plan[f"{price_col}_{cycle}"]

    if request.method == "POST":
        u = current_user()
        payment_method = request.form.get("payment_method", "card")
        if payment_method not in ("card", "paypal", "bank_transfer"):
            flash("Choose a valid payment method.", "error")
            return redirect(url_for("subscribe_plan", slug=slug, cycle=cycle))

        currency = currency_for_region(region)
        # Create the subscription in a pending state — only activated once paid.
        db.execute("""INSERT INTO subscriptions (user_id, plan_id, billing_cycle, region,
            currency, price, status) VALUES (?,?,?,?,?,?, 'pending_payment')""",
            (u["id"], plan["id"], cycle, region, currency, price))
        sub_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        # Create a payable order so the customer pays through the SAME flow
        # (card / PayPal / bank transfer) before the subscription is admitted.
        tracking_token = generate_tracking_token()
        order_number = f"KCB-SUB-{datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
        db.execute("""INSERT INTO orders (order_number, user_id, guest_email, full_name,
            email, phone, region, currency, subtotal, delivery_fee, total,
            fulfillment_type, notes, payment_method, tracking_token,
            is_subscription, subscription_id)
            VALUES (?,?,?,?,?,?,?,?, ?,?,?, ?,?,?,?, ?,?)""", (
            order_number, u["id"], None, u["full_name"],
            u["email"], u["phone"] or "—", region, currency,
            price, 0.0, price, "subscription",
            f"{plan['name']} subscription · {cycle}", payment_method, tracking_token,
            1, sub_id,
        ))
        order_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        db.execute("UPDATE subscriptions SET order_id=? WHERE id=?", (order_id, sub_id))
        db.execute("""INSERT INTO order_items (order_id, item_name, item_meta,
            unit_price, quantity, line_total) VALUES (?,?,?,?,?,?)""", (
            order_id, f"{plan['name']} plan ({cycle})",
            plan["tagline"] or "", price, 1, price))
        db.commit()
        record_order_event(order_id, "pending",
                           note="Subscription order placed", actor="customer")
        audit("subscription.pending", "subscription_plans", plan["id"],
              {"cycle": cycle, "price": price, "order_id": order_id})
        return redirect(url_for("payment", order_id=order_id))

    return render_template("public/subscribe_confirm.html",
                           plan=plan, region=region, cycle=cycle, price=price)


# ─── Override the static FAQ page with DB-driven, categorised one ────────────
# (kept @app.route("/faq") defined earlier — we replace its handler below
# but Flask binds endpoints to view funcs, so we just redefine the function.)
@app.route("/faq", endpoint="faq_v2")
def faq_v2():
    """Categorised FAQ — Customers / Our Team / Insights."""
    rows = get_db().execute("""SELECT * FROM faqs WHERE is_published=1
        ORDER BY category, sort_order""").fetchall()
    by_cat = {"customers": [], "team": [], "insights": []}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    return render_template("public/faq.html", faq_groups=by_cat)


# Make Flask use the v2 view for /faq even though faq() was defined earlier.
# Same URL, new function: we point the existing endpoint's view_func at faq_v2.
app.view_functions["faq"] = faq_v2


# ─── Admin: delete category (with safe product re-assignment) ────────────────
@app.route("/admin/categories/<int:cid>/delete", methods=["POST"])
@admin_required
def admin_category_delete(cid):
    db = get_db()
    cat = db.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone()
    if not cat:
        abort(404)
    # Don't actually drop the row — soft-delete so old product links keep
    # working in historical orders. Detach products so they're not orphaned.
    db.execute("UPDATE products SET category_id=NULL WHERE category_id=?", (cid,))
    db.execute("UPDATE categories SET is_deleted=1, is_active=0 WHERE id=?", (cid,))
    db.commit()
    audit("category.delete", "category", cid, {"name": cat["name"]})
    flash(f"Category “{cat['name']}” deleted. Products kept and reassigned to Uncategorised.", "success")
    return redirect(url_for("admin_categories"))


# ─── Admin: promo codes ──────────────────────────────────────────────────────
@app.route("/admin/promo", methods=["GET", "POST"])
@admin_required
def admin_promo():
    db = get_db()
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        if not code:
            flash("Code is required.", "error"); return redirect(url_for("admin_promo"))
        try:
            db.execute("""INSERT INTO promo_codes (code, description, discount_type,
                discount_value, min_subtotal, region, ends_at, max_uses, is_active)
                VALUES (?,?,?,?,?,?,?,?,1)""", (
                code,
                request.form.get("description", "").strip(),
                request.form.get("discount_type", "percent"),
                float(request.form.get("discount_value") or 0),
                float(request.form.get("min_subtotal") or 0),
                request.form.get("region") or None,
                request.form.get("ends_at") or None,
                int(request.form["max_uses"]) if request.form.get("max_uses") else None,
            ))
            db.commit()
            audit("promo.create", "promo_codes", None, {"code": code})
            flash(f"Promo code {code} created.", "success")
        except sqlite3.IntegrityError:
            flash("That code already exists.", "error")
        return redirect(url_for("admin_promo"))
    promos = db.execute("SELECT * FROM promo_codes ORDER BY id DESC").fetchall()
    return render_template("admin/promo.html", promos=promos)


@app.route("/admin/promo/<int:pid>/toggle", methods=["POST"])
@admin_required
def admin_promo_toggle(pid):
    db = get_db()
    db.execute("UPDATE promo_codes SET is_active = 1 - is_active WHERE id=?", (pid,))
    db.commit()
    audit("promo.toggle", "promo_codes", pid)
    flash("Promo code updated.", "info")
    return redirect(url_for("admin_promo"))


@app.route("/admin/promo/<int:pid>/edit", methods=["POST"])
@admin_required
def admin_promo_edit(pid):
    db = get_db()
    if not db.execute("SELECT 1 FROM promo_codes WHERE id=?", (pid,)).fetchone():
        abort(404)
    f = request.form
    db.execute("""UPDATE promo_codes SET code=?, description=?, discount_type=?,
        discount_value=?, min_subtotal=?, region=?, ends_at=?, max_uses=?
        WHERE id=?""", (
        f.get("code", "").strip().upper(),
        f.get("description", "").strip(),
        f.get("discount_type", "percent"),
        float(f.get("discount_value") or 0),
        float(f.get("min_subtotal") or 0),
        f.get("region") or None,
        f.get("ends_at") or None,
        int(f["max_uses"]) if f.get("max_uses") else None,
        pid,
    ))
    db.commit()
    audit("promo.update", "promo_codes", pid)
    flash("Promo code updated.", "success")
    return redirect(url_for("admin_promo"))


@app.route("/admin/promo/<int:pid>/delete", methods=["POST"])
@admin_required
def admin_promo_delete(pid):
    db = get_db()
    db.execute("DELETE FROM promo_codes WHERE id=?", (pid,))
    db.commit()
    audit("promo.delete", "promo_codes", pid)
    flash("Promo code removed.", "info")
    return redirect(url_for("admin_promo"))


# ─── Admin: refund queue ─────────────────────────────────────────────────────
@app.route("/admin/refunds")
@admin_required
def admin_refunds():
    rows = get_db().execute("""SELECT r.*, o.order_number, o.total, o.region, o.currency,
        o.full_name AS customer_name
        FROM refund_requests r JOIN orders o ON o.id=r.order_id
        ORDER BY CASE r.status WHEN 'pending' THEN 0 ELSE 1 END, r.created_at DESC""").fetchall()
    return render_template("admin/refunds.html", refunds=rows)


@app.route("/admin/refunds/<int:rid>/resolve", methods=["POST"])
@admin_required
def admin_refund_resolve(rid):
    action = request.form.get("action")
    response = request.form.get("response", "").strip()
    if action not in ("approve", "reject", "mark_refunded"):
        abort(400)
    db = get_db()
    r = db.execute("SELECT * FROM refund_requests WHERE id=?", (rid,)).fetchone()
    if not r:
        abort(404)
    new_status = {"approve": "approved", "reject": "rejected",
                  "mark_refunded": "refunded"}[action]
    db.execute("""UPDATE refund_requests SET status=?, admin_response=?, resolved_at=datetime('now')
        WHERE id=?""", (new_status, response, rid))
    if new_status == "refunded":
        db.execute("UPDATE orders SET payment_status='refunded' WHERE id=?", (r["order_id"],))
        record_order_event(r["order_id"], "cancelled",
                           note="Refund issued", actor="admin")
    db.commit()
    # Notify the customer
    if r["user_id"]:
        order = db.execute("SELECT order_number FROM orders WHERE id=?", (r["order_id"],)).fetchone()
        notify(r["user_id"], f"Refund request {new_status} · {order['order_number']}",
               response, url_for("account_order_detail", order_id=r["order_id"]))
    audit(f"refund.{new_status}", "refund_requests", rid)
    flash(f"Refund {new_status}.", "success")
    return redirect(url_for("admin_refunds"))


# ─── Admin: FAQ management ───────────────────────────────────────────────────
@app.route("/admin/faqs", methods=["GET", "POST"])
@admin_required
def admin_faqs():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            cat = request.form.get("category", "customers")
            q = request.form.get("question", "").strip()
            a = request.form.get("answer", "").strip()
            if q and a:
                db.execute("""INSERT INTO faqs (category, question, answer, sort_order)
                    VALUES (?,?,?,COALESCE((SELECT MAX(sort_order)+1 FROM faqs),0))""",
                    (cat, q, a))
                db.commit()
                audit("faq.create", "faqs", None, {"q": q})
                flash("FAQ added.", "success")
        elif action == "update":
            fid = int(request.form["faq_id"])
            q = request.form.get("question", "").strip()
            a = request.form.get("answer", "").strip()
            if q and a:
                db.execute("""UPDATE faqs SET category=?, question=?, answer=?, sort_order=?
                    WHERE id=?""", (request.form.get("category", "customers"), q, a,
                    int(request.form.get("sort_order", 0) or 0), fid))
                db.commit()
                audit("faq.update", "faqs", fid)
                flash("FAQ updated.", "success")
        elif action == "toggle":
            fid = int(request.form["faq_id"])
            db.execute("UPDATE faqs SET is_published = 1 - is_published WHERE id=?", (fid,))
            db.commit()
            audit("faq.toggle", "faqs", fid)
            flash("FAQ visibility updated.", "info")
        elif action == "delete":
            db.execute("DELETE FROM faqs WHERE id=?", (int(request.form["faq_id"]),))
            db.commit()
            audit("faq.delete", "faqs", None)
            flash("FAQ removed.", "info")
        return redirect(url_for("admin_faqs"))
    faqs = db.execute("SELECT * FROM faqs ORDER BY category, sort_order").fetchall()
    return render_template("admin/faqs.html", faqs=faqs)


# ─── Admin: order CSV export (the one missing from the orders page) ──────────
@app.route("/admin/orders/export.csv")
@admin_required
def admin_orders_export():
    import csv, io
    db = get_db()
    rows = db.execute("""SELECT order_number, created_at, region, currency, full_name, email, phone,
        order_status, payment_status, payment_method, subtotal, delivery_fee, discount_amount, total
        FROM orders WHERE COALESCE(is_subscription,0)=0 AND payment_status='paid'
        ORDER BY created_at DESC""").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL)
    w.writerow([d[0] for d in db.execute("SELECT order_number, created_at, region, currency, "
        "full_name, email, phone, order_status, payment_status, payment_method, subtotal, "
        "delivery_fee, discount_amount, total FROM orders LIMIT 0").description])
    for r in rows:
        w.writerow(list(r))
    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=kcblendz-orders.csv"
    return resp


# ─── Admin: report export (split by currency this time) ──────────────────────
def _csv_response(filename, header, rows):
    """Build a downloadable CSV response from a header + iterable of rows."""
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL)
    w.writerow(header)
    for r in rows:
        w.writerow(list(r))
    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return resp


@app.route("/admin/reports/export.csv")
@admin_required
def admin_reports_export():
    """Export any report section. ?type=daily|monthly|region|products|growth|all
    Defaults to the per-region daily revenue export (back-compatible)."""
    db = get_db()
    kind = request.args.get("type", "region").strip().lower()

    if kind == "daily":
        rows = db.execute("""SELECT date(created_at) d, region, currency,
            COUNT(*) n, COALESCE(SUM(total),0) v
            FROM orders WHERE payment_status='paid'
              AND date(created_at) >= date('now','-30 days')
            GROUP BY d, region ORDER BY d DESC, region""").fetchall()
        return _csv_response("kcblendz-daily-revenue.csv",
            ["date", "region", "currency", "orders", "revenue"],
            [[r["d"], r["region"], r["currency"], r["n"], f"{r['v']:.2f}"] for r in rows])

    if kind == "monthly":
        rows = db.execute("""SELECT strftime('%Y-%m', created_at) m, region, currency,
            COUNT(*) n, COALESCE(SUM(total),0) v
            FROM orders WHERE payment_status='paid'
            GROUP BY m, region ORDER BY m DESC, region""").fetchall()
        return _csv_response("kcblendz-monthly-summary.csv",
            ["month", "region", "currency", "orders", "revenue"],
            [[r["m"], r["region"], r["currency"], r["n"], f"{r['v']:.2f}"] for r in rows])

    if kind == "products":
        rows = db.execute("""SELECT oi.item_name, o.region,
            SUM(oi.quantity) qty, SUM(oi.line_total) revenue
            FROM order_items oi JOIN orders o ON o.id=oi.order_id
            WHERE o.payment_status='paid'
            GROUP BY oi.item_name, o.region
            ORDER BY qty DESC""").fetchall()
        return _csv_response("kcblendz-top-products.csv",
            ["product", "region", "quantity_sold", "revenue"],
            [[r["item_name"], r["region"], r["qty"], f"{r['revenue']:.2f}"] for r in rows])

    if kind == "growth":
        rows = db.execute("""SELECT date(created_at) d, COUNT(*) n
            FROM users WHERE role='customer'
              AND date(created_at) >= date('now','-90 days')
            GROUP BY d ORDER BY d DESC""").fetchall()
        return _csv_response("kcblendz-customer-growth.csv",
            ["date", "new_customers"],
            [[r["d"], r["n"]] for r in rows])

    if kind == "all":
        # One combined workbook-style CSV with section headers.
        import csv, io
        buf = io.StringIO()
        w = csv.writer(buf, quoting=csv.QUOTE_ALL)

        w.writerow(["=== REVENUE BY REGION ==="])
        w.writerow(["region", "orders", "revenue"])
        for r in db.execute("""SELECT region, COUNT(*) n, COALESCE(SUM(total),0) v
            FROM orders WHERE payment_status='paid' GROUP BY region""").fetchall():
            w.writerow([r["region"], r["n"], f"{r['v']:.2f}"])

        w.writerow([])
        w.writerow(["=== DAILY REVENUE (30d) ==="])
        w.writerow(["date", "region", "currency", "orders", "revenue"])
        for r in db.execute("""SELECT date(created_at) d, region, currency,
            COUNT(*) n, COALESCE(SUM(total),0) v FROM orders
            WHERE payment_status='paid' AND date(created_at)>=date('now','-30 days')
            GROUP BY d, region ORDER BY d DESC""").fetchall():
            w.writerow([r["d"], r["region"], r["currency"], r["n"], f"{r['v']:.2f}"])

        w.writerow([])
        w.writerow(["=== MONTHLY SUMMARY ==="])
        w.writerow(["month", "region", "currency", "orders", "revenue"])
        for r in db.execute("""SELECT strftime('%Y-%m', created_at) m, region, currency,
            COUNT(*) n, COALESCE(SUM(total),0) v FROM orders
            WHERE payment_status='paid' GROUP BY m, region ORDER BY m DESC""").fetchall():
            w.writerow([r["m"], r["region"], r["currency"], r["n"], f"{r['v']:.2f}"])

        w.writerow([])
        w.writerow(["=== TOP PRODUCTS ==="])
        w.writerow(["product", "region", "quantity_sold", "revenue"])
        for r in db.execute("""SELECT oi.item_name, o.region, SUM(oi.quantity) qty,
            SUM(oi.line_total) revenue FROM order_items oi JOIN orders o ON o.id=oi.order_id
            WHERE o.payment_status='paid' GROUP BY oi.item_name, o.region
            ORDER BY qty DESC""").fetchall():
            w.writerow([r["item_name"], r["region"], r["qty"], f"{r['revenue']:.2f}"])

        w.writerow([])
        w.writerow(["=== CUSTOMER GROWTH (90d) ==="])
        w.writerow(["date", "new_customers"])
        for r in db.execute("""SELECT date(created_at) d, COUNT(*) n FROM users
            WHERE role='customer' AND date(created_at)>=date('now','-90 days')
            GROUP BY d ORDER BY d DESC""").fetchall():
            w.writerow([r["d"], r["n"]])

        resp = make_response(buf.getvalue())
        resp.headers["Content-Type"] = "text/csv; charset=utf-8"
        resp.headers["Content-Disposition"] = "attachment; filename=kcblendz-full-report.csv"
        return resp

    # Default — per-region revenue (back-compatible)
    rows = db.execute("""SELECT date(created_at) d, region, currency,
        COUNT(*) n, COALESCE(SUM(total),0) v
        FROM orders WHERE payment_status='paid'
        GROUP BY d, region ORDER BY d DESC, region""").fetchall()
    return _csv_response("kcblendz-revenue.csv",
        ["date", "region", "currency", "orders", "revenue"],
        [[r["d"], r["region"], r["currency"], r["n"], f"{r['v']:.2f}"] for r in rows])


# ─────────────────────────────────────────────────────────────────────────────
# SEO — sitemap & robots
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/sitemap.xml")
def sitemap():
    db = get_db()
    base = request.url_root.rstrip("/")
    urls = [
        "/", "/store", "/home", "/shop", "/builder", "/wellness",
        "/about", "/contact", "/faq", "/privacy", "/terms",
        "/refund-policy", "/shipping-policy",
    ]
    for p in db.execute("SELECT slug FROM products WHERE is_active=1").fetchall():
        urls.append(f"/product/{p['slug']}")
    for b in db.execute("SELECT slug FROM blog_posts WHERE is_published=1").fetchall():
        urls.append(f"/wellness/{b['slug']}")
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    for u in urls:
        body += f"<url><loc>{base}{u}</loc></url>"
    body += "</urlset>"
    resp = make_response(body)
    resp.headers["Content-Type"] = "application/xml"
    return resp


@app.route("/robots.txt")
def robots():
    body = f"User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /account\nSitemap: {request.url_root}sitemap.xml\n"
    resp = make_response(body)
    resp.headers["Content-Type"] = "text/plain"
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# ERROR HANDLERS
# ─────────────────────────────────────────────────────────────────────────────
@app.errorhandler(403)
def err_403(e): return render_template("public/error.html", code=403, title="Forbidden",
                                       msg="You don't have access to this page."), 403
@app.errorhandler(404)
def err_404(e): return render_template("public/error.html", code=404, title="Not found",
                                       msg="The page you were looking for has wandered off."), 404
@app.errorhandler(500)
def err_500(e): return render_template("public/error.html", code=500, title="Something broke",
                                       msg="We're on it. Please try again in a moment."), 500


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
@app.cli.command("init-db")
def cli_init_db():
    init_db()
    print(f"Database initialised at {DB_PATH}")


def _ensure_db():
    """Initialise the database if it does not yet exist, and always run
    additive migrations on top of an existing DB so deploys can roll forward
    without manual SQL.

    Runs on import so production WSGI servers (gunicorn on Railway/Render/etc.)
    have a ready database — fixes the 'database missing on startup → 500'.
    """
    try:
        need_init = not DB_PATH.exists()
        if not need_init:
            try:
                c = sqlite3.connect(DB_PATH)
                c.execute("SELECT 1 FROM users LIMIT 1")
                c.close()
            except sqlite3.DatabaseError:
                need_init = True
        if need_init:
            init_db()
        else:
            # DB already exists — apply additive schema (new tables) and column migrations.
            conn = sqlite3.connect(DB_PATH)
            conn.executescript(SCHEMA_SQL)
            migrate(conn)
            conn.close()
            _seed_v2_data()
    except Exception as exc:  # never let import crash the worker
        import logging
        logging.getLogger(__name__).warning("DB auto-init skipped: %s", exc)


# Initialise immediately at import time (covers gunicorn / WSGI on Railway).
_ensure_db()


if __name__ == "__main__":
    _ensure_db()
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=port, debug=debug)
