"""Typed transport for supported Books planning outputs, never a forecast.

Accounting acceptance and projection verification are distinct. All arithmetic
outputs arrive in producer-rounded minor units. Conditional estimates retain
null endpoints, missing-evidence reasons and their non-additive measure.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
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


def rows_hash(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_bytes(rows)).hexdigest()


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
    return value["lower_minor"] is not None and value["upper_minor"] is not None


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
        for name in ("closing", "minimum"):
            _range(horizon[name])
            if horizon["estimate_state"] == "UNAVAILABLE" and any(
                horizon[name][endpoint] is not None
                for endpoint in ("lower_minor", "upper_minor")
            ):
                raise PublicationError("projection_unavailable_has_values")
        if horizon["estimate_state"] == "PROVIDED" and (
            not horizon["evidence_ids"]
            or all(
                horizon[name][endpoint] is None
                for name in ("closing", "minimum")
                for endpoint in ("lower_minor", "upper_minor")
            )
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
        if (
            not start
            <= date.fromisoformat(row["earliest_date"])
            <= date.fromisoformat(row["latest_date"])
            < end
        ):
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
        grouped = _events(contexts, result["detail_events"], evidence)
        for row in result["detail_events"]:
            if any(
                row["context_id"] not in records[e]["context_ids"]
                for e in row["evidence_ids"]
            ):
                raise PublicationError("projection_evidence_context_invalid")
            if any(
                g not in gaps or row["context_id"] not in gaps[g]["context_ids"]
                for g in row["conditions"]
            ):
                raise PublicationError("projection_gap_scope_invalid")
        manifests = {m["context_id"]: m for m in result["detail_manifest"]}
        if len(manifests) != len(result["detail_manifest"]) or set(manifests) != set(
            contexts
        ):
            raise PublicationError("projection_manifest_contexts_mismatch")
        for context_id, manifest in manifests.items():
            rows = grouped.get(context_id, [])
            if manifest["row_count"] != len(rows) or manifest[
                "rows_sha256"
            ] != rows_hash(rows):
                raise PublicationError("projection_manifest_coverage_mismatch")
        # Income replacement never changes a bank path. Compare only declared
        # contexts; no missing combinations or sums of measures are invented.
        bank_paths: dict[tuple[Any, ...], bytes] = {}
        for context_id, context in contexts.items():
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
            path = canonical_bytes(
                {
                    "context": {
                        k: v
                        for k, v in context.items()
                        if k not in {"context_id", "income_basis"}
                    },
                    "events": [
                        {
                            k: v
                            for k, v in row.items()
                            if k not in {"event_id", "context_id"}
                        }
                        for row in grouped.get(context_id, [])
                    ],
                }
            )
            if coordinate in bank_paths and bank_paths[coordinate] != path:
                raise PublicationError("projection_bank_income_basis_changed")
            bank_paths[coordinate] = path
        return result
    except PublicationError:
        raise
    except Exception:
        raise PublicationError("projection_invalid") from None
