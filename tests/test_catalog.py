"""Regression checks for safe migration of the supplied product catalog."""

import json
import secrets
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import catalog
import database
import server


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db_patch = patch.object(database, "DB_FILE", root / "test.sqlite3")
        self.credentials_patch = patch.object(database, "CREDENTIAL_FILE", root / ".credentials.json")
        self.catalog_patch = patch.object(catalog, "CATALOG_FILE", Path(__file__).parent / "fixtures" / "legacy_catalog_50.json")
        self.db_patch.start()
        self.credentials_patch.start()
        self.catalog_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.credentials_patch.stop)
        self.addCleanup(self.catalog_patch.stop)
        database.init_db()
        self.products = catalog.load_products()

    def test_source_values_and_assets_are_preserved(self):
        stored = {p["id"]: p for p in database.get_products()}
        self.assertEqual(len(stored), 50)
        self.assertEqual(sum(p["category"] == "基金" for p in stored.values()), 30)
        self.assertEqual(sum(p["expected_return_mid"] is None for p in stored.values()), 13)
        self.assertTrue(any((p["expected_return_mid"] or 0) < 0 for p in stored.values()))
        for original in self.products:
            for field, value in original.items():
                self.assertEqual(stored[original["id"]][field], value)
            image = catalog.ROOT / "frontend" / stored[original["id"]]["image"].lstrip("/")
            self.assertEqual(image.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertIsNone(stored[37]["term_days"])
        self.assertIsNone(stored[31]["expected_return"])

    def test_reimport_retains_accounts_and_current_interactions(self):
        password = secrets.token_urlsafe(12)
        user = database.create_customer_account("客户", "existing", password, 2, 365, 10000)
        with database.connect() as connection:
            connection.execute("INSERT INTO behaviors (customer_id, product_id, behavior_type, session_id) VALUES (?, 1, 'view', 'current')", (user["id"],))
        for _ in range(2):
            database.init_db()
        self.assertIsNotNone(database.get_user_by_credentials("existing", password))
        self.assertEqual(len(database.get_behaviors()), 1)
        with database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM archived_behaviors").fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_legacy_catalog_migration_archives_incompatible_history(self):
        password = secrets.token_urlsafe(12)
        database.create_customer_account("原客户", "legacy", password, 2, 365, 10000)
        with database.connect() as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DROP TABLE products")
            connection.execute("""CREATE TABLE products (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, issuer TEXT NOT NULL,
                category TEXT NOT NULL, product_type TEXT NOT NULL, risk_level INTEGER NOT NULL,
                term_days INTEGER NOT NULL, min_amount REAL NOT NULL, expected_return REAL NOT NULL,
                status TEXT NOT NULL, image TEXT NOT NULL, description TEXT NOT NULL)""")
            fields = tuple(database._seed()["products"][0])
            connection.executemany(
                f"INSERT INTO products ({','.join(fields)}) VALUES ({','.join(':'+f for f in fields)})",
                database._seed()["products"],
            )
            connection.executemany("INSERT INTO behaviors (customer_id, product_id, behavior_type, session_id) VALUES (:customer_id, :product_id, :behavior_type, :session_id)", database._seed()["behaviors"])
            connection.execute("DELETE FROM catalog_state")
        database.init_db()
        database.init_db()
        self.assertIsNotNone(database.get_user_by_credentials("legacy", password))
        self.assertEqual(database.get_behaviors(), [])
        with database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM archived_behaviors").fetchone()[0], 16)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertTrue(list((Path(self.temp.name) / "backups").glob("*.sqlite3")))

    def test_failed_import_rolls_back(self):
        broken = [dict(product) for product in self.products]
        broken[0]["name"] = None
        with database.connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                catalog.sync_catalog(connection, broken)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(database.get_products()[0]["name"], self.products[0]["name"])

    def test_changed_product_identity_archives_only_affected_interactions(self):
        with database.connect() as connection:
            connection.executemany(
                "INSERT INTO behaviors (customer_id, product_id, behavior_type, session_id) VALUES ('U001', ?, 'view', 'current')",
                [(1,), (2,)],
            )
        changed = [dict(product) for product in self.products]
        changed[0]["code"] = "changed-product"
        with database.connect() as connection:
            catalog.sync_catalog(connection, changed)
            catalog.sync_catalog(connection, changed)
            self.assertEqual(connection.execute("SELECT product_id FROM behaviors").fetchall()[0][0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM behaviors").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM archived_behaviors").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_bad_source_rejected(self):
        invalid = Path(self.temp.name) / "invalid.json"
        invalid.write_text(json.dumps([self.products[0], self.products[0]]))
        with self.assertRaises(ValueError):
            catalog.load_products(invalid)

    def test_recommendations_handle_missing_values_without_fabricated_returns(self):
        for customer in database.get_customers():
            result = server.recommendations(customer["id"])
            for product in result["items"]:
                self.assertLessEqual(product["risk_level"], customer["risk_tolerance"])
                self.assertLessEqual(product["min_amount"], customer["investment_budget"])
                self.assertEqual(product["expected_return"], product["expected_return_mid"])
        product = database.get_products({"search": "26G0618H"})[0]
        self.assertIsNone(server.feature_value(product, "expected_return"))
        self.assertEqual(server.pair_rules(), [])
        for product in database.get_products():
            if product["id"] in (33, 34, 36, 50):
                self.assertFalse(server.product_is_eligible(product, {"risk_tolerance": 5, "investment_budget": 100000}))


if __name__ == "__main__":
    unittest.main()
