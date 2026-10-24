"""Wholly synthetic producer outputs; no household values or forecast engine."""

from copy import deepcopy
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
                            "minimum_date": {
                                "date": (as_of + timedelta(days=1)).isoformat(),
                                "unknown_reason": None,
                            },
                            "first_negative_date": {
                                "date": None,
                                "unknown_reason": "not_in_horizon"
                                if measure == BANK
                                else "not_bank_cash",
                            },
                            "first_reserve_breach_date": {
                                "date": (as_of + timedelta(days=1)).isoformat(),
                                "unknown_reason": None,
                            },
                            "cash_in": bounds(closing, closing + 1),
                            "cash_out": bounds(100, 101),
                            "first_zero_crossing_date": {
                                "date": None,
                                "unknown_reason": "not_in_horizon",
                            },
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
    context_ids = [c["context_id"] for c in contexts]
    value["engines"] = [
        {
            "role": role,
            "engine_id": value["engine_id"] if role == "dated" else "synthetic-" + role,
            "engine_version": "1.2",
            "input_sha256": value["input_sha256"] if role == "dated" else "c" * 64,
        }
        for role in ("dated", "funding", "income")
    ]
    value["sources"] = [
        {
            "source_id": "source-synthetic",
            "content_sha256": "a" * 64,
            "timezone": "America/New_York",
            "balance_as_of": {"date": None, "unknown_reason": "balance_clock_unknown"},
            **deepcopy(value["source_clocks"]),
            "provider_sync_at": {
                "value": None,
                "unknown_reason": "provider_sync_clock_unknown",
            },
            "refresh_state": "OFF",
        }
    ]
    value["evidence"] = [
        {
            "evidence_id": "evidence-synthetic",
            "context_ids": context_ids[:],
            "audience_member_ids": [value["owner_id"]],
            "artifact_ref": "artifact-synthetic",
            "content_sha256": "a" * 64,
            "source_id": "source-synthetic",
            "description": "Synthetic source reference; source content is not included.",
            "source_content_state": "not_included",
        }
    ]
    value["gaps"] = [
        {
            "gap_id": gap_id,
            "context_ids": context_ids[:],
            "horizon_days": [7, 30, 90, 180, 365],
            "audience_member_ids": [value["owner_id"]],
            "explanation": explanation,
            "required_input": needed,
            "timing": "before_safety_conclusion",
            "evidence_ids": ["evidence-synthetic"],
        }
        for gap_id, explanation, needed in (
            (
                "flow_coverage_incomplete",
                "Mandatory flow coverage is incomplete.",
                "Confirm the dated mandatory inflows and outflows for this context.",
            ),
            (
                "conditional_timing",
                "The source has conditional timing for this event.",
                "Confirm the earliest and latest settlement dates.",
            ),
            (
                "encumbrance_unverified",
                "Restricted or committed amounts are unknown.",
                "Confirm encumbrances before treating balances as unrestricted.",
            ),
        )
    ]
    for c in contexts:
        for h in c["horizons"]:
            h["conditions"].append("encumbrance_unverified")
    value["collections"] = {
        "cash_events": "included",
        "horizon_flows": "included",
        "daily_balances": "unsupported",
        "card_debt_paths": "unsupported",
        "account_shortfalls": "unsupported",
    }
    return seal(value)


def seal(value):
    if "gap_issues" in value:
        value["issue_manifest"] = {
            "row_count": len(value["gap_issues"]),
            "required_count": sum(issue["required"] for issue in value["gap_issues"]),
            "rows_sha256": rows_hash(value["gap_issues"]),
        }
    if "shared_event_inventory" in value:
        inventory = {row["record_id"]: row for row in value["shared_event_inventory"]}
        value["shared_inventory_sha256"] = rows_hash(value["shared_event_inventory"])
        for manifest in value["detail_manifest"]:
            manifest["row_count"] = len(manifest["record_ids"])
            manifest["rows_sha256"] = rows_hash(
                {
                    **{
                        k: v
                        for k, v in inventory[record_id].items()
                        if k != "record_id"
                    },
                    "context_id": manifest["context_id"],
                }
                for record_id in manifest["record_ids"]
            )
    else:
        value["detail_manifest"] = [
            {
                "context_id": c["context_id"],
                "row_count": len(rows),
                "rows_sha256": rows_hash(rows),
            }
            for c in value["contexts"]
            for rows in [
                [
                    r
                    for r in value["detail_events"]
                    if r["context_id"] == c["context_id"]
                ]
            ]
        ]
    value["content_sha256"] = content_hash(value)
    return value


def with_unavailable_openings(value):
    """Add the second opening selection without inventing a verified balance."""
    from copy import deepcopy

    extra = []
    for original in value["contexts"]:
        context = deepcopy(original)
        context["context_id"] += "-verified"
        context["opening_basis"] = "verified_unrestricted"
        context["opening"] = bounds(None, reason="verified_opening_unavailable")
        for horizon in context["horizons"]:
            horizon["estimate_state"] = "UNAVAILABLE"
            horizon["closing"] = bounds(None, reason="verified_opening_unavailable")
            horizon["minimum"] = bounds(None, reason="verified_opening_unavailable")
            horizon["conditions"].append("verified_opening_unavailable")
            for name in ("reserve_breach", "negative_bank_cash", "checking_shortage"):
                horizon[name] = {
                    "value": None,
                    "unknown_reason": "verified_opening_unavailable",
                }
            for name in (
                "minimum_date",
                "first_negative_date",
                "first_reserve_breach_date",
                "first_zero_crossing_date",
            ):
                horizon[name] = {
                    "date": None,
                    "unknown_reason": "verified_opening_unavailable",
                }
        extra.append(context)
    extra_ids = [c["context_id"] for c in extra]
    value["contexts"].extend(extra)
    for record in [*value["gaps"], *value["evidence"]]:
        record["context_ids"].extend(extra_ids)
    value["gaps"].append(
        {
            "gap_id": "verified_opening_unavailable",
            "context_ids": extra_ids,
            "horizon_days": [7, 30, 90, 180, 365],
            "audience_member_ids": [value["owner_id"]],
            "explanation": "The verified-opening selection has no verified amount.",
            "required_input": "Provide a dated verified unrestricted opening, with its source evidence.",
            "timing": "before_safety_conclusion",
            "evidence_ids": ["evidence-synthetic"],
        }
    )
    return seal(value)


def shared_projection(accounting):
    """Synthetic 3,215-body/38,382-reference fit case, not native parity."""
    value = with_unavailable_openings(projection(accounting))
    value["shared_event_inventory"] = [
        {
            "record_id": f"record-{index:04d}",
            "event_id": f"shared-{index:04d}",
            "earliest_date": None if index % 97 == 0 else "2026-03-08",
            "latest_date": None if index % 97 == 0 else "2026-03-08",
            "date_unknown_reason": "source_undated" if index % 97 == 0 else None,
            "kind": "income",
            "account_route_id": "route-synthetic",
            "recognition_id": f"recognition-{index:04d}",
            "included": True,
            "direction": "inflow",
            "amount": bounds(1),
            "bank_effect": bounds(1),
            "debt_effect": bounds(0),
            "evidence_ids": ["evidence-synthetic"],
            "conditions": [],
        }
        for index in range(3215)
    ]
    groups = {}
    value["detail_manifest"] = []
    for context in value["contexts"]:
        key = (
            context["measure"],
            context["scenario_id"],
            context["opening_basis"],
            None if context["measure"] == BANK else context["income_basis"],
        )
        group = groups.setdefault(key, len(groups))
        count = (
            1600
            if context["measure"] == BANK
            and context["opening_basis"] == "observed_cash_hypothetical"
            else 1599
        )
        value["detail_manifest"].append(
            {
                "context_id": context["context_id"],
                "record_ids": [
                    f"record-{(group * 200 + index) % 3215:04d}"
                    for index in range(count)
                ],
            }
        )
    assert len(groups) == 18
    return seal(value)


def with_conservative_minima(value):
    """Source scalar minima and explicitly absent zero-crossing evidence."""
    for context in value["contexts"]:
        for horizon in context["horizons"]:
            unavailable = horizon["estimate_state"] == "UNAVAILABLE"
            horizon["minimum"] = {
                "kind": "CONSERVATIVE_SOURCE_SCALAR",
                "value_minor": None if unavailable else 100,
                "unknown_reason": "verified_opening_unavailable"
                if unavailable
                else None,
            }
            horizon["first_zero_crossing_date"] = {
                "date": None,
                "unknown_reason": "source_does_not_provide_zero_crossing",
            }
    return seal(value)


def with_grouped_issues(value):
    """141 original issues, 49 required overall, exactly 46 per horizon."""
    context_ids = [context["context_id"] for context in value["contexts"]]
    groups = {}
    value["gap_issues"] = []
    for index in range(141):
        days = (
            [7, 30]
            if 43 <= index <= 45
            else [90, 180, 365]
            if 46 <= index <= 48
            else [7, 30, 90, 180, 365]
        )
        group_id = f"source-issues-{index // 16}"
        groups.setdefault(group_id, []).append(index)
        value["gap_issues"].append(
            {
                "issue_id": f"source-issue-{index:03d}",
                "gap_id": group_id,
                "required": index < 49,
                "audience_member_ids": [value["owner_id"]],
                "memberships": [
                    {"context_id": context_id, "horizon_days": days[:]}
                    for context_id in context_ids
                ],
                "explanation": f"Synthetic original issue {index} remains unresolved.",
                "required_input": "Provide the source evidence for this original issue.",
                "timing": "before_safety_conclusion",
                "evidence_ids": ["evidence-synthetic"],
            }
        )
    for group_id in groups:
        value["gaps"].append(
            {
                "gap_id": group_id,
                "issue_group": True,
                "context_ids": context_ids[:],
                "horizon_days": [7, 30, 90, 180, 365],
                "audience_member_ids": [value["owner_id"]],
                "explanation": "Grouped original issues retain their IDs, required flags and memberships.",
                "required_input": "Review the original issues in this group.",
                "timing": "before_safety_conclusion",
                "evidence_ids": ["evidence-synthetic"],
            }
        )
    for context in value["contexts"]:
        for horizon in context["horizons"]:
            needed = {
                issue["gap_id"]
                for issue in value["gap_issues"]
                if issue["required"]
                and any(
                    m["context_id"] == context["context_id"]
                    and horizon["days"] in m["horizon_days"]
                    for m in issue["memberships"]
                )
            }
            horizon["conditions"].extend(sorted(needed))
    return seal(value)
