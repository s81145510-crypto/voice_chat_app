"""Import-time smoke tests for the Flask API and the new PWA endpoints."""
import os
import tempfile
import unittest

_TEST_DATA = tempfile.mkdtemp(prefix="twenty-one-party-tests-")
os.environ["DATA_DIR"] = _TEST_DATA
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TEST_DATA, "smoke.db")
os.environ["SECRET_KEY"] = "test-only-secret"

from backend.main import app, db, FIN, strict_num


class AppSmokeTests(unittest.TestCase):
    def test_strict_num_rejects_fractional_and_boolean_inputs(self):
        self.assertEqual(strict_num(12), 12)
        self.assertEqual(strict_num("12"), 12)
        self.assertEqual(strict_num(" 12 "), 12)
        self.assertIsNone(strict_num(12.5))
        self.assertIsNone(strict_num(12.0))
        self.assertIsNone(strict_num(True))
        self.assertIsNone(strict_num("12.5"))
        self.assertIsNone(strict_num(""))

    def test_app_imports_and_new_routes_are_registered(self):
        rules = {rule.rule for rule in app.url_map.iter_rules()}
        self.assertIn("/api/notifications", rules)
        self.assertIn("/api/notifications/read", rules)
        self.assertIn("/api/rooms/<rid>/audience", rules)
        self.assertIn("/api/pk/history", rules)
        self.assertIn("/api/users/<int:uid>/block", rules)
        self.assertIn("/api/users/<int:uid>/report", rules)
        self.assertIn("/api/blocks", rules)
        self.assertIn("/api/admin/reports", rules)
        self.assertIn("/api/admin/report", rules)
        self.assertIn("/api/creator/dashboard", rules)

    def test_notification_table_is_created(self):
        from sqlalchemy import inspect
        with app.app_context():
            self.assertTrue(inspect(db.engine).has_table("app_notification"))
            self.assertTrue(inspect(db.engine).has_table("user_block"))
            self.assertTrue(inspect(db.engine).has_table("user_report"))
            self.assertIn("gift_earnings", {c["name"] for c in inspect(db.engine).get_columns("user")})
            self.assertIn("payout_reference", {c["name"] for c in inspect(db.engine).get_columns("withdraw")})

    def test_financial_reconciliation_runs_on_empty_database(self):
        with app.app_context():
            result = FIN["reconcile"]()
            self.assertTrue(result["ok"], result["issues"])
            self.assertEqual(result["eligible_gift_base_coins"], 0)
            self.assertEqual(result["commission_paid_coins"], 0)
            self.assertEqual(result["commission_cap_coins"], 0)

    def test_new_private_endpoints_require_authentication(self):
        client = app.test_client()
        for path in ("/api/notifications", "/api/rooms/test-room/audience", "/api/pk/history", "/api/blocks", "/api/admin/reports", "/api/creator/dashboard"):

            response = client.get(path)
            self.assertEqual(response.status_code, 401, path)


if __name__ == "__main__":
    unittest.main()
