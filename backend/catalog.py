"""Validated product catalog import and transactional SQLite migration."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CATALOG_FILE = ROOT / "data" / "actual_products.json"
PRODUCT_FIELDS = {
    "id": "INTEGER PRIMARY KEY", "name": "TEXT NOT NULL", "full_name": "TEXT",
    "code": "TEXT", "issuer": "TEXT NOT NULL", "category": "TEXT NOT NULL",
    "product_type": "TEXT NOT NULL", "risk_level": "INTEGER", "term_days": "INTEGER",
    "min_amount": "REAL", "expected_return": "REAL", "expected_return_mid": "REAL",
    "expected_return_text": "TEXT", "return_basis": "TEXT", "status": "TEXT",
    "image": "TEXT", "image_path": "TEXT", "description": "TEXT", "source_url": "TEXT",
    "data_source": "TEXT", "as_of": "TEXT", "notes": "TEXT", "image_kind": "TEXT",
    "image_source": "TEXT", "image_alt": "TEXT", "link_status": "TEXT", "link_checked_at": "TEXT",
    "is_simulated": "INTEGER NOT NULL DEFAULT 0", "capital_protection": "TEXT",
    "eligibility": "TEXT", "verification_status": "TEXT",
}
OPTIONAL_FIELDS = {"image", "expected_return", "is_simulated", "capital_protection", "eligibility", "verification_status"}
PRODUCT_SCHEMA = "CREATE TABLE IF NOT EXISTS products (" + ", ".join(
    f"{field} {kind}" for field, kind in PRODUCT_FIELDS.items()
) + ")"


def load_products(path: Path | None = None) -> list[dict]:
    path = path or CATALOG_FILE
    products = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(products, list) or not products:
        raise ValueError("商品数据必须是非空列表")
    seen = set()
    required = set(PRODUCT_FIELDS) - OPTIONAL_FIELDS
    for item in products:
        if not isinstance(item, dict) or not required.issubset(item):
            raise ValueError("商品缺少必要字段")
        product_id = item["id"]
        if type(product_id) is not int or product_id <= 0 or product_id in seen:
            raise ValueError(f"商品编号无效或重复：{product_id}")
        seen.add(product_id)
        for key in ("name", "issuer", "category", "product_type", "status"):
            if not isinstance(item[key], str) or not item[key].strip():
                raise ValueError(f"商品 {product_id} 的 {key} 不能为空")
        for key in required - {"id", "risk_level", "term_days", "min_amount", "expected_return_mid"}:
            if item[key] is not None and not isinstance(item[key], str):
                raise ValueError(f"商品 {product_id} 的 {key} 应为文本")
        if item["risk_level"] is not None and (type(item["risk_level"]) is not int or not 1 <= item["risk_level"] <= 5):
            raise ValueError(f"商品 {product_id} 风险等级无效")
        for key in ("term_days", "min_amount", "expected_return_mid"):
            value = item[key]
            if value is None:
                continue
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"商品 {product_id} 的 {key} 无效")
            if key != "expected_return_mid" and value < 0:
                raise ValueError(f"商品 {product_id} 的 {key} 不能为负数")
        if item["term_days"] is not None and int(item["term_days"]) != item["term_days"]:
            raise ValueError(f"商品 {product_id} 期限必须为整数天")
        if item["source_url"] and urlparse(item["source_url"]).scheme not in ("http", "https"):
            raise ValueError(f"商品 {product_id} 来源链接无效")
        image_path = Path(item["image_path"] or "")
        if image_path.is_absolute() or ".." in image_path.parts or image_path.parts[:1] != ("images",):
            raise ValueError(f"商品 {product_id} 图片路径无效")
        if item.get("image"):
            asset = Path(item["image"].lstrip("/"))
            if asset.parts[:1] != ("assets",) or ".." in asset.parts or asset.suffix not in {".png", ".svg"}:
                raise ValueError(f"商品 {product_id} 页面图片路径无效")
        if item.get("is_simulated", 0) not in (0, 1):
            raise ValueError(f"商品 {product_id} 数据类型无效")
        seen_variants = set()
        for variant in item.get("variants", []):
            if not isinstance(variant.get("id"), str) or variant["id"] in seen_variants:
                raise ValueError(f"商品 {product_id} 期限档编号无效")
            seen_variants.add(variant["id"])
            for key in ("term_days", "min_amount", "rate"):
                value = variant.get(key)
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or (key != "rate" and value < 0)):
                    raise ValueError(f"商品 {product_id} 期限档字段无效")
            if variant.get("term_days") is not None and int(variant["term_days"]) != variant["term_days"]:
                raise ValueError(f"商品 {product_id} 期限档天数必须为整数")
    return products


def catalog_digest(products: list[dict]) -> str:
    payload = json.dumps(products, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sync_catalog(connection: sqlite3.Connection, products: list[dict] | None = None) -> Path | None:
    """Import changed source data; preserve accounts and archive incompatible interactions."""
    products = products if products is not None else load_products()
    digest = catalog_digest(products)
    has_state = connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'catalog_state'").fetchone()
    previous = connection.execute("SELECT value FROM catalog_state WHERE key = 'digest'").fetchone() if has_state else None
    if previous and previous[0] == digest:
        return None

    previous_products = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM products")}
    backup = None
    db_path = connection.execute("PRAGMA database_list").fetchone()[2]
    if db_path and (previous_products or connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]):
        backup_dir = Path(db_path).parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / f"before-catalog-{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3"
        with closing(sqlite3.connect(backup)) as destination:
            connection.backup(destination)
        backup.chmod(0o600)

    prepared = [{**dict.fromkeys(PRODUCT_FIELDS), **item,
                 "is_simulated": item.get("is_simulated", 0), "expected_return": item["expected_return_mid"],
                 "image": item.get("image") or f"/assets/products/P{item['id']:02d}.png"} for item in products]
    incoming = {item["id"]: item for item in products}
    incompatible = set(previous_products) - set(incoming)
    for product_id in set(previous_products) & set(incoming):
        old, new = previous_products[product_id], incoming[product_id]
        if (old.get("code"), old["name"], old["issuer"]) != (new["code"], new["name"], new["issuer"]):
            incompatible.add(product_id)

    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE IF NOT EXISTS catalog_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("""CREATE TABLE IF NOT EXISTS product_variants (
            id TEXT PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
            label TEXT NOT NULL, term_days INTEGER, min_amount REAL, rate REAL,
            status TEXT NOT NULL, source_url TEXT, source_note TEXT
        )""")
        connection.execute("DELETE FROM product_variants")
        connection.execute("""CREATE TABLE IF NOT EXISTS archived_behaviors (
            archive_id INTEGER PRIMARY KEY, catalog_digest TEXT NOT NULL,
            original_id INTEGER, customer_id TEXT, product_id INTEGER,
            behavior_type TEXT, session_id TEXT, product_snapshot TEXT
        )""")
        if "created_at" not in {row[1] for row in connection.execute("PRAGMA table_info(archived_behaviors)")}:
            connection.execute("ALTER TABLE archived_behaviors ADD COLUMN created_at TEXT")
        has_preferences = connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'product_preferences'").fetchone()
        if has_preferences:
            connection.execute("""CREATE TABLE IF NOT EXISTS archived_product_preferences (
                archive_id INTEGER PRIMARY KEY, catalog_digest TEXT NOT NULL,
                customer_id TEXT, product_id INTEGER, favorite INTEGER, avoided INTEGER,
                updated_at TEXT, product_snapshot TEXT
            )""")
            for row in connection.execute("SELECT * FROM product_preferences").fetchall():
                if row["product_id"] in incompatible:
                    connection.execute(
                        """INSERT INTO archived_product_preferences
                        (catalog_digest, customer_id, product_id, favorite, avoided, updated_at, product_snapshot)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (previous[0] if previous else "legacy", row["customer_id"], row["product_id"],
                         row["favorite"], row["avoided"], row["updated_at"],
                         json.dumps(previous_products.get(row["product_id"]), ensure_ascii=False)),
                    )
                    connection.execute("DELETE FROM product_preferences WHERE customer_id = ? AND product_id = ?", (row["customer_id"], row["product_id"]))
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'association_rules'").fetchone():
            connection.execute("DELETE FROM association_rules")
        for row in connection.execute("SELECT * FROM behaviors").fetchall():
            if not previous or row["product_id"] in incompatible:
                connection.execute(
                    """INSERT INTO archived_behaviors
                    (catalog_digest, original_id, customer_id, product_id, behavior_type, session_id, product_snapshot, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (previous[0] if previous else "legacy", row["id"], row["customer_id"], row["product_id"],
                     row["behavior_type"], row["session_id"],
                     json.dumps(previous_products.get(row["product_id"]), ensure_ascii=False), dict(row).get("created_at")),
                )
                connection.execute("DELETE FROM behaviors WHERE id = ?", (row["id"],))
        connection.execute(PRODUCT_SCHEMA.replace("products (", "products_import ("))
        fields = ", ".join(PRODUCT_FIELDS)
        placeholders = ", ".join(f":{key}" for key in PRODUCT_FIELDS)
        connection.executemany(f"INSERT INTO products_import ({fields}) VALUES ({placeholders})", prepared)
        connection.execute("DROP TABLE products")
        connection.execute("ALTER TABLE products_import RENAME TO products")
        for product in products:
            connection.executemany(
                "INSERT INTO product_variants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(variant["id"], product["id"], variant["label"], variant.get("term_days"), variant.get("min_amount"),
                  variant.get("rate"), variant["status"], variant.get("source_url"), variant.get("source_note"))
                 for variant in product.get("variants", [])],
            )
        connection.execute("INSERT OR REPLACE INTO catalog_state VALUES ('digest', ?)", (digest,))
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("商品导入后关联数据不完整，已回滚")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
    return backup
