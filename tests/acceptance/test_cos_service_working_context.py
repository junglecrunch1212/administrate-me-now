"""The CoS combines authorized household facts without acquiring disclosure rights."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import create_application
from tests.acceptance.test_cos_identity_delegation_kernel import (
    A,
    B,
    B_HEADERS,
    OPENCLAW_HEADERS,
    _grant_private,
    _settings,
)
from tests.acceptance.test_cos_service_private_work import CREATE, READ, _read, _service

CONTEXT = "/api/openclaw/v1/tools/service-context"


def _source(
    client: TestClient, member: str, title: str, *, sensitivity: str = "normal"
) -> str:
    runtime = client.app.state.runtime
    event = EventEnvelope(
        event_at_ms=1,
        tenant_id=runtime.settings.instance.tenant_id,
        type="task.created",
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="member-fixture",
        source_account_id=member,
        owner_scope=f"private:{member}",
        visibility_scope=f"private:{member}",
        sensitivity=sensitivity,
        payload={
            "task_id": f"source-{member}-{title}",
            "title": title,
            "description": "Authorized operational fact",
            "owner_member_id": member,
        },
    )
    event_id = client.portal.call(runtime.events.write, event)
    client.portal.call(runtime.wait_for_projection, "tasks", event_id)
    return event_id


def _context(client: TestClient, correlation: str, **values: Any) -> Any:
    return client.post(
        CONTEXT,
        headers=OPENCLAW_HEADERS,
        json={"provenance": _service(correlation), **values},
    )


def test_staff_combines_two_heads_context_and_retains_confidential_provenance(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        first = _source(client, A, "Coordinate availability")
        second = _source(
            client, B, "Restricted client preparation", sensitivity="privileged"
        )
        assert _context(client, "before-authority").json()["facts"] == []
        grant_a = _grant_private(client, A, "standing-context-a")
        grant_b = _grant_private(client, B, "standing-context-b")
        context = _context(
            client, "combine-standing-context", source_event_ids=[first, second]
        )
        assert context.status_code == 200, context.text
        facts = context.json()["facts"]
        assert {fact["authority"]["authority_grant_id"] for fact in facts} == {
            grant_a,
            grant_b,
        }
        assert {fact["fact"]["title"] for fact in facts} == {
            "Coordinate availability",
            "Restricted client preparation",
        }
        assert all(fact["outside_disclosure_authorized"] is False for fact in facts)
        assert facts[1]["authority"]["source_sensitivity"] == "privileged"
        assert facts[1]["authority"]["restriction"] == "internal_processing_only"
        first_page = _context(client, "context-page-one", limit=1).json()
        assert len(first_page["facts"]) == 1 and first_page["next_cursor"]
        second_page = _context(
            client,
            "context-page-two",
            limit=1,
            after_event_id=first_page["next_cursor"],
        ).json()
        assert {
            first_page["facts"][0]["event_id"],
            second_page["facts"][0]["event_id"],
        } == {first, second}

        independent = client.post(
            CREATE,
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _service("independent-own-work"),
                "request_id": "independent-own-work",
                "title": "Older independent service work",
            },
        )
        assert independent.status_code == 201, independent.text
        for invalid_ids in ([first, first], [""]):
            assert (
                _context(
                    client, "invalid-source-shape", source_event_ids=invalid_ids
                ).status_code
                == 422
            )
        # The routine note uses existing standing authority, not another receipt.
        created = client.post(
            CREATE,
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _service("record-working-note"),
                "request_id": "record-working-note",
                "record_kind": "note",
                "title": "Household coordination note",
                "description": "Combine availability and client preparation internally",
                "source_event_ids": [first, second],
            },
        )
        assert created.status_code == 201, created.text
        work = created.json()["work"]
        task_id = created.json()["task_id"]
        assert work["record_kind"] == "note" and work["sensitivity"] == "privileged"
        assert {source["source_event_id"] for source in work["source_authorities"]} == {
            first,
            second,
        }
        assert work["outside_disclosure_authorized"] is False
        client.portal.call(runtime.projections.rebuild, "tasks")
        assert _read(client, "note-after-replay", task_id=task_id)["work"][0] == work
        private_event = created.json()["event_id"]
        # Re-saving a service-derived record as an unqualified source is refused.
        assert (
            _context(
                client, "no-source-laundering", source_event_ids=[private_event]
            ).status_code
            == 404
        )
        forbidden = client.post(
            CREATE,
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _service("no-disclosure-grant"),
                "request_id": "no-disclosure-grant",
                "title": "Must not authorize outside send",
                "source_event_ids": [second],
                "audience_ids": ["external-recipient"],
                "provider_id": "gmail",
            },
        )
        assert forbidden.status_code == 422
        assert runtime.fixture_adapter.delivered == []
        revoked = client.request(
            "DELETE",
            f"/api/member/v1/cos/delegations/{grant_b}",
            headers=B_HEADERS,
            json={
                "request_id": "revoke-standing-context-b",
                "reason": "fixture owner revocation",
            },
        )
        assert revoked.status_code == 200, revoked.text
        assert (
            _context(client, "revoked-source", source_event_ids=[second]).status_code
            == 404
        )
        denied = client.post(
            READ,
            headers=OPENCLAW_HEADERS,
            json={"provenance": _service("revoked-derived-note"), "task_id": task_id},
        )
        assert denied.status_code == 404
        state = _read(client, "revoked-list-and-audit")
        assert "Household coordination note" not in str(state)
        assert "Combine availability" not in str(state)
        assert (
            _read(client, "older-authorized-work-remains", limit=1)["work"][0][
                "task_id"
            ]
            == independent.json()["task_id"]
        )
        assert (
            _context(client, "other-head-remains", source_event_ids=[first]).status_code
            == 200
        )
        retained = client.portal.call(runtime.event_log.get, private_event)
        assert retained["payload"]["title"] == "Household coordination note"
        assert len(retained["payload"]["source_authorities"]) == 2

    with TestClient(create_application(settings)) as restarted:
        assert "Household coordination note" not in str(
            _read(restarted, "revocation-survives-restart")
        )
        assert (
            _context(restarted, "a-after-restart", source_event_ids=[first]).status_code
            == 200
        )


def test_source_processing_restriction_applies_to_retained_scratchpad(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        runtime = client.app.state.runtime
        source = _source(client, A, "Working context")
        _grant_private(client, A, "standing-scratchpad-context")
        created = client.post(
            CREATE,
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _service("scratchpad-create"),
                "request_id": "scratchpad-create",
                "title": "Useful scratchpad",
                "record_kind": "scratchpad",
                "source_event_ids": [source],
            },
        )
        assert created.status_code == 201, created.text
        authority = created.json()["work"]["source_authorities"][0]
        state = runtime.settings_control.read()
        state["source_policies_v2"].setdefault(authority["source_policy_owner_id"], {})[
            authority["source_policy_key"]
        ] = {"policy": {"context_mode": "none"}}
        monkeypatch.setattr(runtime.settings_control, "read", lambda: state)
        assert (
            _context(
                client, "processing-restricted", source_event_ids=[source]
            ).status_code
            == 404
        )
        assert _read(client, "scratchpad-restricted")["work"] == []
        assert "Useful scratchpad" not in str(_read(client, "restricted-audit"))
        assert runtime.fixture_adapter.delivered == []
