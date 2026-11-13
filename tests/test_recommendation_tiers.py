"""Stage 2 checks for three-tier suitability, scoring, rules, and substitutes."""

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import database
import recommender
import server


TODAY = date(2026, 11, 13)


def plan(**changes):
    value = {
        "id": "stage2", "plan_completed": 1, "risk_tolerance": 3,
        "investment_budget": 100000, "emergency_reserve": 0,
        "target_date": (TODAY + timedelta(days=365)).isoformat(),
        "can_accept_loss": 1, "preferred_category": "全部", "retirement_status": "其他",
        "personal_pension_status": "未确认", "personal_pension_budget": 0,
    }
    value.update(changes)
    return value


def product(product_id, *, risk=1, term=365, minimum=100, category="储蓄", status="在售", **extra):
    return {"id": product_id, "name": f"商品{product_id}", "issuer": "测试机构", "category": category,
            "product_type": "定期存款", "risk_level": risk, "term_days": term, "min_amount": minimum,
            "status": status, "capital_protection": "deposit", "term_basis": "fixed",
            "settlement_days": 0, **extra}


class Stage2Tests(unittest.TestCase):
    def test_three_tiers_and_soft_penalty(self):
        soft = product(1, term=10, minimum=1, settlement_days=3)
        soft_plan = plan(target_date=(TODAY + timedelta(days=11)).isoformat())
        assessment = recommender.assess(soft, soft_plan, TODAY)
        self.assertEqual(assessment["tier"], "pass")
        self.assertTrue(assessment["eligible"])
        self.assertIn("settlement_buffer", assessment["soft_penalties"])

        pending = product(2, term=None, minimum=None, status="开放申购（额度待确认）")
        pending_assessment = recommender.assess(pending, plan(), TODAY)
        self.assertEqual(pending_assessment["tier"], "pending")
        self.assertFalse(pending_assessment["eligible"])
        self.assertTrue(pending_assessment["gaps"])

        hard = product(3, minimum=200000)
        hard_assessment = recommender.assess(hard, plan(investment_budget=10000), TODAY)
        self.assertEqual(hard_assessment["tier"], "hard")
        self.assertIn("budget", hard_assessment["codes"])

    def test_near_items_have_gaps_and_budget_or_term_gaps_are_hard(self):
        products = [product(1), product(2, term=None, minimum=None, status="待确认"),
                    product(3, term=1000, minimum=1), product(4, minimum=200000)]
        result = recommender.recommend(plan(), products, [], [], [], TODAY)
        self.assertEqual([item["id"] for item in result["items"]], [1])
        self.assertEqual([item["id"] for item in result["near_items"]], [2])
        self.assertTrue(result["near_items"][0]["assessment"]["gaps"])
        self.assertEqual(result["meta"]["near_count"], 1)
        self.assertFalse(result["meta"]["fallback_used"])

    def test_retirement_and_other_risk_ordering(self):
        products = [product(1, risk=1), product(2, risk=3)]
        retired = recommender.recommend(plan(retirement_status="已退休"), products, [], [], [], TODAY)
        other = recommender.recommend(plan(retirement_status="其他"), products, [], [], [], TODAY)
        self.assertEqual(retired["items"][0]["id"], 1)
        self.assertEqual(other["items"][0]["id"], 2)

    def test_positive_and_negative_rules_change_scores_without_filtering(self):
        products = [product(1), product(2)]
        preferences = [{"product_id": 1, "favorite": 1, "avoided": 0}]
        negative = [{"product_a": 1, "product_b": 2, "rule_type": "negative", "confidence": .8}]
        positive = [{"product_a": 1, "product_b": 2, "rule_type": "positive", "confidence": .2}]
        baseline = recommender.recommend(plan(), products, [], preferences, [], TODAY)
        adjusted = recommender.recommend(plan(), products, [], preferences, negative + positive, TODAY)
        base_score = next(item["final_score"] for item in baseline["items"] if item["id"] == 2)
        adjusted_item = next(item for item in adjusted["items"] if item["id"] == 2)
        self.assertLess(adjusted_item["final_score"], base_score)
        self.assertIn(2, [item["id"] for item in adjusted["items"]])

    def test_substitute_never_increases_risk_and_can_widen_category(self):
        original = product(1, risk=3, category="储蓄")
        cross_category = product(2, risk=2, category="现金管理")
        replacement = recommender.find_substitute(original, [original, cross_category], plan(), set(), TODAY)
        self.assertIsNotNone(replacement["replacement"])
        self.assertLessEqual(replacement["replacement"]["risk_level"], original["risk_level"])
        self.assertTrue(any("类别不同" in difference for difference in replacement["differences"]))

    def test_substitute_rejects_cross_category_candidate_over_distance_threshold(self):
        original = product(1, risk=3, term=365, minimum=100, category="储蓄")
        distant = product(2, risk=1, term=2000, minimum=1000000, category="现金管理", product_type="权益基金")
        long_plan = plan(investment_budget=2000000, target_date=(TODAY + timedelta(days=3000)).isoformat())
        self.assertGreater(recommender.product_distance(original, distant), recommender.DISTANCE_THRESHOLD)
        replacement = recommender.find_substitute(original, [original, distant], long_plan, set(), TODAY)
        self.assertIsNone(replacement["replacement"])

    def test_substitute_behavior_is_daily_deduplicated_and_not_rule_exposure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(database, "DB_FILE", root / "db.sqlite3"), patch.object(database, "CREDENTIAL_FILE", root / "credentials.json"):
                database.init_db()
                for _ in range(3):
                    database.record_behavior("U001", 1, "substitute", "test")
                self.assertEqual(len([event for event in database.get_behaviors() if event["behavior_type"] == "substitute"]), 1)
                self.assertEqual(recommender.build_rules(database.get_behaviors(), {1, 2}), [])

    def test_abnormal_record_is_daily_deduplicated_without_cross_day_unique_constraint(self):
        result = {"items": [], "substitute_items": [], "caution_items": [],
                  "meta": {"profile_complete": True, "plan_expired": False}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(database, "DB_FILE", root / "db.sqlite3"), patch.object(database, "CREDENTIAL_FILE", root / "credentials.json"):
                database.init_db()
                with patch.object(database, "utc_now", side_effect=[
                    "2026-11-13T00:00:00+00:00", "2026-11-13T12:00:00+00:00",
                    "2026-11-14T00:00:00+00:00",
                ]):
                    database.save_recommendation_result("U001", result, [])
                    database.save_recommendation_result("U001", result, [])
                    database.save_recommendation_result("U001", result, [])
                with database.connect() as connection:
                    rows = connection.execute("SELECT reason, min_distance, created_at FROM abnormal_records ORDER BY id").fetchall()
                    self.assertEqual(len(rows), 2)
                    self.assertEqual([row[1] for row in rows], [0, 0])
                    self.assertEqual([row[2][:10] for row in rows], ["2026-11-13", "2026-11-14"])

    def test_customer_result_exposes_near_and_category_note_without_scores(self):
        result = recommender.recommend(plan(preferred_category="保险"), [product(1, category="储蓄")], [], [], [], TODAY)
        public = server.customer_result(result)
        self.assertEqual(public["category_note"], "保险需单独核对保障、缴费和退保现金价值，本页不把保险作为理财推荐。")
        self.assertIn("near_items", public)
        self.assertIn("near_count", public["plan"])
        self.assertNotIn("final_score", public["items"][0])


if __name__ == "__main__":
    unittest.main()
