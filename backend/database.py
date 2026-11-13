"""SQLite persistence for the financial product platform."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from catalog import PRODUCT_SCHEMA, sync_catalog


ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "seed.json"
DB_FILE = ROOT / "data" / "financial_products.sqlite3"
CREDENTIAL_FILE = ROOT / "data" / ".credentials.json"


@contextmanager
def connect():
    connection = sqlite3.connect(DB_FILE, timeout=30)
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _seed() -> dict[str, Any]:
    with DATA_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def sync_seed_credentials(connection: sqlite3.Connection, seed_users: list[dict[str, Any]]) -> None:
    """Keep seed accounts and the local credentials file in sync."""
    try:
        with CREDENTIAL_FILE.open("r", encoding="utf-8") as file:
            stored = json.load(file)
        credentials = stored.get("users") if isinstance(stored, dict) and not ({"customers", "admin"} & stored.keys()) else None
        if not isinstance(credentials, dict):
            credentials = {}
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
        credentials = {}

    changed = False
    for user in seed_users:
        password = credentials.get(user["username"])
        generated = not isinstance(password, str) or not password
        if generated:
            env_name = "FINANCIAL_ADMIN_PASSWORD" if user["role"] == "admin" else "FINANCIAL_CUSTOMER_PASSWORD"
            password = os.environ.get(env_name) or secrets.token_urlsafe(12 if user["role"] == "admin" else 10)
            credentials[user["username"]] = password
            changed = True
        exists = connection.execute("SELECT 1 FROM users WHERE id = ?", (user["id"],)).fetchone()
        if generated or not exists:
            salt = secrets.token_hex(16)
            digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000).hex()
            connection.execute(
                """INSERT INTO users (id, username, password_salt, password_hash, name, role, customer_id, tag)
                VALUES (:id, :username, :password_salt, :password_hash, :name, :role, :customer_id, :tag)
                ON CONFLICT(id) DO UPDATE SET password_salt = excluded.password_salt, password_hash = excluded.password_hash""",
                {**user, "password_salt": salt, "password_hash": digest},
            )
    if changed:
        descriptor = os.open(CREDENTIAL_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump({"users": credentials}, file, ensure_ascii=False, indent=2)
            file.write("\n")
        print(f"本地账号凭据已写入 {CREDENTIAL_FILE}")
    if CREDENTIAL_FILE.exists():
        try:
            CREDENTIAL_FILE.chmod(0o600)
        except OSError:
            pass


def init_db() -> None:
    seed = _seed()
    with connect() as connection:
        connection.execute(PRODUCT_SCHEMA)
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                tag TEXT NOT NULL,
                age INTEGER NOT NULL,
                income REAL NOT NULL,
                risk_tolerance INTEGER NOT NULL,
                preferred_term INTEGER NOT NULL,
                investment_budget REAL NOT NULL,
                summary TEXT NOT NULL,
                plan_source TEXT NOT NULL DEFAULT 'user'
            );
            CREATE TABLE IF NOT EXISTS behaviors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT NOT NULL REFERENCES customers(id),
                product_id INTEGER NOT NULL REFERENCES products(id),
                behavior_type TEXT NOT NULL,
                session_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('customer', 'admin')),
                customer_id TEXT REFERENCES customers(id),
                tag TEXT NOT NULL
            );
            """
        )
        user_columns = {row[1] for row in connection.execute("PRAGMA table_info(users)").fetchall()}
        if "password_hash" not in user_columns:
            connection.execute("DROP TABLE users")
            connection.execute(
                """CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('customer', 'admin')),
                    customer_id TEXT REFERENCES customers(id),
                    tag TEXT NOT NULL
                )"""
            )
        new_customers = connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0
        if new_customers:
            connection.executemany(
                """INSERT INTO customers
                (id, name, tag, age, income, risk_tolerance, preferred_term,
                 investment_budget, summary)
                VALUES (:id, :name, :tag, :age, :income, :risk_tolerance,
                        :preferred_term, :investment_budget, :summary)""",
                seed["customers"],
            )
        sync_seed_credentials(connection, seed["users"])
    with connect() as connection:
        backup = sync_catalog(connection)
        if backup:
            print(f"商品导入前的数据库备份：{backup}")
    migrate_retirement_plan()
    if new_customers:
        fields = ("retirement_status", "pension_type", "emergency_reserve", "can_accept_loss",
                  "preferred_category", "personal_pension_status", "personal_pension_budget", "plan_completed")
        with connect() as connection:
            for customer in seed["customers"]:
                target_date = (date.today() + timedelta(days=customer["preferred_term"])).isoformat()
                if customer.get("plan_completed"):
                    connection.execute(
                        f"UPDATE customers SET {', '.join(field + ' = ?' for field in fields)}, target_date = ?, plan_source = 'seed' WHERE id = ?",
                        [customer[field] for field in fields] + [target_date, customer["id"]],
                    )
    with connect() as connection:
        for customer in seed["customers"]:
            target_date = (date.today() + timedelta(days=customer["preferred_term"])).isoformat()
            connection.execute(
                "UPDATE customers SET target_date = ? WHERE id = ? AND plan_source = 'seed'",
                (target_date, customer["id"]),
            )


def migrate_retirement_plan() -> None:
    """Add plan and activity storage without replacing existing account or catalog rows."""
    with connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(customers)")}
        additions = {
            "retirement_status": "TEXT NOT NULL DEFAULT '未填写'",
            "pension_type": "TEXT NOT NULL DEFAULT '未填写'",
            "target_date": "TEXT",
            "emergency_reserve": "REAL NOT NULL DEFAULT 0",
            "can_accept_loss": "INTEGER",
            "preferred_category": "TEXT NOT NULL DEFAULT '全部'",
            "plan_completed": "INTEGER NOT NULL DEFAULT 0",
            "personal_pension_status": "TEXT NOT NULL DEFAULT '未确认'",
            "personal_pension_budget": "REAL NOT NULL DEFAULT 0",
            "plan_source": "TEXT NOT NULL DEFAULT 'user'",
        }
        added_plan_source = False
        for field, definition in additions.items():
            if field not in columns:
                connection.execute(f"ALTER TABLE customers ADD COLUMN {field} {definition}")
                added_plan_source = added_plan_source or field == "plan_source"
        if added_plan_source:
            seed_ids = {customer["id"] for customer in _seed().get("customers", [])}
            if seed_ids:
                placeholders = ", ".join("?" for _ in seed_ids)
                connection.execute(
                    f"UPDATE customers SET plan_source = 'seed' WHERE plan_completed = 1 AND id IN ({placeholders})",
                    tuple(seed_ids),
                )
        behavior_columns = {row[1] for row in connection.execute("PRAGMA table_info(behaviors)")}
        if "created_at" not in behavior_columns:
            # Unknown legacy times remain unknown and never enter temporal rules.
            connection.execute("ALTER TABLE behaviors ADD COLUMN created_at TEXT")
        connection.executescript("""
            CREATE INDEX IF NOT EXISTS behavior_customer_time ON behaviors(customer_id, created_at);
            CREATE TABLE IF NOT EXISTS product_preferences (
                customer_id TEXT NOT NULL REFERENCES customers(id),
                product_id INTEGER NOT NULL REFERENCES products(id),
                favorite INTEGER NOT NULL DEFAULT 0,
                avoided INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (customer_id, product_id)
            );
            CREATE TABLE IF NOT EXISTS association_rules (
                product_a INTEGER NOT NULL, product_b INTEGER NOT NULL,
                rule_type TEXT NOT NULL, evidence TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (product_a, product_b, rule_type)
            );
            CREATE TABLE IF NOT EXISTS recommendation_logs (
                id INTEGER PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id),
                block_type TEXT NOT NULL, product_ids TEXT NOT NULL,
                details TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS abnormal_records (
                id INTEGER PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id),
                reason TEXT NOT NULL, min_distance REAL NOT NULL, created_at TEXT NOT NULL
            );
        """)
        abnormal_columns = connection.execute("PRAGMA table_info(abnormal_records)").fetchall()
        if abnormal_columns:
            has_unique_index = any(index[2] for index in connection.execute("PRAGMA index_list(abnormal_records)").fetchall())
            if has_unique_index:
                connection.execute("ALTER TABLE abnormal_records RENAME TO abnormal_records_old")
                connection.execute("""
                    CREATE TABLE abnormal_records (
                        id INTEGER PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id),
                        reason TEXT NOT NULL, min_distance REAL NOT NULL, created_at TEXT NOT NULL
                    )
                """)
                columns = ["id", "customer_id", "reason", "min_distance", "created_at"]
                connection.execute(
                    f"INSERT INTO abnormal_records ({', '.join(columns)}) SELECT {', '.join(columns)} FROM abnormal_records_old"
                )
                connection.execute("DROP TABLE abnormal_records_old")
        connection.execute("UPDATE customers SET preferred_category = '全部' WHERE preferred_category IN ('基金', '银行理财')")
        connection.execute("PRAGMA user_version = 2")


def rows_to_dict(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def get_products(filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    clauses: list[str] = []
    values: list[Any] = []
    search = filters.get("search", "").strip()
    category = filters.get("category", "全部")
    risk = filters.get("risk", "全部")
    status = filters.get("status", "全部")
    if search:
        clauses.append("(name LIKE ? OR full_name LIKE ? OR code LIKE ? OR issuer LIKE ? OR product_type LIKE ?)")
        values.extend([f"%{search}%"] * 5)
    if category != "全部":
        clauses.append("category = ?")
        values.append(category)
    if risk != "全部":
        clauses.append("risk_level = ?")
        values.append(int(risk))
    if status != "全部":
        clauses.append("status = ?")
        values.append(status)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as connection:
        rows = connection.execute(f"SELECT * FROM products{where} ORDER BY id", values).fetchall()
        variants = rows_to_dict(connection.execute("SELECT * FROM product_variants ORDER BY product_id, term_days, id").fetchall())
    return [{**dict(row), "variants": [variant for variant in variants if variant["product_id"] == row["id"]]} for row in rows]


def get_customers() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute("SELECT * FROM customers ORDER BY id").fetchall()
    return rows_to_dict(rows)


def get_customer(customer_id: str) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    return dict(row) if row else None


def get_behaviors() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute("SELECT id, customer_id, product_id, behavior_type, session_id, created_at FROM behaviors ORDER BY id").fetchall()
    return rows_to_dict(rows)


def get_user_by_credentials(username: str, password: str) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT id, username, password_salt, password_hash, name, role, customer_id, tag FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if not row:
        return None
    salt = row["password_salt"].encode("utf-8")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120000).hex()
    return dict(row) if hmac.compare_digest(digest, row["password_hash"]) else None


def get_user(user_id: str) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT id, username, password_salt, password_hash, name, role, customer_id, tag FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def create_customer_account(name: str, username: str, password: str, risk_tolerance: int, preferred_term: int, investment_budget: float) -> dict[str, Any]:
    try:
        return _create_customer_account(name, username, password, risk_tolerance, preferred_term, investment_budget)
    except sqlite3.IntegrityError as error:
        raise ValueError("username_exists") from error


def _create_customer_account(name: str, username: str, password: str, risk_tolerance: int, preferred_term: int, investment_budget: float) -> dict[str, Any]:
    with connect() as connection:
        if connection.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise ValueError("username_exists")
        customer_id = "U" + secrets.token_hex(4).upper()
        while connection.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone():
            customer_id = "U" + secrets.token_hex(4).upper()
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000).hex()
        connection.execute(
            """INSERT INTO customers
            (id, name, tag, age, income, risk_tolerance, preferred_term, investment_budget, summary, plan_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'user')""",
            (customer_id, name, "新客户", 0, 0, risk_tolerance, preferred_term, investment_budget, "已注册客户"),
        )
        connection.execute(
            """INSERT INTO users
            (id, username, password_salt, password_hash, name, role, customer_id, tag)
            VALUES (?, ?, ?, ?, ?, 'customer', ?, '新客户')""",
            (customer_id, username, salt, digest, name, customer_id),
        )
        row = connection.execute(
            "SELECT id, username, password_salt, password_hash, name, role, customer_id, tag FROM users WHERE id = ?",
            (customer_id,),
        ).fetchone()
    return dict(row)


def get_users_public() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute("SELECT id, username, name, role, customer_id, tag FROM users ORDER BY role, id").fetchall()
    return rows_to_dict(rows)


def get_admin_overview() -> dict[str, Any]:
    with connect() as connection:
        stats = {
            "products": connection.execute("SELECT COUNT(*) FROM products").fetchone()[0],
            "on_sale": connection.execute("SELECT COUNT(*) FROM products WHERE status IN ('在售', '开放申购', '开放中')").fetchone()[0],
            "customers": connection.execute("SELECT COUNT(*) FROM users WHERE role = 'customer'").fetchone()[0],
            "behaviors": connection.execute("SELECT COUNT(*) FROM behaviors").fetchone()[0],
        }
        rows = connection.execute(
            "SELECT id, name, issuer, category, product_type, risk_level, term_days, min_amount, expected_return, expected_return_mid, expected_return_text, status FROM products ORDER BY id"
        ).fetchall()
    return {"stats": stats, "products": rows_to_dict(rows)}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def update_plan(customer_id: str, plan: dict) -> dict:
    fields = ("retirement_status", "pension_type", "target_date", "emergency_reserve",
              "can_accept_loss", "preferred_category", "risk_tolerance", "investment_budget", "preferred_term",
              "personal_pension_status", "personal_pension_budget")
    with connect() as connection:
        connection.execute(
            f"UPDATE customers SET {', '.join(field + ' = ?' for field in fields)}, plan_completed = 1, plan_source = 'user' WHERE id = ?",
            [plan.get(field, 0 if field == "personal_pension_budget" else "未确认") for field in fields] + [customer_id],
        )
    return get_customer(customer_id)


def get_preferences(customer_id: str) -> list[dict]:
    with connect() as connection:
        return rows_to_dict(connection.execute(
            "SELECT product_id, favorite, avoided FROM product_preferences WHERE customer_id = ? ORDER BY product_id",
            (customer_id,),
        ).fetchall())


def record_behavior(customer_id: str, product_id: int, action: str, session_id: str) -> None:
    now = utc_now()
    with connect() as connection:
        if action == "substitute":
            duplicate = connection.execute(
                "SELECT 1 FROM behaviors WHERE customer_id = ? AND product_id = ? AND behavior_type = 'substitute' AND substr(created_at, 1, 10) = substr(?, 1, 10) LIMIT 1",
                (customer_id, product_id, now),
            ).fetchone()
            if duplicate:
                return
        connection.execute(
            "INSERT INTO behaviors (customer_id, product_id, behavior_type, session_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (customer_id, product_id, action, session_id, now),
        )
        if action in {"favorite", "unfavorite", "avoid", "restore"}:
            connection.execute(
                "INSERT OR IGNORE INTO product_preferences (customer_id, product_id, updated_at) VALUES (?, ?, ?)",
                (customer_id, product_id, now),
            )
            if action in {"favorite", "unfavorite"}:
                connection.execute(
                    "UPDATE product_preferences SET favorite = ?, updated_at = ? WHERE customer_id = ? AND product_id = ?",
                    (int(action == "favorite"), now, customer_id, product_id),
                )
            else:
                connection.execute(
                    "UPDATE product_preferences SET avoided = ?, updated_at = ? WHERE customer_id = ? AND product_id = ?",
                    (int(action == "avoid"), now, customer_id, product_id),
                )


def save_recommendation_result(customer_id: str, result: dict, rules: list[dict]) -> None:
    now = utc_now()
    with connect() as connection:
        fields = ("product_a", "product_b", "rule_type", "support", "confidence", "interest")
        existing = [json.loads(row[0]) for row in connection.execute("SELECT evidence FROM association_rules")]
        rule_set = lambda rows: {tuple(rule[field] for field in fields) for rule in rows}
        if rule_set(existing) != rule_set(rules):
            connection.execute("DELETE FROM association_rules")
            connection.executemany(
                "INSERT INTO association_rules VALUES (?, ?, ?, ?, ?)",
                [(rule["product_a"], rule["product_b"], rule["rule_type"], json.dumps(rule, ensure_ascii=False), now) for rule in rules],
            )
        for block, key in (("recommend", "items"), ("substitute", "substitute_items"), ("caution", "caution_items")):
            items = result[key]
            ids = [item["original"]["id"] if block == "substitute" else item["id"] for item in items]
            latest = connection.execute(
                "SELECT product_ids FROM recommendation_logs WHERE customer_id = ? AND block_type = ? ORDER BY id DESC LIMIT 1",
                (customer_id, block),
            ).fetchone()
            if latest and json.loads(latest["product_ids"]) == ids:
                continue
            connection.execute(
                "INSERT INTO recommendation_logs (customer_id, block_type, product_ids, details, created_at) VALUES (?, ?, ?, ?, ?)",
                (customer_id, block, json.dumps(ids), json.dumps({"meta": result["meta"], "items": items}, ensure_ascii=False), now),
            )
        if result["meta"].get("profile_complete") and not result["meta"].get("plan_expired") and not result["items"]:
            reason = "没有符合当前计划的商品"
            already_logged = connection.execute(
                "SELECT 1 FROM abnormal_records WHERE customer_id = ? AND reason = ? AND substr(created_at, 1, 10) = substr(?, 1, 10) LIMIT 1",
                (customer_id, reason, now),
            ).fetchone()
            if not already_logged:
                connection.execute(
                    "INSERT OR IGNORE INTO abnormal_records (customer_id, reason, min_distance, created_at) VALUES (?, ?, ?, ?)",
                    (customer_id, reason, 0, now),
                )


def get_diagnostics() -> dict:
    with connect() as connection:
        rules = [json.loads(row[0]) for row in connection.execute("SELECT evidence FROM association_rules ORDER BY rule_type, product_a, product_b")]
        anomalies = rows_to_dict(connection.execute("SELECT * FROM abnormal_records ORDER BY id DESC LIMIT 50").fetchall())
        logs = rows_to_dict(connection.execute(
            "SELECT id, customer_id, block_type, product_ids, created_at FROM recommendation_logs ORDER BY id DESC LIMIT 30"
        ).fetchall())
        counts = {"rules": connection.execute("SELECT COUNT(*) FROM association_rules").fetchone()[0],
                  "anomalies": connection.execute("SELECT COUNT(*) FROM abnormal_records").fetchone()[0],
                  "recommendations": connection.execute("SELECT COUNT(*) FROM recommendation_logs").fetchone()[0]}
    return {"rules": rules, "anomalies": anomalies, "logs": logs, "counts": counts}
