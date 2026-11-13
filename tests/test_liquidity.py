"""Stage 1 checks for calendar terms, liquidity, and seed plan migration."""

import copy
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import catalog
import database
import recommender


TODAY = date(2026, 11, 13)
PLAN = {
    "id": "stage1", "plan_completed": 1, "risk_tolerance": 1,
    "investment_budget": 250000, "emergency_reserve": 0,
    "target_date": (TODAY + timedelta(days=365)).isoformat(),
    "can_accept_loss": 1, "preferred_category": "全部", "retirement_status": "已退休",
    "personal_pension_status": "已确认", "personal_pension_budget": 0,
}


class Stage1Tests(unittest.TestCase):
    def test_boc_one_year_variant_matches_exact_year(self):
        products = catalog.load_products()
        result = recommender.recommend(PLAN, products, [], [], [], TODAY)
        self.assertEqual(result["items"][0]["id"], 3)
        self.assertEqual(result["items"][0]["selected_variant"]["label"], "1年")

    def test_calendar_months_handle_leap_year_and_month_end(self):
        product = {"term_months": 1, "term_days": 30}
        self.assertEqual(recommender.lock_days(product, {}, date(2024, 1, 31)), 29)
        self.assertEqual(recommender.lock_days(product, {}, date(2023, 1, 31)), 28)
        self.assertEqual(recommender.lock_days({"term_months": 12}, {}, date(2024, 2, 29)), 365)

    def test_value_date_and_settlement_buffer_are_natural_days(self):
        product = {"term_days": 5, "term_basis": "fixed", "settlement_days": 2,
                   "value_date": "2026-11-18", "min_amount": 1, "risk_level": 1,
                   "status": "在售", "capital_protection": "deposit", "product_type": "定期存款"}
        self.assertEqual(recommender.lock_days(product, PLAN, TODAY), 10)
        assessment = recommender.assess(product, {**PLAN, "target_date": (TODAY + timedelta(days=11)).isoformat()}, TODAY)
        self.assertIn("settlement_buffer", assessment["codes"])
        assessment = recommender.assess(product, {**PLAN, "target_date": (TODAY + timedelta(days=10)).isoformat()}, TODAY)
        self.assertNotIn("liquidity", assessment["codes"])

    def test_pension_lock_and_expired_plan(self):
        product = {"term_days": 1, "term_basis": "min_hold", "settlement_days": 0,
                   "min_amount": 1, "risk_level": 1, "status": "在售",
                   "product_type": "固收类", "eligibility": "personal_pension"}
        pension = recommender.assess(product, {**PLAN, "retirement_status": "其他"}, TODAY)
        self.assertIn("pension_lock", pension["codes"])
        expired = recommender.assess(product, {**PLAN, "target_date": (TODAY - timedelta(days=1)).isoformat()}, TODAY)
        self.assertEqual(expired["codes"], ["plan_expired"])
        self.assertEqual(expired["status"], "请更新资金计划")

    def test_cross_year_recruitment_period(self):
        product = {"status": "募集期 12-20 至 01-05"}
        self.assertIsNone(recommender.availability_reason(product, date(2026, 12, 25)))
        self.assertIsNone(recommender.availability_reason(product, date(2027, 1, 3)))
        self.assertIsNotNone(recommender.availability_reason(product, date(2027, 1, 6)))

    def test_legacy_catalog_gets_product_type_defaults(self):
        products = catalog.load_products()
        selected = [copy.deepcopy(products[i - 1]) for i in (3, 21, 25, 33)]
        for product in selected:
            for field in ("term_basis", "settlement_days", "value_date", "term_months"):
                product.pop(field, None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_text(json.dumps(selected, ensure_ascii=False), encoding="utf-8")
            loaded = catalog.load_products(path)
        self.assertEqual((loaded[0]["term_basis"], loaded[0]["settlement_days"]), ("fixed", 0))
        self.assertEqual((loaded[1]["term_basis"], loaded[1]["settlement_days"]), ("min_hold", 7))
        self.assertEqual((loaded[2]["term_basis"], loaded[2]["settlement_days"]), ("fixed", 7))
        self.assertEqual((loaded[3]["term_basis"], loaded[3]["settlement_days"]), ("open", 3))

    def test_seed_plan_source_migration_and_user_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(database, "DB_FILE", root / "db.sqlite3"), patch.object(database, "CREDENTIAL_FILE", root / "credentials.json"):
                database.init_db()
                database.update_plan("U001", {**PLAN, "target_date": "2030-01-01", "preferred_term": 100})
                self.assertEqual(database.get_customer("U001")["plan_source"], "user")
                database.init_db()
                self.assertEqual(database.get_customer("U001")["target_date"], "2030-01-01")
                with database.connect() as connection:
                    connection.execute("ALTER TABLE customers DROP COLUMN plan_source")
                database.init_db()
                self.assertEqual(database.get_customer("U001")["plan_source"], "seed")
                self.assertEqual(database.get_customer("U005")["plan_source"], "user")


if __name__ == "__main__":
    unittest.main()
