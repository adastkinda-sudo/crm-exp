#!/usr/bin/env python3
"""Generate or remove demo customer accounts that show the recommendation algorithm at work.

Demo accounts are recognisable by id D0001..D1000, customers.plan_source = 'demo',
phone 13900010000..13900010999 and a persona name in customers.tag.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import secrets
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import database  # noqa: E402
import server  # noqa: E402
from recommender import assess, build_rules  # noqa: E402


DEFAULT_COUNT = 1000
MAX_COUNT = 1000
DEFAULT_SEED = 20261118
PHONE_BASE = 13900010000
DEFAULT_DB_FILE = ROOT / "data" / "financial_products.sqlite3"
DEMO_CREDENTIAL_FILE = ROOT / "data" / ".demo-credentials.json"
PASSWORD_ENV = "FINANCIAL_DEMO_PASSWORD"
DEMO_ID_GLOB = "D[0-9][0-9][0-9][0-9]"
DEMO_PHONE_GLOB = "1390001[0-9][0-9][0-9][0-9]"
HISTORY_DAYS = 60
BEHAVIOR_MIN, BEHAVIOR_MAX = 10, 20
FAVORITE_PROBABILITY = 0.8
AVOID_JOIN_PROBABILITY = 0.85
AVOID_PROBABILITY = 0.9

SURNAMES = "张王李赵刘陈杨黄周吴徐孙马朱胡郭何林高罗郑梁谢宋唐韩曹许邓冯"
HONORIFICS = ("阿姨", "叔叔", "大姐", "大哥", "老师", "师傅", "女士", "先生")

# Catalog selectors never name product ids: a selector filters the live catalog by
# type / category / risk, and anchors only keep products that pass the group's reference plan.
GROUPS = (
    {
        "name": "保本储蓄", "age": (55, 72), "retirement": ("已退休", "临近退休"), "risk": (1, 1), "loss": 0.0,
        "term": (730, 1825), "budget": (100000, 800000), "category": "储蓄",
        "anchors": (("个人大额存单", 0, 1), ("定期存款", 0, 2)),
        "theme": {"types": ("储蓄国债",), "count": 2}, "avoid_rank": 0,
    },
    {
        "name": "短期备用", "age": (35, 62), "retirement": ("其他", "临近退休"), "risk": (1, 2), "loss": 1.0,
        "term": (90, 365), "budget": (50000, 300000), "category": "现金管理",
        "anchors": (("货币基金", 0, 2), ("定期存款", 2, 3)),
        "theme": {"types": ("货币基金",), "count": 2, "skip_anchors": True}, "avoid_rank": 1,
    },
    {
        "name": "稳健养老", "age": (58, 75), "retirement": ("已退休", "已退休", "临近退休"), "risk": (2, 2), "loss": 1.0,
        "term": (365, 1095), "budget": (200000, 1000000), "category": "养老理财",
        "anchors": (("个人大额存单", 0, 1), ("定期存款", 1, 3)),
        "theme": {"categories": ("养老理财",), "types": ("固定收益类", "固收增强"), "max_risk": 2, "count": 2}, "avoid_rank": None,
    },
    {
        "name": "家庭保障", "age": (35, 55), "retirement": ("其他",), "risk": (1, 2), "loss": 0.2,
        "term": (1095, 3650), "budget": (100000, 600000), "category": "保险",
        "anchors": (("个人大额存单", 0, 1), ("定期存款", 0, 1), ("定期存款", 3, 4)),
        "theme": {"categories": ("保险",), "simulated": False, "count": 2}, "avoid_rank": None,
    },
    {
        "name": "稳健进取", "age": (40, 58), "retirement": ("其他", "临近退休"), "risk": (3, 3), "loss": 1.0,
        "term": (1095, 1825), "budget": (300000, 1500000), "category": "全部",
        "anchors": (("个人大额存单", 0, 1), ("定期存款", 2, 4)),
        "theme": {"risk": 3, "fallback_types": ("混合类",), "count": 2}, "avoid_rank": None,
    },
)


def hours(value: float) -> timedelta:
    return timedelta(hours=value)


# ---------------------------------------------------------------- catalog selection

def _matches(product: dict, spec: dict) -> bool:
    if spec.get("types") and product["product_type"] not in spec["types"]:
        return False
    if spec.get("categories") and product["category"] not in spec["categories"]:
        return False
    if spec.get("simulated") is False and product.get("is_simulated"):
        return False
    risk = product.get("risk_level")
    if spec.get("risk") is not None and risk != spec["risk"]:
        return False
    if spec.get("max_risk") is not None and (risk is None or risk > spec["max_risk"]):
        return False
    return True


def reference_customer(group: dict, today: date) -> dict:
    """A mid-range plan for the group, used to choose products that suit it."""
    budget = round((group["budget"][0] + group["budget"][1]) / 2, -4)
    days = (group["term"][0] + group["term"][1]) // 2
    return {"id": "reference", "plan_completed": 1, "risk_tolerance": group["risk"][1], "can_accept_loss": int(group["loss"] >= 0.5),
            "investment_budget": budget, "emergency_reserve": round(budget * 0.15, -3), "preferred_term": days,
            "target_date": (today + timedelta(days=days)).isoformat(), "preferred_category": group["category"],
            "retirement_status": group["retirement"][0], "personal_pension_status": "未确认", "personal_pension_budget": 0}


def select_core(group: dict, products: list[dict], today: date, warnings: list[str]) -> dict:
    """Return the group's anchor products, theme products and the product its members mark as not interested."""
    reference = reference_customer(group, today)
    ordered = sorted(products, key=lambda product: product["id"])
    anchors: list[int] = []
    for product_type, start, stop in group["anchors"]:
        passing = [p["id"] for p in ordered if p["product_type"] == product_type and assess(p, reference, today)["tier"] == "pass"]
        chosen = [pid for pid in passing[start:stop] if pid not in anchors]
        if len(passing[start:stop]) < stop - start:
            warnings.append(f"{group['name']}：「{product_type}」中符合参考计划的商品不足（需要第 {start + 1}–{stop} 个，实际 {len(passing)} 个）")
        anchors.extend(chosen)

    theme_spec = group["theme"]
    theme: list[int] = []
    candidates = [p for p in ordered if _matches(p, theme_spec) and p["id"] not in anchors]
    if theme_spec.get("skip_anchors"):
        candidates = [p for p in candidates if p["id"] not in anchors]
    # Exact-risk selectors (R3) may be empty in the current catalog: fall back to the named types.
    if len(candidates) < theme_spec["count"] and theme_spec.get("fallback_types"):
        warnings.append(f"{group['name']}：目录中没有符合主题条件的商品（风险 R{theme_spec.get('risk')}），改用 {'、'.join(theme_spec['fallback_types'])}")
        candidates += [p for p in ordered if p["product_type"] in theme_spec["fallback_types"] and p["id"] not in anchors and p not in candidates]
    theme = [p["id"] for p in candidates[:theme_spec["count"]]]
    if len(theme) < theme_spec["count"]:
        warnings.append(f"{group['name']}：主题商品不足（需要 {theme_spec['count']} 个，实际 {len(theme)} 个）")

    avoid = None
    if group["avoid_rank"] is not None:
        risky = sorted((p for p in ordered if (p.get("risk_level") or 0) >= 4), key=lambda p: (p["risk_level"], p["id"]))
        if len(risky) > group["avoid_rank"]:
            avoid = risky[group["avoid_rank"]]["id"]
        else:
            warnings.append(f"{group['name']}：目录中风险 R4 及以上的商品不足，不生成“不感兴趣”记录")
    return {"anchors": anchors, "theme": theme, "avoid": avoid}


# ---------------------------------------------------------------- account data

def _round(value: float, step: int) -> int:
    return int(round(value / step) * step)


def make_account(index: int, group: dict, rng: random.Random, today: date) -> dict:
    status = rng.choice(group["retirement"])
    low, high = group["budget"]
    budget = _round(rng.uniform(low, high), 10000)
    reserve = _round(budget * rng.uniform(0.1, 0.25), 1000)
    pension_confirmed = group["name"] == "稳健养老" and status == "已退休" and rng.random() < 0.3
    pension_budget = _round(min(budget - reserve, rng.uniform(10000, 30000)), 1000) if pension_confirmed else 0
    days = rng.randint(*group["term"])
    pension_type = (rng.choice(("职工基本养老金", "城乡居民养老金")) if status == "已退休"
                    else rng.choice(("暂未领取", "不便提供", "其他来源")))
    plan = server.validate_plan({
        "retirement_status": status, "pension_type": pension_type, "preferred_category": group["category"],
        "target_date": (today + timedelta(days=days)).isoformat(), "risk_tolerance": rng.randint(*group["risk"]),
        "investment_budget": budget, "emergency_reserve": reserve, "can_accept_loss": int(rng.random() < group["loss"]),
        "personal_pension_status": "已确认" if pension_confirmed else "未确认", "personal_pension_budget": pension_budget,
    })
    age = rng.randint(*group["age"])
    name = f"演示·{rng.choice(SURNAMES)}{rng.choice(HONORIFICS)} {index:04d}"
    return {"id": f"D{index:04d}", "phone": str(PHONE_BASE + index - 1), "name": name, "tag": group["name"], "age": age,
            "income": _round(rng.uniform(30000, 180000), 1000), "plan": plan,
            "summary": f"演示账号：{group['name']}人群，用于展示推荐算法"}


def make_behaviors(account: dict, core: dict, product_ids: list[int], rng: random.Random, now: datetime) -> list[tuple[str, int, datetime, str]]:
    """Ordered (behavior_type, product_id, time, session_id) events for one account."""
    steps: list[tuple[str, int]] = []
    favorites: set[int] = set()

    def interact(product_id: int, probability: float) -> None:
        steps.append(("view", product_id))
        if rng.random() < probability:
            steps.append(("favorite", product_id))
            favorites.add(product_id)

    for product_id in rng.sample(core["anchors"], len(core["anchors"])):
        interact(product_id, FAVORITE_PROBABILITY)
    for product_id in core["theme"]:
        interact(product_id, FAVORITE_PROBABILITY)
    if core["avoid"] is not None and favorites and rng.random() < AVOID_JOIN_PROBABILITY:
        steps.append(("view", core["avoid"]))
        if rng.random() < AVOID_PROBABILITY:
            steps.append(("avoid", core["avoid"]))
            if rng.random() < 0.06:
                steps.append(("restore", core["avoid"]))
    core_ids = set(core["anchors"]) | set(core["theme"])
    others = [pid for pid in product_ids if pid not in core_ids]
    wanted = rng.randint(2, 4)
    for product_id in rng.sample(others, min(wanted, len(others))):
        steps.append(("view", product_id))
    if favorites and rng.random() < 0.08:
        steps.append(("unfavorite", rng.choice(sorted(favorites))))
    if len(steps) > BEHAVIOR_MAX:
        # Drop random views first, never the core interactions.
        removable = [i for i, (kind, pid) in enumerate(steps) if kind == "view" and pid not in core_ids and pid != core["avoid"]]
        for i in sorted(rng.sample(removable, min(len(removable), len(steps) - BEHAVIOR_MAX)), reverse=True):
            del steps[i]
    pool = [pid for pid in others if pid != core["avoid"]]
    while len(steps) < BEHAVIOR_MIN and pool:
        steps.insert(rng.randint(0, len(steps)), ("view", rng.choice(pool)))

    # Times increase along the steps and stay inside the last 60 days, with 5 minutes to 36 hours between steps.
    clock = now - timedelta(days=rng.randint(32, HISTORY_DAYS - 1), seconds=rng.randint(0, 86399))
    events, session, serial = [], 0, 0
    for kind, product_id in steps:
        gap = timedelta(seconds=rng.randint(300, 36 * 3600)) if events else timedelta()
        if gap > hours(4):
            session += 1
        clock += gap
        serial += 1
        events.append((kind, product_id, clock, f"demo-{account['id'][1:]}-{session + 1}"))
    return events


def replay_preferences(events: list[tuple[str, int, datetime, str]]) -> list[tuple[int, int, int, datetime]]:
    """Derive product_preferences from the events the same way record_behavior does."""
    state: dict[int, list] = {}
    for kind, product_id, stamp, _ in events:
        if kind not in {"favorite", "unfavorite", "avoid", "restore"}:
            continue
        row = state.setdefault(product_id, [0, 0, stamp])
        if kind in {"favorite", "unfavorite"}:
            row[0] = int(kind == "favorite")
        else:
            row[1] = int(kind == "avoid")
        row[2] = stamp
    return [(product_id, row[0], row[1], row[2]) for product_id, row in sorted(state.items())]


def stamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


# ---------------------------------------------------------------- database operations

def demo_customer_ids(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """SELECT c.id FROM customers c LEFT JOIN users u ON u.customer_id = c.id
        WHERE c.id GLOB ? AND (c.plan_source = 'demo' OR u.phone GLOB ?) ORDER BY c.id""",
        (DEMO_ID_GLOB, DEMO_PHONE_GLOB),
    ).fetchall()
    return [row[0] for row in rows]


def remove_demo_data(connection: sqlite3.Connection) -> int:
    ids = demo_customer_ids(connection)
    connection.execute("CREATE TEMP TABLE IF NOT EXISTS demo_ids (id TEXT PRIMARY KEY)")
    connection.execute("DELETE FROM demo_ids")
    connection.executemany("INSERT INTO demo_ids VALUES (?)", [(i,) for i in ids])
    existing = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    for table in ("behaviors", "product_preferences", "recommendation_logs", "abnormal_records",
                  "archived_behaviors", "archived_product_preferences"):
        if table in existing:
            connection.execute(f"DELETE FROM {table} WHERE customer_id IN (SELECT id FROM demo_ids)")
    connection.execute("DELETE FROM users WHERE id IN (SELECT id FROM demo_ids) OR customer_id IN (SELECT id FROM demo_ids)")
    connection.execute("DELETE FROM customers WHERE id IN (SELECT id FROM demo_ids)")
    connection.execute("DROP TABLE demo_ids")
    return len(ids)


def resolve_password() -> tuple[str, bool]:
    """Return (password, from_environment)."""
    env_password = os.environ.get(PASSWORD_ENV)
    if env_password:
        return env_password, True
    return secrets.token_urlsafe(10), False


def write_credentials(credential_file: Path, password: str, count: int) -> None:
    descriptor = os.open(credential_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump({"password": password, "phones": f"{PHONE_BASE}–{PHONE_BASE + count - 1}", "count": count},
                  file, ensure_ascii=False, indent=2)
        file.write("\n")
    try:
        credential_file.chmod(0o600)
    except OSError:
        pass


def insert_accounts(connection: sqlite3.Connection, count: int, seed: int, password: str, products: list[dict],
                    now: datetime, warnings: list[str]) -> dict:
    rng = random.Random(seed)
    today = now.date()
    cores = [select_core(group, products, today, warnings) for group in GROUPS]
    product_ids = sorted(product["id"] for product in products)
    behavior_total = 0
    group_counts = {group["name"]: 0 for group in GROUPS}
    started = time.monotonic()
    for index in range(1, count + 1):
        group_index = (index - 1) % len(GROUPS)
        group, core = GROUPS[group_index], cores[group_index]
        account = make_account(index, group, rng, today)
        plan = account["plan"]
        salt = secrets.token_hex(16)
        connection.execute(
            """INSERT INTO customers (id, name, tag, age, income, risk_tolerance, preferred_term, investment_budget, summary,
               plan_source, retirement_status, pension_type, target_date, emergency_reserve, can_accept_loss, preferred_category,
               plan_completed, personal_pension_status, personal_pension_budget)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'demo', ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (account["id"], account["name"], account["tag"], account["age"], account["income"], plan["risk_tolerance"],
             plan["preferred_term"], plan["investment_budget"], account["summary"], plan["retirement_status"], plan["pension_type"],
             plan["target_date"], plan["emergency_reserve"], plan["can_accept_loss"], plan["preferred_category"],
             plan["personal_pension_status"], plan["personal_pension_budget"]),
        )
        connection.execute(
            """INSERT INTO users (id, username, phone, password_salt, password_hash, name, role, customer_id, tag)
               VALUES (?, ?, ?, ?, ?, ?, 'customer', ?, ?)""",
            (account["id"], account["phone"], account["phone"], salt, database.hash_password(password, salt),
             account["name"], account["id"], account["tag"]),
        )
        events = make_behaviors(account, core, product_ids, rng, now)
        connection.executemany(
            "INSERT INTO behaviors (customer_id, product_id, behavior_type, session_id, created_at) VALUES (?, ?, ?, ?, ?)",
            [(account["id"], product_id, kind, session_id, stamp_text(stamp)) for kind, product_id, stamp, session_id in events],
        )
        connection.executemany(
            "INSERT INTO product_preferences (customer_id, product_id, favorite, avoided, updated_at) VALUES (?, ?, ?, ?, ?)",
            [(account["id"], product_id, favorite, avoided, stamp_text(stamp)) for product_id, favorite, avoided, stamp in replay_preferences(events)],
        )
        behavior_total += len(events)
        group_counts[group["name"]] += 1
        if index % 200 == 0:
            print(f"  已生成 {index}/{count} 个账号（{time.monotonic() - started:.0f} 秒）", flush=True)
    return {"behaviors": behavior_total, "groups": group_counts, "cores": {g["name"]: c for g, c in zip(GROUPS, cores)}}


def refresh_rules(customer_id: str | None) -> dict:
    """Refresh association_rules with the recommendation endpoint's own mining and saving code.

    Without a customer (after --remove) only the rule table is rebuilt, so no recommendation log is written
    for accounts that were not generated here.
    """
    if customer_id:
        server.recommendations(customer_id)
    else:
        rules = build_rules(database.get_behaviors(), {product["id"] for product in database.get_products()})
        now = database.utc_now()
        with database.connect() as connection:
            connection.execute("DELETE FROM association_rules")
            connection.executemany(
                "INSERT INTO association_rules VALUES (?, ?, ?, ?, ?)",
                [(rule["product_a"], rule["product_b"], rule["rule_type"], json.dumps(rule, ensure_ascii=False), now) for rule in rules],
            )
    with database.connect() as connection:
        rows = connection.execute("SELECT rule_type, COUNT(*) FROM association_rules GROUP BY rule_type").fetchall()
    counts = {row[0]: row[1] for row in rows}
    return {"positive": counts.get("positive", 0), "negative": counts.get("negative", 0)}


def generate(count: int, seed: int, password: str, now: datetime | None = None) -> dict:
    """Insert `count` demo accounts into the database currently selected in database.DB_FILE."""
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    warnings: list[str] = []
    products = database.get_products()
    with database.connect() as connection:
        if demo_customer_ids(connection):
            raise SystemExit("数据库中已有演示账号；请加 --reset 重新生成，或用 --remove 清除")
        wanted = [str(PHONE_BASE + offset) for offset in range(count)]
        taken = [row[0] for row in connection.execute(
            f"SELECT phone FROM users WHERE phone IN ({', '.join('?' for _ in wanted)}) ORDER BY phone", wanted)]
        if taken:
            raise SystemExit(f"演示号段内的手机号已被其他账号占用（{taken[0]} 等 {len(taken)} 个），未生成；请减小 --count 或先处理这些账号")
        summary = insert_accounts(connection, count, seed, password, products, now, warnings)
    summary["warnings"] = warnings
    return summary


# ---------------------------------------------------------------- command line

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成或清除演示客户账号")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help=f"账号数量，1–{MAX_COUNT}，默认 {DEFAULT_COUNT}")
    parser.add_argument("--reset", action="store_true", help="先清除旧的演示数据再生成")
    parser.add_argument("--remove", action="store_true", help="只清除演示数据，不生成")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"随机种子，默认 {DEFAULT_SEED}")
    parser.add_argument("--db", type=Path, default=None, help="数据库路径，默认使用 database.DB_FILE")
    args = parser.parse_args(argv)
    if not 1 <= args.count <= MAX_COUNT:
        parser.error(f"--count 需在 1 到 {MAX_COUNT} 之间（手机号段只有 {MAX_COUNT} 个）")
    if args.remove and args.reset:
        parser.error("--remove 与 --reset 不能同时使用")
    return args


def database_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    credential_file = DEMO_CREDENTIAL_FILE
    if args.db is not None:
        database.DB_FILE = args.db.expanduser().resolve()
    if database.DB_FILE.resolve() != DEFAULT_DB_FILE.resolve():
        # A database other than the real one keeps its credential files next to itself, never in data/.
        database.CREDENTIAL_FILE = database.DB_FILE.with_name(database.DB_FILE.name + ".credentials.json")
        credential_file = database.DB_FILE.with_name(database.DB_FILE.name + ".demo-credentials.json")
    database.DB_FILE.parent.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    size_before = database_size(database.DB_FILE)
    database.init_db()

    if args.remove or args.reset:
        with database.connect() as connection:
            removed = remove_demo_data(connection)
        print(f"已清除演示账号 {removed} 个及其行为、偏好、推荐记录")
        if args.remove:
            if credential_file.exists():
                credential_file.unlink()
            rules = refresh_rules(None)
            print(f"剩余规则：正向 {rules['positive']}，反向 {rules['negative']}")
            print(f"数据库大小：{size_before:,} → {database_size(database.DB_FILE):,} 字节；耗时 {time.monotonic() - started:.1f} 秒")
            return

    password, from_environment = resolve_password()
    summary = generate(args.count, args.seed, password)
    if from_environment:
        print(f"演示账号密码取自环境变量 {PASSWORD_ENV}")
    else:
        write_credentials(credential_file, password, args.count)
        print(f"演示账号密码已写入 {credential_file}")
    for warning in summary["warnings"]:
        print(f"警告：{warning}")

    rules = refresh_rules("D0001")
    with database.connect() as connection:
        accounts = len(demo_customer_ids(connection))
    print(f"账号数：{accounts}；行为数：{summary['behaviors']}")
    print("各人群人数：" + "，".join(f"{name} {number}" for name, number in summary["groups"].items()))
    print(f"挖出的规则数：正向 {rules['positive']}，反向 {rules['negative']}")
    size_after = database_size(database.DB_FILE)
    print(f"数据库大小：{size_before:,} → {size_after:,} 字节（{size_after - size_before:+,}）；耗时 {time.monotonic() - started:.1f} 秒")


if __name__ == "__main__":
    main()
