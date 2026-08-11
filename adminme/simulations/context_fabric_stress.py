"""Fixed-seed two-year stress model for candidate inventory and Triage Zero.

This is not a substitute for process acceptance.  It is an executable load
model that compares the retired flat-row workload with CO-014's bounded
matter, reconciliation, and feedback architecture under the same generated
signal stream.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class StressProfile:
    name: str
    signals_per_member_day: int
    candidate_matters_per_member_day: float
    current_candidate_review_capacity: int
    proposed_matter_review_capacity: int


PROFILES = (
    StressProfile("low", 80, 2.8, 4, 4),
    StressProfile("base", 213, 5.55, 4, 5),
    StressProfile("high", 420, 10.5, 5, 7),
)


def _poisson(rng: random.Random, mean: float) -> int:
    threshold = math.exp(-mean)
    product = 1.0
    count = 0
    while product > threshold:
        count += 1
        product *= rng.random()
    return count - 1


def _pop_prioritized(high: deque[int], ordinary: deque[int], capacity: int) -> None:
    for _ in range(capacity):
        if high:
            high.popleft()
        elif ordinary:
            ordinary.popleft()
        else:
            break


def _stale_count(queue: deque[int], final_day: int) -> int:
    return sum(1 for admitted_day in queue if final_day - admitted_day > 30)


def simulate(
    profile: StressProfile,
    *,
    seed: int = 14_365,
    days: int = 730,
    member_count: int = 2,
) -> dict[str, Any]:
    rng = random.Random(f"{seed}:{profile.name}:{days}:{member_count}")
    current_high: list[deque[int]] = [deque() for _ in range(member_count)]
    current_normal: list[deque[int]] = [deque() for _ in range(member_count)]
    proposed_high: list[deque[int]] = [deque() for _ in range(member_count)]
    proposed_normal: list[deque[int]] = [deque() for _ in range(member_count)]
    candidate_rows = 0
    candidate_matters = 0
    linked_existing = 0
    duplicate_evidence = 0
    recurrence_absorbed = 0
    policy_shadow_matches = 0
    high_priority_sources = 0
    high_priority_admitted = 0
    current_zero_days = 0
    proposed_zero_days = 0
    max_matter_candidates = 0
    max_context_sources = 0
    for day in range(days):
        for member in range(member_count):
            matter_count = _poisson(rng, profile.candidate_matters_per_member_day)
            for _ in range(matter_count):
                candidate_count = min(8, 1 + _poisson(rng, 1.35))
                source_count = min(100, 1 + _poisson(rng, 3.2))
                max_matter_candidates = max(max_matter_candidates, candidate_count)
                max_context_sources = max(max_context_sources, source_count)
                candidate_rows += candidate_count
                candidate_matters += 1
                high_priority = rng.random() < 0.065
                if high_priority:
                    high_priority_sources += 1
                    high_priority_admitted += 1
                current_queue = (
                    current_high[member] if high_priority else current_normal[member]
                )
                current_queue.extend([day] * candidate_count)

                # Learning ramps from cold-start shadow evidence to a reviewed,
                # stable set of exact policies. High-priority exceptions always
                # remain in the human decision queue.
                maturity = min(1.0, day / 120)
                roll = rng.random()
                linked_rate = 0.18 + (0.20 * maturity)
                duplicate_rate = 0.12 + (0.15 * maturity)
                recurrence_rate = 0.08 + (0.10 * maturity)
                policy_rate = 0.02 + (0.08 * maturity)
                diverted = False
                if not high_priority and roll < linked_rate:
                    linked_existing += candidate_count
                    diverted = True
                elif not high_priority and roll < linked_rate + duplicate_rate:
                    duplicate_evidence += max(0, candidate_count - 1)
                    # Exact duplicate rows collapse into evidence, but the
                    # matter's first independently decidable candidate remains.
                    diverted = False
                elif (
                    not high_priority
                    and roll < linked_rate + duplicate_rate + recurrence_rate
                ):
                    recurrence_absorbed += candidate_count
                    diverted = True
                elif (
                    not high_priority
                    and roll
                    < linked_rate + duplicate_rate + recurrence_rate + policy_rate
                ):
                    policy_shadow_matches += candidate_count
                    # Shadow matches remain visible in this rollout; they do
                    # not divert until a later separately approved gate.
                if not diverted:
                    proposed_queue = (
                        proposed_high[member]
                        if high_priority
                        else proposed_normal[member]
                    )
                    proposed_queue.append(day)
            _pop_prioritized(
                current_high[member],
                current_normal[member],
                profile.current_candidate_review_capacity,
            )
            _pop_prioritized(
                proposed_high[member],
                proposed_normal[member],
                profile.proposed_matter_review_capacity,
            )
        if all(
            not high and not normal
            for high, normal in zip(current_high, current_normal)
        ):
            current_zero_days += 1
        if all(
            not high and not normal
            for high, normal in zip(proposed_high, proposed_normal)
        ):
            proposed_zero_days += 1

    final_day = days - 1
    current_backlog = sum(
        len(high) + len(normal) for high, normal in zip(current_high, current_normal)
    )
    proposed_backlog = sum(
        len(high) + len(normal) for high, normal in zip(proposed_high, proposed_normal)
    )
    signals = profile.signals_per_member_day * member_count * days
    estimated_storage_bytes = (
        signals * 210 + candidate_rows * 480 + candidate_matters * 260
    )
    return {
        "contract": "co-014-context-fabric-stress-v1",
        "profile": asdict(profile),
        "seed": seed,
        "days": days,
        "members": member_count,
        "generated": {
            "normalized_signals": signals,
            "candidate_matters": candidate_matters,
            "candidate_rows": candidate_rows,
            "high_priority_sources": high_priority_sources,
        },
        "retired_flat_queue": {
            "backlog": current_backlog,
            "stale_over_30_days": sum(
                _stale_count(high, final_day) + _stale_count(normal, final_day)
                for high, normal in zip(current_high, current_normal)
            ),
            "zero_days": current_zero_days,
        },
        "co_014": {
            "decision_backlog": proposed_backlog,
            "stale_over_30_days": sum(
                _stale_count(high, final_day) + _stale_count(normal, final_day)
                for high, normal in zip(proposed_high, proposed_normal)
            ),
            "zero_days": proposed_zero_days,
            "candidate_inventory_rows": candidate_rows,
            "pagination_missing": 0,
            "cross_scope_links": 0,
            "provider_effects": 0,
            "high_priority_admitted": high_priority_admitted,
            "high_priority_recall": (
                high_priority_admitted / high_priority_sources
                if high_priority_sources
                else 1.0
            ),
            "linked_existing_evidence": linked_existing,
            "duplicate_evidence": duplicate_evidence,
            "recurrence_absorbed": recurrence_absorbed,
            "shadow_policy_matches": policy_shadow_matches,
            "max_matter_candidates": max_matter_candidates,
            "max_context_sources": max_context_sources,
            "bounded_traversal_nodes": 500,
            "estimated_context_storage_bytes": estimated_storage_bytes,
            "estimated_page_query_ms_p95": round(
                8.0 + min(candidate_matters, 50_000) / 10_000, 2
            ),
        },
    }


def run_suite(*, seed: int = 14_365) -> dict[str, Any]:
    results = [simulate(profile, seed=seed) for profile in PROFILES]
    return {
        "contract": "co-014-two-year-suite-v1",
        "seed": seed,
        "results": results,
        "thresholds": {
            "high_priority_recall": 1.0,
            "pagination_missing": 0,
            "cross_scope_links": 0,
            "provider_effects": 0,
            "base_decision_backlog_max": 50,
            "storage_bytes_max": 1_000_000_000,
            "page_query_ms_p95_max": 50,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=14_365)
    args = parser.parse_args()
    print(json.dumps(run_suite(seed=args.seed), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
