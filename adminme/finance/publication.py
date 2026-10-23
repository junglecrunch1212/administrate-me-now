"""Canonical provider-neutral Books publication; no provider or Sheet credentials.

The JSON Schema is the interchange authority. Its exact UTF-8 bytes are hashed;
content uses sorted, ASCII JSON with compact separators, excluding its own hash.
Only accepted aggregate metrics cross this initial boundary, never a mutable tail.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

CONTRACT = "household-books.accepted-publication.v1"
SCHEMA_PATH = Path(__file__).parent / "contracts" / f"{CONTRACT}.schema.json"
MAX_PUBLICATION_BYTES = 4 * 1024 * 1024


class PublicationError(ValueError):
    """Safe error code only: never interpolate source values or validator errors."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def content_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        canonical_bytes({k: v for k, v in value.items() if k != "content_sha256"})
    ).hexdigest()


def schema_hash() -> str:
    return hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(
        json.loads(SCHEMA_PATH.read_bytes()), format_checker=FormatChecker()
    )


def validate_publication(value: dict[str, Any]) -> dict[str, Any]:
    """Validate an entire detached snapshot before any event/pointer can advance."""
    try:
        raw = canonical_bytes(value)
        if len(raw) > MAX_PUBLICATION_BYTES:
            raise PublicationError("publication_size_limit")
        value = json.loads(raw)
        _validator().validate(value)
        if value["schema_sha256"] != schema_hash():
            raise PublicationError("publication_schema_mismatch")
        if value["content_sha256"] != content_hash(value):
            raise PublicationError("publication_content_mismatch")
        if value["prior_publication_id"] == value["publication_id"]:
            raise PublicationError("publication_self_reference")
        accepted = set(value["accepted_journal_revision_ids"])
        pending = set(value["excluded_pending_revision_ids"])
        if (
            len(accepted) != len(value["accepted_journal_revision_ids"])
            or accepted & pending
        ):
            raise PublicationError("publication_acceptance_conflict")
        references = {r["evidence_id"] for r in value["source_references"]}
        if len(references) != len(value["source_references"]):
            raise PublicationError("publication_duplicate_evidence")
        report_ids: set[str] = set()
        metric_ids: set[str] = set()
        for report in value["reports"]:
            if report["report_id"] in report_ids:
                raise PublicationError("publication_duplicate_report")
            report_ids.add(report["report_id"])
            bases = {
                "actuals": "accounting_actual",
                "cash": "cash_movement",
                "budget": "chosen_budget",
                "observed_net": "observed_value",
                "balance_sheet": "accounting_actual",
                "scenarios": "forecast",
            }
            if (
                report["report_id"] in bases
                and report["basis"] != bases[report["report_id"]]
            ):
                raise PublicationError("publication_report_basis_invalid")
            if report["release_state"] == "ready" and not report["metrics"]:
                raise PublicationError("publication_empty_report")
            if report["release_state"] == "ready" and report["report_id"] in {
                "cash",
                "balance_sheet",
            }:
                if not value["source_coverage"] or any(
                    source["opening_state"] != "reconciled"
                    or source["coverage"] != "complete"
                    for source in value["source_coverage"]
                ):
                    raise PublicationError("publication_opening_or_coverage_missing")
            if date.fromisoformat(report["period_start"]) > date.fromisoformat(
                report["period_end"]
            ):
                raise PublicationError("publication_period_invalid")
            if report["release_state"] == "ready" and (
                any(q != "complete" for q in report["quality"].values())
                or report["required_evidence"]
            ):
                raise PublicationError("publication_release_incomplete")
            if report["release_state"] == "unavailable" and report["metrics"]:
                raise PublicationError("publication_unavailable_has_metrics")
            for metric in report["metrics"]:
                if metric["metric_id"] in metric_ids:
                    raise PublicationError("publication_duplicate_metric")
                metric_ids.add(metric["metric_id"])
                if metric["owner_id"] != value["owner_id"]:
                    raise PublicationError("publication_joint_authority_required")
                if (
                    metric["basis"] != report["basis"]
                    or metric["period_start"] != report["period_start"]
                    or metric["period_end"] != report["period_end"]
                ):
                    raise PublicationError("publication_metric_basis_mismatch")
                if (
                    not set(metric["revision_ids"]) <= accepted
                    or not set(metric["evidence_ids"]) <= references
                ):
                    raise PublicationError("publication_unaccepted_evidence")
                if metric["value_minor"] is not None and (
                    not metric["evidence_ids"] or metric["quality"] == "unknown"
                ):
                    raise PublicationError("publication_number_without_evidence")
                if metric["value_minor"] is None and metric["quality"] != "unknown":
                    raise PublicationError("publication_missing_not_unknown")
                if (
                    report["release_state"] == "ready"
                    and metric["quality"] != "accepted"
                ):
                    raise PublicationError("publication_release_incomplete")
                if report["report_id"] == "scenarios" and not metric["scenario_id"]:
                    raise PublicationError("publication_scenario_identity_required")
        # Clock dimensions remain independent; acceptance cannot precede creation.
        if datetime.fromisoformat(value["accepted_at"]) < datetime.fromisoformat(
            value["published_at"]
        ):
            raise PublicationError("publication_clock_order")
        return json.loads(raw)
    except PublicationError:
        raise
    except Exception:
        raise PublicationError("publication_invalid") from None


def validate_transition(
    previous: dict[str, Any] | None, current: dict[str, Any]
) -> None:
    """Publication acceptance has no authority to resume a paused recovery."""
    if previous is None:
        return
    held = set(previous["excluded_pending_revision_ids"])
    if previous["recovery_state"] == "paused" and (
        current["recovery_state"] != "paused"
        or not held <= set(current["excluded_pending_revision_ids"])
        or held & set(current["accepted_journal_revision_ids"])
    ):
        raise PublicationError("publication_recovery_hold")
