"""Stage 2 checks for three-tier suitability, scoring, rules, and substitutes."""

import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
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

    def test_top_items_hold_at_most_two_of_one_product_type(self):
        products = [product(1), product(2), product(3), product(4, product_type="货币基金", term=1)]
        items = recommender.recommend(plan(), products, [], [], [], TODAY)["items"]
        self.assertEqual([item["id"] for item in items], [1, 2, 4])
        same_type_only = recommender.recommend(plan(), products[:3], [], [], [], TODAY)
        self.assertEqual([item["id"] for item in same_type_only["items"]], [1, 2])
        self.assertEqual(same_type_only["meta"]["eligible_count"], 3)

    def test_equivalent_protected_items_rank_by_contract_rate_and_drop_dominated(self):
        basis = {"return_basis": "存款年利率"}
        products = [product(1, expected_return_mid=1.05, **basis), product(2, expected_return_mid=1.05, **basis),
                    product(3, expected_return_mid=1.2, minimum=20000, product_type="个人大额存单", **basis),
                    product(4, expected_return_mid=1.0, product_type="储蓄存款", **basis)]
        preferences = [{"product_id": 9, "favorite": 1, "avoided": 0}]
        rules = [{"product_a": 9, "product_b": 1, "rule_type": "positive", "confidence": .9}]
        result = recommender.recommend(plan(), products, [], preferences, rules, TODAY)
        self.assertEqual([item["id"] for item in result["items"]], [3, 1])
        self.assertEqual(result["meta"]["eligible_count"], 4)

    def test_contract_rate_order_skips_floating_returns_and_different_terms(self):
        floating = [product(1, capital_protection=None, product_type="货币基金", term=1, expected_return_mid=1.0),
                    product(2, capital_protection=None, product_type="货币基金", term=1, expected_return_mid=2.0)]
        items = recommender.recommend(plan(), floating, [], [], [], TODAY)["items"]
        self.assertEqual([item["id"] for item in items], [1, 2])
        terms = [product(1, term=365, expected_return_mid=1.0), product(2, term=180, expected_return_mid=2.0)]
        items = recommender.recommend(plan(), terms, [], [], [], TODAY)["items"]
        self.assertEqual([item["id"] for item in items], [1, 2])

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

    def test_favorite_produces_recommended_and_unrecommended_lists(self):
        products = [product(1), product(2), product(3)]
        rules = [{"product_a": 1, "product_b": 2, "rule_type": "positive", "confidence": .8},
                 {"product_a": 1, "product_b": 3, "rule_type": "positive", "confidence": .6},
                 {"product_a": 1, "product_b": 3, "rule_type": "negative", "confidence": .9}]
        without = recommender.recommend(plan(), products, [], [], rules, TODAY)
        self.assertEqual((without["related_items"], without["unrecommended_items"]), ([], []))
        favorite = [{"product_id": 1, "favorite": 1, "avoided": 0}]
        result = recommender.recommend(plan(), products, [], favorite, rules, TODAY)
        self.assertEqual([item["id"] for item in result["related_items"]], [2])
        self.assertEqual(result["related_items"][0]["related_reason"],
                         {"kind": "positive", "source_id": 1, "source_name": "商品1", "confidence": .8})
        self.assertEqual([item["id"] for item in result["unrecommended_items"]], [3])
        self.assertEqual(result["unrecommended_items"][0]["related_reason"]["kind"], "negative")

    def test_avoided_product_lowers_and_lists_products_favorited_with_it(self):
        products = [product(1), product(2)]
        rules = [{"product_a": 1, "product_b": 2, "rule_type": "positive", "confidence": .8}]
        baseline = recommender.recommend(plan(), products, [], [], rules, TODAY)
        avoided = recommender.recommend(plan(), products, [], [{"product_id": 1, "favorite": 0, "avoided": 1}], rules, TODAY)
        base_score = next(item["final_score"] for item in baseline["items"] if item["id"] == 2)
        avoided_item = next(item for item in avoided["items"] if item["id"] == 2)
        self.assertLess(avoided_item["final_score"], base_score)
        self.assertNotIn(1, [item["id"] for item in avoided["items"]])
        self.assertEqual(avoided["related_items"], [])
        # Product 2 is still among the suitable products, so it is not also listed as one to be careful with.
        self.assertEqual(avoided["unrecommended_items"], [])
        crowded = recommender.recommend(plan(), products + [product(3), product(4)], [],
                                        [{"product_id": 1, "favorite": 0, "avoided": 1}], rules, TODAY)
        self.assertNotIn(2, [item["id"] for item in crowded["items"]])
        self.assertEqual([(item["id"], item["related_reason"]["kind"]) for item in crowded["unrecommended_items"]], [(2, "avoided")])

    def test_a_product_carries_one_conclusion(self):
        # Product 3 pays the best rate but customers who favorited product 1 later rejected it.
        products = [product(1, expected_return_mid=1.5), product(2, expected_return_mid=1.6),
                    product(3, expected_return_mid=2.5), product(4, expected_return_mid=1.0)]
        rules = [{"product_a": 1, "product_b": 3, "rule_type": "negative", "confidence": .95}]
        favorite = [{"product_id": 1, "favorite": 1, "avoided": 0}]
        result = recommender.recommend(plan(), products, [], favorite, rules, TODAY)
        shown = {item["id"] for item in result["items"] + result["near_items"]}
        self.assertFalse(shown & {item["id"] for item in result["unrecommended_items"]})
        self.assertNotEqual(result["items"][0]["id"], 3, "合约利率重排不能抵消负向扣分")

    def test_latest_action_leads_full_related_lists(self):
        products = [product(i) for i in range(1, 19)]
        rules = [{"product_a": 1, "product_b": b, "rule_type": "positive", "confidence": .9} for b in range(3, 9)]
        rules += [{"product_a": 1, "product_b": b, "rule_type": "negative", "confidence": .9} for b in range(12, 18)]
        rules += [{"product_a": 2, "product_b": 9, "rule_type": "positive", "confidence": .5},
                  {"product_a": 10, "product_b": 18, "rule_type": "positive", "confidence": .4}]
        customer = {**plan(), "id": "latest"}
        behaviors = [{"customer_id": "latest", "product_id": pid, "behavior_type": "favorite"} for pid in (1, 2)]
        preferences = [{"product_id": pid, "favorite": 1, "avoided": 0} for pid in (1, 2)]
        result = recommender.recommend(customer, products, behaviors, preferences, rules, TODAY)
        self.assertEqual(len(result["related_items"]), recommender.RELATED_LIMIT)
        self.assertEqual(result["related_items"][0]["id"], 9)
        self.assertEqual(len(result["unrecommended_items"]), recommender.RELATED_LIMIT)
        behaviors.append({"customer_id": "latest", "product_id": 10, "behavior_type": "avoid"})
        preferences.append({"product_id": 10, "favorite": 0, "avoided": 1})
        result = recommender.recommend(customer, products, behaviors, preferences, rules, TODAY)
        self.assertEqual(len(result["unrecommended_items"]), recommender.RELATED_LIMIT)
        self.assertEqual(result["unrecommended_items"][0]["id"], 18)

    def test_related_lists_skip_favorites_and_unsuitable_products(self):
        products = [product(1), product(2), product(3, risk=5)]
        rules = [{"product_a": 1, "product_b": 2, "rule_type": "negative", "confidence": .9},
                 {"product_a": 1, "product_b": 3, "rule_type": "positive", "confidence": .9}]
        preferences = [{"product_id": 1, "favorite": 1, "avoided": 0}, {"product_id": 2, "favorite": 1, "avoided": 0}]
        result = recommender.recommend(plan(), products, [], preferences, rules, TODAY)
        self.assertEqual((result["related_items"], result["unrecommended_items"]), ([], []))

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
        self.assertEqual(public["category_note"], "保险要另外看保障、缴费和退保能拿回多少，这里不把保险当作理财推荐。")
        self.assertIn("near_items", public)
        self.assertIn("near_count", public["plan"])
        self.assertNotIn("final_score", public["items"][0])
        self.assertEqual(public["related_items"], [])
        self.assertEqual(public["unrecommended_items"], [])
        favorite = [{"product_id": 1, "favorite": 1, "avoided": 0}]
        rules = [{"product_a": 1, "product_b": 2, "rule_type": "positive", "confidence": .8}]
        related = server.customer_result(recommender.recommend(plan(), [product(1), product(2)], [], favorite, rules, TODAY))["related_items"]
        self.assertEqual(related[0]["related_reason"]["source_id"], 1)
        self.assertNotIn("final_score", related[0])


class AuditRegressionTests(unittest.TestCase):
    def test_assessment_reports_the_real_lock_and_take_out_date(self):
        # A 6-month term counted in calendar months, bought while waiting 7 days for the value date.
        item = product(1, term=180, term_months=6, value_date=(TODAY + timedelta(days=7)).isoformat())
        result = recommender.assess(item, plan(), TODAY)
        expected = recommender.lock_days(item, plan(), TODAY)
        self.assertEqual(result["lock_days"], expected)
        self.assertGreater(expected, 180)
        self.assertEqual(result["available_date"], (TODAY + timedelta(days=expected)).isoformat())
        self.assertIsNone(recommender.assess(product(2, term=None), plan(), TODAY)["lock_days"])

    def test_catalog_carries_take_out_days_for_each_term_option(self):
        waiting = (TODAY + timedelta(days=7)).isoformat()
        item = product(1, term=730, value_date=waiting, variants=[{"id": "a", "label": "6个月", "term_days": 182, "term_months": 6, "min_amount": 100, "rate": 1.0}])
        annotated = recommender.with_take_out_days(item, TODAY)
        self.assertEqual(annotated["lock_days"], 737)
        self.assertEqual(annotated["variants"][0]["lock_days"],
                         recommender.take_out_days(recommender.with_variant(item, item["variants"][0]), TODAY))
        self.assertIsNone(recommender.with_take_out_days(product(2, term=None), TODAY)["lock_days"])
        self.assertEqual(recommender.assess(item, plan(), TODAY)["lock_days"], annotated["variants"][0]["lock_days"])

    def test_money_date_today_asks_to_update_the_plan(self):
        today_plan = plan(target_date=TODAY.isoformat())
        self.assertEqual(recommender.assess(product(1), today_plan, TODAY)["codes"], ["plan_expired"])
        self.assertTrue(recommender.recommend(today_plan, [product(1)], [], [], [], TODAY)["meta"]["plan_expired"])
        self.assertFalse(recommender.recommend(plan(), [product(1)], [], [], [], TODAY)["meta"]["plan_expired"])

    def test_zero_budget_is_not_described_as_all_reserve(self):
        reasons = recommender.assess(product(1), plan(investment_budget=0, emergency_reserve=0), TODAY)["reasons"]
        self.assertTrue(any("0" in reason and "打算安排" in reason for reason in reasons), reasons)
        self.assertFalse(any("应急备用金" in reason for reason in reasons if "留作" in reason))
        reserve_only = recommender.assess(product(1), plan(investment_budget=50000, emergency_reserve=50000), TODAY)["reasons"]
        self.assertTrue(any("留作应急备用金" in reason for reason in reserve_only))

    def test_buyable_status_is_read_the_same_way_everywhere(self):
        check = lambda status: recommender.availability(product(1, status=status), TODAY)
        self.assertIsNone(check("存续（每个交易所工作日开放申购，以364天为一个投资周期）"))
        self.assertIsNone(check("开放申购（官网交易状态）"))
        self.assertEqual(check("在售情况待确认（以手机银行为准）")[1], "pending")
        self.assertEqual(check("暂停申购")[1], "hard")
        self.assertEqual(recommender.availability(product(1, notes="渠道当前非开放期"), TODAY)[1], "hard")
        paused = recommender.assess(product(1, status="暂停申购"), plan(), TODAY)
        self.assertEqual(paused["tier"], "hard")

    def test_cancelled_or_rejected_favorites_stop_counting_as_evidence(self):
        start = datetime(2026, 11, 1)
        events, index = [], 0
        for user in range(40):
            for offset, (pid, action) in enumerate([(1, "favorite"), (2, "favorite")] + ([(1, "unfavorite")] if user < 30 else [])):
                index += 1
                events.append({"id": index, "customer_id": f"u{user}", "product_id": pid, "behavior_type": action,
                               "created_at": (start + timedelta(minutes=user * 10 + offset)).isoformat()})
        rules = recommender.build_rules(events, {1, 2})
        self.assertFalse([rule for rule in rules if rule["rule_type"] == "positive" and 1 in (rule["product_a"], rule["product_b"])])

    def test_both_substitute_paths_avoid_the_same_products(self):
        preferences = [{"product_id": 1, "favorite": 1, "avoided": 0}, {"product_id": 5, "favorite": 0, "avoided": 1}]
        rules = [{"product_a": 1, "product_b": 3, "rule_type": "negative", "confidence": .9},
                 {"product_a": 5, "product_b": 4, "rule_type": "positive", "confidence": .9},
                 {"product_a": 1, "product_b": 6, "rule_type": "positive", "confidence": .9}]
        self.assertEqual(recommender.substitute_exclusions(preferences, rules), {3, 4, 5})


if __name__ == "__main__":
    unittest.main()
