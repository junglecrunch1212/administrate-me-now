"""The CoS combines authorized household facts without acquiring disclosure rights."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import create_application
from adminme.source_policy import policy_for_preset
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
        ] = {
            "policy": {
                **policy_for_preset("balanced").model_dump(mode="json"),
                "context_mode": "none",
            }
        }
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


def _observed_source(
    client: TestClient,
    member: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    version: int = 1,
    sensitivity: str = "normal",
) -> str:
    family = event_type.split(".")[0]
    projection = {
        "calendar": "calendars",
        "contact": "parties",
        "member_knowledge": "member_knowledge",
        "messaging": "interactions",
        "artifact": "artifacts",
    }[family]
    runtime = client.app.state.runtime
    event = EventEnvelope(
        event_at_ms=1,
        tenant_id=runtime.settings.instance.tenant_id,
        type=event_type,
        schema_version=version,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter=f"fixture-{family}",
        source_account_id=str(
            payload.get("provider_account_id") or f"account-{member}"
        ),
        owner_scope=f"private:{member}",
        visibility_scope=f"private:{member}",
        sensitivity=sensitivity,
        payload=payload,
    )
    event_id = client.portal.call(runtime.events.write, event)
    client.portal.call(runtime.wait_for_projection, projection, event_id)
    return str(event_id)


def _calendar(member: str, **changes: Any) -> dict[str, Any]:
    return {
        "member_id": member,
        "provider": "google_calendar",
        "provider_account_id": f"calendar-{member}",
        "calendar_external_id": "fixture-calendar",
        "external_event_id": f"appointment-{member}",
        "summary": "Preparation appointment",
        "description": "PROVIDER_BODY_MUST_NOT_ENTER_CONTEXT",
        "start": "2026-11-02T14:00:00Z",
        "end": "2026-11-02T15:00:00Z",
        "updated_at": "2026-11-01T12:00:00Z",
        **changes,
    }


def _contact(member: str, **changes: Any) -> dict[str, Any]:
    return {
        "contact_record_id": f"contact-{member}",
        "member_id": member,
        "provider": "fixture",
        "provider_account_id": f"contacts-{member}",
        "container_external_id": "fixture-address-book",
        "external_id": f"person-{member}",
        "kind": "person",
        "display_name": "Appointment coordinator",
        "identifiers": [
            {
                "kind": "email",
                "value": "coordinator@example.invalid",
                "value_normalized": "coordinator@example.invalid",
            }
        ],
        "fields": {"private_memo": "UNSTRUCTURED_CONTACT_MEMO"},
        "observed_at": "2026-11-01T12:00:00Z",
        **changes,
    }


def _note(member: str, **changes: Any) -> dict[str, Any]:
    return {
        "knowledge_id": f"note-{member}",
        "member_id": member,
        "title": "Preparation checklist",
        "body": "PROVIDER_NOTE_BODY_MUST_NOT_ENTER_CONTEXT",
        "source_kind": "fixture",
        "source_external_id": f"provider-note-{member}",
        "content_sha256": "a" * 64,
        "captured_at": "2026-11-01T12:00:00Z",
        **changes,
    }


def _message(member: str, **changes: Any) -> dict[str, Any]:
    return {
        "member_id": member,
        "provider": "fixture",
        "provider_account_id": f"mail-{member}",
        "workspace": "personal",
        "source_channel": "fixture",
        "native_message_id": f"message-{member}",
        "from_identifier": "coordinator@example.invalid",
        "to_identifier": "recipient@example.invalid",
        "subject": "Appointment preparation",
        "body_text": "PROVIDER_MAIL_BODY_MUST_NOT_ENTER_CONTEXT",
        "body_html": "<p>PROVIDER_HTML_MUST_NOT_ENTER_CONTEXT</p>",
        "received_at": "2026-11-01T12:00:00Z",
        **changes,
    }


def test_current_sources_support_private_coordination_without_provider_content(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        _grant_private(client, A, "source-context-grant-a")
        _grant_private(client, B, "source-context-grant-b")
        calendar = _observed_source(
            client, A, "calendar.event_added", _calendar(A), version=2
        )
        contact = _observed_source(
            client, B, "contact.record.observed", _contact(B), sensitivity="privileged"
        )
        note = _observed_source(
            client, B, "member_knowledge.note_added", _note(B), version=3
        )
        message = _observed_source(
            client, A, "messaging.received", _message(A), version=3
        )
        artifact = _observed_source(
            client,
            A,
            "artifact.received",
            {
                "source": "fixture",
                "external_artifact_id": "fixture-document",
                "mime_type": "text/plain",
                "size_bytes": 20,
                "filename": "preparation.txt",
                "sha256": "b" * 64,
                "artifact_ref": "PRIVATE_STORAGE_REFERENCE",
                "received_at": "2026-11-01T12:00:00Z",
            },
        )
        ids = [calendar, contact, note, message, artifact]
        response = _context(client, "read-bounded-source-facts", source_event_ids=ids)
        assert response.status_code == 200, response.text
        facts = response.json()["facts"]
        assert [fact["event_id"] for fact in facts] == ids
        assert facts[0]["fact"]["start"] == "2026-11-02T14:00:00Z"
        assert (
            facts[1]["fact"]["identifiers"][0]["value"] == "coordinator@example.invalid"
        )
        assert facts[1]["authority"]["source_sensitivity"] == "privileged"
        assert facts[2]["fact"]["knowledge_id"] == f"note-{B}"
        assert facts[3]["fact"]["subject"] == "Appointment preparation"
        assert facts[4]["fact"]["filename"] == "preparation.txt"
        for forbidden in (
            "PROVIDER_BODY",
            "PROVIDER_NOTE_BODY",
            "PROVIDER_MAIL_BODY",
            "PROVIDER_HTML",
            "PRIVATE_STORAGE_REFERENCE",
            "UNSTRUCTURED_CONTACT_MEMO",
        ):
            assert forbidden not in response.text
        assert all(fact["provider_body_included"] is False for fact in facts)
        created = client.post(
            CREATE,
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _service("coordinate-provider-facts"),
                "request_id": "coordinate-provider-facts",
                "record_kind": "note",
                "title": "Prepare for the appointment",
                "source_event_ids": ids,
            },
        )
        assert created.status_code == 201, created.text
        work = created.json()["work"]
        assert work["sensitivity"] == "privileged"
        task_id = created.json()["task_id"]
        assert {
            source["source_event_id"] for source in work["source_authorities"]
        } == set(ids)
        assert work["outside_disclosure_authorized"] is False
        for projection in (
            "calendars",
            "parties",
            "member_knowledge",
            "interactions",
            "tasks",
        ):
            client.portal.call(runtime.projections.rebuild, projection)
        assert (
            _context(client, "sources-after-rebuild", source_event_ids=ids).json()[
                "facts"
            ]
            == facts
        )
        assert (
            _read(client, "coordination-after-rebuild", task_id=task_id)["work"][0]
            == work
        )
        assert runtime.fixture_adapter.delivered == []
    with TestClient(create_application(settings)) as restarted:
        assert (
            _context(restarted, "sources-after-restart", source_event_ids=ids).json()[
                "facts"
            ]
            == facts
        )
        assert (
            _read(restarted, "coordination-after-restart", task_id=task_id)["work"][0]
            == work
        )


def test_source_updates_and_tombstones_do_not_resurrect_old_context(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        _grant_private(client, A, "current-snapshot-grant")
        first_calendar = _observed_source(
            client, A, "calendar.event_added", _calendar(A), version=2
        )
        first_contact = _observed_source(
            client, A, "contact.record.observed", _contact(A)
        )
        first_note = _observed_source(
            client, A, "member_knowledge.note_added", _note(A), version=3
        )
        first_message = _observed_source(
            client, A, "messaging.received", _message(A), version=3
        )
        calendar = _observed_source(
            client,
            A,
            "calendar.event_updated",
            _calendar(A, summary="Rescheduled appointment", status="cancelled"),
            version=2,
        )
        contact = _observed_source(
            client,
            A,
            "contact.record.observed",
            _contact(A, display_name="Updated coordinator"),
        )
        note = _observed_source(
            client,
            A,
            "member_knowledge.note_updated",
            _note(A, title="Updated checklist"),
            version=3,
        )
        message = _observed_source(
            client,
            A,
            "messaging.received",
            _message(A, subject="Changed appointment"),
            version=3,
        )
        for event_id in (first_calendar, first_contact, first_note, first_message):
            assert (
                _context(
                    client, f"old-snapshot-{event_id}", source_event_ids=[event_id]
                ).status_code
                == 404
            )
        response = _context(client, "current-snapshots").json()
        assert {fact["event_id"] for fact in response["facts"]} == {
            calendar,
            contact,
            note,
            message,
        }
        assert (
            next(fact for fact in response["facts"] if fact["event_id"] == calendar)[
                "fact"
            ]["status"]
            == "cancelled"
        )
        # A valid scan cursor remains usable after its observation was superseded.
        assert (
            _context(
                client, "cursor-after-update", after_event_id=first_calendar
            ).status_code
            == 200
        )
        _observed_source(
            client,
            A,
            "calendar.event_deleted",
            {
                "member_id": A,
                "provider": "google_calendar",
                "provider_account_id": f"calendar-{A}",
                "calendar_external_id": "fixture-calendar",
                "external_event_id": f"appointment-{A}",
                "deleted_at": "2026-11-03T12:00:00Z",
            },
            version=2,
        )
        _observed_source(
            client,
            A,
            "contact.record.deleted",
            {
                key: value
                for key, value in {
                    **_contact(A),
                    "deleted_at": "2026-11-03T12:00:00Z",
                }.items()
                if key
                in {
                    "contact_record_id",
                    "member_id",
                    "provider",
                    "provider_account_id",
                    "container_external_id",
                    "external_id",
                    "deleted_at",
                }
            },
        )
        _observed_source(
            client,
            A,
            "member_knowledge.note_deleted",
            {
                "knowledge_id": f"note-{A}",
                "member_id": A,
                "source_kind": "fixture",
                "source_external_id": f"provider-note-{A}",
                "deleted_at": "2026-11-03T12:00:00Z",
            },
        )
        for event_id in (calendar, contact, note):
            assert (
                _context(
                    client, f"deleted-snapshot-{event_id}", source_event_ids=[event_id]
                ).status_code
                == 404
            )
        assert {
            fact["event_id"]
            for fact in _context(client, "after-tombstones").json()["facts"]
        } == {message}


@pytest.mark.parametrize(
    "mode", ["none", "metadata_only", "content_on_explicit_authorized_demand"]
)
def test_inherited_policy_blocks_sources_and_retained_notes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    from adminme.source_policy import (
        parent_source_coordinate,
        source_coordinate_for_event,
    )

    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        runtime = client.app.state.runtime
        _grant_private(client, A, "inherited-policy-grant")
        source = _observed_source(
            client, A, "calendar.event_added", _calendar(A), version=2
        )
        created = client.post(
            CREATE,
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _service("create-source-derived-note"),
                "request_id": "create-source-derived-note",
                "title": "Source-derived preparation",
                "source_event_ids": [source],
            },
        )
        assert created.status_code == 201, created.text
        source_event = client.portal.call(runtime.event_log.get, source)
        coordinate = source_coordinate_for_event(
            source_event, provider_bindings=runtime.settings.provider_bindings
        )
        parent = parent_source_coordinate(coordinate)
        assert parent.key != coordinate.key
        state = runtime.settings_control.read()
        policy = policy_for_preset("balanced").model_dump(mode="json")
        policy["context_mode"] = mode
        state["source_policies_v2"].setdefault(A, {})[parent.key] = {"policy": policy}
        monkeypatch.setattr(runtime.settings_control, "read", lambda: state)
        assert (
            _context(
                client, "restricted-inherited-source", source_event_ids=[source]
            ).status_code
            == 404
        )
        assert _read(client, "restricted-inherited-work")["work"] == []
        assert "Source-derived preparation" not in str(
            _read(client, "restricted-inherited-history")
        )
        attempted = client.post(
            CREATE,
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _service("no-policy-bypass-note"),
                "request_id": "no-policy-bypass-note",
                "title": "Must remain blocked",
                "source_event_ids": [source],
            },
        )
        assert attempted.status_code == 404
        # Current source policy is re-evaluated, not frozen in the old note.
        policy["context_mode"] = "typed_facts_safe_snippet"
        assert (
            _context(
                client, "inherited-policy-restored", source_event_ids=[source]
            ).status_code
            == 200
        )
        assert (
            _read(client, "derived-note-restored")["work"][0]["task_id"]
            == created.json()["task_id"]
        )
        assert runtime.fixture_adapter.delivered == []


def test_context_bounds_metadata_and_withholds_locked_or_retracted_sources(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        calendar = _observed_source(
            client,
            A,
            "calendar.event_added",
            _calendar(A, summary="x" * 3000),
            version=2,
        )
        assert _context(client, "no-grant-source-context").json()["facts"] == []
        _grant_private(client, A, "bounded-source-context-grant")
        fact = _context(
            client, "bounded-source-title", source_event_ids=[calendar]
        ).json()["facts"][0]
        assert fact["fact"]["summary"] == "x" * 2000
        assert fact["fact_truncated"] is True
        locked = _observed_source(
            client, A, "member_knowledge.note_added", _note(A, locked=True), version=3
        )
        assert (
            _context(
                client, "locked-note-context", source_event_ids=[locked]
            ).status_code
            == 404
        )
        account = f"messages-{A}"
        self_id = f"self:{account}"
        message = _message(
            A,
            provider="bluebubbles",
            provider_account_id=account,
            source_channel="imessage",
            from_identifier="fixture-remote-endpoint",
            to_identifier=self_id,
            to_identifiers=[self_id],
            body_html=None,
            routing_scope="member_private",
            routing_target_id=A,
            bridge_enrollment_id="fixture-bridge-enrollment",
            device_identity_sha256="d" * 64,
            connector_id="messaging-bluebubbles",
            protected_source_id="protected-source:" + "e" * 64,
            native_chat_id="fixture-native-chat",
            native_revision="fixture-revision-1",
            native_revision_ms=1,
            direction="incoming",
            participants=[
                {"participant_id": "fixture-remote-endpoint", "role": "sender"},
                {"participant_id": self_id, "role": "self", "is_self": True},
            ],
            sender_id="fixture-remote-endpoint",
            observed_at="2026-11-01T12:00:00Z",
            transport_class="imessage",
            is_group=False,
            message_state="active",
            transport_version="1.9.9",
            transport_commit="ba31cd1cf6c03e154c18ffa2cd6da47a934c3be1",
            correlation_id="fixture-message-context",
        )
        first = _observed_source(client, A, "messaging.received", message, version=7)
        assert (
            _context(
                client, "active-message-context", source_event_ids=[first]
            ).status_code
            == 200
        )
        retracted = _observed_source(
            client,
            A,
            "messaging.received",
            {
                **message,
                "native_revision": "fixture-revision-2",
                "native_revision_ms": 2,
                "message_state": "retracted",
                "body_text": None,
            },
            version=7,
        )
        for event_id in (first, retracted):
            assert (
                _context(
                    client, "retracted-message-context", source_event_ids=[event_id]
                ).status_code
                == 404
            )
