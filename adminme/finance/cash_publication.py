"""Validate and present dated projection results produced by the accepted Books engine.

This module never computes a cash path, substitutes monthly averages or modifies
scenario math. It indexes the export's results and dated planned cash events.
"""

from __future__ import annotations
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HORIZONS = {7, 30, 90, 180, 365}
SCENARIOS = {"current", "expected", "chosen"}


def validate_cash_export(
    export: dict[str, Any], evidence_ids: set[str], accepted_at: datetime
) -> None:
    from adminme.finance.publication import PublicationError

    try:
        zone = ZoneInfo(export["timezone"])
    except ZoneInfoNotFoundError:
        raise PublicationError("cash_projection_timezone_invalid") from None
    as_of = date.fromisoformat(export["as_of_date"])
    if as_of > accepted_at.astimezone(zone).date():
        raise PublicationError("cash_projection_future_opening")
    scenarios = export["scenarios"]
    if len(scenarios) != 3 or {s["scenario_id"] for s in scenarios} != SCENARIOS:
        raise PublicationError("cash_projection_scenarios_incomplete")
    for scenario in scenarios:
        horizons = scenario["horizons"]
        if len(horizons) != 5 or {h["days"] for h in horizons} != HORIZONS:
            raise PublicationError("cash_projection_horizons_incomplete")
        event_ids: set[str] = set()
        for event in scenario["cash_events"]:
            earliest = date.fromisoformat(event["earliest_date"])
            latest = date.fromisoformat(event["latest_date"])
            if (
                event["cash_event_id"] in event_ids
                or earliest < as_of
                or latest < earliest
                or latest > as_of + timedelta(days=365)
            ):
                raise PublicationError("cash_projection_event_invalid")
            event_ids.add(event["cash_event_id"])
            if not set(event["evidence_ids"]) <= evidence_ids:
                raise PublicationError("cash_projection_evidence_invalid")
            if event["amount_minor"] is not None and not event["evidence_ids"]:
                raise PublicationError("cash_projection_number_without_evidence")
            if event["certainty"] == "unknown" and event["amount_minor"] is not None:
                raise PublicationError("cash_projection_unknown_amount")
        for horizon in horizons:
            end = as_of + timedelta(days=horizon["days"])
            if date.fromisoformat(horizon["end_date"]) != end:
                raise PublicationError("cash_projection_horizon_date_invalid")
            if not set(horizon["evidence_ids"]) <= evidence_ids:
                raise PublicationError("cash_projection_evidence_invalid")
            usable = (
                export["opening_reconciled"]
                and export["opening_liquid_minor"] is not None
                and export["mandatory_flow_coverage"] == "complete"
            )
            values = (
                horizon["closing_minor"],
                horizon["minimum_minor"],
                horizon["first_shortage_date"],
                horizon["first_surplus_date"],
            )
            if (not usable or horizon["release_state"] == "unavailable") and any(
                v is not None for v in values
            ):
                raise PublicationError("cash_projection_missing_input_verdict")
            if horizon["release_state"] == "qualified" and (
                not usable
                or horizon["closing_minor"] is None
                or horizon["minimum_minor"] is None
                or not horizon["evidence_ids"]
            ):
                raise PublicationError("cash_projection_results_incomplete")
            for key in ("first_shortage_date", "first_surplus_date"):
                if (
                    horizon[key] is not None
                    and not as_of <= date.fromisoformat(horizon[key]) <= end
                ):
                    raise PublicationError("cash_projection_signal_date_invalid")


def cash_reports(export: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose unchanged horizon results and expandable events as indexed rows."""
    template = {
        "release_state": "qualified",
        "basis": "forecast",
        "period_start": export["as_of_date"],
        "period_end": (
            date.fromisoformat(export["as_of_date"]) + timedelta(days=365)
        ).isoformat(),
        "model_id": export["model_id"],
        "model_version": export["model_version"],
        "quality": {
            k: "partial"
            for k in (
                "coverage",
                "reconciliation",
                "freshness",
                "classification",
                "model_validity",
            )
        },
        "required_evidence": ["current_source_freshness"],
        "engine_authority": "accepted_books",
        "timezone": export["timezone"],
        "opening_liquid_minor": export["opening_liquid_minor"],
        "opening_reconciled": export["opening_reconciled"],
        "mandatory_flow_coverage": export["mandatory_flow_coverage"],
        "chosen_floor_minor": export["chosen_floor_minor"],
    }
    summaries = {**template, "report_id": "cash_projection", "metrics": []}
    events = {**template, "report_id": "cash_events", "metrics": []}
    all_horizons = [h for s in export["scenarios"] for h in s["horizons"]]
    if all(h["release_state"] == "unavailable" for h in all_horizons):
        summaries["release_state"] = "unavailable"
    summaries["required_evidence"] = sorted(
        {"current_source_freshness"}
        | {item for h in all_horizons for item in h["required_evidence"]}
    )
    for scenario in export["scenarios"]:
        scenario_id = scenario["scenario_id"]
        for horizon in scenario["horizons"]:
            for key in ("closing_minor", "minimum_minor"):
                summaries["metrics"].append(
                    {
                        "metric_id": f"cash-summary-{scenario_id}-{horizon['days']}-{key}",
                        "value_minor": horizon[key],
                        "currency": export["currency"],
                        "unit": "minor_units",
                        "basis": "forecast",
                        "period_start": export["as_of_date"],
                        "period_end": horizon["end_date"],
                        "scenario_id": scenario_id,
                        "horizon_days": horizon["days"],
                        "quality": horizon["release_state"],
                        "evidence_ids": horizon["evidence_ids"],
                        "first_shortage_date": horizon["first_shortage_date"],
                        "first_surplus_date": horizon["first_surplus_date"],
                        "required_evidence": horizon["required_evidence"],
                    }
                )
        for event in sorted(
            scenario["cash_events"],
            key=lambda e: (e["earliest_date"], e["cash_event_id"]),
        ):
            amount = event["amount_minor"]
            events["metrics"].append(
                {
                    **event,
                    "metric_id": f"cash-event-{scenario_id}-{event['cash_event_id']}",
                    "value_minor": amount
                    if amount is None or event["direction"] == "inflow"
                    else -amount,
                    "currency": export["currency"],
                    "unit": "minor_units",
                    "basis": "forecast",
                    "period_start": event["earliest_date"],
                    "period_end": event["latest_date"],
                    "scenario_id": scenario_id,
                    "quality": "qualified",
                    "cash_event_is_actual": False,
                }
            )
    return [summaries, events]
