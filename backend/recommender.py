"""Suitability-first recommendations, temporal pair rules and constrained substitutes."""

from __future__ import annotations

import math
import re
import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta

POSITIVE_ACTIONS = {"favorite"}
EXPOSURE_ACTIONS = {"view", "favorite", "avoid"}
MIN_POSITIVE_SUPPORT = 0.03
MIN_POSITIVE_USERS = 20
MIN_POSITIVE_EXPOSED = 30
MIN_NEGATIVE_SUPPORT = 0.01
MIN_NEGATIVE_USERS = 10
MIN_NEGATIVE_EXPOSED = 20
MIN_VALID_USERS = MIN_POSITIVE_EXPOSED
MIN_USERS = MIN_POSITIVE_USERS
MIN_EXPOSED = MIN_POSITIVE_EXPOSED
DISTANCE_THRESHOLD = 0.6
TOP_ITEMS = 3
MAX_PER_TYPE = 2
CONTRACT_RATE_PROTECTION = {"deposit", "sovereign"}
TIER_ORDER = {"pass": 0, "pending": 1, "hard": 2}
HARD_CODES = {
    "simulated", "loss", "risk", "budget", "reserve", "liquidity", "matured",
    "pension_lock", "qualification", "pension_budget", "insurance", "profile", "plan_expired",
}
PENDING_CODES = {"amount_unknown", "term_unknown"}
UNAVAILABLE_STATUS_RE = re.compile(r"非开放期|不开放|未开放|暂停|停售|售罄|发行(?:已)?结束|终止|清盘|封闭期")
UNSURE_STATUS_RE = re.compile(r"(?:在售|开放|申购)(?:情况|状态)?待确认")
OPEN_STATUS_RE = re.compile(r"^在售|开放申购|开放申赎|开放中")


def available_budget(customer: dict) -> float:
    return max(0, customer.get("investment_budget", 0) - customer.get("emergency_reserve", 0))


def product_budget(product: dict, customer: dict) -> float:
    pension = customer.get("personal_pension_budget", 0)
    return pension if product.get("eligibility") == "personal_pension" else max(0, available_budget(customer) - pension)


def horizon(customer: dict, today: date | None = None) -> int:
    if customer.get("target_date"):
        return (date.fromisoformat(customer["target_date"]) - (today or date.today())).days
    return customer.get("preferred_term", 0)


PLAN_EXPIRED_TEXT = "您填的用钱日期已经到了，请更新资金计划"


def plan_expired(customer: dict, today: date | None = None) -> bool:
    """A completed plan whose money date is today or earlier no longer describes a future need."""
    return bool(customer.get("plan_completed")) and bool(customer.get("target_date")) and horizon(customer, today) <= 0


def _add_months(start: date, months: int) -> date:
    month_index = start.year * 12 + start.month - 1 + months
    year, month_index = divmod(month_index, 12)
    month = month_index + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def lock_days(product: dict, customer: dict, today: date | None = None) -> int:
    """Return募集等待 plus the product's holding lock in natural days."""
    today = today or date.today()
    value_date = product.get("value_date")
    waiting = 0
    if value_date:
        try:
            value = date.fromisoformat(value_date)
        except (TypeError, ValueError):
            value = None
        if value and value > today:
            waiting = (value - today).days
    start = today + timedelta(days=waiting)
    months = product.get("term_months")
    if months is not None:
        try:
            holding = (_add_months(start, int(months)) - start).days
        except (TypeError, ValueError, OverflowError):
            holding = 0
    else:
        holding = product.get("term_days") or 0
    return waiting + max(0, int(holding))


MATURITY_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*到期")


def take_out_days(product: dict, today: date | None = None) -> int | None:
    """Days from today until the money can be taken out (募集等待, calendar-month terms, stated maturity); None if unknown."""
    today = today or date.today()
    if product.get("term_days") is None and product.get("term_months") is None:
        return None
    lock = lock_days(product, {}, today)
    maturity = MATURITY_RE.search((product.get("notes") or "") + (product.get("status") or ""))
    if maturity:
        lock = max(lock, (date.fromisoformat(maturity[1]) - today).days)
    return lock


def with_take_out_days(product: dict, today: date | None = None) -> dict:
    """Catalog copy of a product carrying its own and each term option's take-out days."""
    variants = [{**variant, "lock_days": take_out_days(with_variant(product, variant), today)} for variant in product.get("variants") or []]
    return {**product, "lock_days": take_out_days(product, today), **({"variants": variants} if variants else {})}


def minimum_known(product: dict) -> bool:
    return product.get("min_amount") is not None and not re.search(
        r"起[投购]金额[^；。]*未(?:披露|列明)", product.get("notes") or ""
    )


def availability(product: dict, today: date) -> tuple[str, str] | None:
    """Why the product may not be buyable now, with its tier ("hard" or "pending"); None when it is."""
    status = product.get("status") or ""
    notes = product.get("notes") or ""
    if UNAVAILABLE_STATUS_RE.search(status) or re.search(r"当前非开放期", notes):
        return "资料显示现在不能买，请向销售机构确认", "hard"
    in_recruitment_period = False
    if "募集" in status + notes:
        match = re.search(r"募集(?:期)?\s*((?:\d{4}-)?\d{2}-\d{2})\s*至\s*((?:\d{4}-)?\d{2}-\d{2})", status + "；" + notes)
        if match:
            start_text, end_text = match.groups()
            start_has_year = len(start_text.split("-")[0]) == 4
            if start_has_year:
                start = date.fromisoformat(start_text)
                end = date.fromisoformat(end_text if len(end_text.split("-")[0]) == 4 else f"{start.year}-{end_text}")
            else:
                start_month, start_day = (int(part) for part in start_text.split("-"))
                end_month, end_day = (int(part) for part in end_text.split("-"))
                crosses_year = (end_month, end_day) < (start_month, start_day)
                start_year = (today.year - 1 if (today.month, today.day) <= (end_month, end_day) else today.year) if crosses_year else today.year
                start = date(start_year, start_month, start_day)
                end = date(start_year, end_month, end_day)
            if end < start:
                end = date(end.year + 1, end.month, end.day)
            if not start <= today <= end:
                return "现在不在资料写明的募集期内，请向销售机构确认什么时候能买", "hard"
            in_recruitment_period = True
    if not in_recruitment_period and (UNSURE_STATUS_RE.search(status) or not OPEN_STATUS_RE.search(status)):
        return "资料没写明现在还能不能买，请先向销售机构确认", "pending"
    return None


def availability_reason(product: dict, today: date) -> str | None:
    result = availability(product, today)
    return result[0] if result else None


def hard_problem_count(result: dict) -> int:
    """How many blocking problems an unsuitable variant has, so the closest one is shown first."""
    return len(set(result["codes"]) & HARD_CODES) if result["tier"] == "hard" else 0


def assess(product: dict, customer: dict, today: date | None = None) -> dict:
    if product.get("variants") and not product.get("selected_variant"):
        options = [with_variant(product, variant) for variant in product["variants"]]
        assessments = [(option, assess(option, customer, today)) for option in options]
        pool = sorted(
            assessments,
            key=lambda pair: (
                TIER_ORDER[pair[1]["tier"]],
                hard_problem_count(pair[1]),
                preference_distance(pair[0], customer, today),
                pair[0].get("term_days") or 0,
            ),
        )
        option, result = pool[0]
        passed = [p["selected_variant"]["id"] for p, r in assessments if r["tier"] == "pass"]
        return {**result, "selected_variant": option["selected_variant"], "eligible_variant_ids": passed}
    today = today or date.today()
    if not customer.get("plan_completed"):
        return {"eligible": False, "tier": "hard", "gaps": [], "soft_penalties": [],
                "reasons": ["请先填好资金计划，我们再告诉您这款适不适合您"], "codes": ["profile"], "status": "还没填好资金计划"}
    if plan_expired(customer, today):
        return {"eligible": False, "tier": "hard", "gaps": [], "soft_penalties": [],
                "reasons": [PLAN_EXPIRED_TEXT], "codes": ["plan_expired"], "status": "请更新资金计划"}
    reasons, codes, gaps, soft_penalties = [], [], [], []

    def reject(code: str, text: str, *, gap: str | None = None) -> None:
        codes.append(code)
        reasons.append(text)
        if gap:
            gaps.append(gap)

    if product.get("is_simulated"):
        reject("simulated", "这是模拟产品，不能真的购买，所以不推荐")
    if not customer.get("can_accept_loss") and product.get("capital_protection") not in {"deposit", "sovereign"}:
        reject("loss", "您选了不能接受本金亏损，这款产品不像存款或国债那样保本")
    if product.get("risk_level") is None or product["risk_level"] > customer["risk_tolerance"]:
        reject("risk", "资料没写明这款产品的风险等级，请先向销售机构确认" if product.get("risk_level") is None else f"这款产品的风险等级是 R{product['risk_level']}，高于您能接受的 R{customer['risk_tolerance']}")
    if not minimum_known(product):
        reject("amount_unknown", "资料没写明最少要投多少，请先向销售机构确认", gap="请确认最少要投多少")
    elif product["min_amount"] > product_budget(product, customer):
        reject("budget", "最少要投的钱超过了您可以安排的钱（应急备用金和个人养老金账户里的钱不算在内）")
    if (customer.get("investment_budget") or 0) <= 0:
        reject("reserve", "您填的打算安排的钱是 0，请在资金计划里填上这次想安排多少钱")
    elif available_budget(customer) <= 0:
        reject("reserve", "这次的钱都留作应急备用金了，暂时没有可以安排的钱")
    days = horizon(customer, today)
    lock = None
    settlement_note = None
    term = product.get("term_days")
    # Simplified open-ended terms never imply immediate redemption or arrival.
    maturity = MATURITY_RE.search((product.get("notes") or "") + (product.get("status") or ""))
    if maturity:
        remaining = (date.fromisoformat(maturity[1]) - today).days
        if remaining < 0:
            reject("matured", "资料上的到期日已经过了，请向销售机构确认现在的情况")
        elif term is not None:
            term = max(term, remaining)
    elif re.search(r"已到期|到期日已过", (product.get("notes") or "") + (product.get("status") or "")):
        reject("matured", "资料上的到期日已经过了，请向销售机构确认现在的情况")
    if term is None and product.get("term_months") is None:
        reject("term_unknown", "资料没写明要放多久才能取出，请先向销售机构确认", gap="请确认要放多久、取出后几天到账")
    else:
        lock = take_out_days(product, today)
        if lock > days:
            reject("liquidity", "要到您用钱的日子之后才能取出，赶不上您用钱")
        elif days < lock + (product.get("settlement_days") or 0):
            settlement_days = product.get("settlement_days") or 0
            codes.append("settlement_buffer")
            soft_penalties.append("settlement_buffer")
            settlement_note = f"到期后还要约 {settlement_days} 天钱才到账，可能晚于您用钱的日子"
            reasons.append(settlement_note)
    unavailable = availability(product, today)
    availability_tier = None
    if unavailable:
        codes.append("availability")
        text, availability_tier = unavailable
        reasons.append(text)
        if availability_tier == "pending":
            gaps.append("请确认现在还能不能买")
    if product.get("eligibility") == "personal_pension":
        if customer.get("retirement_status") != "已退休":
            reject("pension_lock", "个人养老金账户里的钱要满足领取条件才能取出，您目前还不满足")
        if customer.get("personal_pension_status") != "已确认":
            reject("qualification", "这款只能用个人养老金账户里的钱买，需要先开好账户并经销售机构确认；退休了不等于可以买")
        elif not minimum_known(product) or product["min_amount"] > customer.get("personal_pension_budget", 0):
            reject("pension_budget", "个人养老金账户里的钱不够，或者最少要投多少还没写明；不能用其他的钱代替")
    elif product.get("eligibility") == "insurance":
        reject("insurance", "保险要另外看投保年龄、健康条件、缴费和退保能拿回多少，不能当作随时能取的钱")
    elif product.get("capital_protection") not in {"deposit", "sovereign"} and re.search(r"专属|合格投资者|私募|个人养老金", (product.get("product_type") or "") + (product.get("notes") or "") + product.get("name", "")):
        reject("qualification", "这款对购买人有特别要求，请先向销售机构确认您能不能买")
    hard_codes = set(codes) & HARD_CODES
    pending_codes = set(codes) & PENDING_CODES
    if availability_tier == "hard":
        hard_codes.add("availability")
    elif availability_tier == "pending":
        pending_codes.add("availability")
    tier = "hard" if hard_codes else "pending" if pending_codes else "pass"
    if tier == "pending" and not gaps:
        if "amount_unknown" in pending_codes:
            gaps.append("请确认最少要投多少")
        if "term_unknown" in pending_codes:
            gaps.append("请确认要放多久、取出后几天到账")
    if tier == "pass":
        reasons = ["风险在您能接受的范围内，最少要投的钱也在您可以安排的钱以内",
                   settlement_note or "到期取出的时间赶得上您用钱，具体到账时间请向销售机构确认"]
    elif tier == "pending":
        reasons = gaps or reasons
    return {"eligible": tier == "pass", "tier": tier, "gaps": gaps if tier == "pending" else [],
            "soft_penalties": soft_penalties, "reasons": reasons,
            "lock_days": lock, "available_date": (today + timedelta(days=lock)).isoformat() if lock is not None else None,
            "settlement_note": settlement_note,
            "codes": codes, "status": "符合您的计划" if tier == "pass" else "有几项资料要先确认" if tier == "pending" else "请谨慎选择"}


def with_variant(product: dict, variant: dict) -> dict:
    return {**product, "term_days": variant.get("term_days", product.get("term_days")), "min_amount": variant.get("min_amount", product.get("min_amount")),
            "term_months": variant.get("term_months", product.get("term_months")),
            "expected_return_mid": variant.get("rate"), "expected_return": variant.get("rate"),
            "expected_return_text": f"{variant['rate']:.2f}%" if variant.get("rate") is not None else "利率待确认",
            "status": variant.get("status", product.get("status", "")), "selected_variant": variant}


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


def build_rules(
    behaviors: list[dict],
    product_ids: set[int],
    *,
    min_valid_users: int = MIN_VALID_USERS,
    min_positive_support: float = MIN_POSITIVE_SUPPORT,
    min_positive_users: int = MIN_POSITIVE_USERS,
    min_positive_exposed: int = MIN_POSITIVE_EXPOSED,
    min_negative_support: float = MIN_NEGATIVE_SUPPORT,
    min_negative_users: int = MIN_NEGATIVE_USERS,
    min_negative_exposed: int = MIN_NEGATIVE_EXPOSED,
) -> list[dict]:
    """Unique-user evidence; negative confidence is conditioned on exposure after A."""
    timelines = _timed_events(behaviors)
    users = len(timelines)
    if users < min_valid_users:
        return []
    positive_left_users: dict[int, int] = defaultdict(int)
    positive_right_users: dict[int, int] = defaultdict(int)
    positive_both: dict[tuple[int, int], int] = defaultdict(int)
    all_exposed: dict[int, int] = defaultdict(int)
    all_negative: dict[int, int] = defaultdict(int)
    negative_exposed: dict[tuple[int, int], int] = defaultdict(int)
    negative: dict[tuple[int, int], int] = defaultdict(int)
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
            if action in {"unfavorite", "avoid"}:
                # Matches the saved preference: cancelling a favorite or rejecting the product ends it.
                positives.pop(product_id, None)
            if action == "avoid":
                avoids[product_id] = key
            if action == "restore":
                restored[product_id] = key
        avoids = {pid: key for pid, key in avoids.items() if key > restored.get(pid, (-1, -1))}
        for right, exposure_keys in exposures.items():
            all_exposed[right] += 1
            if right in avoids and any(key <= avoids[right] for key in exposure_keys):
                all_negative[right] += 1
        favorite_products = sorted(positives)
        for right in favorite_products:
            positive_right_users[right] += 1
        for left in favorite_products:
            positive_left_users[left] += 1
            for right in favorite_products:
                if left != right:
                    positive_both[(left, right)] += 1
            for right, exposure_keys in exposures.items():
                if left == right:
                    continue
                favorite_key = positives[left]
                if not any(key > favorite_key for key in exposure_keys):
                    continue
                pair = (left, right)
                negative_exposed[pair] += 1
                if right in avoids and favorite_key < avoids[right] and any(
                    favorite_key < key <= avoids[right] for key in exposure_keys
                ):
                    negative[pair] += 1
    result = []
    for (left, right), evidence_users in positive_both.items():
        left_users = positive_left_users[left]
        right_positive = positive_right_users[right]
        confidence = evidence_users / left_users
        lift = confidence / (right_positive / users) if right_positive else 0
        support = evidence_users / users
        if evidence_users >= min_positive_users and left_users >= min_positive_exposed and support >= min_positive_support and (confidence >= .5 or lift > 1):
            result.append({"product_a": left, "product_b": right, "rule_type": "positive", "support": round(support, 4),
                           "confidence": round(confidence, 4), "interest": round(lift, 4), "evidence_users": evidence_users, "exposed_users": left_users})
    for (left, right), evidence_users in negative.items():
        exposed = negative_exposed[(left, right)]
        conf = evidence_users / exposed if exposed else 0
        baseline = all_negative[right] / all_exposed[right] if all_exposed[right] else 0
        interest = conf / baseline if baseline else 0
        if evidence_users >= min_negative_users and exposed >= min_negative_exposed and evidence_users / users >= min_negative_support and conf >= .5 and interest > 1:
            result.append({"product_a": left, "product_b": right, "rule_type": "negative", "support": round(evidence_users / users, 4),
                           "confidence": round(conf, 4), "interest": round(interest, 4), "evidence_users": evidence_users, "exposed_users": exposed})
    return sorted(result, key=lambda rule: (-rule["confidence"], -rule["interest"], rule["product_a"], rule["product_b"]))


def preference_distance(product: dict, customer: dict, today: date | None = None) -> float:
    days = max(1, horizon(customer, today))
    risk_level = product.get("risk_level")
    if risk_level is None:
        risk_distance = 1.0
    elif customer.get("retirement_status") in {"已退休", "临近退休"}:
        risk_distance = (risk_level - 1) / max(1, customer.get("risk_tolerance", 1) - 1)
    else:
        risk_distance = (customer.get("risk_tolerance", 1) - risk_level) / 4
    risk_distance = max(0.0, min(1.0, risk_distance))
    if product.get("term_days") is None and product.get("term_months") is None:
        term_distance = 0.5
    else:
        lock = lock_days(product, customer, today)
        term_distance = 0.5 * max(0, days - lock) / days
        term_distance = max(0.0, min(0.5, term_distance))
    distances = [(0.5, term_distance), (0.3, risk_distance)]
    category = customer.get("preferred_category", "全部")
    if category != "全部":
        distances.append((0.2, float(product.get("category") != category)))
    total_weight = sum(weight for weight, _ in distances)
    return sum(weight * distance for weight, distance in distances) / total_weight


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
    original_risk = original.get("risk_level")
    risk_cap = original_risk if original_risk is not None else customer.get("risk_tolerance", 5)
    candidates = []
    for product in eligible_candidates(products, customer, today):
        if product["id"] == original["id"] or product["id"] in avoided:
            continue
        if product.get("risk_level") is None or product.get("risk_level") > risk_cap:
            continue
        distance = product_distance(original, product)
        if distance <= DISTANCE_THRESHOLD:
            candidates.append((product, distance))
    same_category = [pair for pair in candidates if pair[0].get("category") == original.get("category")]
    widened = not same_category
    pool = same_category or candidates
    pool.sort(key=lambda pair: (pair[1], preference_distance(pair[0], customer, today),
                                pair[0].get("risk_level", 99), pair[0].get("id", 0)))
    replacement = pool[0][0] if pool else None
    differences = []
    if replacement:
        if original_risk is not None and original_risk != replacement["risk_level"]:
            differences.append(f"风险等级从 R{original['risk_level']} 变为 R{replacement['risk_level']}")
        if original.get("term_days") != replacement.get("term_days"):
            differences.append("要放多久、怎么取出和原来那款不同，请看产品说明")
        if original.get("category") != replacement.get("category"):
            differences.append(f"类别不同：{original.get('category')} → {replacement.get('category')}")
        if original["product_type"] != replacement["product_type"]:
            differences.append(f"产品类型变为{replacement['product_type']}，钱投向哪里、涨跌幅度可能不同")
        if replacement.get("selected_variant"):
            differences.append(f"适合您的是 {replacement['selected_variant']['label']} 这一档")
        if original.get("expected_return_mid") is not None and replacement.get("expected_return_mid") is not None and original.get("return_basis") == replacement.get("return_basis") and original.get("term_days") == replacement.get("term_days"):
            change = replacement["expected_return_mid"] - original["expected_return_mid"]
            differences.append(f"期限和收益算法相同，年利率{'高' if change >= 0 else '低'} {abs(change):.2f} 个百分点；具体批次和额度请向销售机构确认")
        differences.append("两款产品的收益和费用各自计算，不代表收益一样")
    message = "可进一步比较的同类选择" if replacement and not widened else "可进一步比较的其他类别选择" if replacement else "暂未找到符合当前资金计划的同类替代品，请勿为替换而提高风险或占用应急资金"
    return {"original": original, "original_assessment": original_assessment, "replacement": replacement,
            "differences": differences, "message": message}


def _contract_rate_group(item: dict, customer: dict, today: date | None) -> tuple | None:
    """Group capital-protected items whose terms differ only by their contractual rate."""
    if item.get("capital_protection") not in CONTRACT_RATE_PROTECTION or item.get("expected_return_mid") is None:
        return None
    if item.get("negative_penalty"):
        return None
    return (item["capital_protection"], item.get("risk_level"), item.get("return_basis"),
            lock_days(item, customer, today), item.get("settlement_days") or 0)


def prefer_contract_rates(items: list[dict], customer: dict, today: date | None = None) -> list[dict]:
    """Within one group of equivalent capital-protected items, put higher contractual rates first.

    The group keeps the positions its members already hold in score order. A member is dropped when
    another member offers at least the same rate with no higher minimum amount, since it adds nothing
    for the customer.
    """
    groups: dict[tuple, list[int]] = defaultdict(list)
    for index, item in enumerate(items):
        key = _contract_rate_group(item, customer, today)
        if key is not None:
            groups[key].append(index)
    ordered: list[dict | None] = list(items)
    for indices in groups.values():
        members = sorted((items[index] for index in indices),
                         key=lambda item: (-item["expected_return_mid"], item.get("min_amount") or 0))
        kept_minimums: list[float] = []
        for slot, member in zip(indices, members):
            minimum = member.get("min_amount") or 0
            dominated = any(kept <= minimum for kept in kept_minimums)
            ordered[slot] = None if dominated else member
            if not dominated:
                kept_minimums.append(minimum)
    return [item for item in ordered if item is not None]


def diversified_top(items: list[dict], limit: int = TOP_ITEMS, per_type: int = MAX_PER_TYPE) -> list[dict]:
    """Take items in score order, allowing at most `per_type` of one product_type."""
    chosen, counts = [], {}
    for item in items:
        kind = item.get("product_type")
        if counts.get(kind, 0) >= per_type:
            continue
        chosen.append(item)
        counts[kind] = counts.get(kind, 0) + 1
        if len(chosen) == limit:
            break
    return chosen


RELATED_LIMIT = 6


def _with_reason(item: dict, kind: str, rule: dict, names: dict[int, str]) -> dict:
    return {**item, "related_reason": {"kind": kind, "source_id": rule["product_a"],
                                       "source_name": names.get(rule["product_a"], ""), "confidence": rule["confidence"]}}


def related_lists(candidates: list[dict], positive_rules: dict[int, dict], negative_rules: dict[int, dict],
                  avoided_rules: dict[int, dict], favorites: set[int], products: list[dict],
                  limit: int = RELATED_LIMIT, action_order: dict[int, int] | None = None,
                  shown: set[int] | None = None) -> tuple[list[dict], list[dict]]:
    """Customer-facing results of the pair rules, each item carrying the rule that produced it.

    Recommended: suitable products favorited together with one of the customer's favorites.
    Not recommended: suitable products that the same rules argue against, either because customers who
    favorited one of the customer's favorites later rejected them, or because they are usually favorited
    together with a product the customer rejected.
    Items triggered by the product the customer acted on most recently come first, so each favorite or
    rejection visibly changes the lists even when they are already full.
    Products already shown to the customer as suitable or nearly suitable (`shown`) are not listed as ones to be
    careful with, so each product carries one conclusion.
    """
    action_order = action_order or {}
    names = {product["id"]: product["name"] for product in products}
    recommended, unrecommended = [], []
    for item in candidates:
        product_id = item["id"]
        if product_id in favorites:
            continue
        negative, avoided = negative_rules.get(product_id), avoided_rules.get(product_id)
        if negative or avoided:
            if product_id in (shown or set()):
                continue
            strongest = max((rule for rule in (negative, avoided) if rule), key=lambda rule: rule.get("confidence", 0))
            unrecommended.append(_with_reason(item, "negative" if strongest is negative else "avoided", strongest, names))
        elif product_id in positive_rules and item["assessment"]["tier"] == "pass":
            recommended.append(_with_reason(item, "positive", positive_rules[product_id], names))
    order = lambda item: (-action_order.get(item["related_reason"]["source_id"], -1),
                          -item["related_reason"]["confidence"], -item["final_score"], item["id"])
    return sorted(recommended, key=order)[:limit], sorted(unrecommended, key=order)[:limit]


def substitute_exclusions(preferences: list[dict], rules: list[dict]) -> set[int]:
    """Products never offered as a substitute: the customer's rejections and what the rules argue against."""
    avoided = {row["product_id"] for row in preferences if row["avoided"]}
    favorites = {row["product_id"] for row in preferences if row["favorite"]} - avoided
    flagged = {rule["product_b"] for rule in rules
               if (rule["rule_type"] == "negative" and rule["product_a"] in favorites)
               or (rule["rule_type"] == "positive" and rule["product_a"] in avoided)}
    return avoided | flagged


def recommend(customer: dict, products: list[dict], behaviors: list[dict], preferences: list[dict], rules: list[dict], today: date | None = None) -> dict:
    avoided = {row["product_id"] for row in preferences if row["avoided"]}
    favorites = {row["product_id"] for row in preferences if row["favorite"]} - avoided
    interest = {event["product_id"] for event in behaviors if event["customer_id"] == customer["id"] and event["behavior_type"] in {"view", "favorite", "substitute"}}
    positive_rules: dict[int, dict] = {}
    negative_rules: dict[int, dict] = {}
    avoided_rules: dict[int, dict] = {}

    def keep_strongest(target: dict[int, dict], rule: dict) -> None:
        previous = target.get(rule["product_b"])
        if previous is None or rule.get("confidence", 0) > previous.get("confidence", 0):
            target[rule["product_b"]] = rule

    for rule in rules:
        if rule["rule_type"] == "positive" and rule["product_a"] in favorites:
            keep_strongest(positive_rules, rule)
        elif rule["rule_type"] == "negative" and rule["product_a"] in favorites:
            keep_strongest(negative_rules, rule)
        elif rule["rule_type"] == "positive" and rule["product_a"] in avoided:
            # Products that are usually favorited together with one the customer rejected.
            keep_strongest(avoided_rules, rule)
    negative_ids = set(negative_rules)
    eligible, near, cautions = [], [], []
    for product in products:
        evaluation = assess(product, customer, today)
        if evaluation.get("selected_variant"):
            product = with_variant(product, evaluation["selected_variant"])
        item = {**product, "assessment": evaluation}
        if evaluation["tier"] in {"pass", "pending"} and product["id"] not in avoided:
            distance = preference_distance(product, customer, today)
            rule = positive_rules.get(product["id"])
            positive_bonus = .2 * min(1, rule.get("confidence", 0)) if rule else 0
            negative_penalty = .15 * min(1, max(negative_rules.get(product["id"], {}).get("confidence", 0),
                                                avoided_rules.get(product["id"], {}).get("confidence", 0)))
            soft_penalty = .1 if "settlement_buffer" in evaluation.get("soft_penalties", []) else 0
            final_score = 1 - distance - soft_penalty - negative_penalty + positive_bonus
            item.update(distance=round(distance, 4), similarity_score=round(1 - distance, 4),
                        association_score=round(positive_bonus / .2 if positive_bonus else 0, 4),
                        positive_bonus=round(positive_bonus, 4), negative_penalty=round(negative_penalty, 4),
                        soft_penalty_score=round(soft_penalty, 4), final_score=round(final_score, 4))
            (eligible if evaluation["tier"] == "pass" else near).append(item)
        if product["id"] in interest and product["id"] not in avoided and evaluation["tier"] == "hard":
            if product["id"] in negative_ids:
                item["assessment"] = {**evaluation, "status": "建议再考虑", "reasons": evaluation["reasons"] + ["收藏了相同产品的人中，很多对这款点了「不感兴趣」，可以先比较同类产品"], "codes": evaluation["codes"] + ["negative_feedback"]}
            cautions.append(item)
    minimum = min((item["distance"] for item in eligible), default=None)
    eligible.sort(key=lambda item: (-item["final_score"], item["id"]))
    near.sort(key=lambda item: (-item["final_score"], item["id"]))
    substitute_avoid = substitute_exclusions(preferences, rules)
    interest_order = {event["product_id"]: index for index, event in enumerate(behaviors) if event["customer_id"] == customer["id"]}
    cautions.sort(key=lambda item: -interest_order.get(item["id"], -1))
    substitutes = [find_substitute(item, products, customer, substitute_avoid, today) for item in cautions[:3]] if customer.get("plan_completed") else []
    expired = plan_expired(customer, today)
    preferred_category = customer.get("preferred_category", "全部")
    category_note = ("保险要另外看保障、缴费和退保能拿回多少，这里不把保险当作理财推荐。"
                     if preferred_category == "保险" else None)
    top = diversified_top(prefer_contract_rates(eligible, customer, today))
    action_order = {event["product_id"]: index for index, event in enumerate(behaviors)
                    if event["customer_id"] == customer["id"] and event["behavior_type"] in {"favorite", "avoid"}}
    related, unrecommended = related_lists(eligible + near, positive_rules, negative_rules, avoided_rules, favorites, products,
                                           action_order=action_order, shown={item["id"] for item in top + near[:6]})
    return {"customer": customer, "items": top, "recommend_items": top, "near_items": near[:6],
            "substitute_items": substitutes, "caution_items": cautions[:6], "related_items": related, "unrecommended_items": unrecommended,
            "meta": {"profile_complete": bool(customer.get("plan_completed")), "available_budget": available_budget(customer),
                     "min_distance": minimum, "distance_threshold": DISTANCE_THRESHOLD, "fallback_used": False,
                     "association_rule_count": len(rules), "eligible_count": len(eligible), "near_count": len(near),
                     "category_note": category_note, "plan_expired": expired,
                     "weights": {"term": .5, "risk": .3, "category": .2}}}
