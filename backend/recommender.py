"""Suitability-first recommendations, temporal pair rules and constrained substitutes."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from itertools import permutations

POSITIVE_ACTIONS = {"favorite"}
EXPOSURE_ACTIONS = {"view", "favorite", "avoid", "substitute"}
MIN_USERS = 3
MIN_EXPOSED = 5
DISTANCE_THRESHOLD = 0.6


def available_budget(customer: dict) -> float:
    return max(0, customer.get("investment_budget", 0) - customer.get("emergency_reserve", 0))


def product_budget(product: dict, customer: dict) -> float:
    pension = customer.get("personal_pension_budget", 0)
    return pension if product.get("eligibility") == "personal_pension" else max(0, available_budget(customer) - pension)


def horizon(customer: dict, today: date | None = None) -> int:
    if customer.get("target_date"):
        return (date.fromisoformat(customer["target_date"]) - (today or date.today())).days
    return customer.get("preferred_term", 0)


def minimum_known(product: dict) -> bool:
    return product.get("min_amount") is not None and not re.search(
        r"起[投购]金额[^；。]*未(?:披露|列明)", product.get("notes") or ""
    )


def availability_reason(product: dict, today: date) -> str | None:
    status = product.get("status") or ""
    notes = product.get("notes") or ""
    if re.search(r"非开放期|暂停|停售|售罄|发行已结束|终止|清盘|封闭期", status):
        return "采集资料显示当前不开放申购，请向销售机构确认"
    if re.search(r"当前非开放期|渠道当前非开放期", notes):
        return "采集资料显示当前不开放申购，请向销售机构确认"
    if "募集" in status + notes:
        match = re.search(r"募集(?:期)?\s*(\d{4}-\d{2}-\d{2})\s*至\s*((?:\d{4}-)?\d{2}-\d{2})", status + "；" + notes)
        if match:
            start = date.fromisoformat(match[1])
            end = date.fromisoformat(match[2] if len(match[2]) == 10 else f"{start.year}-{match[2]}")
            if not start <= today <= end:
                return "不在资料披露的募集期内，请确认最新开放安排"
    if not (any(status.startswith(prefix) for prefix in ("在售", "开放申购", "开放中")) or "开放申赎" in status):
        return "存续状态不能确认可申购，请先确认开放安排"
    return None


def assess(product: dict, customer: dict, today: date | None = None) -> dict:
    if product.get("variants") and not product.get("selected_variant"):
        options = [with_variant(product, variant) for variant in product["variants"]]
        assessments = [(option, assess(option, customer, today)) for option in options]
        passed = [(option, result) for option, result in assessments if result["eligible"]]
        pool = passed or assessments
        pool.sort(key=lambda pair: (preference_distance(pair[0], customer, today) if pair[1]["eligible"] else len(pair[1]["codes"]), pair[0]["term_days"] or 0))
        option, result = pool[0]
        return {**result, "selected_variant": option["selected_variant"], "eligible_variant_ids": [p["selected_variant"]["id"] for p, _ in passed]}
    today = today or date.today()
    if not customer.get("plan_completed"):
        return {"eligible": False, "reasons": ["请先完善资金计划，再查看与您需求的匹配情况"], "codes": ["profile"], "status": "待完善计划"}
    reasons, codes = [], []

    def reject(code: str, text: str) -> None:
        codes.append(code)
        reasons.append(text)

    if product.get("is_simulated"):
        reject("simulated", "这是模拟产品，没有真实销售合同或购买入口，不纳入投资推荐")
    if not customer.get("can_accept_loss") and product.get("capital_protection") not in {"deposit", "sovereign"}:
        reject("loss", "您选择不能接受本金损失；该产品不能按普通存款或储蓄国债的保障安排处理")
    if product.get("risk_level") is None or product["risk_level"] > customer["risk_tolerance"]:
        reject("risk", "该产品风险分级尚未明确，需销售机构正式核验" if product.get("risk_level") is None else f"该商品风险等级 R{product['risk_level']}，高于您选择的范围 R{customer['risk_tolerance']}")
    if not minimum_known(product):
        reject("amount_unknown", "起购金额缺少明确披露，暂不纳入按预算推荐")
    elif product["min_amount"] > product_budget(product, customer):
        reject("budget", "起购金额超过可用资金；应急预留和个人养老金账户资金不用于普通商品")
    if available_budget(customer) <= 0:
        reject("reserve", "本次资金已全部预留应急，暂不安排投资")
    days = horizon(customer, today)
    term = product.get("term_days")
    # Simplified open-ended terms never imply immediate redemption or arrival.
    maturity = re.search(r"(\d{4}-\d{2}-\d{2})\s*到期", (product.get("notes") or "") + (product.get("status") or ""))
    if maturity:
        remaining = (date.fromisoformat(maturity[1]) - today).days
        if remaining < 0:
            reject("matured", "资料披露的到期日已过，请核实最新状态")
        elif term is not None:
            term = max(term, remaining)
    if term is None:
        reject("term_unknown", "最短持有或赎回安排尚不明确，无法核对用款时间")
    elif days < max(term, 1) + 7:
        reject("liquidity", "持有要求与用款时间过近，未留足至少 7 天申赎缓冲；实际到账仍需确认")
    unavailable = availability_reason(product, today)
    if unavailable:
        reject("availability", unavailable)
    if product.get("eligibility") == "personal_pension":
        if customer.get("personal_pension_status") != "已确认":
            reject("qualification", "这是个人养老金账户内产品，需已有符合条件的账户并经销售机构确认可购买；退休身份不能代替资格核验")
        elif not minimum_known(product) or product["min_amount"] > customer.get("personal_pension_budget", 0):
            reject("pension_budget", "个人养老金账户内可用资金不足或起购金额尚待确认，不能用普通资金预算替代")
    elif product.get("eligibility") == "insurance":
        reject("insurance", "投保年龄、健康条件、缴费安排和退保现金价值需单独核对，不能当作可随时支取的理财资金")
    elif product.get("capital_protection") not in {"deposit", "sovereign"} and re.search(r"专属|合格投资者|私募|个人养老金", (product.get("product_type") or "") + (product.get("notes") or "") + product.get("name", "")):
        reject("qualification", "存在特定购买资格要求，需向销售机构确认后再选择")
    return {"eligible": not reasons, "reasons": reasons or ["风险与您填写的范围相符，起购金额在可安排资金内", "已按披露的持有要求预留申赎缓冲，实际到账时间仍请确认"],
            "codes": codes, "status": "符合当前计划筛选条件" if not reasons else "请谨慎选择"}


def with_variant(product: dict, variant: dict) -> dict:
    return {**product, "term_days": variant["term_days"], "min_amount": variant["min_amount"],
            "expected_return_mid": variant.get("rate"), "expected_return": variant.get("rate"),
            "expected_return_text": f"{variant['rate']:.2f}%" if variant.get("rate") is not None else "利率待确认",
            "status": variant["status"], "selected_variant": variant}


def eligible_candidates(products: list[dict], customer: dict, today: date | None = None) -> list[dict]:
    result = []
    for product in products:
        variants = product.get("variants", [])
        options = [with_variant(product, variant) for variant in variants] if variants else [product]
        result.extend(option for option in options if assess(option, customer, today)["eligible"])
    return result


def _timed_events(behaviors: list[dict]) -> dict:
    customers: dict = {}
    for event in behaviors:
        if not event.get("created_at") or event["behavior_type"] not in EXPOSURE_ACTIONS | {"unfavorite", "restore"}:
            continue
        try:
            stamp = datetime.fromisoformat(event["created_at"]).timestamp()
        except (ValueError, TypeError):
            continue
        customers.setdefault(event["customer_id"], []).append((stamp, event.get("id", 0), event["product_id"], event["behavior_type"]))
    return {customer: sorted(events) for customer, events in customers.items()}


def build_rules(behaviors: list[dict], product_ids: set[int]) -> list[dict]:
    """Unique-user evidence; negative confidence is conditioned on exposure after A."""
    timelines = _timed_events(behaviors)
    users = len(timelines)
    if users < MIN_EXPOSED:
        return []
    prepared = []
    for events in timelines.values():
        positives, exposures, avoids, restored = {}, {}, {}, {}
        for stamp, sequence, product_id, action in events:
            if product_id not in product_ids:
                continue
            key = (stamp, sequence)
            if action in EXPOSURE_ACTIONS:
                exposures.setdefault(product_id, []).append(key)
            if action in POSITIVE_ACTIONS:
                positives.setdefault(product_id, key)
            if action == "avoid":
                avoids[product_id] = key
            if action == "restore":
                restored[product_id] = key
        avoids = {pid: key for pid, key in avoids.items() if key > restored.get(pid, (-1, -1))}
        prepared.append((positives, exposures, avoids))
    result = []
    positive_ids = set().union(*(set(row[0]) for row in prepared))
    for left, right in permutations(sorted(product_ids), 2):
        if left not in positive_ids:
            continue
        left_users = sum(left in positives for positives, _, _ in prepared)
        positive_both = sum(left in positives and right in positives for positives, _, _ in prepared)
        right_positive = sum(right in positives for positives, _, _ in prepared)
        confidence = positive_both / left_users
        lift = confidence / (right_positive / users) if right_positive else 0
        support = positive_both / users
        if positive_both >= MIN_USERS and left_users >= MIN_EXPOSED and support >= .1 and (confidence >= .5 or lift > 1):
            result.append({"product_a": left, "product_b": right, "rule_type": "positive", "support": round(support, 4),
                           "confidence": round(confidence, 4), "interest": round(lift, 4), "evidence_users": positive_both, "exposed_users": left_users})
        exposed = negative = all_exposed = all_negative = 0
        for positives, exposures, avoids in prepared:
            if right in exposures:
                all_exposed += 1
                all_negative += int(right in avoids and any(key <= avoids[right] for key in exposures[right]))
            if left in positives and any(key > positives[left] for key in exposures.get(right, [])):
                exposed += 1
                if right in avoids and positives[left] < avoids[right] and any(positives[left] < key <= avoids[right] for key in exposures[right]):
                    negative += 1
        conf = negative / exposed if exposed else 0
        baseline = all_negative / all_exposed if all_exposed else 0
        interest = conf / baseline if baseline else 0
        if negative >= MIN_USERS and exposed >= MIN_EXPOSED and negative / users >= .1 and conf >= .5 and interest > 1:
            result.append({"product_a": left, "product_b": right, "rule_type": "negative", "support": round(negative / users, 4),
                           "confidence": round(conf, 4), "interest": round(interest, 4), "evidence_users": negative, "exposed_users": exposed})
    return sorted(result, key=lambda rule: (-rule["confidence"], -rule["interest"], rule["product_a"], rule["product_b"]))


def preference_distance(product: dict, customer: dict, today: date | None = None) -> float:
    days = max(1, horizon(customer, today))
    distances = [(0.4, abs(product["risk_level"] - customer["risk_tolerance"]) / 4),
                 (0.3, min(1, abs((product.get("term_days") or 1) - days) / max(days, 365))),
                 (0.2, min(1, math.log1p(product["min_amount"]) / max(1, math.log1p(product_budget(product, customer)))))]
    if customer.get("preferred_category", "全部") != "全部":
        distances.append((0.1, int(product["category"] != customer["preferred_category"])))
    return sum(weight * distance for weight, distance in distances) / sum(weight for weight, _ in distances)


def product_distance(left: dict, right: dict) -> float:
    components = [(.15, int(left["product_type"] != right["product_type"]))]
    if left.get("risk_level") is not None and right.get("risk_level") is not None:
        components.append((.30, abs(left["risk_level"] - right["risk_level"]) / 4))
    if left.get("term_days") is not None and right.get("term_days") is not None:
        components.append((.25, min(1, abs(left["term_days"] - right["term_days"]) / 1095)))
    if minimum_known(left) and minimum_known(right):
        components.append((.20, min(1, abs(math.log1p(left["min_amount"]) - math.log1p(right["min_amount"])) / math.log1p(1000000))))
    # Compare returns only if their definitions match; formulas are never coerced to numbers.
    if left.get("return_basis") == right.get("return_basis") and "历史" in (left.get("return_basis") or ""):
        if left.get("expected_return_mid") is not None and right.get("expected_return_mid") is not None:
            components.append((.10, min(1, abs(left["expected_return_mid"] - right["expected_return_mid"]) / 100)))
    return sum(weight * value for weight, value in components) / sum(weight for weight, _ in components)


def find_substitute(original: dict, products: list[dict], customer: dict, avoided: set[int], today: date | None = None) -> dict:
    original_assessment = assess(original, customer, today)
    candidates = [product for product in eligible_candidates(products, customer, today) if product["id"] != original["id"] and product["id"] not in avoided
                  and product["category"] == original["category"]]
    candidates.sort(key=lambda product: (product_distance(original, product), product["risk_level"], product["id"]))
    replacement = candidates[0] if candidates and product_distance(original, candidates[0]) <= DISTANCE_THRESHOLD else None
    differences = []
    if replacement:
        if original.get("risk_level") is not None and original["risk_level"] != replacement["risk_level"]:
            differences.append(f"风险由 R{original['risk_level']} 调整为 R{replacement['risk_level']}")
        if original.get("term_days") != replacement.get("term_days"):
            differences.append("持有与赎回安排不同，请查看产品资料")
        if original["product_type"] != replacement["product_type"]:
            differences.append(f"投资类型变为{replacement['product_type']}，资产投向与波动可能不同")
        if replacement.get("selected_variant"):
            differences.append(f"替代期限档：{replacement['selected_variant']['label']}")
        if original.get("expected_return_mid") is not None and replacement.get("expected_return_mid") is not None and original.get("return_basis") == replacement.get("return_basis") and original.get("term_days") == replacement.get("term_days"):
            change = replacement["expected_return_mid"] - original["expected_return_mid"]
            differences.append(f"同期限、同口径利率{'高' if change >= 0 else '低'} {abs(change):.2f} 个百分点；适用批次与额度仍需确认")
        differences.append("收益和费用独立计算，不代表等价或同等收益")
    return {"original": original, "original_assessment": original_assessment, "replacement": replacement,
            "differences": differences, "message": "可进一步比较的同类选择" if replacement else "暂未找到符合当前资金计划的同类替代品，请勿为替换而提高风险或占用应急资金"}


def recommend(customer: dict, products: list[dict], behaviors: list[dict], preferences: list[dict], rules: list[dict], today: date | None = None) -> dict:
    avoided = {row["product_id"] for row in preferences if row["avoided"]}
    favorites = {row["product_id"] for row in preferences if row["favorite"]} - avoided
    interest = {event["product_id"] for event in behaviors if event["customer_id"] == customer["id"] and event["behavior_type"] in {"view", "favorite", "substitute"}}
    positive_rules = {rule["product_b"]: rule for rule in reversed(rules) if rule["rule_type"] == "positive" and rule["product_a"] in favorites}
    use_association = bool(positive_rules)
    negative_ids = {rule["product_b"] for rule in rules if rule["rule_type"] == "negative" and rule["product_a"] in favorites}
    eligible, cautions = [], []
    for product in products:
        evaluation = assess(product, customer, today)
        if evaluation.get("selected_variant"):
            product = with_variant(product, evaluation["selected_variant"])
        item = {**product, "assessment": evaluation}
        if evaluation["eligible"] and product["id"] not in avoided | negative_ids:
            distance = preference_distance(product, customer, today)
            rule = positive_rules.get(product["id"])
            association = min(1, rule["confidence"]) if rule else 0
            item.update(distance=round(distance, 4), similarity_score=round(1 - distance, 4), association_score=association,
                        final_score=round(.6 * association + .4 * (1 - distance) if use_association else 1 - distance, 4))
            eligible.append(item)
        elif product["id"] in interest and product["id"] not in avoided:
            if product["id"] in negative_ids:
                item["assessment"] = {**evaluation, "eligible": False, "status": "建议再考虑", "reasons": evaluation["reasons"] if not evaluation["eligible"] else ["相关用户的明确偏好反馈提示此选择需要再考虑，可先比较同类产品"], "codes": evaluation["codes"] + ["negative_feedback"]}
            cautions.append(item)
    minimum = min((item["distance"] for item in eligible), default=None)
    fallback = minimum is not None and minimum > DISTANCE_THRESHOLD
    eligible.sort(key=(lambda item: (item["risk_level"], item.get("term_days") or 0, item["min_amount"], item["id"])) if fallback else (lambda item: (-item["final_score"], item["id"])))
    # Substitutes also honor negative feedback; no filter is relaxed during fallback.
    substitute_avoid = avoided | negative_ids
    interest_order = {event["product_id"]: index for index, event in enumerate(behaviors) if event["customer_id"] == customer["id"]}
    cautions.sort(key=lambda item: -interest_order.get(item["id"], -1))
    substitutes = [find_substitute(item, products, customer, substitute_avoid, today) for item in cautions[:3]] if customer.get("plan_completed") else []
    return {"customer": customer, "items": eligible[:3], "recommend_items": eligible[:3], "substitute_items": substitutes, "caution_items": cautions[:6],
            "meta": {"profile_complete": bool(customer.get("plan_completed")), "available_budget": available_budget(customer), "min_distance": minimum,
                     "distance_threshold": DISTANCE_THRESHOLD, "fallback_used": fallback, "association_rule_count": len(rules),
                     "eligible_count": len(eligible), "weights": {"association": .6, "nearest": .4}}}
