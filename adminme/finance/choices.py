"""Explicit owner choices anchored to accepted Books, not editable accounting.

Only goals and preferences are integrated here. Joint agreements, financial
execution and professional disclosure retain their separate authority paths.
"""

from __future__ import annotations

import json
from typing import Any

from adminme.finance.workflows import FinanceGoalRecordedV1, FinancePreferenceRecordedV1
from adminme.lib.session import Session

CHOICE_TYPES: dict[
    str, type[FinanceGoalRecordedV1] | type[FinancePreferenceRecordedV1]
] = {
    "finance.goal_recorded": FinanceGoalRecordedV1,
    "finance.preference_recorded": FinancePreferenceRecordedV1,
}


class FinanceChoiceError(ValueError):
    pass


def choice_identity(event_type: str, payload: dict[str, Any]) -> tuple[str, str]:
    if event_type == "finance.goal_recorded":
        return "goal", payload["goal_id"]
    if event_type == "finance.preference_recorded":
        return "preference", payload["dimension"]
    raise FinanceChoiceError("unsupported_finance_choice")


def current_choice(
    conn: Any, session: Session, book: str, kind: str, choice_id: str
) -> dict[str, Any] | None:
    if (
        session.auth_role != "principal"
        or session.is_view_as
        or session.service_context is not None
        or f"private:{session.auth_member_id}" not in session.allowed_scopes
    ):
        raise FinanceChoiceError("finance_owner_required")
    row = conn.execute(
        "SELECT payload_json,last_event_id FROM finance_choices WHERE tenant_id=? AND owner_id=? AND book_id=? AND kind=? AND choice_id=?",
        (session.tenant_id, session.auth_member_id, book, kind, choice_id),
    ).fetchone()
    return {"record": json.loads(row[0]), "event_id": row[1]} if row else None


def list_choices(
    conn: Any, session: Session, book: str, offset: int = 0
) -> dict[str, Any]:
    if (
        session.auth_role != "principal"
        or session.is_view_as
        or session.service_context is not None
        or f"private:{session.auth_member_id}" not in session.allowed_scopes
        or not 0 <= offset <= 1000
    ):
        raise FinanceChoiceError("finance_owner_required")
    rows = conn.execute(
        "SELECT kind,payload_json,last_event_id FROM finance_choices WHERE tenant_id=? AND owner_id=? AND book_id=? ORDER BY kind,choice_id LIMIT 51 OFFSET ?",
        (session.tenant_id, session.auth_member_id, book, offset),
    ).fetchall()
    return {
        "items": [
            {"kind": row[0], "record": json.loads(row[1]), "event_id": row[2]}
            for row in rows[:50]
        ],
        "next_offset": offset + 50 if len(rows) > 50 else None,
    }


def validate_choice(conn: Any, envelope: dict[str, Any]) -> dict[str, Any]:
    event_type = envelope["type"]
    model = CHOICE_TYPES[event_type]
    record = model.model_validate(envelope["payload"])
    payload = record.model_dump(mode="json")
    scope = record.scope
    owner = scope.owner_member_id
    private = f"private:{owner}"
    if (
        envelope["owner_scope"] != private
        or envelope["visibility_scope"] != private
        or envelope["sensitivity"] != "privileged"
        or envelope.get("actor_identity") != owner
        or envelope.get("source_adapter") != "member-portal"
        or envelope.get("source_account_id") != owner
        or scope.applicable_adult_ids != [owner]
        or scope.audience_member_ids != [owner]
        or scope.policy_revision != "explicit-owner-choice-v1"
    ):
        raise FinanceChoiceError("finance_explicit_owner_choice_required")
    if (
        isinstance(record, FinanceGoalRecordedV1)
        and record.selected_by_member_id != owner
    ):
        raise FinanceChoiceError("finance_explicit_owner_choice_required")
    baseline = conn.execute(
        "SELECT 1 FROM finance_publications WHERE tenant_id=? AND owner_id=? AND book_id=? AND publication_id=?",
        (envelope["tenant_id"], owner, scope.book_id, scope.baseline_publication_id),
    ).fetchone()
    if baseline is None:
        raise FinanceChoiceError("finance_accepted_baseline_required")
    kind, identity = choice_identity(event_type, payload)
    prior = conn.execute(
        "SELECT payload_json,last_event_id FROM finance_choices WHERE tenant_id=? AND owner_id=? AND book_id=? AND kind=? AND choice_id=?",
        (envelope["tenant_id"], owner, scope.book_id, kind, identity),
    ).fetchone()
    if prior and prior[1] == envelope["event_id"]:
        return payload
    predecessor = prior[1] if prior else None
    if envelope.get("causation_id") != predecessor:
        raise FinanceChoiceError("finance_choice_changed")
    if isinstance(record, FinanceGoalRecordedV1):
        expected_revision = json.loads(prior[0])["revision"] + 1 if prior else 1
        if (
            record.revision != expected_revision
            or record.supersedes_event_id != predecessor
        ):
            raise FinanceChoiceError("finance_choice_changed")
    return payload


def apply_choice(envelope: dict[str, Any], conn: Any) -> None:
    try:
        payload = validate_choice(conn, envelope)
    except (ValueError, KeyError):
        # A forged or stale event cannot create authority during replay.
        return
    scope = payload["scope"]
    kind, identity = choice_identity(envelope["type"], payload)
    conn.execute(
        "INSERT INTO finance_choices VALUES (?,?,?,?,?,?,?) ON CONFLICT(tenant_id,owner_id,book_id,kind,choice_id) DO UPDATE SET payload_json=excluded.payload_json,last_event_id=excluded.last_event_id",
        (
            envelope["tenant_id"],
            scope["owner_member_id"],
            scope["book_id"],
            kind,
            identity,
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            envelope["event_id"],
        ),
    )
