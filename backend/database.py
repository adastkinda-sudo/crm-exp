"""SQLite persistence for the financial product platform."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "seed.json"
DB_FILE = ROOT / "data" / "financial_products.sqlite3"
CREDENTIAL_FILE = ROOT / "data" / ".credentials.json"


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_FILE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _seed() -> dict[str, Any]:
    with DATA_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def _users_with_credentials(users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create local credentials on first run; no password material is committed."""
    if CREDENTIAL_FILE.exists():
        with CREDENTIAL_FILE.open("r", encoding="utf-8") as file:
            credentials = json.load(file)
    else:
        customer_password = os.environ.get("FINANCIAL_CUSTOMER_PASSWORD") or secrets.token_urlsafe(10)
        admin_password = os.environ.get("FINANCIAL_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
        credentials = {
            "customers": {user["username"]: customer_password for user in users if user["role"] == "customer"},
            "admin": admin_password,
        }
        CREDENTIAL_FILE.write_text(json.dumps(credentials, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            CREDENTIAL_FILE.chmod(0o600)
        except OSError:
            pass
        print(f"本地账号凭据已写入 {CREDENTIAL_FILE}")

    enriched: list[dict[str, Any]] = []
    for user in users:
        password = credentials["admin"] if user["role"] == "admin" else credentials["customers"][user["username"]]
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000).hex()
        enriched.append({**user, "password_salt": salt, "password_hash": digest})
    return enriched


def init_db() -> None:
    seed = _seed()
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                issuer TEXT NOT NULL,
                category TEXT NOT NULL,
                product_type TEXT NOT NULL,
                risk_level INTEGER NOT NULL,
                term_days INTEGER NOT NULL,
                min_amount REAL NOT NULL,
                expected_return REAL NOT NULL,
                status TEXT NOT NULL,
                image TEXT NOT NULL,
                description TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS customers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                tag TEXT NOT NULL,
                age INTEGER NOT NULL,
                income REAL NOT NULL,
                risk_tolerance INTEGER NOT NULL,
                preferred_term INTEGER NOT NULL,
                investment_budget REAL NOT NULL,
                summary TEXT NOT NULL
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
        if connection.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            connection.executemany(
                """INSERT INTO products
                (id, name, issuer, category, product_type, risk_level, term_days,
                 min_amount, expected_return, status, image, description)
                VALUES (:id, :name, :issuer, :category, :product_type, :risk_level,
                        :term_days, :min_amount, :expected_return, :status, :image, :description)""",
                seed["products"],
            )
        if connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0:
            connection.executemany(
                """INSERT INTO customers
                (id, name, tag, age, income, risk_tolerance, preferred_term,
                 investment_budget, summary)
                VALUES (:id, :name, :tag, :age, :income, :risk_tolerance,
                        :preferred_term, :investment_budget, :summary)""",
                seed["customers"],
            )
        if connection.execute("SELECT COUNT(*) FROM behaviors").fetchone()[0] == 0:
            connection.executemany(
                """INSERT INTO behaviors
                (customer_id, product_id, behavior_type, session_id)
                VALUES (:customer_id, :product_id, :behavior_type, :session_id)""",
                seed["behaviors"],
            )
        if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            users_seed = _users_with_credentials(seed["users"])
            connection.executemany(
                """INSERT INTO users
                (id, username, password_salt, password_hash, name, role, customer_id, tag)
                VALUES (:id, :username, :password_salt, :password_hash, :name, :role, :customer_id, :tag)""",
                users_seed,
            )


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
        clauses.append("(name LIKE ? OR issuer LIKE ? OR product_type LIKE ?)")
        values.extend([f"%{search}%"] * 3)
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
    return rows_to_dict(rows)


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
        rows = connection.execute("SELECT customer_id, product_id, behavior_type, session_id FROM behaviors ORDER BY id").fetchall()
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
            (id, name, tag, age, income, risk_tolerance, preferred_term, investment_budget, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            "on_sale": connection.execute("SELECT COUNT(*) FROM products WHERE status = '在售'").fetchone()[0],
            "customers": connection.execute("SELECT COUNT(*) FROM users WHERE role = 'customer'").fetchone()[0],
            "behaviors": connection.execute("SELECT COUNT(*) FROM behaviors").fetchone()[0],
        }
        rows = connection.execute(
            "SELECT id, name, issuer, category, product_type, risk_level, term_days, min_amount, expected_return, status FROM products ORDER BY id"
        ).fetchall()
    return {"stats": stats, "products": rows_to_dict(rows)}
