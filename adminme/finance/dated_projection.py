"""Typed transport for supported Books planning outputs, never a forecast.

Accounting acceptance and projection verification are distinct. All arithmetic
outputs arrive in producer-rounded minor units. Conditional estimates retain
null endpoints, missing-evidence reasons and their non-additive measure.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker

from adminme.finance.publication import PublicationError, canonical_bytes, content_hash

CONTRACT = "household-books.dated-projection.v1"
SCHEMA_PATH = Path(__file__).parent / "contracts" / f"{CONTRACT}.schema.json"
MAX_BYTES = 8 * 1024 * 1024
BANK = "BANK_CASH_SUBSET"
FUNDING = "CASH_EQUIVALENT_FUNDING_NOT_BANK"
HORIZONS = {7, 30, 90, 180, 365}


def schema_hash() -> str:
    return hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()


def rows_hash(rows: Iterable[dict[str, Any]]) -> str:
    """Canonical ordered-array digest without building a repeated JSON array."""
    digest = hashlib.sha256(b"[")
    separator = b""
    for row in rows:
        digest.update(separator)
        digest.update(canonical_bytes(row))
        separator = b","
    digest.update(b"]")
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(
        json.loads(SCHEMA_PATH.read_bytes()), format_checker=FormatChecker()
    )


def _nullable(value: Any, reason: Any) -> None:
    if (value is None) != (reason is not None):
        raise PublicationError("projection_unknown_reason_required")


def _range(value: dict[str, Any], *, nonnegative: bool = False) -> None:
    lower, upper = value["lower_minor"], value["upper_minor"]
    _nullable(lower, value["lower_unknown_reason"])
    _nullable(upper, value["upper_unknown_reason"])
    if lower is not None and upper is not None and lower > upper:
        raise PublicationError("projection_range_reversed")
    if nonnegative and any(v is not None and v < 0 for v in (lower, upper)):
        raise PublicationError("projection_negative_amount")


def _known(value: dict[str, Any]) -> bool:
    if value.get("kind") == "CONSERVATIVE_SOURCE_SCALAR":
        return value["value_minor"] is not None
    return value["lower_minor"] is not None and value["upper_minor"] is not None


def _minimum_values(value: dict[str, Any]) -> list[int | None]:
    if value.get("kind") == "CONSERVATIVE_SOURCE_SCALAR":
        _nullable(value["value_minor"], value["unknown_reason"])
        return [value["value_minor"]]
    _range(value)
    return [value["lower_minor"], value["upper_minor"]]


def _zero(value: dict[str, Any]) -> bool:
    return value["lower_minor"] == value["upper_minor"] == 0


def _context(context: dict[str, Any], chosen: str) -> None:
    ZoneInfo(context["timezone"])
    as_of = date.fromisoformat(context["as_of_date"])
    if context["scenario_id"] == "chosen" and context["plan_case_id"] != chosen:
        raise PublicationError("projection_chosen_control_mismatch")
    _range(context["opening"])
    _range(context["encumbered"], nonnegative=True)
    if {h["days"] for h in context["horizons"]} != HORIZONS:
        raise PublicationError("projection_horizons_incomplete")
    for horizon in context["horizons"]:
        if date.fromisoformat(horizon["start_inclusive"]) != as_of + timedelta(
            days=1
        ) or date.fromisoformat(horizon["end_exclusive"]) != as_of + timedelta(
            days=horizon["days"] + 1
        ):
            raise PublicationError("projection_local_date_boundary")
        if (
            context["opening_basis"] == "verified_unrestricted"
            and not _known(context["opening"])
            and horizon["estimate_state"] != "UNAVAILABLE"
        ):
            raise PublicationError("projection_verified_opening_unavailable")
        for name in ("cash_in", "cash_out"):
            _range(horizon[name], nonnegative=True)
        _range(horizon["closing"])
        estimates = [
            horizon["closing"]["lower_minor"],
            horizon["closing"]["upper_minor"],
            *_minimum_values(horizon["minimum"]),
        ]
        if horizon["estimate_state"] == "UNAVAILABLE" and any(
            value is not None for value in estimates
        ):
            raise PublicationError("projection_unavailable_has_values")
        if horizon["estimate_state"] == "PROVIDED" and (
            not horizon["evidence_ids"] or all(value is None for value in estimates)
        ):
            raise PublicationError("projection_estimate_evidence_required")
        if horizon["completeness_state"] == "INCOMPLETE" and not horizon["conditions"]:
            raise PublicationError("projection_missing_conditions")
        for name in ("reserve_threshold", "checking_threshold"):
            threshold = horizon[name]
            _nullable(threshold["value_minor"], threshold["unknown_reason"])
            if threshold["value_minor"] is not None and threshold["value_minor"] < 0:
                raise PublicationError("projection_negative_threshold")
        for name in ("reserve_breach", "negative_bank_cash", "checking_shortage"):
            signal = horizon[name]
            _nullable(signal["value"], signal["unknown_reason"])
        if (
            horizon["reserve_threshold"]["value_minor"] is None
            and horizon["reserve_breach"]["value"] is not None
        ) or (
            horizon["checking_threshold"]["value_minor"] is None
            and horizon["checking_shortage"]["value"] is not None
        ):
            raise PublicationError("projection_signal_without_threshold")
        if context["measure"] == FUNDING and any(
            horizon[name]["value"] is not None
            for name in ("negative_bank_cash", "checking_shortage")
        ):
            raise PublicationError("projection_funding_is_not_bank_cash")
        for name in (
            "minimum_date",
            "first_negative_date",
            "first_reserve_breach_date",
            "first_zero_crossing_date",
        ):
            dated = horizon[name]
            _nullable(dated["date"], dated["unknown_reason"])
            if dated["date"] is not None and (
                not date.fromisoformat(horizon["start_inclusive"])
                <= date.fromisoformat(dated["date"])
                < date.fromisoformat(horizon["end_exclusive"])
                or horizon["estimate_state"] == "UNAVAILABLE"
            ):
                raise PublicationError("projection_signal_date_invalid")
        for signal, dated in (
            ("negative_bank_cash", "first_negative_date"),
            ("reserve_breach", "first_reserve_breach_date"),
        ):
            if horizon[signal]["value"] is False and horizon[dated]["date"] is not None:
                raise PublicationError("projection_signal_date_conflict")
        if horizon["safety_state"] == "SOURCE_VERIFIED" and (
            context["measure"] != BANK
            or context["opening_basis"] != "verified_unrestricted"
            or not _known(context["opening"])
            or context["opening"]["lower_minor"] != context["opening"]["upper_minor"]
            or not _known(context["encumbered"])
            or context["mandatory_flow_coverage"] != "COMPLETE"
            or horizon["completeness_state"] != "COMPLETE"
            or horizon["estimate_state"] != "PROVIDED"
            or horizon["conditions"]
            or not all(_known(horizon[name]) for name in ("closing", "minimum"))
            or any(
                horizon[name]["value"] is None
                for name in (
                    "reserve_breach",
                    "negative_bank_cash",
                    "checking_shortage",
                )
            )
        ):
            raise PublicationError("projection_safety_not_established")


def _events(
    contexts: dict[str, dict[str, Any]], rows: list[dict[str, Any]], evidence: set[str]
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    included: set[tuple[str, str]] = set()
    for row in rows:
        context_id = row["context_id"]
        context = contexts.get(context_id)
        if context is None or (context_id, row["event_id"]) in seen:
            raise PublicationError("projection_detail_identity_invalid")
        seen.add((context_id, row["event_id"]))
        start = date.fromisoformat(context["as_of_date"]) + timedelta(days=1)
        end = start + timedelta(days=365)
        if row["earliest_date"] is None:
            if row["latest_date"] is not None or not row.get("date_unknown_reason"):
                raise PublicationError("projection_undated_reason_required")
        else:
            earliest = date.fromisoformat(row["earliest_date"])
            latest = date.fromisoformat(row["latest_date"])
            # Original possible-date bounds are inclusive. The context horizon
            # is [start, end): overlap admits a source row, not certainty that
            # it occurs inside the horizon. Never clip the source window.
            if earliest > latest or earliest >= end or latest < start:
                raise PublicationError("projection_detail_date_invalid")
        if not set(row["evidence_ids"]) <= evidence:
            raise PublicationError("projection_detail_evidence_invalid")
        _range(row["amount"], nonnegative=True)
        _range(row["bank_effect"])
        _range(row["debt_effect"])
        recognition = (context_id, row["recognition_id"])
        if row["included"]:
            if recognition in included:
                raise PublicationError("projection_duplicate_recognition")
            included.add(recognition)
        if row["kind"] == "card_purchase" and not _zero(row["bank_effect"]):
            raise PublicationError("projection_card_purchase_bank_debit")
        if row["kind"] == "internal_transfer" and (
            row["direction"] != "neutral"
            or not _zero(row["bank_effect"])
            or not _zero(row["debt_effect"])
        ):
            raise PublicationError("projection_transfer_not_neutral")
        if row["kind"] == "card_settlement" and _known(row["amount"]):
            lower = -row["amount"]["upper_minor"]
            upper = -row["amount"]["lower_minor"]
            if row["direction"] != "outflow" or any(
                row[name]["lower_minor"] != lower or row[name]["upper_minor"] != upper
                for name in ("bank_effect", "debt_effect")
            ):
                raise PublicationError("projection_settlement_legs_invalid")
        grouped[context_id].append(row)
    return grouped


def _validate_issues(
    result: dict[str, Any],
    contexts: dict[str, dict[str, Any]],
    gaps: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
) -> None:
    """Require reversible groups without weakening original required flags."""
    issues = result.get("gap_issues", [])
    if "gap_issues" in result:
        manifest = result["issue_manifest"]
        if (
            manifest["row_count"] != len(issues)
            or manifest["required_count"] != sum(issue["required"] for issue in issues)
            or manifest["rows_sha256"] != rows_hash(issues)
        ):
            raise PublicationError("projection_issue_manifest_mismatch")
    seen: set[str] = set()
    applicable: dict[tuple[str, int], set[str]] = defaultdict(set)
    required: dict[tuple[str, int], set[str]] = defaultdict(set)
    group_contexts: dict[str, set[str]] = defaultdict(set)
    group_horizons: dict[str, set[int]] = defaultdict(set)
    for issue in result.get("gap_issues", []):
        group = gaps.get(issue["gap_id"])
        if issue["issue_id"] in seen or not group or not group.get("issue_group"):
            raise PublicationError("projection_issue_identity_or_group_invalid")
        seen.add(issue["issue_id"])
        if issue["audience_member_ids"] != [result["owner_id"]]:
            raise PublicationError("projection_issue_audience_invalid")
        if any("://" in issue[name] for name in ("explanation", "required_input")):
            raise PublicationError("projection_private_link_not_permitted")
        if not set(issue["evidence_ids"]) <= evidence.keys():
            raise PublicationError("projection_issue_evidence_unknown")
        seen_contexts = set()
        for membership in issue["memberships"]:
            context_id = membership["context_id"]
            days = set(membership["horizon_days"])
            if (
                context_id in seen_contexts
                or context_id not in contexts
                or context_id not in group["context_ids"]
                or not days <= set(group["horizon_days"])
            ):
                raise PublicationError("projection_issue_membership_invalid")
            if any(
                context_id not in evidence[e]["context_ids"]
                for e in issue["evidence_ids"]
            ):
                raise PublicationError("projection_issue_evidence_scope_invalid")
            seen_contexts.add(context_id)
            group_contexts[issue["gap_id"]].add(context_id)
            group_horizons[issue["gap_id"]].update(days)
            for day in days:
                applicable[(context_id, day)].add(issue["gap_id"])
                if issue["required"]:
                    required[(context_id, day)].add(issue["gap_id"])
    for group_id, group in gaps.items():
        if group.get("issue_group") and (
            set(group["context_ids"]) != group_contexts[group_id]
            or set(group["horizon_days"]) != group_horizons[group_id]
        ):
            raise PublicationError("projection_issue_group_coverage_invalid")
    for context_id, context in contexts.items():
        for horizon in context["horizons"]:
            key = (context_id, horizon["days"])
            conditions = set(horizon["conditions"])
            if not required[key] <= conditions:
                raise PublicationError("projection_required_issue_unrepresented")
            if required[key] and (
                horizon["completeness_state"] != "INCOMPLETE"
                or horizon["safety_state"] != "NOT_ESTABLISHED"
            ):
                raise PublicationError("projection_required_issue_safety_conflict")
            if any(
                gaps[g].get("issue_group") and g not in applicable[key]
                for g in conditions
                if g in gaps
            ):
                raise PublicationError("projection_issue_condition_scope_invalid")


def validate_projection(value: dict[str, Any]) -> dict[str, Any]:
    """Validate the complete producer export without recalculating its forecast."""
    try:
        raw = canonical_bytes(value)
        if len(raw) > MAX_BYTES:
            raise PublicationError("projection_size_limit")
        result = json.loads(raw)
        _validator().validate(result)
        if result["schema_sha256"] != schema_hash():
            raise PublicationError("projection_schema_mismatch")
        if result["content_sha256"] != content_hash(result):
            raise PublicationError("projection_content_mismatch")
        if result["projection_id"] == result["prior_projection_id"]:
            raise PublicationError("projection_self_reference")
        verified = datetime.fromisoformat(result["verified_at"])
        if datetime.fromisoformat(result["published_at"]) > verified:
            raise PublicationError("projection_clock_order")
        for clock in result["source_clocks"].values():
            _nullable(clock["value"], clock["unknown_reason"])
            if (
                clock["value"] is not None
                and datetime.fromisoformat(clock["value"]) > verified
            ):
                raise PublicationError("projection_clock_order")
        engines = {e["role"]: e for e in result["engines"]}
        if set(engines) != {"dated", "funding", "income"}:
            raise PublicationError("projection_engine_manifest_incomplete")
        if any(
            engines["dated"][k] != result[k]
            for k in ("engine_id", "engine_version", "input_sha256")
        ):
            raise PublicationError("projection_dated_engine_mismatch")
        sources = {source["source_id"]: source for source in result["sources"]}
        if len(sources) != len(result["sources"]):
            raise PublicationError("projection_source_identity_invalid")
        for source in sources.values():
            local_verified = verified.astimezone(ZoneInfo(source["timezone"])).date()
            balance = source["balance_as_of"]
            _nullable(balance["date"], balance["unknown_reason"])
            if (
                balance["date"] is not None
                and date.fromisoformat(balance["date"]) > local_verified
            ):
                raise PublicationError("projection_future_balance_date")
            for name in (
                "provider_as_of",
                "provider_sync_at",
                "retrieved_at",
                "observed_at",
            ):
                clock = source[name]
                _nullable(clock["value"], clock["unknown_reason"])
                if (
                    clock["value"] is not None
                    and datetime.fromisoformat(clock["value"]) > verified
                ):
                    raise PublicationError("projection_clock_order")
        evidence = set(result["evidence_ids"])
        if len(evidence) != len(result["evidence_ids"]):
            raise PublicationError("projection_duplicate_evidence")
        contexts = {c["context_id"]: c for c in result["contexts"]}
        if len(contexts) != len(result["contexts"]):
            raise PublicationError("projection_duplicate_context")
        coordinates: set[tuple[Any, ...]] = set()
        for context in contexts.values():
            _context(context, result["selected_plan_case_id"])
            if (
                date.fromisoformat(context["as_of_date"])
                > verified.astimezone(ZoneInfo(context["timezone"])).date()
            ):
                raise PublicationError("projection_future_context_date")
            coordinate = tuple(
                context[k]
                for k in (
                    "measure",
                    "scenario_id",
                    "income_basis",
                    "opening_basis",
                    "plan_case_id",
                    "currency",
                    "timezone",
                    "as_of_date",
                )
            )
            if coordinate in coordinates:
                raise PublicationError("projection_duplicate_coordinates")
            coordinates.add(coordinate)
            for horizon in context["horizons"]:
                if not set(horizon["evidence_ids"]) <= evidence:
                    raise PublicationError("projection_horizon_evidence_invalid")
        records = {e["evidence_id"]: e for e in result["evidence"]}
        gaps = {g["gap_id"]: g for g in result["gaps"]}
        if (
            set(records) != evidence
            or len(records) != len(result["evidence"])
            or len(gaps) != len(result["gaps"])
        ):
            raise PublicationError("projection_evidence_manifest_invalid")
        for record in [*records.values(), *gaps.values()]:
            if len(set(record["context_ids"])) != len(record["context_ids"]):
                raise PublicationError("projection_evidence_context_duplicate")
            if record["audience_member_ids"] != [result["owner_id"]] or not set(
                record["context_ids"]
            ) <= set(contexts):
                raise PublicationError("projection_evidence_audience_invalid")
            for name in ("description", "explanation", "required_input"):
                if "://" in record.get(name, ""):
                    raise PublicationError("projection_private_link_not_permitted")
        for record in records.values():
            if record["source_id"] not in sources:
                raise PublicationError("projection_evidence_source_unknown")
        for gap in gaps.values():
            if not set(gap["evidence_ids"]) <= evidence:
                raise PublicationError("projection_gap_evidence_unknown")
            if any(
                not set(gap["context_ids"]) <= set(records[e]["context_ids"])
                for e in gap["evidence_ids"]
            ):
                raise PublicationError("projection_gap_evidence_context_invalid")
        for context in contexts.values():
            for horizon in context["horizons"]:
                for gap_id in horizon["conditions"]:
                    gap = gaps.get(gap_id)
                    if (
                        gap is None
                        or context["context_id"] not in gap["context_ids"]
                        or horizon["days"] not in gap["horizon_days"]
                    ):
                        raise PublicationError("projection_gap_scope_invalid")
                if any(
                    context["context_id"] not in records[e]["context_ids"]
                    for e in horizon["evidence_ids"]
                ):
                    raise PublicationError("projection_evidence_context_invalid")
        _validate_issues(result, contexts, gaps, records)
        manifests = {m["context_id"]: m for m in result["detail_manifest"]}
        if len(manifests) != len(result["detail_manifest"]) or set(manifests) != set(
            contexts
        ):
            raise PublicationError("projection_manifest_contexts_mismatch")
        inventory = None
        referenced: set[str] = set()
        grouped = {}
        if "shared_event_inventory" in result:
            inventory = {
                row["record_id"]: row for row in result["shared_event_inventory"]
            }
            if len(inventory) != len(result["shared_event_inventory"]):
                raise PublicationError("projection_shared_event_identity_conflict")
            if result["shared_inventory_sha256"] != rows_hash(
                result["shared_event_inventory"]
            ):
                raise PublicationError("projection_shared_inventory_hash_mismatch")
        else:
            grouped = _events(contexts, result["detail_events"], evidence)
        # Validate one bounded context at a time. Shared inventory bodies are
        # never expanded into the union of all context selections in memory.
        bank_paths: dict[tuple[Any, ...], bytes] = {}
        for context_id, context in contexts.items():
            manifest = manifests[context_id]
            if inventory is not None:
                record_ids = manifest["record_ids"]
                if (
                    len(record_ids) != len(set(record_ids))
                    or not set(record_ids) <= inventory.keys()
                ):
                    raise PublicationError("projection_shared_reference_invalid")
                referenced.update(record_ids)
                rows = [
                    {
                        **{
                            k: v
                            for k, v in inventory[record_id].items()
                            if k != "record_id"
                        },
                        "context_id": context_id,
                    }
                    for record_id in record_ids
                ]
                _events({context_id: context}, rows, evidence)
            else:
                rows = grouped.get(context_id, [])
            for row in rows:
                if any(
                    context_id not in records[e]["context_ids"]
                    for e in row["evidence_ids"]
                ):
                    raise PublicationError("projection_evidence_context_invalid")
                if any(
                    g not in gaps or context_id not in gaps[g]["context_ids"]
                    for g in row["conditions"]
                ):
                    raise PublicationError("projection_gap_scope_invalid")
            if manifest["row_count"] != len(rows) or manifest[
                "rows_sha256"
            ] != rows_hash(rows):
                raise PublicationError("projection_manifest_coverage_mismatch")
            # Bank aliases retain the same source path across income selectors.
            # Hash only compares source values; it never computes a forecast.
            if context["measure"] != BANK:
                continue
            coordinate = tuple(
                context[k]
                for k in (
                    "scenario_id",
                    "opening_basis",
                    "plan_case_id",
                    "currency",
                    "timezone",
                    "as_of_date",
                )
            )
            path = hashlib.sha256(
                canonical_bytes(
                    {
                        "context": {
                            k: v
                            for k, v in context.items()
                            if k not in {"context_id", "income_basis"}
                        },
                        "events_sha256": rows_hash(
                            {
                                k: v
                                for k, v in row.items()
                                if k not in {"event_id", "context_id"}
                            }
                            for row in rows
                        ),
                    }
                )
            ).digest()
            if coordinate in bank_paths and bank_paths[coordinate] != path:
                raise PublicationError("projection_bank_income_basis_changed")
            bank_paths[coordinate] = path
        if inventory is not None and referenced != set(inventory):
            raise PublicationError("projection_shared_inventory_unreferenced")
        return result
    except PublicationError:
        raise
    except Exception:
        raise PublicationError("projection_invalid") from None
