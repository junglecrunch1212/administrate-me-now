"""Wholly synthetic producer outputs; no household values or forecast engine."""

from datetime import date, timedelta
from adminme.finance.dated_projection import (
    BANK,
    FUNDING,
    CONTRACT,
    schema_hash,
    rows_hash,
)
from adminme.finance.publication import content_hash


def bounds(lower, upper=None, reason="not_verified"):
    upper = lower if upper is None and lower is not None else upper
    return {
        "lower_minor": lower,
        "upper_minor": upper,
        "lower_unknown_reason": reason if lower is None else None,
        "upper_unknown_reason": reason if upper is None else None,
    }


def projection(accounting, total_events=0):
    contexts = []
    rows = []
    as_of = date(2026, 3, 7)
    for measure in (BANK, FUNDING):
        for scenario in ("current", "expected", "chosen"):
            for income in (
                "OBSERVED_BASE_ONLY",
                "PLAN_MODELED_NET_FUNDING_NOT_PAYROLL",
            ):
                context_id = f"{'bank' if measure == BANK else 'funding'}-{scenario}-{'base' if income == 'OBSERVED_BASE_ONLY' else 'plan'}"
                # Fixed independent outputs: funding replacement is 800, not
                # base 500 plus 800. Bank outputs remain exactly unchanged.
                closing = (
                    500 if measure == BANK or income == "OBSERVED_BASE_ONLY" else 800
                )
                context = {
                    "context_id": context_id,
                    "measure": measure,
                    "scenario_id": scenario,
                    "income_basis": income,
                    "opening_basis": "observed_cash_hypothetical",
                    "plan_case_id": "case-selected"
                    if scenario == "chosen"
                    else f"case-{scenario}",
                    "currency": "USD",
                    "timezone": "America/New_York",
                    "as_of_date": as_of.isoformat(),
                    "opening": bounds(1000),
                    "encumbered": bounds(None),
                    "mandatory_flow_coverage": "INCOMPLETE",
                    "horizons": [
                        {
                            "days": days,
                            "start_inclusive": (as_of + timedelta(days=1)).isoformat(),
                            "end_exclusive": (
                                as_of + timedelta(days=days + 1)
                            ).isoformat(),
                            "estimate_state": "PROVIDED",
                            "completeness_state": "INCOMPLETE",
                            "safety_state": "NOT_ESTABLISHED",
                            "closing": bounds(closing, closing + 1),
                            "minimum": bounds(100, 101),
                            "reserve_threshold": {
                                "value_minor": 200,
                                "unknown_reason": None,
                            },
                            "checking_threshold": {
                                "value_minor": None,
                                "unknown_reason": "route_unknown",
                            },
                            "reserve_breach": {"value": True, "unknown_reason": None},
                            "negative_bank_cash": {
                                "value": False if measure == BANK else None,
                                "unknown_reason": None
                                if measure == BANK
                                else "not_bank_cash",
                            },
                            "checking_shortage": {
                                "value": None,
                                "unknown_reason": "route_unknown",
                            },
                            "conditions": ["flow_coverage_incomplete"],
                            "evidence_ids": ["evidence-synthetic"],
                        }
                        for days in (7, 30, 90, 180, 365)
                    ],
                }
                contexts.append(context)
                count = total_events // 12 + (len(contexts) <= total_events % 12)
                for i in range(count):
                    day = (as_of + timedelta(days=1 + i % 365)).isoformat()
                    rows.append(
                        {
                            "event_id": f"event-{i}",
                            "context_id": context_id,
                            "earliest_date": day,
                            "latest_date": day,
                            "kind": "income",
                            "account_route_id": "route-synthetic",
                            "recognition_id": f"recognition-{i}",
                            "included": True,
                            "direction": "inflow",
                            "amount": bounds(closing),
                            "bank_effect": bounds(closing if measure == BANK else 0),
                            "debt_effect": bounds(0),
                            "evidence_ids": ["evidence-synthetic"],
                            "conditions": ["conditional_timing"],
                        }
                    )
    value = {
        "contract": CONTRACT,
        "schema_sha256": schema_hash(),
        "content_sha256": "0" * 64,
        "tenant_id": accounting["tenant_id"],
        "owner_id": accounting["owner_id"],
        "book_id": accounting["book_id"],
        "projection_id": "projection-synthetic-1",
        "prior_projection_id": None,
        "accounting_publication_id": accounting["publication_id"],
        "accounting_content_sha256": accounting["content_sha256"],
        "accounting_revision_ids": accounting["accepted_journal_revision_ids"],
        "engine_id": "synthetic-external-engine",
        "engine_version": "1.2",
        "input_sha256": "b" * 64,
        "published_at": "2026-03-07T18:00:00Z",
        "verified_at": "2026-03-07T18:01:00Z",
        "verification_id": "verification-synthetic",
        "selected_plan_case_id": "case-selected",
        "source_clocks": {
            "provider_as_of": {
                "value": None,
                "unknown_reason": "provider_clock_unknown",
            },
            "retrieved_at": {"value": "2026-03-07T17:00:00Z", "unknown_reason": None},
            "observed_at": {
                "value": None,
                "unknown_reason": "observation_clock_unknown",
            },
        },
        "refresh_state": "OFF",
        "rounding_policy": "PRODUCER_MINOR_UNITS",
        "evidence_ids": ["evidence-synthetic"],
        "contexts": contexts,
        "detail_manifest": [],
        "detail_events": rows,
    }
    return seal(value)


def seal(value):
    value["detail_manifest"] = [
        {
            "context_id": c["context_id"],
            "row_count": len(rows),
            "rows_sha256": rows_hash(rows),
        }
        for c in value["contexts"]
        for rows in [
            [r for r in value["detail_events"] if r["context_id"] == c["context_id"]]
        ]
    ]
    value["content_sha256"] = content_hash(value)
    return value
