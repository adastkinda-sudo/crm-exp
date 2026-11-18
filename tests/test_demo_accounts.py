"""Demo account generator: counts, reproducibility, isolation from real data, rules and persona recommendations."""

import contextlib
import io
import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
import database
import server
import seed_demo_accounts as seeder


def fast_hash(password: str, salt: str) -> str:
    return f"fast:{salt}:{password}"


class DemoAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "test.sqlite3"
        for target, value in (("DB_FILE", self.db), ("CREDENTIAL_FILE", self.root / ".credentials.json")):
            patcher = patch.object(database, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(seeder.PASSWORD_ENV, None)

    def run_seeder(self, *args, fast=True):
        output = io.StringIO()
        context = patch.object(database, "hash_password", fast_hash) if fast else contextlib.nullcontext()
        with context, contextlib.redirect_stdout(output):
            seeder.main(["--db", str(self.db), *args])
        return output.getvalue()

    def query(self, sql, params=()):
        connection = sqlite3.connect(self.db)
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def demo_snapshot(self):
        """Demo data without values that depend on the clock or on random salts."""
        return {
            "customers": self.query("""SELECT id, name, tag, age, income, risk_tolerance, preferred_term, investment_budget,
                emergency_reserve, can_accept_loss, preferred_category, retirement_status, pension_type,
                personal_pension_status, personal_pension_budget FROM customers WHERE plan_source = 'demo' ORDER BY id"""),
            "behaviors": self.query("""SELECT customer_id, product_id, behavior_type, session_id FROM behaviors
                WHERE customer_id GLOB 'D[0-9][0-9][0-9][0-9]' ORDER BY id"""),
            "preferences": self.query("""SELECT customer_id, product_id, favorite, avoided FROM product_preferences
                WHERE customer_id GLOB 'D[0-9][0-9][0-9][0-9]' ORDER BY customer_id, product_id"""),
            "phones": self.query("SELECT phone FROM users WHERE customer_id GLOB 'D[0-9][0-9][0-9][0-9]' ORDER BY id"),
        }

    def other_snapshot(self):
        """Everything that does not belong to demo accounts."""
        glob = "D[0-9][0-9][0-9][0-9]"
        return {
            "customers": self.query("SELECT * FROM customers WHERE id NOT GLOB ? ORDER BY id", (glob,)),
            "users": self.query("SELECT * FROM users WHERE id NOT GLOB ? ORDER BY id", (glob,)),
            "behaviors": self.query("SELECT * FROM behaviors WHERE customer_id NOT GLOB ? ORDER BY id", (glob,)),
            "preferences": self.query("SELECT * FROM product_preferences WHERE customer_id NOT GLOB ? ORDER BY customer_id, product_id", (glob,)),
            "logs": self.query("SELECT customer_id, block_type, product_ids FROM recommendation_logs WHERE customer_id NOT GLOB ? ORDER BY id", (glob,)),
            "abnormal": self.query("SELECT * FROM abnormal_records WHERE customer_id NOT GLOB ? ORDER BY id", (glob,)),
            "archived_behaviors": self.query("SELECT * FROM archived_behaviors WHERE customer_id NOT GLOB ? ORDER BY archive_id", (glob,)),
            "archived_preferences": self.query("SELECT * FROM archived_product_preferences WHERE customer_id NOT GLOB ? ORDER BY archive_id", (glob,)),
        }

    def add_real_account(self):
        """A registered account whose phone sits inside the demo range, with history in every related table."""
        database.init_db()
        user = database._create_customer_account("真实用户", "13900010500", "password-123", 2, 365, 100000)
        customer_id = user["id"]
        database.record_behavior(customer_id, 39, "view", "real-session")
        database.record_behavior(customer_id, 39, "favorite", "real-session")
        with database.connect() as connection:
            # Created by catalog migrations only when interactions had to be archived.
            connection.execute("""CREATE TABLE IF NOT EXISTS archived_product_preferences (
                archive_id INTEGER PRIMARY KEY, catalog_digest TEXT NOT NULL,
                customer_id TEXT, product_id INTEGER, favorite INTEGER, avoided INTEGER,
                updated_at TEXT, product_snapshot TEXT)""")
            connection.execute("INSERT INTO recommendation_logs (customer_id, block_type, product_ids, details, created_at) VALUES (?, 'recommend', '[39]', '{}', 'x')", (customer_id,))
            connection.execute("INSERT INTO abnormal_records (customer_id, reason, min_distance, created_at) VALUES (?, 'r', 0, 'x')", (customer_id,))
            connection.execute("INSERT INTO archived_behaviors (catalog_digest, original_id, customer_id, product_id, behavior_type, session_id, product_snapshot) VALUES ('d', 1, ?, 39, 'view', 's', '{}')", (customer_id,))
            connection.execute("INSERT INTO archived_product_preferences (catalog_digest, customer_id, product_id, favorite, avoided, updated_at, product_snapshot) VALUES ('d', ?, 39, 1, 0, 'x', '{}')", (customer_id,))
        return customer_id

    def test_generates_requested_accounts_with_markers_and_valid_plans(self):
        output = self.run_seeder("--count", "100")
        self.assertIn("账号数：100", output)
        customers = self.query("SELECT id, name, tag, plan_source, plan_completed, target_date, preferred_term FROM customers WHERE plan_source = 'demo' ORDER BY id")
        self.assertEqual(len(customers), 100)
        self.assertEqual(Counter(row[2] for row in customers), {group["name"]: 20 for group in seeder.GROUPS})
        phones = [row[0] for row in self.query("SELECT phone FROM users WHERE customer_id GLOB 'D[0-9][0-9][0-9][0-9]' ORDER BY phone")]
        self.assertEqual(phones, [str(13900010000 + i) for i in range(100)])
        for customer_id, name, _, source, completed, _, _ in customers:
            self.assertRegex(name, r"^演示·\S+ \d{4}$")
            self.assertEqual((source, completed), ("demo", 1))
            self.assertEqual(name[-4:], customer_id[1:])
        users = self.query("SELECT username, phone, role, tag FROM users WHERE customer_id GLOB 'D[0-9][0-9][0-9][0-9]'")
        self.assertTrue(all(username == phone and role == "customer" for username, phone, role, _ in users))
        now = datetime.now(timezone.utc)
        for customer in database.get_customers():
            if customer["plan_source"] != "demo":
                continue
            plan = {key: customer[key] for key in ("retirement_status", "pension_type", "target_date", "emergency_reserve",
                    "can_accept_loss", "preferred_category", "risk_tolerance", "investment_budget", "personal_pension_status",
                    "personal_pension_budget")}
            self.assertEqual(server.validate_plan(plan)["preferred_term"], customer["preferred_term"])
            events = [b for b in database.get_behaviors() if b["customer_id"] == customer["id"]]
            self.assertTrue(seeder.BEHAVIOR_MIN <= len(events) <= seeder.BEHAVIOR_MAX, customer["id"])
            stamps = [datetime.fromisoformat(b["created_at"]) for b in events]
            self.assertEqual(stamps, sorted(stamps))
            self.assertTrue(now - timedelta(days=60) <= stamps[0] and stamps[-1] <= now)
            self.assertTrue(all(b["session_id"] for b in events))
        preferences = self.query("SELECT COUNT(*) FROM product_preferences WHERE customer_id GLOB 'D[0-9][0-9][0-9][0-9]'")[0][0]
        self.assertGreater(preferences, 100)
        kinds = {row[0] for row in self.query("SELECT DISTINCT behavior_type FROM behaviors WHERE customer_id GLOB 'D[0-9][0-9][0-9][0-9]'")}
        self.assertTrue({"view", "favorite", "avoid"} <= kinds)

    def test_same_seed_is_reproducible_and_other_seed_differs(self):
        self.run_seeder("--count", "50", "--seed", "7")
        first = self.demo_snapshot()
        self.run_seeder("--count", "50", "--seed", "7", "--reset")
        self.assertEqual(self.demo_snapshot(), first)
        self.run_seeder("--count", "50", "--seed", "8", "--reset")
        self.assertNotEqual(self.demo_snapshot(), first)

    def test_reset_and_remove_only_touch_demo_data(self):
        real_id = self.add_real_account()
        self.run_seeder("--count", "50")
        server.recommendations("D0001")
        before = self.other_snapshot()
        self.assertTrue(any(row[0] == real_id for row in before["customers"]))
        self.assertTrue(before["archived_behaviors"] and before["archived_preferences"] and before["abnormal"] and before["logs"])
        demo_logs = self.query("SELECT COUNT(*) FROM recommendation_logs WHERE customer_id = 'D0001'")[0][0]
        self.assertGreater(demo_logs, 0)
        with database.connect() as connection:
            connection.execute("INSERT INTO abnormal_records (customer_id, reason, min_distance, created_at) VALUES ('D0001', 'r', 0, 'x')")
            connection.execute("INSERT INTO archived_behaviors (catalog_digest, original_id, customer_id, product_id, behavior_type, session_id, product_snapshot) VALUES ('d', 2, 'D0001', 39, 'view', 's', '{}')")
            connection.execute("INSERT INTO archived_product_preferences (catalog_digest, customer_id, product_id, favorite, avoided, updated_at, product_snapshot) VALUES ('d', 'D0001', 39, 1, 0, 'x', '{}')")
        self.assertEqual(self.other_snapshot(), before)

        self.run_seeder("--count", "50", "--reset")
        self.assertEqual(self.query("SELECT COUNT(*) FROM customers WHERE plan_source = 'demo'")[0][0], 50)
        self.assertEqual(self.other_snapshot(), before)
        self.assertEqual(self.query("SELECT COUNT(*) FROM archived_behaviors WHERE customer_id = 'D0001'")[0][0], 0)

        output = self.run_seeder("--remove")
        self.assertIn("已清除演示账号 50 个", output)
        self.assertEqual(self.other_snapshot(), before)
        for table in ("customers", "users", "behaviors", "product_preferences", "recommendation_logs", "abnormal_records",
                      "archived_behaviors", "archived_product_preferences"):
            column = "id" if table in {"customers", "users"} else "customer_id"
            self.assertEqual(self.query(f"SELECT COUNT(*) FROM {table} WHERE {column} GLOB 'D[0-9][0-9][0-9][0-9]'")[0][0], 0, table)
        self.assertEqual(self.query("SELECT COUNT(*) FROM association_rules")[0][0], 0)

    def test_phone_taken_by_real_account_aborts_without_changes(self):
        self.run_seeder("--remove")  # initialises the database and its credential file
        real_id = self.add_real_account()
        before = self.other_snapshot()
        output = io.StringIO()
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stdout(output):
            self.run_seeder("--count", "600")
        self.assertIn("13900010500", str(raised.exception))
        self.assertEqual(self.query("SELECT COUNT(*) FROM customers WHERE plan_source = 'demo'")[0][0], 0)
        self.assertEqual(self.other_snapshot(), before)
        self.assertEqual(database.get_customer(real_id)["plan_source"], "user")

    def test_existing_demo_data_requires_reset_and_count_is_bounded(self):
        self.run_seeder("--count", "10")
        with self.assertRaises(SystemExit):
            self.run_seeder("--count", "10")
        self.assertEqual(self.query("SELECT COUNT(*) FROM customers WHERE plan_source = 'demo'")[0][0], 10)
        for bad in ("0", "1001"):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                seeder.parse_args(["--count", bad])

    def test_rules_include_positive_and_negative_and_are_stored(self):
        output = self.run_seeder("--count", "200")
        stored = Counter(row[0] for row in self.query("SELECT rule_type FROM association_rules"))
        self.assertGreaterEqual(stored["positive"], 1)
        self.assertGreaterEqual(stored["negative"], 1)
        self.assertIn(f"正向 {stored['positive']}，反向 {stored['negative']}", output)
        self.assertEqual(len(database.get_diagnostics()["rules"]), stored["positive"] + stored["negative"])

    def test_personas_get_different_top_recommendations(self):
        self.run_seeder("--count", "200")
        products = database.get_products()
        behaviors = database.get_behaviors()
        from recommender import build_rules, recommend
        rules = build_rules(behaviors, {product["id"] for product in products})
        tops: dict[str, Counter] = {group["name"]: Counter() for group in seeder.GROUPS}
        for customer in database.get_customers():
            if customer["plan_source"] != "demo":
                continue
            result = recommend(customer, products, behaviors, database.get_preferences(customer["id"]), rules)
            tops[customer["tag"]][frozenset(item["id"] for item in result["items"])] += 1
        representative = {tag: counter.most_common(1)[0][0] for tag, counter in tops.items()}
        for tag, ids in representative.items():
            self.assertTrue(ids, tag)
        self.assertEqual(len(set(representative.values())), len(seeder.GROUPS), representative)

    def test_shared_password_from_environment_uses_independent_salts(self):
        os.environ[seeder.PASSWORD_ENV] = "shared-demo-pass"
        output = self.run_seeder("--count", "4", fast=False)
        self.assertNotIn("shared-demo-pass", output)
        self.assertFalse((self.root / "test.sqlite3.demo-credentials.json").exists())
        rows = self.query("SELECT phone, password_salt, password_hash FROM users WHERE customer_id GLOB 'D[0-9][0-9][0-9][0-9]' ORDER BY phone")
        self.assertEqual(len({row[1] for row in rows}), 4)
        self.assertEqual(len({row[2] for row in rows}), 4)
        for phone, _, _ in rows:
            self.assertIsNotNone(database.get_user_by_phone_credentials(phone, "shared-demo-pass"))
            self.assertIsNone(database.get_user_by_phone_credentials(phone, "wrong-password"))

    def test_random_password_goes_to_private_file_not_terminal(self):
        output = self.run_seeder("--count", "4", fast=False)
        credential_file = self.root / "test.sqlite3.demo-credentials.json"
        password = json.loads(credential_file.read_text(encoding="utf-8"))["password"]
        self.assertTrue(password)
        self.assertNotIn(password, output)
        self.assertIn(str(credential_file), output)
        self.assertEqual(stat.S_IMODE(credential_file.stat().st_mode), 0o600)
        self.assertIsNotNone(database.get_user_by_credentials("13900010000", password))
        self.run_seeder("--remove")
        self.assertFalse(credential_file.exists())

    def test_default_credentials_file_is_ignored_by_git(self):
        self.assertEqual(seeder.DEMO_CREDENTIAL_FILE, ROOT / "data" / ".demo-credentials.json")
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("data/.demo-credentials.json", ignored)


if __name__ == "__main__":
    unittest.main()
