"""
KCBlendz unit tests
===================

Run all:
    python -m unittest tests.py -v

Or pick a class:
    python -m unittest tests.AuthTests -v
    python -m unittest tests.CardValidationTests -v

These tests use Flask's test_client + an isolated test database so they do not
touch your production data. Each test class re-seeds a fresh DB before running.
"""
import os
import sys
import json
import unittest
import tempfile
from pathlib import Path

# Force the app to use a temporary DB before importing it
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["KCB_DB_PATH"] = _tmp.name

# We import after setting the env var so app.py picks up the test DB.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import app as kc                                            # noqa: E402

# Override DB_PATH at module level (app reads at import time, so this rebinds)
kc.DB_PATH = Path(_tmp.name)


def _fresh_db():
    """Re-create and re-seed the test database from scratch."""
    kc.app.testing = True  # disables in-process rate limiters so test order can't trip them
    if kc.DB_PATH.exists():
        kc.DB_PATH.unlink()
    kc.init_db()


def _csrf(client, route="/login"):
    """Pull a valid CSRF token after fetching a page that populates the session."""
    client.get(route)
    with client.session_transaction() as s:
        return s.get("_csrf")


def _login(client, email, password):
    """Log in and return the session uid (or None on failure)."""
    tok = _csrf(client, "/login")
    r = client.post("/login", data={"_csrf": tok, "email": email, "password": password},
                    follow_redirects=False)
    with client.session_transaction() as s:
        return s.get("uid"), r.status_code


# ─────────────────────────────────────────────────────────────────────────────
# Card validation — pure functions, no test client needed.
# ─────────────────────────────────────────────────────────────────────────────
class CardValidationTests(unittest.TestCase):
    def test_luhn_accepts_valid_visa(self):
        self.assertTrue(kc.luhn_check("4242424242424242"))

    def test_luhn_accepts_valid_mastercard(self):
        self.assertTrue(kc.luhn_check("5555555555554444"))

    def test_luhn_rejects_invalid_number(self):
        self.assertFalse(kc.luhn_check("1234567812345678"))

    def test_luhn_rejects_short_numbers(self):
        self.assertFalse(kc.luhn_check("1234"))

    def test_brand_detection_visa(self):
        self.assertEqual(kc.detect_card_brand("4242424242424242"), "visa")

    def test_brand_detection_mastercard(self):
        self.assertEqual(kc.detect_card_brand("5555555555554444"), "mastercard")

    def test_brand_detection_amex(self):
        self.assertEqual(kc.detect_card_brand("378282246310005"), "amex")

    def test_brand_detection_unknown(self):
        self.assertEqual(kc.detect_card_brand("9999999999999999"), "card")

    def test_validate_form_passes_with_clean_data(self):
        class _F:
            def get(self, k, d=""): return {
                "card_number": "4242 4242 4242 4242",
                "card_name": "John Doe",
                "card_expiry": "12/30",
                "card_cvv": "123",
            }.get(k, d)
        ok, errs, card = kc.validate_card_form(_F())
        self.assertTrue(ok)
        self.assertEqual(errs, [])
        self.assertEqual(card["brand"], "visa")
        self.assertEqual(card["last4"], "4242")

    def test_validate_form_fails_on_expired_card(self):
        class _F:
            def get(self, k, d=""): return {
                "card_number": "4242 4242 4242 4242",
                "card_name": "John",
                "card_expiry": "01/20",  # expired
                "card_cvv": "123",
            }.get(k, d)
        ok, errs, _ = kc.validate_card_form(_F())
        self.assertFalse(ok)
        self.assertTrue(any("expired" in e.lower() for e in errs))

    def test_validate_form_fails_on_short_cvv(self):
        class _F:
            def get(self, k, d=""): return {
                "card_number": "4242 4242 4242 4242",
                "card_name": "John",
                "card_expiry": "12/30",
                "card_cvv": "12",  # too short
            }.get(k, d)
        ok, errs, _ = kc.validate_card_form(_F())
        self.assertFalse(ok)
        self.assertTrue(any("CVV" in e for e in errs))


# ─────────────────────────────────────────────────────────────────────────────
# Public route smoke tests
# ─────────────────────────────────────────────────────────────────────────────
class PublicRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _fresh_db()

    def setUp(self):
        self.client = kc.app.test_client()
        with self.client.session_transaction() as s:
            s["region"] = "MU"

    def test_homepage_returns_200(self):
        r = self.client.get("/home")
        self.assertEqual(r.status_code, 200)

    def test_shop_returns_200(self):
        r = self.client.get("/shop")
        self.assertEqual(r.status_code, 200)

    def test_builder_returns_200(self):
        r = self.client.get("/builder")
        self.assertEqual(r.status_code, 200)

    def test_wellness_returns_200(self):
        r = self.client.get("/wellness")
        self.assertEqual(r.status_code, 200)

    def test_all_static_pages_render(self):
        for path in ("/about", "/contact", "/faq", "/privacy", "/terms",
                     "/refund-policy", "/shipping-policy"):
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200, f"{path} -> {r.status_code}")

    def test_root_redirects_when_region_set(self):
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/home", r.headers["Location"])

    def test_root_redirects_to_store_picker_without_region(self):
        client = kc.app.test_client()  # fresh, no region
        r = client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/store", r.headers["Location"])

    def test_sitemap_and_robots_render(self):
        for path in ("/sitemap.xml", "/robots.txt"):
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200)


# ─────────────────────────────────────────────────────────────────────────────
# Authentication tests — signup bug regression + login flows
# ─────────────────────────────────────────────────────────────────────────────
class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _fresh_db()

    def setUp(self):
        self.client = kc.app.test_client()
        with self.client.session_transaction() as s:
            s["region"] = "MU"

    def test_register_with_single_password_succeeds(self):
        """Regression: previously this said 'passwords don't match' incorrectly."""
        tok = _csrf(self.client, "/register")
        r = self.client.post("/register", data={
            "_csrf": tok, "full_name": "Single Pw", "email": "single@kc.com",
            "phone": "+23055551111", "password": "aaaaaaaa",
        }, follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/account", r.headers["Location"])

    def test_register_with_matching_confirm_succeeds(self):
        tok = _csrf(self.client, "/register")
        r = self.client.post("/register", data={
            "_csrf": tok, "full_name": "Match", "email": "match@kc.com",
            "phone": "+23055552222", "password": "bbbbbbbb", "confirm": "bbbbbbbb",
        }, follow_redirects=False)
        self.assertEqual(r.status_code, 302)

    def test_register_with_mismatched_confirm_fails(self):
        tok = _csrf(self.client, "/register")
        r = self.client.post("/register", data={
            "_csrf": tok, "full_name": "Bad", "email": "bad@kc.com",
            "phone": "+23055553333", "password": "cccccccc", "confirm": "different",
        }, follow_redirects=True)
        self.assertIn("do not match", r.get_data(as_text=True))

    def test_register_rejects_short_password(self):
        tok = _csrf(self.client, "/register")
        r = self.client.post("/register", data={
            "_csrf": tok, "full_name": "Short", "email": "short@kc.com",
            "phone": "+23055554444", "password": "abc",
        }, follow_redirects=True)
        self.assertIn("at least 8 characters", r.get_data(as_text=True))

    def test_register_rejects_duplicate_email(self):
        tok = _csrf(self.client, "/register")
        self.client.post("/register", data={
            "_csrf": tok, "full_name": "Dup1", "email": "dup@kc.com",
            "phone": "+23055555555", "password": "aaaaaaaa",
        })
        tok = _csrf(self.client, "/register")
        r = self.client.post("/register", data={
            "_csrf": tok, "full_name": "Dup2", "email": "dup@kc.com",
            "phone": "+23055556666", "password": "aaaaaaaa",
        }, follow_redirects=True)
        self.assertIn("already registered", r.get_data(as_text=True))

    def test_admin_login_uses_2026_password(self):
        uid, status = _login(self.client, "admin@kcblendz.com", "KCBlendz@2026")
        self.assertEqual(status, 302)
        self.assertIsNotNone(uid)

    def test_admin_login_rejects_old_password(self):
        uid, status = _login(self.client, "admin@kcblendz.com", "KCBlendz@2025")
        self.assertEqual(status, 200)            # re-renders form
        self.assertIsNone(uid)


# ─────────────────────────────────────────────────────────────────────────────
# Authorization tests — role-based access control
# ─────────────────────────────────────────────────────────────────────────────
class AuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _fresh_db()

    def setUp(self):
        self.client = kc.app.test_client()
        with self.client.session_transaction() as s:
            s["region"] = "MU"

    def test_guest_cannot_access_account_pages(self):
        for path in ("/account", "/account/orders", "/account/favorites",
                     "/account/profile"):
            with self.subTest(path=path):
                r = self.client.get(path, follow_redirects=False)
                self.assertEqual(r.status_code, 302)
                self.assertIn("/login", r.headers["Location"])

    def test_guest_cannot_access_admin(self):
        r = self.client.get("/admin", follow_redirects=False)
        # admin_required either redirects to /login (302) or forbids outright (403)
        self.assertIn(r.status_code, (302, 403))

    def test_customer_cannot_access_admin(self):
        # Create a regular customer
        tok = _csrf(self.client, "/register")
        self.client.post("/register", data={
            "_csrf": tok, "full_name": "Reg", "email": "reg@kc.com",
            "phone": "+23055557777", "password": "aaaaaaaa",
        })
        # Already logged in as customer now
        r = self.client.get("/admin", follow_redirects=False)
        # Admin decorator should redirect/forbid
        self.assertIn(r.status_code, (302, 403))

    def test_admin_can_access_all_admin_pages(self):
        _login(self.client, "admin@kcblendz.com", "KCBlendz@2026")
        for path in ("/admin", "/admin/products", "/admin/orders", "/admin/users",
                     "/admin/reports", "/admin/blogs", "/admin/builder",
                     "/admin/categories", "/admin/messages", "/admin/notifications"):
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200, f"{path} -> {r.status_code}")


# ─────────────────────────────────────────────────────────────────────────────
# Favorites + Reviews
# ─────────────────────────────────────────────────────────────────────────────
class FavoritesAndReviewsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _fresh_db()
        cls.client = kc.app.test_client()
        with cls.client.session_transaction() as s:
            s["region"] = "MU"
        # Register a customer
        tok = _csrf(cls.client, "/register")
        cls.client.post("/register", data={
            "_csrf": tok, "full_name": "Fav Test", "email": "fav@kc.com",
            "phone": "+23055558888", "password": "aaaaaaaa",
        })
        with cls.client.session_transaction() as s:
            s["region"] = "MU"
        # Pick a real product
        with kc.app.app_context():
            row = kc.get_db().execute(
                "SELECT id, slug FROM products WHERE is_available_mu=1 LIMIT 1"
            ).fetchone()
        cls.pid, cls.pslug = row["id"], row["slug"]

    def test_favorite_toggle_adds_and_removes(self):
        tok = _csrf(self.client, "/shop")
        r = self.client.post(
            f"/favorites/toggle/{self.pid}", data={"_csrf": tok},
            headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json["action"], "added")
        # Toggle again -> removed
        tok = _csrf(self.client, "/shop")
        r = self.client.post(
            f"/favorites/toggle/{self.pid}", data={"_csrf": tok},
            headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(r.json["action"], "removed")

    def test_favorite_toggle_requires_login(self):
        client = kc.app.test_client()
        with client.session_transaction() as s:
            s["region"] = "MU"
        tok = _csrf(client, "/shop")
        r = client.post(f"/favorites/toggle/{self.pid}", data={"_csrf": tok},
                        follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_favorite_toggle_404_on_missing_product(self):
        tok = _csrf(self.client, "/shop")
        r = self.client.post("/favorites/toggle/9999999", data={"_csrf": tok},
                             headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(r.status_code, 404)

    def test_review_submission_succeeds(self):
        tok = _csrf(self.client, f"/product/{self.pslug}")
        r = self.client.post(f"/product/{self.pslug}/review", data={
            "_csrf": tok, "rating": "5", "title": "Loved it",
            "body": "Truly excellent — would buy again.",
        }, follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("#reviews", r.headers["Location"])
        # Verify it appears on product page
        r = self.client.get(f"/product/{self.pslug}")
        self.assertIn("Loved it", r.get_data(as_text=True))

    def test_review_rejects_missing_rating(self):
        tok = _csrf(self.client, f"/product/{self.pslug}")
        r = self.client.post(f"/product/{self.pslug}/review", data={
            "_csrf": tok, "rating": "0", "body": "anything",
        }, follow_redirects=True)
        self.assertIn("rating", r.get_data(as_text=True).lower())


# ─────────────────────────────────────────────────────────────────────────────
# Custom smoothie image helper
# ─────────────────────────────────────────────────────────────────────────────
class CustomBlendImageTests(unittest.TestCase):
    def test_returns_image_for_known_fruit(self):
        img = kc.image_for_blend(["Mango"])
        self.assertTrue(img.startswith("https://"))
        self.assertIn("unsplash", img)

    def test_falls_back_for_empty_list(self):
        img = kc.image_for_blend([])
        self.assertEqual(img, kc.DEFAULT_BLEND_IMAGE)

    def test_uses_first_fruit_for_dominant(self):
        # Mango first → mango photo, not strawberry
        mango_only = kc.image_for_blend(["Mango"])
        strawberry_first = kc.image_for_blend(["Strawberry", "Mango"])
        self.assertEqual(mango_only, kc.CUSTOM_BLEND_IMAGE_BY_FRUIT["Mango"])
        self.assertEqual(strawberry_first, kc.CUSTOM_BLEND_IMAGE_BY_FRUIT["Strawberry"])

    def test_unknown_fruit_uses_default(self):
        self.assertEqual(kc.image_for_blend(["Dragonfruit"]), kc.DEFAULT_BLEND_IMAGE)


# ─────────────────────────────────────────────────────────────────────────────
# Region & currency helpers
# ─────────────────────────────────────────────────────────────────────────────
class RegionHelperTests(unittest.TestCase):
    def test_price_field_mapping(self):
        self.assertEqual(kc.price_field_for("NG"), "price_ngn")
        self.assertEqual(kc.price_field_for("MU"), "price_mur")
        self.assertEqual(kc.price_field_for("GL"), "price_usd")

    def test_currency_for_region(self):
        self.assertEqual(kc.currency_for_region("NG"), "NGN")
        self.assertEqual(kc.currency_for_region("MU"), "MUR")
        self.assertEqual(kc.currency_for_region("GL"), "USD")

    def test_format_money_handles_none(self):
        self.assertEqual(kc.format_money(None, "MU"), "—")

    def test_format_money_includes_symbol(self):
        self.assertIn("Rs", kc.format_money(100, "MU"))
        self.assertIn("₦", kc.format_money(100, "NG"))
        self.assertIn("$", kc.format_money(100, "GL"))


# ─────────────────────────────────────────────────────────────────────────────
# Cart & order flow
# ─────────────────────────────────────────────────────────────────────────────
class CartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _fresh_db()

    def setUp(self):
        self.client = kc.app.test_client()
        with self.client.session_transaction() as s:
            s["region"] = "MU"

    def test_add_product_to_cart(self):
        with kc.app.app_context():
            pid = kc.get_db().execute(
                "SELECT id FROM products WHERE is_available_mu=1 LIMIT 1"
            ).fetchone()["id"]
        tok = _csrf(self.client, "/shop")
        r = self.client.post("/cart/add", data={
            "_csrf": tok, "product_id": str(pid), "quantity": "2",
        }, follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        # Verify cart page shows it
        r = self.client.get("/cart")
        self.assertEqual(r.status_code, 200)

    def test_cart_page_empty_state_renders(self):
        r = self.client.get("/cart")
        self.assertEqual(r.status_code, 200)


# ─────────────────────────────────────────────────────────────────────────────
# Sandbox visibility — admin-only test cards
# ─────────────────────────────────────────────────────────────────────────────
class SandboxVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _fresh_db()
        cls.client = kc.app.test_client()
        with cls.client.session_transaction() as s:
            s["region"] = "MU"
        # Create an order to view the payment page
        with kc.app.app_context():
            db = kc.get_db()
            db.execute("""INSERT INTO orders (order_number, full_name, email, phone,
                region, currency, subtotal, total, fulfillment_type, payment_method)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                ("KC-TEST-001", "T A", "t@a.com", "+23055552222", "MU", "MUR",
                 200, 200, "pickup", "card"))
            db.commit()
            cls.oid = db.execute(
                "SELECT id FROM orders WHERE order_number='KC-TEST-001'"
            ).fetchone()["id"]

    def test_admin_does_not_see_sandbox_test_cards(self):
        """Sandbox / demo banners must never appear on the customer payment
        page — even for admins. Devs see them only in source comments."""
        _login(self.client, "admin@kcblendz.com", "KCBlendz@2026")
        with self.client.session_transaction() as s:
            s["region"] = "MU"
        r = self.client.get(f"/payment/{self.oid}")
        self.assertNotIn("Sandbox test cards", r.get_data(as_text=True))

    def test_guest_does_not_see_sandbox(self):
        client = kc.app.test_client()
        with client.session_transaction() as s:
            s["region"] = "MU"
        r = client.get(f"/payment/{self.oid}")
        self.assertNotIn("Sandbox test cards", r.get_data(as_text=True))


# ─────────────────────────────────────────────────────────────────────────────
# Seed data integrity
# ─────────────────────────────────────────────────────────────────────────────
class SeedDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _fresh_db()

    def test_admin_user_seeded(self):
        with kc.app.app_context():
            row = kc.get_db().execute(
                "SELECT email FROM users WHERE role='admin'"
            ).fetchone()
        self.assertEqual(row["email"], "admin@kcblendz.com")

    def test_products_seeded(self):
        with kc.app.app_context():
            count = kc.get_db().execute(
                "SELECT COUNT(*) AS n FROM products WHERE is_active=1"
            ).fetchone()["n"]
        self.assertGreaterEqual(count, 50, "Expected at least 50 seeded products")

    def test_blog_posts_seeded(self):
        with kc.app.app_context():
            count = kc.get_db().execute(
                "SELECT COUNT(*) AS n FROM blog_posts WHERE is_published=1"
            ).fetchone()["n"]
        self.assertEqual(count, 5)

    def test_reviews_seeded(self):
        with kc.app.app_context():
            count = kc.get_db().execute(
                "SELECT COUNT(*) AS n FROM reviews"
            ).fetchone()["n"]
        self.assertGreater(count, 0, "Expected seeded sample reviews")

    def test_builder_options_seeded(self):
        with kc.app.app_context():
            types = {r["option_type"] for r in kc.get_db().execute(
                "SELECT DISTINCT option_type FROM builder_options"
            ).fetchall()}
        self.assertEqual(types, {"cup_size", "fruit", "base", "sweetener",
                                  "addon", "booster"})

    def test_all_product_images_are_http_urls(self):
        """Guard against regression where image_url is empty or a placeholder SVG."""
        with kc.app.app_context():
            rows = kc.get_db().execute(
                "SELECT name, image_url FROM products WHERE is_active=1"
            ).fetchall()
        for r in rows:
            with self.subTest(product=r["name"]):
                self.assertIsNotNone(r["image_url"], f"{r['name']} has no image")
                self.assertTrue(
                    r["image_url"].startswith("http"),
                    f"{r['name']} image is not an http URL: {r['image_url']}",
                )


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        # Clean up temporary DB
        try: os.unlink(_tmp.name)
        except Exception: pass
