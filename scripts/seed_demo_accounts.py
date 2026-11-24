#!/usr/bin/env python3
"""Generate or remove demo customer accounts that show the recommendation algorithm at work.

Demo accounts are recognisable by id D0001..D1000, customers.plan_source = 'demo',
phone 13900010000..13900010999 and a persona name in customers.tag.

Products are grouped into affinity clusters by risk, term and type. Each persona favorites products
from one main cluster and one or two adjacent clusters; neighbouring personas share adjacent clusters,
so the pair rules link products across clusters. Members later browse a cluster that conflicts with
their style and mark some of it as not interested. A coverage pass then makes sure every product with
real product is favorited often enough, and together with enough other products, to take part in the
pair rules.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import secrets
import sqlite3
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import database  # noqa: E402
import server  # noqa: E402
from recommender import MIN_POSITIVE_SUPPORT, assess, build_rules  # noqa: E402


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
MAX_STEP_SECONDS = 24 * 3600
BEHAVIOR_MIN, BEHAVIOR_MAX = 20, 30
FAVORITE_MIN, FAVORITE_MAX = 7, 10
MAIN_SHARE = 0.7
ANCHOR_PROBABILITY = 0.65
AVOID_JOIN_PROBABILITY = 0.9
AVOID_RANGE = (2, 4)
AVOID_PROBABILITY = 0.85
RESTORE_PROBABILITY = 0.05
UNFAVORITE_PROBABILITY = 0.05
# Coverage per 1000 accounts, scaled with --count: favorites per product, and the number of other
# products each one must share with at least COVERAGE_PAIR_USERS favoriting customers. The pair floor
# is raised to the positive-rule support floor when that is higher.
COVERAGE_FAVORITES = 40
COVERAGE_PAIR_USERS = 20
COVERAGE_PARTNERS = 3

SURNAMES = "张王李赵刘陈杨黄周吴徐孙马朱胡郭何林高罗郑梁谢宋唐韩曹许邓冯"
HONORIFICS = ("阿姨", "叔叔", "大姐", "大哥", "老师", "师傅", "女士", "先生")

# Affinity clusters by risk × term × type. A product belongs to the first cluster it matches;
# simulated products belong to none. "general" excludes personal-pension and insurance products.
CLUSTERS = (
    {"name": "R1 定期存款", "types": ("定期存款",)},
    {"name": "R1 大额存单与国债", "types": ("个人大额存单", "储蓄国债")},
    {"name": "R1 现金管理", "categories": ("现金管理",), "risk": 1},
    {"name": "R2 短期理财", "risk": 2, "max_term": 183, "general": True},
    {"name": "R2 固收", "risk": 2, "general": True},
    {"name": "R2 个人养老金", "eligibility": "personal_pension"},
    {"name": "R3 混合与债基", "risk": 3, "general": True},
    {"name": "保险", "categories": ("保险",)},
)

# Catalog selectors never name product ids. Anchors are (cluster, start, stop) slices of the cluster's
# products that pass the group's reference plan, in id order; members favorite each anchor with
# ANCHOR_PROBABILITY, so every product the group likes is favorited together with them.
GROUPS = (
    {
        "name": "保本储蓄", "age": (55, 72), "retirement": ("已退休", "临近退休"), "risk": (1, 1), "loss": 0.0,
        "term": (730, 1825), "budget": (100000, 800000), "category": "储蓄",
        "main": "R1 定期存款", "adjacent": ("R1 大额存单与国债",), "anchors": (),
        "avoid": ("R3 混合与债基",),
    },
    {
        "name": "短期备用", "age": (35, 62), "retirement": ("其他", "临近退休"), "risk": (1, 2), "loss": 1.0,
        "term": (90, 365), "budget": (50000, 300000), "category": "现金管理",
        "main": "R1 现金管理", "adjacent": ("R2 短期理财",), "anchors": (("R1 现金管理", 1, 3), ("R1 定期存款", 9, 10)),
        "avoid": ("R1 大额存单与国债",),
    },
    {
        "name": "稳健养老", "age": (58, 75), "retirement": ("已退休", "已退休", "临近退休"), "risk": (2, 2), "loss": 1.0,
        "term": (365, 1095), "budget": (200000, 1000000), "category": "养老理财",
        "main": "R2 个人养老金", "adjacent": ("R1 定期存款", "R2 固收"), "anchors": (("R1 定期存款", 3, 5),),
        "avoid": ("R1 大额存单与国债",),
    },
    {
        "name": "家庭保障", "age": (35, 55), "retirement": ("其他",), "risk": (1, 2), "loss": 0.2,
        "term": (1095, 3650), "budget": (100000, 600000), "category": "保险",
        "main": "R1 大额存单与国债", "adjacent": ("R1 定期存款", "保险"), "anchors": (("R1 定期存款", 0, 3),),
        "avoid": ("R1 现金管理",),
    },
    {
        "name": "稳健增值", "age": (45, 62), "retirement": ("临近退休", "其他"), "risk": (2, 3), "loss": 1.0,
        "term": (730, 1460), "budget": (200000, 800000), "category": "全部",
        "main": "R2 固收", "adjacent": ("R1 定期存款", "R3 混合与债基"), "anchors": (("R1 定期存款", 5, 8),),
        "avoid": ("R1 大额存单与国债",),
    },
    {
        "name": "稳健进取", "age": (40, 58), "retirement": ("其他", "临近退休"), "risk": (3, 3), "loss": 1.0,
        "term": (1095, 1825), "budget": (300000, 1500000), "category": "全部",
        "main": "R3 混合与债基", "adjacent": ("R1 现金管理", "R1 定期存款"),
        "anchors": (("R1 现金管理", 3, 5), ("R1 定期存款", 8, 9)),
        "avoid": ("R1 大额存单与国债",),
    },
)


def hours(value: float) -> timedelta:
    return timedelta(hours=value)


# ---------------------------------------------------------------- catalog selection

def _matches(product: dict, spec: dict) -> bool:
    if product.get("is_simulated"):
        return False
    if spec.get("types") and product["product_type"] not in spec["types"]:
        return False
    if spec.get("categories") and product["category"] not in spec["categories"]:
        return False
    if spec.get("risk") is not None and product.get("risk_level") != spec["risk"]:
        return False
    if spec.get("eligibility") and product.get("eligibility") != spec["eligibility"]:
        return False
    if spec.get("general") and (product.get("eligibility") or product["category"] == "保险"):
        return False
    if spec.get("max_term") is not None and (product.get("term_days") is None or product["term_days"] > spec["max_term"]):
        return False
    return True


def cluster_of(product: dict) -> str | None:
    return next((cluster["name"] for cluster in CLUSTERS if _matches(product, cluster)), None)


def reference_customer(group: dict, today: date) -> dict:
    """A mid-range plan for the group, used to choose products that suit it."""
    budget = round((group["budget"][0] + group["budget"][1]) / 2, -4)
    days = (group["term"][0] + group["term"][1]) // 2
    return {"id": "reference", "plan_completed": 1, "risk_tolerance": group["risk"][1], "can_accept_loss": int(group["loss"] >= 0.5),
            "investment_budget": budget, "emergency_reserve": round(budget * 0.15, -3), "preferred_term": days,
            "target_date": (today + timedelta(days=days)).isoformat(), "preferred_category": group["category"],
            "retirement_status": group["retirement"][0], "personal_pension_status": "未确认", "personal_pension_budget": 0}


def build_contexts(products: list[dict], today: date, warnings: list[str]) -> list[dict]:
    """Resolve each group's clusters and anchors against the live catalog."""
    ordered = sorted(products, key=lambda product: product["id"])
    members: dict[str, list[dict]] = {cluster["name"]: [] for cluster in CLUSTERS}
    for product in ordered:
        name = cluster_of(product)
        if name:
            members[name].append(product)
        elif not product.get("is_simulated") and product.get("risk_level") is not None:
            warnings.append(f"商品 {product['id']} 不属于任何亲和簇，不参与收藏")
    contexts = []
    for group in GROUPS:
        reference = reference_customer(group, today)
        anchors: list[int] = []
        for cluster, start, stop in group["anchors"]:
            passing = [p["id"] for p in members[cluster] if assess(p, reference, today)["tier"] == "pass"]
            if not passing:
                warnings.append(f"{group['name']}：「{cluster}」中没有符合参考计划的商品，缺少常收藏商品")
                continue
            if len(passing) < stop:
                warnings.append(f"{group['name']}：「{cluster}」中符合参考计划的商品不足（需要第 {start + 1}–{stop} 个，实际 {len(passing)} 个），循环选用")
            for position in range(start, stop):
                product_id = passing[position % len(passing)]
                if product_id not in anchors:
                    anchors.append(product_id)
        main = [p["id"] for p in members[group["main"]]]
        adjacent = [p["id"] for name in group["adjacent"] for p in members[name] if p["id"] not in main]
        avoid = [p["id"] for name in group["avoid"] for p in members[name] if p["id"] not in anchors]
        if not main:
            warnings.append(f"{group['name']}：主簇「{group['main']}」在目录中没有商品，改从相邻簇收藏")
        if not avoid:
            warnings.append(f"{group['name']}：风格冲突簇在目录中没有商品，不生成“不感兴趣”记录")
        contexts.append({
            "group": group, "anchors": anchors, "main": main, "adjacent": adjacent, "avoid": avoid,
            "main_pool": [pid for pid in main if pid not in anchors],
            "adjacent_pool": [pid for pid in adjacent if pid not in anchors],
            "clusters": {group["main"], *group["adjacent"]},
        })
    return contexts


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


def plan_interests(context: dict, rng: random.Random) -> dict:
    """Pick the products one member favorites and the conflicting products they later reject."""
    wanted = rng.randint(FAVORITE_MIN, FAVORITE_MAX)
    main_quota = round(wanted * MAIN_SHARE)
    hits = [pid for pid in context["anchors"] if rng.random() < ANCHOR_PROBABILITY]
    favorites = list(hits)
    main_hits = sum(pid in context["main"] for pid in hits)
    main_pool = context["main_pool"]
    favorites += rng.sample(main_pool, min(len(main_pool), max(0, main_quota - main_hits)))
    adjacent_pool = [pid for pid in context["adjacent_pool"] if pid not in favorites]
    favorites += rng.sample(adjacent_pool, min(len(adjacent_pool), max(0, wanted - len(favorites))))
    # A small main cluster leaves the quota short: fill it from whatever the group still likes.
    rest = [pid for pid in main_pool + context["adjacent_pool"] if pid not in favorites]
    favorites += rng.sample(rest, min(len(rest), max(0, wanted - len(favorites))))
    rng.shuffle(favorites)
    avoid: list[int] = []
    if rng.random() < AVOID_JOIN_PROBABILITY:
        pool = [pid for pid in context["avoid"] if pid not in favorites]
        avoid = rng.sample(pool, min(len(pool), rng.randint(*AVOID_RANGE)))
    return {"favorites": favorites, "avoid": avoid}


def _pair(left: int, right: int) -> tuple[int, int]:
    return (left, right) if left < right else (right, left)


def coverage_floors(count: int, rule_users: int) -> tuple[int, int]:
    scale = count / DEFAULT_COUNT
    favorites = max(1, math.ceil(COVERAGE_FAVORITES * scale))
    pair_users = max(1, math.ceil(COVERAGE_PAIR_USERS * scale), math.ceil(round(MIN_POSITIVE_SUPPORT * rule_users, 6)))
    return favorites, pair_users


def ensure_coverage(interests: list[dict], group_indexes: list[int], contexts: list[dict], products: list[dict],
                    count: int, rule_users: int, rng: random.Random) -> dict:
    """Top up favorites until each clustered (real) product reaches the coverage floors.

    A short product is added to members of the groups whose main (then adjacent) cluster contains it,
    preferring members who already favorite its strongest partners. A member at FAVORITE_MAX gives up
    their most common other favorite, provided it stays above the floor.
    """
    favorite_floor, pair_floor = coverage_floors(count, rule_users)
    favorites = Counter(pid for interest in interests for pid in interest["favorites"])
    pairs: Counter = Counter()
    for interest in interests:
        chosen = sorted(interest["favorites"])
        for i, left in enumerate(chosen):
            for right in chosen[i + 1:]:
                pairs[(left, right)] += 1
    catalog_ids = sorted(product["id"] for product in products)

    def strong_partners(product_id: int) -> int:
        return sum(pairs[_pair(product_id, other)] >= pair_floor for other in catalog_ids if other != product_id)

    def add(member: int, product_id: int) -> None:
        chosen = interests[member]["favorites"]
        for other in chosen:
            pairs[_pair(product_id, other)] += 1
        chosen.append(product_id)
        favorites[product_id] += 1

    def drop(member: int, product_id: int) -> None:
        chosen = interests[member]["favorites"]
        chosen.remove(product_id)
        favorites[product_id] -= 1
        for other in chosen:
            pairs[_pair(product_id, other)] -= 1

    required = [p for p in sorted(products, key=lambda p: p["id"]) if cluster_of(p)]
    added = 0
    for product in required:
        product_id = product["id"]
        cluster = cluster_of(product)
        homes = [i for i, context in enumerate(contexts) if context["group"]["main"] == cluster]
        homes += [i for i, context in enumerate(contexts) if cluster in context["group"]["adjacent"] and i not in homes]
        members = [m for m, g in enumerate(group_indexes) if g in homes]
        rng.shuffle(members)
        members.sort(key=lambda m: homes.index(group_indexes[m]))
        while favorites[product_id] < favorite_floor or strong_partners(product_id) < COVERAGE_PARTNERS:
            ranked = sorted((other for other in catalog_ids if other != product_id),
                            key=lambda other: (-pairs[_pair(product_id, other)], -favorites[other], other))
            targets = set(ranked[:COVERAGE_PARTNERS])
            best, best_score = None, 0
            for member in members:
                interest = interests[member]
                if product_id in interest["favorites"] or product_id in interest["avoid"]:
                    continue
                score = len(targets & set(interest["favorites"]))
                if len(interest["favorites"]) >= FAVORITE_MAX:
                    anchors = set(contexts[group_indexes[member]]["anchors"])
                    spare = [pid for pid in interest["favorites"] if pid not in targets and pid not in anchors
                             and favorites[pid] > favorite_floor + 10]
                    if not spare:
                        continue
                if best is None or score > best_score:
                    best, best_score = member, score
                    if score == len(targets):
                        break
            if best is None:
                break
            if len(interests[best]["favorites"]) >= FAVORITE_MAX:
                anchors = set(contexts[group_indexes[best]]["anchors"])
                spare = [pid for pid in interests[best]["favorites"] if pid not in targets and pid not in anchors
                         and favorites[pid] > favorite_floor + 10]
                drop(best, max(spare, key=lambda pid: (favorites[pid], pid)))
            add(best, product_id)
            added += 1
    short = [p["id"] for p in required
             if favorites[p["id"]] < favorite_floor or strong_partners(p["id"]) < COVERAGE_PARTNERS]
    return {"products": len(required), "short": short, "added": added,
            "favorite_floor": favorite_floor, "pair_floor": pair_floor}


def make_behaviors(account: dict, interest: dict, context: dict, product_ids: list[int], rng: random.Random,
                   now: datetime) -> list[tuple[str, int, datetime, str]]:
    """Ordered (behavior_type, product_id, time, session_id) events for one account.

    Favorites come first, so every rejection follows the favorites: favorite A, then view B, then mark B
    as not interested.
    """
    favorites, rejected = interest["favorites"], interest["avoid"]
    steps: list[tuple[str, int]] = []
    for product_id in favorites:
        steps += [("view", product_id), ("favorite", product_id)]
    avoided = []
    for product_id in rejected:
        steps.append(("view", product_id))
        if rng.random() < AVOID_PROBABILITY:
            steps.append(("avoid", product_id))
            avoided.append(product_id)
    if avoided and rng.random() < RESTORE_PROBABILITY * len(avoided):
        steps.append(("restore", rng.choice(avoided)))
    if len(favorites) > FAVORITE_MIN - 1 and rng.random() < UNFAVORITE_PROBABILITY:
        steps.append(("unfavorite", rng.choice(sorted(favorites))))
    # Browsing: views of products the group likes but this member did not favorite, anywhere in the timeline.
    taken = set(favorites) | set(rejected)
    browse = [pid for pid in context["main"] + context["adjacent"] if pid not in taken]
    browse = browse or [pid for pid in product_ids if pid not in taken and pid not in context["avoid"]]
    target = rng.randint(BEHAVIOR_MIN, BEHAVIOR_MAX)
    while len(steps) < target and browse:
        steps.insert(rng.randint(0, len(steps)), ("view", rng.choice(browse)))

    # Times increase along the steps and stay inside the last 60 days, with 5 minutes to 24 hours between steps.
    clock = now - timedelta(days=rng.randint(32, HISTORY_DAYS - 1), seconds=rng.randint(0, 86399))
    events, session = [], 0
    for kind, product_id in steps:
        gap = timedelta(seconds=rng.randint(300, MAX_STEP_SECONDS)) if events else timedelta()
        if gap > hours(4):
            session += 1
        clock += gap
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
                    now: datetime, warnings: list[str], rule_users: int | None = None) -> dict:
    rng = random.Random(seed)
    today = now.date()
    contexts = build_contexts(products, today, warnings)
    product_ids = sorted(product["id"] for product in products)
    group_indexes = [(index - 1) % len(GROUPS) for index in range(1, count + 1)]
    accounts = [make_account(index, GROUPS[group_indexes[index - 1]], rng, today) for index in range(1, count + 1)]
    interests = [plan_interests(contexts[group_index], rng) for group_index in group_indexes]
    coverage = ensure_coverage(interests, group_indexes, contexts, products, count, rule_users or count, rng)
    behavior_total = 0
    group_counts = {group["name"]: 0 for group in GROUPS}
    started = time.monotonic()
    for index, (account, interest, group_index) in enumerate(zip(accounts, interests, group_indexes), start=1):
        group = GROUPS[group_index]
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
        events = make_behaviors(account, interest, contexts[group_index], product_ids, rng, now)
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
    return {"behaviors": behavior_total, "groups": group_counts, "coverage": coverage,
            "contexts": {context["group"]["name"]: context for context in contexts}}


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
        # Customers outside the demo range also count towards the rules' support denominator.
        rule_users = count + connection.execute(
            "SELECT COUNT(DISTINCT customer_id) FROM behaviors WHERE customer_id NOT GLOB ?", (DEMO_ID_GLOB,)).fetchone()[0]
        summary = insert_accounts(connection, count, seed, password, products, now, warnings, rule_users)
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
    coverage = summary["coverage"]
    short = coverage["short"]
    print(f"收藏覆盖自检：{coverage['products']} 款真实商品中 {coverage['products'] - len(short)} 款达到"
          f"每款 ≥ {coverage['favorite_floor']} 人收藏、与 ≥ {COVERAGE_PARTNERS} 款商品共同收藏 ≥ {coverage['pair_floor']} 人；"
          f"补充收藏 {coverage['added']} 条" + (f"；未达到：{short}" if short else ""))
    print(f"挖出的规则数：正向 {rules['positive']}，反向 {rules['negative']}")
    size_after = database_size(database.DB_FILE)
    print(f"数据库大小：{size_before:,} → {size_after:,} 字节（{size_after - size_before:+,}）；耗时 {time.monotonic() - started:.1f} 秒")


if __name__ == "__main__":
    main()
