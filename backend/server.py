#!/usr/bin/env python3
"""SQLite-backed API and static server for the financial product platform."""

from __future__ import annotations

import json
import math
import mimetypes
import re
import secrets
import sys
import time
from datetime import date
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import (  # noqa: E402
    create_customer_account, get_admin_overview, get_behaviors, get_customer,
    get_customers, get_diagnostics, get_preferences, get_products, get_user,
    get_user_by_credentials, get_users_public, init_db, record_behavior,
    save_recommendation_result, update_plan,
)
from recommender import assess, build_rules, find_substitute, recommend, with_variant  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
SESSIONS: dict[str, tuple[str, float]] = {}
SESSION_TTL_SECONDS = 8 * 60 * 60


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(24)
    SESSIONS[token] = (user_id, time.time() + SESSION_TTL_SECONDS)
    return token


def public_user(user: dict) -> dict:
    return {key: user.get(key) for key in ("id", "username", "name", "role", "customer_id", "tag")}


def json_response(handler: SimpleHTTPRequestHandler, payload: object, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def pair_rules() -> list[dict]:
    return build_rules(get_behaviors(), {item["id"] for item in get_products()})


def feature_value(product: dict, field: str) -> float | None:
    if field == "expected_return" and "业绩比较基准" in (product.get("return_basis") or ""):
        return None
    return product.get(field)


def product_is_eligible(product: dict, customer: dict) -> bool:
    return assess(product, customer)["eligible"]


def recommendations(customer_id: str) -> dict:
    customer = get_customer(customer_id)
    if not customer:
        raise ValueError("客户不存在")
    products, behaviors = get_products(), get_behaviors()
    rules = build_rules(behaviors, {product["id"] for product in products})
    result = recommend(customer, products, behaviors, get_preferences(customer_id), rules)
    save_recommendation_result(customer_id, result, rules)
    return result


def customer_result(result: dict) -> dict:
    """Customer output contains suitability reasons, without scoring diagnostics."""
    internal_fields = {"distance", "similarity_score", "association_score", "final_score", "positive_bonus", "negative_penalty", "soft_penalty_score"}
    strip_scores = lambda item: {key: value for key, value in item.items() if key not in internal_fields}
    items = [strip_scores(item) for item in result["items"]]
    near_items = [strip_scores(item) for item in result.get("near_items", [])]
    return {"customer": result["customer"], "items": items, "recommend_items": items, "near_items": near_items,
            "substitute_items": result["substitute_items"], "caution_items": result["caution_items"],
            "category_note": result["meta"].get("category_note"),
            "plan": {key: result["meta"][key] for key in ("profile_complete", "available_budget", "eligible_count", "plan_expired", "near_count")}}


def read_json(handler: SimpleHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    if length < 0 or length > 65536:
        raise ValueError("请求内容过长")
    payload = json.loads(handler.rfile.read(length).decode("utf-8") if length else "{}")
    if not isinstance(payload, dict):
        raise ValueError("请求格式不正确")
    return payload


def request_user(handler: SimpleHTTPRequestHandler) -> dict | None:
    authorization = handler.headers.get("Authorization", "")
    token = authorization.removeprefix("Bearer ").strip()
    session = SESSIONS.get(token)
    if not session:
        return None
    user_id, expires_at = session
    if time.time() >= expires_at:
        SESSIONS.pop(token, None)
        return None
    return get_user(user_id)


def validate_plan(payload: dict) -> dict:
    try:
        target = date.fromisoformat(str(payload.get("target_date", "")))
        risk = int(payload.get("risk_tolerance", 0))
        budget = float(payload.get("investment_budget", -1))
        reserve = float(payload.get("emergency_reserve", -1))
        pension_budget = float(payload.get("personal_pension_budget", 0))
    except (ValueError, TypeError, OverflowError):
        raise ValueError("请填写有效的用款日期、金额与风险范围") from None
    if not all(math.isfinite(value) for value in (budget, reserve)) or not 0 <= reserve <= budget <= 1000000000:
        raise ValueError("应急预留不能超过本次资金，金额需在 0 至 10 亿元之间")
    if not math.isfinite(pension_budget) or not 0 <= pension_budget <= budget - reserve:
        raise ValueError("个人养老金账户可用资金不能超过本次扣除应急预留后的资金")
    if risk not in range(1, 6) or not 1 <= (target - date.today()).days <= 36500:
        raise ValueError("请选择风险范围和未来 100 年内的用款日期")
    if payload.get("can_accept_loss") not in (True, False, 0, 1) or "can_accept_loss" not in payload:
        raise ValueError("请选择能否接受本金损失")
    if payload.get("retirement_status") not in {"已退休", "临近退休", "其他"}:
        raise ValueError("请选择退休状态")
    if payload.get("pension_type") not in {"职工基本养老金", "城乡居民养老金", "其他来源", "暂未领取", "不便提供"}:
        raise ValueError("请选择养老收入来源，也可选择不便提供")
    if payload.get("preferred_category") not in {"全部", "储蓄", "养老理财", "保险", "现金管理", "其他投资"}:
        raise ValueError("请选择产品类别偏好")
    pension_status = payload.get("personal_pension_status", "未确认")
    if pension_status not in {"未确认", "已确认", "不符合"}:
        raise ValueError("请选择个人养老金购买资格状态")
    return {**{key: payload[key] for key in ("retirement_status", "pension_type", "preferred_category")},
            "target_date": target.isoformat(), "risk_tolerance": risk, "investment_budget": budget,
            "emergency_reserve": reserve, "can_accept_loss": int(payload["can_accept_loss"]), "preferred_term": (target - date.today()).days,
            "personal_pension_status": pension_status, "personal_pension_budget": pension_budget}


class Handler(SimpleHTTPRequestHandler):
    server_version = "FinancialProductPlatform/0.3"

    def require_user(self, admin: bool = False, customer: bool = False) -> dict | None:
        user = request_user(self)
        if not user:
            json_response(self, {"error": "请先登录"}, HTTPStatus.UNAUTHORIZED)
            return None
        if (admin and user["role"] != "admin") or (customer and not user.get("customer_id")):
            json_response(self, {"error": "当前账号无权访问"}, HTTPStatus.FORBIDDEN)
            return None
        return user

    def check_customer_scope(self, user: dict, values: dict) -> bool:
        if "customer_id" in values and values["customer_id"] != user["customer_id"]:
            json_response(self, {"error": "只能访问本人的资金计划与记录"}, HTTPStatus.FORBIDDEN)
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        try:
            if parsed.path == "/api/products":
                items = get_products(query)
                json_response(self, {"items": items, "total": len(items)})
                return
            if not parsed.path.startswith("/api/"):
                self.serve_frontend(parsed.path)
                return
            user = self.require_user()
            if not user:
                return
            if parsed.path == "/api/auth/me":
                json_response(self, {"user": public_user(user)})
            elif parsed.path == "/api/auth/users":
                json_response(self, {"items": get_users_public() if user["role"] == "admin" else [public_user(user)]})
            elif parsed.path in {"/api/admin/overview", "/api/admin/diagnostics", "/api/customers", "/api/association-rules", "/api/abnormal-records", "/api/stats"}:
                if user["role"] != "admin":
                    json_response(self, {"error": "需要管理员权限"}, HTTPStatus.FORBIDDEN)
                    return
                if parsed.path == "/api/admin/overview":
                    json_response(self, get_admin_overview())
                elif parsed.path == "/api/customers":
                    json_response(self, {"items": get_customers()})
                else:
                    diagnostics = get_diagnostics()
                    payload = {"/api/admin/diagnostics": diagnostics, "/api/association-rules": {"items": diagnostics["rules"]},
                               "/api/abnormal-records": {"items": diagnostics["anomalies"]}, "/api/stats": {**get_admin_overview()["stats"], **diagnostics["counts"]}}[parsed.path]
                    json_response(self, payload)
            elif parsed.path in {"/api/profile", "/api/preferences", "/api/recommendations", "/api/product-assessment", "/api/substitutes"}:
                if not user.get("customer_id"):
                    json_response(self, {"error": "请使用客户账号查看个人计划"}, HTTPStatus.FORBIDDEN)
                    return
                if not self.check_customer_scope(user, query):
                    return
                customer_id = user["customer_id"]
                if parsed.path == "/api/profile":
                    json_response(self, {"customer": get_customer(customer_id)})
                elif parsed.path == "/api/preferences":
                    json_response(self, {"items": get_preferences(customer_id)})
                elif parsed.path == "/api/recommendations":
                    json_response(self, customer_result(recommendations(customer_id)))
                else:
                    product_id = int(query.get("product_id", "0"))
                    products = get_products()
                    product = next((product for product in products if product["id"] == product_id), None)
                    if not product:
                        json_response(self, {"error": "商品不存在"}, HTTPStatus.NOT_FOUND)
                        return
                    if query.get("variant_id"):
                        variant = next((variant for variant in product.get("variants", []) if variant["id"] == query["variant_id"]), None)
                        if not variant:
                            raise ValueError("该商品没有对应期限档")
                        product = with_variant(product, variant)
                    customer = get_customer(customer_id)
                    if parsed.path == "/api/product-assessment":
                        json_response(self, {"assessment": assess(product, customer)})
                    else:
                        prefs = get_preferences(customer_id)
                        avoided = {row["product_id"] for row in prefs if row["avoided"]}
                        favorites = {row["product_id"] for row in prefs if row["favorite"] and not row["avoided"]}
                        avoided |= {rule["product_b"] for rule in pair_rules() if rule["rule_type"] == "negative" and rule["product_a"] in favorites}
                        record_behavior(customer_id, product_id, "substitute", user["id"])
                        json_response(self, find_substitute(product, products, customer, avoided))
            else:
                json_response(self, {"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
        except (ValueError, TypeError, OverflowError) as error:
            json_response(self, {"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception:
            json_response(self, {"error": "服务暂时不可用，请稍后重试"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            raise

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = read_json(self)
            if parsed.path in {"/api/auth/login", "/api/auth/switch", "/api/auth/register"}:
                if parsed.path.endswith("switch") and not self.require_user():
                    return
                if parsed.path.endswith("register"):
                    name = str(payload.get("name", "")).strip()
                    username = str(payload.get("username", "")).strip()
                    password = str(payload.get("password", ""))
                    if not 1 <= len(name) <= 20:
                        raise ValueError("姓名需为 1 至 20 个字符")
                    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", username):
                        raise ValueError("账号需为 3 至 20 位字母、数字或下划线")
                    if not 6 <= len(password) <= 128:
                        raise ValueError("密码需为 6 至 128 位")
                    try:
                        user = create_customer_account(name, username, password, 2, 365, 0)
                    except ValueError as error:
                        if str(error) != "username_exists":
                            raise
                        json_response(self, {"error": "该账号已存在"}, HTTPStatus.CONFLICT)
                        return
                else:
                    user = get_user_by_credentials(str(payload.get("username", "")), str(payload.get("password", "")))
                    if not user:
                        json_response(self, {"error": "账号或密码不正确"}, HTTPStatus.UNAUTHORIZED)
                        return
                if parsed.path.endswith("switch"):
                    SESSIONS.pop(self.headers.get("Authorization", "").removeprefix("Bearer ").strip(), None)
                token = create_session(user["id"])
                json_response(self, {"token": token, "user": public_user(user)}, HTTPStatus.CREATED if parsed.path.endswith("register") else HTTPStatus.OK)
                return
            if parsed.path == "/api/auth/logout":
                SESSIONS.pop(self.headers.get("Authorization", "").removeprefix("Bearer ").strip(), None)
                json_response(self, {"ok": True})
                return
            user = self.require_user(customer=True)
            if not user or not self.check_customer_scope(user, payload):
                return
            customer_id = user["customer_id"]
            if parsed.path == "/api/profile":
                json_response(self, {"customer": update_plan(customer_id, validate_plan(payload))})
            elif parsed.path == "/api/behaviors":
                action = payload.get("action")
                if action not in {"view", "favorite", "unfavorite", "avoid", "restore"}:
                    raise ValueError("不支持此操作")
                product_id = int(payload.get("product_id", 0))
                if product_id not in {product["id"] for product in get_products()}:
                    json_response(self, {"error": "商品不存在"}, HTTPStatus.NOT_FOUND)
                    return
                record_behavior(customer_id, product_id, action, user["id"])
                json_response(self, {"items": get_preferences(customer_id)})
            elif parsed.path == "/api/recommendations/refresh":
                json_response(self, customer_result(recommendations(customer_id)))
            else:
                json_response(self, {"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
        except (ValueError, TypeError, OverflowError) as error:
            json_response(self, {"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception:
            json_response(self, {"error": "服务暂时不可用，请稍后重试"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            raise

    def serve_frontend(self, path: str) -> None:
        relative = unquote(path).lstrip("/") or "index.html"
        candidate = (FRONTEND / relative).resolve()
        if FRONTEND not in candidate.parents and candidate != FRONTEND:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = candidate.read_bytes()
        content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    init_db()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"金融产品推荐系统运行于 http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
