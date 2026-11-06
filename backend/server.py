#!/usr/bin/env python3
"""SQLite-backed API and static server for the financial product platform."""

from __future__ import annotations

import itertools
import json
import mimetypes
import re
import secrets
import sys
from collections import Counter
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import (  # noqa: E402
    get_admin_overview,
    get_behaviors,
    get_customer,
    get_customers,
    create_customer_account,
    get_products,
    get_user,
    get_user_by_credentials,
    get_users_public,
    init_db,
)


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
STATE = {"abnormal_records": []}
SESSIONS: dict[str, str] = {}


def public_user(user: dict) -> dict:
    return {key: user.get(key) for key in ("id", "username", "name", "role", "customer_id", "tag")}


def json_response(handler: SimpleHTTPRequestHandler, payload: object, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def product_map() -> dict[int, dict]:
    return {item["id"]: item for item in get_products()}


def customer_map() -> dict[str, dict]:
    return {item["id"]: item for item in get_customers()}


def pair_rules() -> list[dict]:
    """Build pairwise association rules from presence-only customer baskets."""
    customers = customer_map()
    products = product_map()
    behaviors = get_behaviors()
    baskets: list[set[int]] = []
    for customer_id in customers:
        basket = {row["product_id"] for row in behaviors if row["customer_id"] == customer_id}
        if basket:
            baskets.append(basket)
    total = max(len(baskets), 1)
    item_count = Counter(product for basket in baskets for product in basket)
    frequent = {product for product, count in item_count.items() if count / total >= 0.10}
    result: list[dict] = []
    for left, right in itertools.permutations(sorted(frequent), 2):
        both = sum(left in basket and right in basket for basket in baskets)
        if not both:
            continue
        support = both / total
        confidence = both / item_count[left]
        right_support = item_count[right] / total
        interest = confidence / right_support if right_support else 0
        if confidence >= 0.45 or interest > 1:
            result.append(
                {
                    "product_a": left,
                    "product_b": right,
                    "product_a_name": products[left]["name"],
                    "product_b_name": products[right]["name"],
                    "support": round(support, 3),
                    "confidence": round(confidence, 3),
                    "interest": round(interest, 3),
                    "is_valid": True,
                }
            )
    return sorted(result, key=lambda row: (row["confidence"], row["interest"]), reverse=True)


def product_counts() -> Counter:
    return Counter(row["product_id"] for row in get_behaviors())


def feature_ranges(products: list[dict], customer: dict) -> tuple[dict, list[str], dict]:
    fields = ("risk_level", "term_days", "min_amount", "expected_return")
    values = {
        "risk_level": [product["risk_level"] for product in products] + [customer["risk_tolerance"]],
        "term_days": [product["term_days"] for product in products] + [customer["preferred_term"]],
        "min_amount": [product["min_amount"] for product in products] + [customer["investment_budget"]],
        "expected_return": [product["expected_return"] for product in products] + [max(0, customer["risk_tolerance"] * 2.2)],
    }
    source_ranges = {field: (min(values[field]), max(values[field])) for field in fields}
    base_ranges = {
        "risk_level": (min(product["risk_level"] for product in products), max(product["risk_level"] for product in products)),
        "term_days": (min(product["term_days"] for product in products), max(product["term_days"] for product in products)),
        "min_amount": (min(product["min_amount"] for product in products), max(product["min_amount"] for product in products)),
        "expected_return": (min(product["expected_return"] for product in products), max(product["expected_return"] for product in products)),
    }
    customer_values = {
        "risk_level": customer["risk_tolerance"],
        "term_days": customer["preferred_term"],
        "min_amount": customer["investment_budget"],
        "expected_return": max(0, customer["risk_tolerance"] * 2.2),
    }
    out_of_range = []
    for field in fields:
        low, high = base_ranges[field]
        if customer_values[field] < low or customer_values[field] > high:
            out_of_range.append(field)
    return source_ranges, out_of_range, base_ranges


def nearest_scores(customer: dict, products: list[dict], range_products: list[dict] | None = None) -> tuple[dict[int, dict], list[str], float]:
    weights = {"risk_level": 0.30, "term_days": 0.25, "min_amount": 0.20, "expected_return": 0.15}
    ranges, out_of_range, base_ranges = feature_ranges(range_products or products, customer)
    customer_values = {
        "risk_level": customer["risk_tolerance"],
        "term_days": customer["preferred_term"],
        "min_amount": customer["investment_budget"],
        "expected_return": max(0, customer["risk_tolerance"] * 2.2),
    }

    def normalized(value: float, field: str) -> float:
        low, high = ranges[field]
        return (value - low) / (high - low) if high != low else 0.0

    scores: dict[int, dict] = {}
    raw_distances: list[float] = []
    for product in products:
        distance = 0.0
        raw_distance = 0.0
        for field, weight in weights.items():
            product_value = product[field]
            distance += weight * abs(normalized(customer_values[field], field) - normalized(product_value, field))
            base_low, base_high = base_ranges[field]
            customer_base = (customer_values[field] - base_low) / (base_high - base_low) if base_high != base_low else 0.0
            product_base = (product_value - base_low) / (base_high - base_low) if base_high != base_low else 0.0
            raw_distance += weight * abs(max(0, min(1, customer_base)) - max(0, min(1, product_base)))
        raw_distances.append(raw_distance)
        scores[product["id"]] = {"distance": round(distance, 3), "similarity": round(max(0, 1 - distance), 3)}
    dynamic_minimum = min((item["distance"] for item in scores.values()), default=1.0)
    novelty_penalty = min(0.95, 0.30 + 0.10 * len(out_of_range)) if out_of_range else 0.0
    minimum = round(max(dynamic_minimum, min(raw_distances, default=1.0), novelty_penalty), 3)
    return scores, out_of_range, minimum


def record_abnormal(customer: dict, fields: list[str], minimum_distance: float) -> None:
    if not fields and minimum_distance <= 0.6:
        return
    key = (customer["id"], tuple(fields), round(minimum_distance, 3))
    existing = {(row["customer_id"], tuple(row["out_of_range_fields"]), row["min_distance"]) for row in STATE["abnormal_records"]}
    if key in existing:
        return
    STATE["abnormal_records"].append(
        {
            "customer_id": customer["id"],
            "customer_name": customer["name"],
            "reason": "客户特征超出样例范围" if fields else "最近邻最小距离超过 0.6",
            "out_of_range_fields": fields,
            "min_distance": round(minimum_distance, 3),
        }
    )


def product_is_eligible(product: dict, customer: dict) -> bool:
    return product["status"] == "在售" and product["risk_level"] <= customer["risk_tolerance"] and product["min_amount"] <= customer["investment_budget"]


def recommendations(customer_id: str) -> dict:
    customers = customer_map()
    products = product_map()
    customer = customers.get(customer_id) or customers.get("U001")
    all_products = list(products.values())
    eligible = [product for product in all_products if product_is_eligible(product, customer)]
    rules = pair_rules()
    behaviors = get_behaviors()
    interaction_ids = {row["product_id"] for row in behaviors if row["customer_id"] == customer["id"]}
    nearest, fields, minimum_distance = nearest_scores(customer, eligible or all_products, all_products)
    record_abnormal(customer, fields, minimum_distance)
    counts = product_counts()
    max_count = max(counts.values(), default=1)
    association_by_product: dict[int, dict] = {}
    for rule in rules:
        if rule["product_a"] in interaction_ids and rule["product_b"] not in interaction_ids:
            current = association_by_product.get(rule["product_b"])
            if not current or rule["confidence"] > current["confidence"]:
                association_by_product[rule["product_b"]] = rule
    use_fallback = minimum_distance > 0.6
    scored: list[dict] = []
    for product in eligible:
        neighbor = nearest[product["id"]]
        rule = association_by_product.get(product["id"])
        association_score = 0.0
        if rule:
            association_score = min(1.0, 0.65 * rule["confidence"] + 0.35 * min(rule["interest"] / 2, 1))
        similarity_score = neighbor["similarity"]
        hot_score = counts[product["id"]] / max_count
        final_score = 0.60 * association_score + 0.40 * similarity_score
        if use_fallback and not rule:
            final_score = 0.25 * similarity_score + 0.75 * hot_score
        scored.append({**product, "association_score": round(association_score, 3), "similarity_score": round(similarity_score, 3), "distance": neighbor["distance"], "hot_score": round(hot_score, 3), "final_score": round(final_score, 3), "algorithm_source": "关联规则 + 最近邻" if rule else ("热门产品兜底" if use_fallback else "最近邻"), "reason": "" if not rule else f"与您关注的{products[rule['product_a']]['name']}存在关联。", "rule": rule})
    scored.sort(key=lambda item: (item["final_score"], item["similarity_score"]), reverse=True)
    return {"customer": customer, "items": scored[:3], "meta": {"min_distance": round(minimum_distance, 3), "distance_threshold": 0.6, "out_of_range_fields": fields, "association_rule_count": len(rules), "fallback_used": use_fallback, "weights": {"association": 0.6, "nearest": 0.4}}}


def read_json(handler: SimpleHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length) if length else b"{}"
    return json.loads(raw.decode("utf-8"))


def request_user(handler: SimpleHTTPRequestHandler) -> dict | None:
    authorization = handler.headers.get("Authorization", "")
    token = authorization.removeprefix("Bearer ").strip()
    user_id = SESSIONS.get(token)
    return get_user(user_id) if user_id else None


class Handler(SimpleHTTPRequestHandler):
    server_version = "FinancialProductPlatform/0.2"

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-User-Role")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        try:
            if parsed.path == "/api/auth/me":
                user = request_user(self)
                if not user:
                    json_response(self, {"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                else:
                    json_response(self, {"user": public_user(user)})
                return
            if parsed.path == "/api/auth/users":
                if not request_user(self):
                    json_response(self, {"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                else:
                    json_response(self, {"items": get_users_public()})
                return
            if parsed.path == "/api/admin/overview":
                user = request_user(self)
                if not user or user["role"] != "admin":
                    json_response(self, {"error": "admin access required"}, HTTPStatus.FORBIDDEN)
                else:
                    json_response(self, get_admin_overview())
                return
            if parsed.path == "/api/products":
                items = get_products({"search": query.get("search", ""), "category": query.get("category", "全部"), "risk": query.get("risk", "全部"), "status": query.get("status", "全部")})
                json_response(self, {"items": items, "total": len(items)})
                return
            if parsed.path == "/api/customers":
                json_response(self, {"items": get_customers()})
                return
            if parsed.path == "/api/recommendations":
                json_response(self, recommendations(query.get("customer_id", "U001")))
                return
            if parsed.path == "/api/association-rules":
                json_response(self, {"items": pair_rules()})
                return
            if parsed.path == "/api/abnormal-records":
                json_response(self, {"items": STATE["abnormal_records"]})
                return
            if parsed.path == "/api/stats":
                overview = get_admin_overview()
                json_response(self, {**overview["stats"], "rules": len(pair_rules()), "abnormal": len(STATE["abnormal_records"])})
                return
            self.serve_frontend(parsed.path)
        except Exception as error:
            json_response(self, {"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = read_json(self)
            if parsed.path == "/api/auth/login":
                user = get_user_by_credentials(payload.get("username", ""), payload.get("password", ""))
                if not user:
                    json_response(self, {"error": "账号或密码不正确"}, HTTPStatus.UNAUTHORIZED)
                    return
                token = secrets.token_urlsafe(24)
                SESSIONS[token] = user["id"]
                json_response(self, {"token": token, "user": public_user(user)})
                return
            if parsed.path == "/api/auth/register":
                name = str(payload.get("name", "")).strip()
                username = str(payload.get("username", "")).strip()
                password = str(payload.get("password", ""))
                if len(name) < 1 or len(name) > 20:
                    json_response(self, {"error": "请输入有效姓名"}, HTTPStatus.BAD_REQUEST)
                    return
                if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", username):
                    json_response(self, {"error": "账号需为3-20位字母、数字或下划线"}, HTTPStatus.BAD_REQUEST)
                    return
                if len(password) < 6:
                    json_response(self, {"error": "密码至少需要6位"}, HTTPStatus.BAD_REQUEST)
                    return
                risk_tolerance = int(payload.get("risk_tolerance", 2))
                preferred_term = int(payload.get("preferred_term", 365))
                investment_budget = float(payload.get("investment_budget", 0))
                if risk_tolerance not in (1, 2, 3, 4) or preferred_term <= 0 or investment_budget < 0:
                    json_response(self, {"error": "注册信息不完整"}, HTTPStatus.BAD_REQUEST)
                    return
                try:
                    user = create_customer_account(name, username, password, risk_tolerance, preferred_term, investment_budget)
                except ValueError as error:
                    if str(error) == "username_exists":
                        json_response(self, {"error": "该账号已存在"}, HTTPStatus.CONFLICT)
                        return
                    raise
                token = secrets.token_urlsafe(24)
                SESSIONS[token] = user["id"]
                json_response(self, {"token": token, "user": public_user(user)}, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/auth/switch":
                if not request_user(self):
                    json_response(self, {"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                    return
                user = get_user_by_credentials(payload.get("username", ""), payload.get("password", ""))
                if not user:
                    json_response(self, {"error": "账号或密码不正确"}, HTTPStatus.UNAUTHORIZED)
                    return
                token = secrets.token_urlsafe(24)
                SESSIONS[token] = user["id"]
                json_response(self, {"token": token, "user": public_user(user)})
                return
            if parsed.path == "/api/auth/logout":
                token = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                SESSIONS.pop(token, None)
                json_response(self, {"ok": True})
                return
            if parsed.path == "/api/recommendations/refresh":
                json_response(self, recommendations(payload.get("customer_id", "U001")))
                return
            json_response(self, {"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as error:
            json_response(self, {"error": f"invalid JSON: {error}"}, HTTPStatus.BAD_REQUEST)
        except Exception as error:
            json_response(self, {"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def serve_frontend(self, path: str) -> None:
        relative = path.lstrip("/") or "index.html"
        candidate = (FRONTEND / relative).resolve()
        if FRONTEND not in candidate.parents and candidate != FRONTEND:
            json_response(self, {"error": "invalid path"}, HTTPStatus.BAD_REQUEST)
            return
        if not candidate.is_file():
            candidate = FRONTEND / "index.html"
        content = candidate.read_bytes()
        content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("[api] " + format % args + "\n")


def main() -> None:
    init_db()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"金融产品平台运行于 http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nserver stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
