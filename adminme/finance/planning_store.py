"""Private immutable dated-projection storage and bounded reads.

The file-adapter seam is disabled with finance publications. It has no provider
credentials, public ingestion endpoint or authority to accept accounting rows.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from adminme.events.envelope import EventEnvelope
from adminme.finance.dated_projection import validate_projection
from adminme.finance.importer import _settled
from adminme.finance.publication import PublicationError, canonical_bytes
from adminme.finance.queries import _owner
from adminme.lib.session import Session


def _coordinates(session: Session, book_id: str) -> tuple[str, str, str]:
    return session.tenant_id, _owner(session), book_id


def _anchored(conn: Any, p: dict[str, Any], *, current: bool) -> None:
    key = (p["tenant_id"], p["owner_id"], p["book_id"])
    row = conn.execute(
        "SELECT content_sha256, publication_json FROM finance_publications WHERE tenant_id=? AND owner_id=? AND book_id=? AND publication_id=?",
        (*key, p["accounting_publication_id"]),
    ).fetchone()
    if row is None or row[0] != p["accounting_content_sha256"]:
        raise PublicationError("projection_accounting_anchor_invalid")
    accounting = json.loads(row[1])
    if not set(p["accounting_revision_ids"]) <= set(
        accounting["accepted_journal_revision_ids"]
    ):
        raise PublicationError("projection_unaccepted_accounting_revision")
    if current:
        head = conn.execute(
            "SELECT publication_id FROM finance_publication_heads WHERE tenant_id=? AND owner_id=? AND book_id=?",
            key,
        ).fetchone()
        if head is None or head[0] != p["accounting_publication_id"]:
            raise PublicationError("projection_accounting_anchor_not_current")


def apply_projection(envelope: dict[str, Any], conn: Any) -> None:
    p = validate_projection(envelope["payload"]["projection"])
    scope = f"private:{p['owner_id']}"
    if (
        envelope["tenant_id"] != p["tenant_id"]
        or envelope["owner_scope"] != scope
        or envelope["visibility_scope"] != scope
        or envelope["sensitivity"] != "privileged"
    ):
        raise PublicationError("projection_scope_invalid")
    key = (p["tenant_id"], p["owner_id"], p["book_id"])
    existing = conn.execute(
        "SELECT content_sha256 FROM finance_dated_projections WHERE tenant_id=? AND owner_id=? AND book_id=? AND projection_id=?",
        (*key, p["projection_id"]),
    ).fetchone()
    if existing:
        if existing[0] != p["content_sha256"]:
            raise PublicationError("projection_identity_conflict")
        return
    _anchored(conn, p, current=True)
    head = conn.execute(
        "SELECT projection_id FROM finance_dated_projection_heads WHERE tenant_id=? AND owner_id=? AND book_id=?",
        key,
    ).fetchone()
    if (head[0] if head else None) != p["prior_projection_id"]:
        raise PublicationError("projection_prior_conflict")
    metadata = {
        k: v
        for k, v in p.items()
        if k
        not in {
            "detail_events",
            "shared_event_inventory",
            "evidence_ids",
            "gaps",
            "gap_issues",
            "evidence",
            "sources",
        }
    }
    metadata["detail_manifest"] = [
        {k: v for k, v in manifest.items() if k != "record_ids"}
        for manifest in p["detail_manifest"]
    ]
    conn.execute(
        "INSERT INTO finance_dated_projections VALUES (?,?,?,?,?,?,?)",
        (
            *key,
            p["projection_id"],
            p["content_sha256"],
            canonical_bytes(metadata).decode(),
            envelope["event_id"],
        ),
    )
    issue_counts: dict[tuple[str, str], dict[int, dict[str, int]]] = {}
    for issue_ordinal, issue in enumerate(p.get("gap_issues", [])):
        for membership in issue["memberships"]:
            context_id = membership["context_id"]
            counts = issue_counts.setdefault((issue["gap_id"], context_id), {})
            for days in membership["horizon_days"]:
                count = counts.setdefault(days, {"total": 0, "required": 0})
                count["total"] += 1
                count["required"] += int(issue["required"])
            record = {k: v for k, v in issue.items() if k != "memberships"}
            record["horizon_days"] = membership["horizon_days"]
            conn.execute(
                "INSERT INTO finance_dated_projection_refs VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    *key,
                    p["projection_id"],
                    "issues",
                    issue["issue_id"],
                    context_id,
                    issue_ordinal,
                    canonical_bytes(record).decode(),
                ),
            )
    for kind in ("gaps", "evidence", "sources"):
        for ordinal, record in enumerate(p[kind]):
            ref_id = record[
                {"gaps": "gap_id", "evidence": "evidence_id", "sources": "source_id"}[
                    kind
                ]
            ]
            context_ids = record.get("context_ids")
            if context_ids is None:
                context_ids = sorted(
                    {
                        c
                        for e in p["evidence"]
                        if e["source_id"] == ref_id
                        for c in e["context_ids"]
                    }
                )
            for context_id in context_ids:
                scoped_record = record
                if kind == "gaps" and record.get("issue_group"):
                    counts = issue_counts[(ref_id, context_id)]
                    scoped_record = {
                        **record,
                        "horizon_days": sorted(counts),
                        "issue_counts_by_horizon": counts,
                    }
                conn.execute(
                    "INSERT INTO finance_dated_projection_refs VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        *key,
                        p["projection_id"],
                        kind,
                        ref_id,
                        context_id,
                        ordinal,
                        canonical_bytes(scoped_record).decode(),
                    ),
                )
    for row in p.get("shared_event_inventory", []):
        conn.execute(
            "INSERT INTO finance_dated_shared_events VALUES (?,?,?,?,?,?)",
            (
                *key,
                p["projection_id"],
                row["record_id"],
                canonical_bytes(
                    {k: v for k, v in row.items() if k != "record_id"}
                ).decode(),
            ),
        )
    if "shared_event_inventory" in p:
        for manifest in p["detail_manifest"]:
            for ordinal, record_id in enumerate(manifest["record_ids"]):
                conn.execute(
                    "INSERT INTO finance_dated_event_bindings VALUES (?,?,?,?,?,?,?)",
                    (
                        *key,
                        p["projection_id"],
                        manifest["context_id"],
                        ordinal,
                        record_id,
                    ),
                )
    event_ordinals: dict[str, int] = {}
    for row in p["detail_events"]:
        context_id = row["context_id"]
        ordinal = event_ordinals.get(context_id, 0)
        conn.execute(
            "INSERT INTO finance_dated_projection_events VALUES (?,?,?,?,?,?,?,?)",
            (
                *key,
                p["projection_id"],
                context_id,
                ordinal,
                row["event_id"],
                canonical_bytes(row).decode(),
            ),
        )
        event_ordinals[context_id] = ordinal + 1
    conn.execute(
        "INSERT INTO finance_dated_projection_heads VALUES (?,?,?,?) ON CONFLICT(tenant_id,owner_id,book_id) DO UPDATE SET projection_id=excluded.projection_id",
        (*key, p["projection_id"]),
    )


async def import_projection(
    runtime: Any, session: Session, value: dict[str, Any]
) -> dict[str, Any]:
    if not runtime.settings.finance_publications_enabled:
        raise PublicationError("projection_ingress_disabled")
    if (
        session.auth_role != "principal"
        or session.is_view_as
        or session.service_context is not None
    ):
        raise PublicationError("projection_owner_authority_required")
    lock = getattr(runtime, "_finance_publication_lock", None)
    if lock is None:
        lock = runtime._finance_publication_lock = asyncio.Lock()
    if lock.locked():
        raise PublicationError("projection_ingress_busy")
    async with lock:
        p = await asyncio.to_thread(validate_projection, value)
        if (
            p["tenant_id"] != session.tenant_id
            or p["owner_id"] != session.auth_member_id
        ):
            raise PublicationError("projection_owner_authority_required")
        if datetime.fromisoformat(p["verified_at"]) > datetime.now(
            timezone.utc
        ) + timedelta(minutes=5):
            raise PublicationError("projection_future_clock")
        event_key = (
            "finance-dated-projection:"
            + hashlib.sha256(
                canonical_bytes(
                    [
                        p[k]
                        for k in (
                            "tenant_id",
                            "owner_id",
                            "book_id",
                            "prior_projection_id",
                        )
                    ]
                )
            ).hexdigest()
        )
        prior = await runtime.event_log.get_by_idempotency(session.tenant_id, event_key)
        if prior:
            previous = prior["payload"]["projection"]
            if (
                previous["projection_id"] != p["projection_id"]
                or previous["content_sha256"] != p["content_sha256"]
            ):
                raise PublicationError("projection_identity_or_prior_conflict")
            await _settled(runtime)
            return {
                "state": "verified",
                "projection_id": p["projection_id"],
                "event_id": prior["event_id"],
                "idempotent": True,
                "provider_effect_count": 0,
            }
        await _settled(runtime)

        def check(conn):
            _anchored(conn, p, current=True)
            key = _coordinates(session, p["book_id"])
            head = conn.execute(
                "SELECT projection_id FROM finance_dated_projection_heads WHERE tenant_id=? AND owner_id=? AND book_id=?",
                key,
            ).fetchone()
            if (head[0] if head else None) != p["prior_projection_id"]:
                raise PublicationError("projection_prior_conflict")
            if conn.execute(
                "SELECT 1 FROM finance_dated_projections WHERE tenant_id=? AND owner_id=? AND book_id=? AND projection_id=?",
                (*key, p["projection_id"]),
            ).fetchone():
                raise PublicationError("projection_identity_conflict")

        await runtime.projections.read("money", check)
        now = datetime.now(timezone.utc)
        scope = f"private:{session.auth_member_id}"
        event_id = await runtime.events.write(
            EventEnvelope(
                event_at_ms=int(now.timestamp() * 1000),
                tenant_id=session.tenant_id,
                type="finance.projection_verified",
                schema_version=1,
                occurred_at=now.isoformat(),
                source_adapter="accepted_books_projection",
                source_account_id=p["book_id"],
                owner_scope=scope,
                visibility_scope=scope,
                sensitivity="privileged",
                actor_identity=session.event_actor_identity,
                idempotency_key=event_key,
                payload={"projection": p},
            )
        )
        await _settled(runtime)
        return {
            "state": "verified",
            "projection_id": p["projection_id"],
            "event_id": event_id,
            "idempotent": False,
            "provider_effect_count": 0,
        }


def projection_head(conn: Any, session: Session, book_id: str) -> str | None:
    row = conn.execute(
        "SELECT projection_id FROM finance_dated_projection_heads WHERE tenant_id=? AND owner_id=? AND book_id=?",
        _coordinates(session, book_id),
    ).fetchone()
    return row[0] if row else None


def _compact_range(value: dict[str, Any]) -> list[int | None]:
    return [value["lower_minor"], value["upper_minor"]]


def _summary(context: dict[str, Any], horizon: dict[str, Any]) -> dict[str, Any]:
    unknowns: dict[str, str] = {}
    for name, value in (
        ("opening", context["opening"]),
        ("encumbered", context["encumbered"]),
        ("closing", horizon["closing"]),
        ("minimum", horizon["minimum"]),
        ("cash_in", horizon["cash_in"]),
        ("cash_out", horizon["cash_out"]),
    ):
        if value.get("kind") == "CONSERVATIVE_SOURCE_SCALAR":
            continue
        for endpoint in ("lower", "upper"):
            reason = value[f"{endpoint}_unknown_reason"]
            if reason is not None:
                unknowns[f"{name}.{endpoint}"] = reason
    for name in (
        "reserve_threshold",
        "checking_threshold",
        "reserve_breach",
        "negative_bank_cash",
        "checking_shortage",
    ):
        if horizon[name]["unknown_reason"] is not None:
            unknowns[name] = horizon[name]["unknown_reason"]
    return {
        "days": horizon["days"],
        "start_inclusive": horizon["start_inclusive"],
        "end_exclusive": horizon["end_exclusive"],
        "estimate_state": horizon["estimate_state"],
        "completeness_state": horizon["completeness_state"],
        "safety_state": horizon["safety_state"],
        "opening": _compact_range(context["opening"]),
        "encumbered": _compact_range(context["encumbered"]),
        "closing": _compact_range(horizon["closing"]),
        "minimum": dict(horizon["minimum"])
        if horizon["minimum"].get("kind") == "CONSERVATIVE_SOURCE_SCALAR"
        else _compact_range(horizon["minimum"]),
        "cash_in": _compact_range(horizon["cash_in"]),
        "cash_out": _compact_range(horizon["cash_out"]),
        "reserve_threshold": horizon["reserve_threshold"]["value_minor"],
        "checking_threshold": horizon["checking_threshold"]["value_minor"],
        "reserve_breach": horizon["reserve_breach"]["value"],
        "negative_bank_cash": horizon["negative_bank_cash"]["value"],
        "checking_shortage": horizon["checking_shortage"]["value"],
        "unknowns": unknowns,
        "conditions": horizon["conditions"],
        "evidence_ids": horizon["evidence_ids"],
        "minimum_date": horizon["minimum_date"],
        "first_negative_date": horizon["first_negative_date"],
        "first_zero_crossing_date": horizon["first_zero_crossing_date"],
        "first_reserve_breach_date": horizon["first_reserve_breach_date"],
    }


def query_projection(
    conn: Any,
    session: Session,
    *,
    book_id: str,
    projection_id: str | None = None,
    context_id: str | None = None,
    horizon_days: int = 30,
    report_id: str = "planning_contexts",
    offset: int = 0,
    limit: int = 1,
    selector: dict[str, str] | None = None,
    view: str = "all",
    reference_id: str | None = None,
    gap_group_id: str | None = None,
) -> dict[str, Any]:
    if (
        type(offset) is not int
        or not 0 <= offset <= 10000
        or type(limit) is not int
        or not 1 <= limit <= 100
    ):
        raise PublicationError("projection_pagination_invalid")
    if horizon_days not in {7, 30, 90, 180, 365} or type(horizon_days) is not int:
        raise PublicationError("projection_horizon_invalid")
    if view not in {"all", "balances", "signals", "dates", "provenance", "clocks"}:
        raise PublicationError("projection_view_invalid")
    if reference_id is not None and report_id not in {
        "planning_gaps",
        "planning_issues",
        "planning_evidence",
        "planning_sources",
    }:
        raise PublicationError("projection_reference_filter_invalid")
    if gap_group_id is not None and report_id != "planning_issues":
        raise PublicationError("projection_issue_group_filter_invalid")
    selector = selector or {}
    required_axes = {"measure", "scenario_id", "income_basis", "opening_basis"}
    allowed_axes = required_axes | {
        "plan_case_id",
        "currency",
        "timezone",
        "as_of_date",
    }
    if not set(selector) <= allowed_axes or (context_id is not None and selector):
        raise PublicationError("projection_selector_invalid")
    key = _coordinates(session, book_id)
    head_id = projection_head(conn, session, book_id)
    requested = projection_id or head_id
    row = conn.execute(
        "SELECT metadata_json FROM finance_dated_projections WHERE tenant_id=? AND owner_id=? AND book_id=? AND projection_id=?",
        (*key, requested),
    ).fetchone()
    if row is None:
        return {
            "state": "missing_evidence",
            "required_evidence": ["verified_dated_projection"],
        }
    p = json.loads(row[0])
    accounting_head = conn.execute(
        "SELECT publication_id FROM finance_publication_heads WHERE tenant_id=? AND owner_id=? AND book_id=?",
        key,
    ).fetchone()
    result: dict[str, Any] = {
        "state": "qualified",
        "result_kind": report_id,
        "publication": {
            "book_id": book_id,
            "publication_id": p["accounting_publication_id"],
            "content_hash": p["accounting_content_sha256"],
            "current_head_publication_id": accounting_head[0]
            if accounting_head
            else None,
        },
        "projection": {
            "id": requested,
            "hash": p["content_sha256"],
            "head_id": head_id,
        },
        "engine": [p["engine_id"], p["engine_version"], p["input_sha256"]],
        "refresh_state": p["refresh_state"],
        "unsupported_collections": [
            name for name, state in p["collections"].items() if state == "unsupported"
        ],
        "source_clocks": p["source_clocks"],
        "limits": [
            "historical_projection",
            "non_additive_measures",
            "positive_remainder_is_not_available_surplus",
        ],
    }
    if accounting_head is None or accounting_head[0] != p["accounting_publication_id"]:
        result["limits"].append("accounting_baseline_superseded")
    # Fixed view sections preserve their full conditions and provenance pins;
    # they do not drop arbitrary fields to squeeze a result under its budget.
    if view != "all":
        # These fixed qualifications are rendered for every native planning
        # card by domain code; they are not variable producer evidence.
        result.pop("limits")
        if (
            accounting_head is None
            or accounting_head[0] != p["accounting_publication_id"]
        ):
            result["baseline_state"] = "superseded"
        result.pop("source_clocks")
        result["engine"] = result["engine"][:2]
    selected_contexts = [
        c
        for c in p["contexts"]
        if (context_id is None or c["context_id"] == context_id)
        and all(c[k] == v for k, v in selector.items())
    ]
    if report_id == "planning_contexts":
        result["contexts"] = [
            {
                k: v
                for k, v in c.items()
                if k
                not in {"opening", "encumbered", "mandatory_flow_coverage", "horizons"}
            }
            for c in selected_contexts[offset : offset + limit]
        ]
        result["next_offset"] = (
            offset + limit if offset + limit < len(selected_contexts) else None
        )
        result["total_contexts"] = len(selected_contexts)
        return result
    if context_id is None:
        if not required_axes <= set(selector) or len(selected_contexts) != 1:
            raise PublicationError("projection_exact_coordinates_required")
        context = selected_contexts[0]
        context_id = context["context_id"]
    else:
        context = next(
            (c for c in p["contexts"] if c["context_id"] == context_id), None
        )
    if context is None:
        raise PublicationError("projection_context_required")
    result["context"] = {
        k: v
        for k, v in context.items()
        if k not in {"opening", "encumbered", "horizons"}
    }
    if report_id == "planning_projection":
        horizon = next(h for h in context["horizons"] if h["days"] == horizon_days)
        summary = _summary(context, horizon)
        if view == "all":
            result["summary"] = summary
        else:
            shared = {
                "days",
                "start_inclusive",
                "end_exclusive",
                "estimate_state",
                "completeness_state",
                "safety_state",
                "conditions",
                "evidence_ids",
            }
            balances = {
                "opening",
                "encumbered",
                "closing",
                "minimum",
                "cash_in",
                "cash_out",
            }
            signals = {
                "reserve_threshold",
                "checking_threshold",
                "reserve_breach",
                "negative_bank_cash",
                "checking_shortage",
            }
            dates = {
                "minimum_date",
                "first_negative_date",
                "first_zero_crossing_date",
                "first_reserve_breach_date",
            }
            fields = shared | (
                balances
                if view == "balances"
                else signals
                if view == "signals"
                else dates
                if view == "dates"
                else set()
            )
            result["view"] = view
            result["summary"] = {k: v for k, v in summary.items() if k in fields}
            if view in {"balances", "signals", "dates"}:
                result["summary"]["unknowns"] = {
                    k: v
                    for k, v in summary["unknowns"].items()
                    if k.split(".")[0] in fields
                }
            elif view == "clocks":
                result["source_clocks"] = p["source_clocks"]
            else:
                result["engine"] = [
                    p["engine_id"],
                    p["engine_version"],
                    p["input_sha256"],
                ]
                result["verification"] = {
                    "id": p["verification_id"],
                    "published_at": p["published_at"],
                    "verified_at": p["verified_at"],
                }
    elif report_id in {
        "planning_gaps",
        "planning_issues",
        "planning_evidence",
        "planning_sources",
    }:
        kind = report_id.removeprefix("planning_")
        rows = conn.execute(
            "SELECT record_json FROM finance_dated_projection_refs WHERE tenant_id=? AND owner_id=? AND book_id=? AND projection_id=? AND kind=? AND context_id=? AND (? IS NULL OR reference_id=?) AND (? NOT IN ('gaps','issues') OR EXISTS(SELECT 1 FROM json_each(record_json,'$.horizon_days') WHERE value=?)) AND (? IS NULL OR json_extract(record_json,'$.gap_id')=?) ORDER BY ordinal LIMIT ? OFFSET ?",
            (
                *key,
                requested,
                kind,
                context_id,
                reference_id,
                reference_id,
                kind,
                horizon_days,
                gap_group_id,
                gap_group_id,
                limit + 1,
                offset,
            ),
        ).fetchall()
        result["records"] = []
        for row in rows[:limit]:
            record = {
                k: v
                for k, v in json.loads(row[0]).items()
                if k not in {"context_ids", "audience_member_ids"}
            }
            counts_by_horizon = record.pop("issue_counts_by_horizon", None)
            if counts_by_horizon is not None:
                record["issue_counts"] = counts_by_horizon[str(horizon_days)]
            result["records"].append(record)
        result["next_offset"] = offset + limit if len(rows) > limit else None
        result["horizon_days"] = horizon_days
    elif report_id == "planning_engines":
        result["engines"] = p["engines"]
        result["verification"] = {
            "id": p["verification_id"],
            "published_at": p["published_at"],
            "verified_at": p["verified_at"],
        }
        result["horizon_days"] = horizon_days
    elif report_id == "planning_events":
        end = (
            date.fromisoformat(context["as_of_date"]) + timedelta(days=horizon_days + 1)
        ).isoformat()
        if "shared_inventory_sha256" in p:
            rows = conn.execute(
                "SELECT e.event_json FROM finance_dated_event_bindings b JOIN finance_dated_shared_events e USING(tenant_id,owner_id,book_id,projection_id,record_id) WHERE b.tenant_id=? AND b.owner_id=? AND b.book_id=? AND b.projection_id=? AND b.context_id=? AND (json_extract(e.event_json,'$.earliest_date') IS NULL OR json_extract(e.event_json,'$.earliest_date') < ?) ORDER BY b.ordinal LIMIT ? OFFSET ?",
                (*key, requested, context_id, end, limit + 1, offset),
            ).fetchall()
            result["events"] = [
                {**json.loads(r[0]), "context_id": context_id} for r in rows[:limit]
            ]
        else:
            rows = conn.execute(
                "SELECT event_json FROM finance_dated_projection_events WHERE tenant_id=? AND owner_id=? AND book_id=? AND projection_id=? AND context_id=? AND (json_extract(event_json,'$.earliest_date') IS NULL OR json_extract(event_json,'$.earliest_date') < ?) ORDER BY ordinal LIMIT ? OFFSET ?",
                (*key, requested, context_id, end, limit + 1, offset),
            ).fetchall()
            result["events"] = [json.loads(r[0]) for r in rows[:limit]]
        result["next_offset"] = offset + limit if len(rows) > limit else None
        result["horizon_days"] = horizon_days
        result["manifest"] = next(
            m for m in p["detail_manifest"] if m["context_id"] == context_id
        )
    else:
        raise PublicationError("projection_report_unknown")
    return result
