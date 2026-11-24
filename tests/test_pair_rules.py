"""Equivalence, threshold, and performance checks for association rules."""

import random
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from itertools import permutations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import recommender


BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
THRESHOLDS = {
    "min_valid_users": recommender.MIN_VALID_USERS,
    "min_positive_support": recommender.MIN_POSITIVE_SUPPORT,
    "min_positive_users": recommender.MIN_POSITIVE_USERS,
    "min_positive_exposed": recommender.MIN_POSITIVE_EXPOSED,
    "min_negative_support": recommender.MIN_NEGATIVE_SUPPORT,
    "min_negative_users": recommender.MIN_NEGATIVE_USERS,
    "min_negative_exposed": recommender.MIN_NEGATIVE_EXPOSED,
}


def _reference_timed_events(behaviors):
    customers = {}
    for event in behaviors:
        if not event.get("created_at") or event["behavior_type"] not in recommender.EXPOSURE_ACTIONS | {"unfavorite", "restore"}:
            continue
        try:
            stamp = datetime.fromisoformat(event["created_at"]).timestamp()
        except (ValueError, TypeError):
            continue
        customers.setdefault(event["customer_id"], []).append((stamp, event.get("id", 0), event["product_id"], event["behavior_type"]))
    return {customer: sorted(events) for customer, events in customers.items()}


def reference_build_rules(
    behaviors,
    product_ids,
    *,
    min_valid_users,
    min_positive_support,
    min_positive_users,
    min_positive_exposed,
    min_negative_support,
    min_negative_users,
    min_negative_exposed,
):
    """Reference copy of the pre-rewrite implementation with parameterized thresholds."""
    timelines = _reference_timed_events(behaviors)
    users = len(timelines)
    if users < min_valid_users:
        return []
    prepared = []
    for events in timelines.values():
        positives, exposures, avoids, restored = {}, {}, {}, {}
        for stamp, sequence, product_id, action in events:
            if product_id not in product_ids:
                continue
            key = (stamp, sequence)
            if action in recommender.EXPOSURE_ACTIONS:
                exposures.setdefault(product_id, []).append(key)
            if action in recommender.POSITIVE_ACTIONS:
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
        if positive_both >= min_positive_users and left_users >= min_positive_exposed and support >= min_positive_support and (confidence >= .5 or lift > 1):
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
        if negative >= min_negative_users and exposed >= min_negative_exposed and negative / users >= min_negative_support and conf >= .5 and interest > 1:
            result.append({"product_a": left, "product_b": right, "rule_type": "negative", "support": round(negative / users, 4),
                           "confidence": round(conf, 4), "interest": round(interest, 4), "evidence_users": negative, "exposed_users": exposed})
    return sorted(result, key=lambda rule: (-rule["confidence"], -rule["interest"], rule["product_a"], rule["product_b"]))


def _event(customer_id, product_id, action, event_id, *, with_time=True):
    return {
        "id": event_id,
        "customer_id": customer_id,
        "product_id": product_id,
        "behavior_type": action,
        "created_at": (BASE_TIME + timedelta(seconds=event_id)).isoformat() if with_time else None,
    }


def _small_behaviors():
    events = []
    event_id = 1
    for customer_id, actions in (
        ("U001", [(1, "favorite"), (2, "favorite"), (3, "view"), (4, "avoid"), (4, "restore"), (5, "unfavorite")]),
        ("U002", [(1, "favorite"), (2, "view"), (4, "avoid"), (5, "favorite")]),
        ("U003", [(2, "favorite"), (3, "view"), (4, "avoid")]),
    ):
        for product_id, action in actions:
            events.append(_event(customer_id, product_id, action, event_id))
            event_id += 1
    events.append(_event("U001", 6, "favorite", event_id, with_time=False))
    return events


def _random_behaviors(seed=20261124, product_count=64, user_count=840, events_per_user=18):
    rng = random.Random(seed)
    events = []
    event_id = 1
    random_actions = ("view", "favorite", "avoid", "restore", "unfavorite")
    for user_index in range(user_count):
        customer_id = f"R{user_index:04d}"
        planned = []
        if user_index < 60:
            planned.extend([(1, "favorite"), (2, "favorite")])
        elif user_index < 120:
            planned.append((1, "favorite"))
        elif user_index < 150:
            planned.append((2, "favorite"))
        if 200 <= user_index < 250:
            planned.extend([(4, "favorite"), (5, "view"), (5, "avoid")])
        elif 250 <= user_index < 300:
            planned.extend([(4, "favorite"), (5, "view")])
        if 400 <= user_index < 410:
            planned.extend([(7, "avoid"), (7, "restore")])
        if 500 <= user_index < 510:
            planned.extend([(8, "favorite"), (8, "unfavorite")])
        while len(planned) < events_per_user:
            planned.append((rng.randint(6, product_count), rng.choice(random_actions)))
        for product_id, action in planned[:events_per_user]:
            events.append(_event(customer_id, product_id, action, event_id, with_time=event_id % 17 != 0))
            event_id += 1
    return events


def _threshold_positive_behaviors(user_count, left_users, both_users):
    events = []
    event_id = 1
    for user_index in range(user_count):
        customer_id = f"P{user_index:04d}"
        if user_index < left_users:
            events.append(_event(customer_id, 1, "favorite", event_id))
            event_id += 1
            if user_index < both_users:
                events.append(_event(customer_id, 2, "favorite", event_id))
                event_id += 1
        else:
            events.append(_event(customer_id, 3, "view", event_id))
            event_id += 1
    return events


def _threshold_negative_behaviors(user_count, exposed_users, negative_users):
    events = []
    event_id = 1
    for user_index in range(user_count):
        customer_id = f"N{user_index:04d}"
        if user_index < exposed_users:
            events.append(_event(customer_id, 1, "favorite", event_id))
            event_id += 1
            events.append(_event(customer_id, 2, "view", event_id))
            event_id += 1
            if user_index < negative_users:
                events.append(_event(customer_id, 2, "avoid", event_id))
                event_id += 1
        else:
            events.append(_event(customer_id, 2, "view", event_id))
            event_id += 1
    return events


def _find_rule(rules, rule_type, product_a, product_b):
    return next((rule for rule in rules if (rule["rule_type"], rule["product_a"], rule["product_b"]) == (rule_type, product_a, product_b)), None)


class PairRulesTests(unittest.TestCase):
    def assert_equivalent(self, behaviors, product_ids):
        expected = reference_build_rules(behaviors, product_ids, **THRESHOLDS)
        actual = recommender.build_rules(behaviors, product_ids, **THRESHOLDS)
        self.assertEqual(actual, expected)

    def test_reference_and_user_traversal_match_small_data(self):
        self.assert_equivalent(_small_behaviors(), set(range(1, 7)))

    def test_reference_and_user_traversal_match_seeded_random_data(self):
        behaviors = _random_behaviors()
        actions = {event["behavior_type"] for event in behaviors}
        self.assertTrue({"favorite", "view", "avoid", "restore", "unfavorite"}.issubset(actions))
        self.assertTrue(any(event["created_at"] is None for event in behaviors))
        rules = reference_build_rules(behaviors, set(range(1, 65)), **THRESHOLDS)
        self.assertTrue(any(rule["rule_type"] == "positive" for rule in rules))
        self.assertTrue(any(rule["rule_type"] == "negative" for rule in rules))
        self.assert_equivalent(behaviors, set(range(1, 65)))

    def test_threshold_constants(self):
        self.assertEqual(recommender.MIN_POSITIVE_SUPPORT, 0.03)
        self.assertEqual(recommender.MIN_POSITIVE_USERS, 20)
        self.assertEqual(recommender.MIN_POSITIVE_EXPOSED, 30)
        self.assertEqual(recommender.MIN_NEGATIVE_SUPPORT, 0.01)
        self.assertEqual(recommender.MIN_NEGATIVE_USERS, 10)
        self.assertEqual(recommender.MIN_NEGATIVE_EXPOSED, 20)
        self.assertGreaterEqual(recommender.MIN_VALID_USERS, recommender.MIN_POSITIVE_EXPOSED)

    def test_positive_threshold_boundaries(self):
        support_boundary = recommender.build_rules(_threshold_positive_behaviors(800, 30, 24), {1, 2, 3})
        self.assertIsNotNone(_find_rule(support_boundary, "positive", 1, 2))
        support_below = recommender.build_rules(_threshold_positive_behaviors(800, 30, 23), {1, 2, 3})
        self.assertIsNone(_find_rule(support_below, "positive", 1, 2))

        common_boundary = recommender.build_rules(_threshold_positive_behaviors(600, 30, 20), {1, 2, 3})
        self.assertIsNotNone(_find_rule(common_boundary, "positive", 1, 2))
        common_below = recommender.build_rules(_threshold_positive_behaviors(600, 30, 19), {1, 2, 3})
        self.assertIsNone(_find_rule(common_below, "positive", 1, 2))

        exposed_boundary = recommender.build_rules(_threshold_positive_behaviors(600, 30, 20), {1, 2, 3})
        self.assertIsNotNone(_find_rule(exposed_boundary, "positive", 1, 2))
        exposed_below = recommender.build_rules(_threshold_positive_behaviors(600, 29, 20), {1, 2, 3})
        self.assertIsNone(_find_rule(exposed_below, "positive", 1, 2))

    def test_negative_threshold_boundaries(self):
        support_boundary = recommender.build_rules(_threshold_negative_behaviors(1000, 20, 10), {1, 2})
        self.assertIsNotNone(_find_rule(support_boundary, "negative", 1, 2))
        support_below = recommender.build_rules(_threshold_negative_behaviors(1000, 20, 9), {1, 2})
        self.assertIsNone(_find_rule(support_below, "negative", 1, 2))

        exposed_below = recommender.build_rules(_threshold_negative_behaviors(1000, 19, 10), {1, 2})
        self.assertIsNone(_find_rule(exposed_below, "negative", 1, 2))

    def test_build_rules_performance_on_full_synthetic_shape(self):
        behaviors = _random_behaviors(seed=20261125, product_count=80, user_count=1000, events_per_user=25)
        started = time.perf_counter()
        recommender.build_rules(behaviors, set(range(1, 81)))
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.0)


if __name__ == "__main__":
    unittest.main()
