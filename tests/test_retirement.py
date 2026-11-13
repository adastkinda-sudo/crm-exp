"""Behavioral checks for retirement plans, evidence rules and account isolation."""

import copy
import json
import secrets
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import catalog
import database
import recommender as engine
import server

TODAY = date(2026, 11, 6)
PLAN = {"id": "U001", "plan_completed": 1, "risk_tolerance": 2, "investment_budget": 20000,
        "emergency_reserve": 5000, "target_date": "2027-11-06", "can_accept_loss": 1,
        "preferred_category": "全部", "retirement_status": "已退休", "pension_type": "职工基本养老金", "preferred_term": 365}


def event(user, product, action, day, sequence=0):
    return {"customer_id": user, "product_id": product, "behavior_type": action,
            "created_at": f"2026-10-{day:02d}T12:00:00+00:00", "id": sequence}


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.products = [{**p, "image": f"/assets/products/P{p['id']:02d}.png"} for p in catalog.load_products(Path(__file__).parent / "fixtures" / "legacy_catalog_50.json")]

    def result(self, plan=None, behaviors=None, preferences=None, rules=None):
        return engine.recommend(plan or PLAN, self.products, behaviors or [], preferences or [], rules or [], TODAY)

    def test_incomplete_profile_and_no_loss_never_get_investment_recommendations(self):
        self.assertEqual(self.result({**PLAN, "plan_completed": 0})["items"], [])
        result = self.result({**PLAN, "can_accept_loss": 0})
        self.assertEqual(result["items"], [])
        self.assertFalse(result["meta"]["fallback_used"])

    def test_budget_reserve_risk_and_liquidity_are_hard_constraints(self):
        for changes in ({}, {"investment_budget": 5001}, {"risk_tolerance": 1}, {"target_date": "2026-11-20"}):
            plan = {**PLAN, **changes}
            result = self.result(plan)
            for product in result["items"]:
                self.assertTrue(engine.assess(product, plan, TODAY)["eligible"])
                self.assertLessEqual(product["min_amount"], engine.available_budget(plan))
                self.assertLessEqual(product["risk_level"], plan["risk_tolerance"])
        self.assertEqual(self.result({**PLAN, "emergency_reserve": 20000})["items"], [])
        self.assertEqual(self.result({**PLAN, "target_date": "2026-11-08"})["items"], [])

    def test_uncertain_amount_status_future_sale_and_qualification_excluded(self):
        plan = {**PLAN, "risk_tolerance": 5, "target_date": "2030-11-06"}
        for pid, code in ((33, "amount_unknown"), (34, "amount_unknown"), (36, "amount_unknown"),
                          (50, "amount_unknown"), (41, "availability"), (31, "availability"),
                          (37, "term_unknown"), (47, "qualification")):
            assessment = engine.assess(self.products[pid - 1], plan, TODAY)
            self.assertIn(code, assessment["codes"], pid)
            self.assertFalse(assessment["eligible"])

    def test_cautions_only_for_actual_interest_and_safe_substitute(self):
        self.assertEqual(self.result()["caution_items"], [])
        behaviors = [event("U001", 15, "view", 1)]
        result = self.result(behaviors=behaviors)
        self.assertEqual([p["id"] for p in result["caution_items"]], [15])
        substitute = result["substitute_items"][0]
        self.assertIsNotNone(substitute["replacement"])
        self.assertEqual(substitute["replacement"]["category"], substitute["original"]["category"])
        self.assertTrue(engine.assess(substitute["replacement"], PLAN, TODAY)["eligible"])
        self.assertIn("risk", substitute["original_assessment"]["codes"])
        no_loss = self.result({**PLAN, "can_accept_loss": 0}, behaviors=behaviors)
        self.assertIsNone(no_loss["substitute_items"][0]["replacement"])

    def test_avoid_filters_recommendations_and_substitutes(self):
        preferences = [{"product_id": p["id"], "avoided": 1, "favorite": 0} for p in self.products]
        result = self.result(behaviors=[event("U001", 15, "view", 1)], preferences=preferences)
        self.assertEqual(result["items"], [])
        substitute = engine.find_substitute(self.products[14], self.products, PLAN, {p["id"] for p in self.products}, TODAY)
        self.assertIsNone(substitute["replacement"])

    def test_sparse_and_repeated_events_do_not_create_rules(self):
        events = [event("one", pid, "favorite", 1, i) for i in range(50) for pid in (1, 2)]
        self.assertEqual(engine.build_rules(events, {1, 2}), [])
        events = [event(f"u{i}", pid, "view", 1) for i in range(10) for pid in (1, 2)]
        self.assertEqual(engine.build_rules(events, {1, 2}), [])

    def test_positive_rules_use_unique_favorite_users(self):
        events = [event(f"u{i}", pid, "favorite", pid) for i in range(5) for pid in (1, 2)]
        rules = engine.build_rules(events, {1, 2})
        self.assertEqual(len(rules), 2)
        self.assertTrue(all(r["evidence_users"] == 5 and r["rule_type"] == "positive" for r in rules))
        duplicated = events * 10
        self.assertEqual(engine.build_rules(duplicated, {1, 2}), rules)

    def negative_events(self):
        events = []
        for i in range(5):
            events.extend([event(f"a{i}", 1, "favorite", 1), event(f"a{i}", 2, "view", 2), event(f"a{i}", 2, "avoid", 3)])
        events.extend(event(f"b{i}", 2, "view", 2) for i in range(5))
        return events

    def test_negative_rules_require_order_exposure_and_baseline_lift(self):
        events = self.negative_events()
        negative = [r for r in engine.build_rules(events, {1, 2}) if r["rule_type"] == "negative"]
        self.assertEqual(len(negative), 1)
        self.assertEqual((negative[0]["evidence_users"], negative[0]["exposed_users"]), (5, 5))
        self.assertEqual(negative[0]["interest"], 2)
        reversed_events = copy.deepcopy(events)
        for e in reversed_events:
            if e["behavior_type"] == "favorite":
                e["created_at"] = "2026-10-04T12:00:00+00:00"
        self.assertEqual(engine.build_rules(reversed_events, {1, 2}), [])
        # Restoration withdraws the explicit negative preference.
        restored = events + [event(f"a{i}", 2, "restore", 5) for i in range(5)]
        self.assertEqual(engine.build_rules(restored, {1, 2}), [])
        for e in events:
            e["created_at"] = None
        self.assertEqual(engine.build_rules(events, {1, 2}), [])

    def test_negative_feedback_cannot_bypass_suitability(self):
        rules = [{"product_a": 1, "product_b": 2, "rule_type": "negative", "confidence": .9}]
        result = self.result(behaviors=[event("U001", 2, "view", 1)], preferences=[{"product_id": 1, "favorite": 1, "avoided": 0}], rules=rules)
        self.assertNotIn(2, [p["id"] for p in result["items"]])
        self.assertIn("negative_feedback", result["caution_items"][0]["assessment"]["codes"])
        for substitute in result["substitute_items"]:
            self.assertNotEqual(substitute["replacement"]["id"], 2)

    def test_fusion_uses_same_scale_for_all_candidates(self):
        rules = [{"product_a": 1, "product_b": 2, "rule_type": "positive", "confidence": .9}]
        result = self.result(preferences=[{"product_id": 1, "favorite": 1, "avoided": 0}], rules=rules)
        self.assertEqual(result["items"][0]["id"], 2)
        for item in result["items"]:
            expected = .6 * item["association_score"] + .4 * (1 - item["distance"])
            self.assertAlmostEqual(item["final_score"], expected, places=3)

    def test_fallback_keeps_all_constraints_and_no_return_target(self):
        plan = {**PLAN, "risk_tolerance": 5, "target_date": "2050-11-06"}
        products = [self.products[0]]
        result = engine.recommend(plan, products, [], [], [], TODAY)
        self.assertTrue(result["meta"]["fallback_used"])
        self.assertTrue(all(engine.assess(p, plan, TODAY)["eligible"] for p in result["items"]))
        original = self.result()["items"]
        for product in self.products:
            product["expected_return_mid"] = 99999
        self.assertEqual([p["id"] for p in original], [p["id"] for p in self.result()["items"]])


class ReportCatalogTests(unittest.TestCase):
    def setUp(self):
        self.products = catalog.load_products()
        self.plan = {**PLAN, "investment_budget": 300000, "emergency_reserve": 50000,
                     "target_date": "2030-11-06", "can_accept_loss": 0, "risk_tolerance": 1}

    def test_exact_report_pool_and_simulation_labels(self):
        from collections import Counter
        self.assertEqual([p["id"] for p in self.products], list(range(1, 39)))
        self.assertEqual(Counter(p["category"] for p in self.products),
                         {"储蓄": 10, "养老理财": 14, "保险": 8, "现金管理": 4, "其他投资": 2})
        self.assertEqual([p["id"] for p in self.products if p["is_simulated"]], [29, 30, 31, 32, 37, 38])
        for product in self.products:
            if product["is_simulated"]:
                self.assertIn("模拟", product["name"])
                self.assertIsNone(product["source_url"])
            for variant in product["variants"]:
                self.assertGreater(variant["term_days"], 0)
        self.assertEqual(len(self.products[2]["variants"]), 7)

    def test_deposit_can_match_no_loss_and_variants_do_not_duplicate_product(self):
        result = engine.recommend(self.plan, self.products, [], [], [], TODAY)
        self.assertTrue(result["items"])
        self.assertEqual([p["id"] for p in result["items"]], [3])
        self.assertEqual(result["items"][0]["selected_variant"]["label"], "3年")
        self.assertTrue(all(p["capital_protection"] in {"deposit", "sovereign"} for p in result["items"]))
        self.assertEqual(result["recommend_items"], result["items"])
        smaller = {**self.plan, "investment_budget": 100000, "emergency_reserve": 10000}
        self.assertEqual(engine.recommend(smaller, self.products, [], [], [], TODAY)["items"], [])
        short_plan = {**self.plan, "target_date": "2027-01-06"}
        short = engine.recommend(short_plan, self.products, [], [], [], TODAY)
        self.assertEqual(short["items"][0]["selected_variant"]["label"], "1个月")

    def test_sold_out_deposit_gets_same_term_boc_variant_without_relaxing_plan(self):
        original = self.products[7]
        result = engine.find_substitute(original, self.products, self.plan, set(), TODAY)
        replacement = result["replacement"]
        self.assertIsNotNone(replacement)
        self.assertEqual(replacement["id"], 3)
        self.assertEqual(replacement["term_days"], 1095)
        self.assertEqual(replacement["selected_variant"]["label"], "3年")
        self.assertIn("availability", result["original_assessment"]["codes"])
        self.assertTrue(any("0.20" in difference for difference in result["differences"]))
        tight = {**self.plan, "investment_budget": 100000, "emergency_reserve": 10000}
        self.assertIsNone(engine.find_substitute(original, self.products, tight, set(), TODAY)["replacement"])

    def test_pension_account_funds_are_not_used_for_normal_deposits(self):
        segregated = {**self.plan, "personal_pension_budget": 200000}
        self.assertFalse(engine.assess(self.products[2], segregated, TODAY)["eligible"])

    def test_simulated_insurance_and_pension_requirements_never_bypass_filters(self):
        plan = {**self.plan, "can_accept_loss": 1, "risk_tolerance": 5}
        for pid in (29, 30, 31, 32, 37, 38):
            self.assertIn("simulated", engine.assess(self.products[pid - 1], plan, TODAY)["codes"])
        self.assertIn("insurance", engine.assess(self.products[25], plan, TODAY)["codes"])
        self.assertIn("qualification", engine.assess(self.products[10], plan, TODAY)["codes"])
        pension = {**self.products[10], "min_amount": 1000, "notes": "个人养老金账户内产品", "status": "开放申购"}
        self.assertFalse(engine.assess(pension, plan, TODAY)["eligible"])
        confirmed = {**plan, "personal_pension_status": "已确认", "personal_pension_budget": 500}
        self.assertIn("pension_budget", engine.assess(pension, confirmed, TODAY)["codes"])
        confirmed["personal_pension_budget"] = 2000
        self.assertTrue(engine.assess(pension, confirmed, TODAY)["eligible"])
        # Report's B share is not a small retail money-market allocation.
        self.assertEqual(self.products[34]["min_amount"], 5000000)
        self.assertIn("budget", engine.assess(self.products[34], plan, TODAY)["codes"])


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for patcher in (patch.object(database, "DB_FILE", root / "test.sqlite3"), patch.object(database, "CREDENTIAL_FILE", root / ".credentials.json")):
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()

    def test_incremental_migration_keeps_accounts_catalog_and_unknown_old_times(self):
        password = secrets.token_urlsafe(12)
        user = database.create_customer_account("保留客户", "kept", password, 2, 365, 10000)
        with database.connect() as connection:
            before = [tuple(row) for row in connection.execute("SELECT * FROM users ORDER BY id")]
            products = [tuple(row) for row in connection.execute("SELECT * FROM products ORDER BY id")]
            connection.execute("INSERT INTO behaviors (customer_id, product_id, behavior_type, session_id) VALUES (?, 1, 'favorite', 'old')", (user["id"],))
            connection.execute("DROP TABLE product_preferences")
            for field in ("retirement_status", "pension_type", "target_date", "emergency_reserve", "can_accept_loss", "preferred_category", "plan_completed"):
                connection.execute(f"ALTER TABLE customers DROP COLUMN {field}")
        database.init_db()
        database.init_db()
        with database.connect() as connection:
            self.assertEqual(before, [tuple(row) for row in connection.execute("SELECT * FROM users ORDER BY id")])
            self.assertEqual(products, [tuple(row) for row in connection.execute("SELECT * FROM products ORDER BY id")])
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertIsNotNone(database.get_user_by_credentials("kept", password))
        self.assertIsNone(database.get_behaviors()[0]["created_at"])
        self.assertEqual(database.get_customer(user["id"])["plan_completed"], 0)

    def test_preferences_logs_rules_and_anomalies_persist(self):
        database.record_behavior("U001", 1, "favorite", "test")
        database.record_behavior("U001", 2, "avoid", "test")
        database.update_plan("U001", {**PLAN, "risk_tolerance": 5, "target_date": "2050-11-06"})
        with patch.object(server, "get_products", return_value=database.get_products()[35:36]):
            result = server.recommendations("U001")
        self.assertTrue(result["meta"]["fallback_used"])
        database.init_db()
        self.assertEqual(len(database.get_behaviors()), 2)
        self.assertEqual(database.get_preferences("U001")[0]["favorite"], 1)
        diagnostics = database.get_diagnostics()
        self.assertEqual(len(diagnostics["logs"]), 3)
        self.assertEqual(diagnostics["counts"]["anomalies"], 1)
        database.record_behavior("U001", 2, "restore", "test")
        database.record_behavior("U001", 1, "unfavorite", "test")
        self.assertTrue(all(not row["favorite"] and not row["avoided"] for row in database.get_preferences("U001")))

    def test_catalog_change_archives_affected_preferences(self):
        database.record_behavior("U001", 1, "favorite", "test")
        database.record_behavior("U001", 2, "favorite", "test")
        products = catalog.load_products()
        products[0]["code"] = "replaced"
        with database.connect() as connection:
            catalog.sync_catalog(connection, products)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM archived_product_preferences").fetchone()[0], 1)
        self.assertEqual([row["product_id"] for row in database.get_preferences("U001")], [2])

    def test_switch_50_to_38_archives_old_product_identity_and_keeps_plan(self):
        report = catalog.load_products()
        legacy = catalog.load_products(Path(__file__).parent / "fixtures" / "legacy_catalog_50.json")
        database.update_plan("U001", PLAN)
        with database.connect() as connection:
            catalog.sync_catalog(connection, legacy)
        database.record_behavior("U001", 50, "favorite", "old-catalog")
        stamp = database.get_behaviors()[0]["created_at"]
        with database.connect() as connection:
            users_before = [tuple(row) for row in connection.execute("SELECT * FROM users ORDER BY id")]
            catalog.sync_catalog(connection, report)
            self.assertEqual(users_before, [tuple(row) for row in connection.execute("SELECT * FROM users ORDER BY id")])
            self.assertEqual(connection.execute("SELECT created_at FROM archived_behaviors WHERE product_id=50").fetchone()[0], stamp)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM archived_product_preferences WHERE product_id=50").fetchone()[0], 1)
            self.assertFalse(connection.execute("PRAGMA foreign_key_check").fetchall())
        self.assertEqual(database.get_preferences("U001"), [])
        self.assertEqual(database.get_behaviors(), [])
        self.assertEqual(database.get_customer("U001")["plan_completed"], 1)
        self.assertEqual(len(database.get_products()), 38)
        self.assertEqual(len(database.get_products()[2]["variants"]), 7)

    def test_plan_validation_rejects_nonfinite_and_invalid_values(self):
        for changes in ({"investment_budget": float("nan")}, {"emergency_reserve": 999999}, {"target_date": "2020-01-01"}, {"risk_tolerance": 6}, {"can_accept_loss": "yes"}):
            with self.assertRaises(ValueError):
                server.validate_plan({**PLAN, **changes})


class ApiTests(unittest.TestCase):
    def setUp(self):
        PersistenceTests.setUp(self)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        self.password = secrets.token_urlsafe(12)
        self.user = database.create_customer_account("本人", "owner", self.password, 2, 365, 10000)
        self.token = self.request("/api/auth/login", {"username": "owner", "password": self.password})[1]["token"]

    def stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        server.SESSIONS.clear()

    def request(self, path, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return response.status, json.loads(raw) if response.headers.get_content_type() == "application/json" else raw

    def test_private_routes_and_cross_user_requests_are_denied(self):
        for path in ("/api/recommendations", "/api/profile", "/api/customers", "/api/association-rules", "/api/abnormal-records", "/api/stats"):
            self.assertEqual(self.request(path)[0], 401, path)
        for path in ("/api/recommendations?customer_id=U001", "/api/profile?customer_id=U001"):
            self.assertEqual(self.request(path, token=self.token)[0], 403)
        for path in ("/api/customers", "/api/admin/overview", "/api/admin/diagnostics"):
            self.assertEqual(self.request(path, token=self.token)[0], 403)
        self.assertEqual(self.request("/api/profile", {**PLAN, "customer_id": "U001"}, self.token)[0], 403)
        status, users = self.request("/api/auth/users", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual([u["id"] for u in users["items"]], [self.user["id"]])

    def test_registration_profile_actions_switch_and_admin(self):
        status, registered = self.request("/api/auth/register", {"name": "新客户", "username": "newperson", "password": self.password})
        self.assertEqual(status, 201)
        token = registered["token"]
        self.assertEqual(self.request("/api/profile", PLAN, token)[0], 200)
        for action in ("view", "favorite", "avoid"):
            self.assertEqual(self.request("/api/behaviors", {"product_id": 15, "action": action}, token)[0], 200)
        own = self.request("/api/preferences", token=token)[1]["items"]
        self.assertEqual((own[0]["favorite"], own[0]["avoided"]), (1, 1))
        self.assertEqual(self.request("/api/preferences", token=self.token)[1]["items"], [])
        status, result = self.request("/api/recommendations", token=token)
        self.assertEqual(status, 200)
        self.assertNotIn("meta", result)
        self.assertTrue(all("final_score" not in item for item in result["items"]))
        status, switched = self.request("/api/auth/switch", {"username": "owner", "password": self.password}, token)
        self.assertEqual(status, 200)
        self.assertEqual(self.request("/api/auth/me", token=token)[0], 401)
        self.assertEqual(switched["user"]["id"], self.user["id"])
        # Resolve the actual seeded administrator ID without relying on a fixture spelling.
        admin_token = server.create_session(next(u["id"] for u in database.get_users_public() if u["role"] == "admin"))
        self.assertEqual(self.request("/api/admin/diagnostics", token=admin_token)[0], 200)
        self.assertEqual(self.request("/api/admin/overview", token=admin_token)[1]["stats"]["products"], 38)

    def test_all_product_images_are_served(self):
        for product in database.get_products():
            status, image = self.request(product["image"])
            self.assertEqual(status, 200)
            self.assertTrue(image.startswith(b"<svg"))
        self.assertEqual(self.request("/assets/products/missing.png")[0], 404)
        self.assertEqual(self.request("/../data/.credentials.json")[0], 400)


if __name__ == "__main__":
    unittest.main()
