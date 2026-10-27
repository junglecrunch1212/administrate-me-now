"""The CoS combines authorized household facts without acquiring disclosure rights."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import create_application
from adminme.source_policy import policy_for_preset
from tests.acceptance.test_cos_identity_delegation_kernel import (
    A,
    A_HEADERS,
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


def _own_mail(
    client: TestClient,
    key: str,
    *,
    native_id: str | None = None,
    principal: str | None = None,
    tenant: str | None = None,
    body: str = "Please prepare the travel options.",
) -> str:
    from adminme.service_identities import service_private_scope
    from tests.acceptance.test_service_owned_cos_sources_direct_intake import (
        _service_message,
    )

    runtime = client.app.state.runtime
    event = _service_message(
        runtime,
        key=key,
        trusted_route_key=None,
        from_identifier="correspondent@example.invalid",
        subject=f"Travel {key}",
        body=body,
        event_at_ms=1,
    )
    if tenant:
        event = event.model_copy(update={"tenant_id": tenant})
    payload = dict(event.payload)
    if native_id:
        payload["native_message_id"] = native_id
    if principal:
        payload["service_principal_id"] = principal
        event = event.model_copy(
            update={
                "owner_scope": service_private_scope(principal),
                "visibility_scope": service_private_scope(principal),
            }
        )
    payload["body_html"] = "<p>HTML must stay private</p>"
    event = event.model_copy(update={"payload": payload})
    event_id = client.portal.call(runtime.events.write, event)
    client.portal.call(runtime.wait_for_projection, "interactions", event_id)
    return event_id


def _run_provenance(run: str, correlation: str) -> dict[str, Any]:
    return {**_service(correlation), "session_key": _service(run)["session_key"]}


def _run_context(client: TestClient, run: str, correlation: str, **values: Any) -> Any:
    return client.post(
        CONTEXT,
        headers=OPENCLAW_HEADERS,
        json={"provenance": _run_provenance(run, correlation), **values},
    )


def _run_note(client: TestClient, run: str, request_id: str, **values: Any) -> Any:
    return client.post(
        CREATE,
        headers=OPENCLAW_HEADERS,
        json={
            "provenance": _run_provenance(run, request_id),
            "request_id": request_id,
            "title": "Private mail follow-up",
            "record_kind": "note",
            **values,
        },
    )


def _mail_policy(
    client: TestClient, event_id: str, monkeypatch: pytest.MonkeyPatch, mode: str
) -> dict[str, Any]:
    from adminme.source_policy import source_coordinate_for_event

    runtime = client.app.state.runtime
    event = client.portal.call(runtime.event_log.get, event_id)
    coordinate = source_coordinate_for_event(
        event, provider_bindings=runtime.settings.provider_bindings
    )
    state = runtime.settings_control.read()
    policy = {
        **policy_for_preset("balanced").model_dump(mode="json"),
        "context_mode": mode,
    }
    state["source_policies_v2"].setdefault(coordinate.owner_principal_id, {})[
        coordinate.key
    ] = {"policy": policy}
    monkeypatch.setattr(runtime.settings_control, "read", lambda: state)
    return policy


def test_cos_reads_own_unrouted_gmail_and_retains_mixed_source_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        mail = _own_mail(client, "own-source", body="Useful plaintext. " * 200)
        other = _own_mail(client, "other-service", principal="service-principal:other")
        foreign = _own_mail(client, "foreign-tenant", tenant="tenant-other-fixture")
        member = _source(client, A, "Member travel timing", sensitivity="privileged")
        # Ordinary unrouted service mail does not depend on any member grant.
        default = _context(client, "own-default")
        assert default.status_code == 200, default.text
        assert [fact["event_id"] for fact in default.json()["facts"]] == [mail]
        assert default.json()["standing_contexts"] == []
        fact = default.json()["facts"][0]
        assert fact["authority"]["authority_kind"] == "service_self"
        assert fact["authority"]["authority_grant_id"] is None
        assert fact["authority"]["source_audience"] == []
        assert fact["authority"]["read_mode"] == "typed_facts"
        assert fact["provider_body_included"] is False
        assert (
            _context(client, "wrong-service", source_event_ids=[other]).status_code
            == 404
        )
        assert (
            _context(client, "wrong-tenant", source_event_ids=[foreign]).status_code
            == 404
        )
        _mail_policy(client, mail, monkeypatch, "typed_facts_safe_snippet")
        _grant_private(client, A, "mail-note-member-authority")
        result = _run_context(
            client,
            "mail-follow-up-run",
            "read-mail-and-member",
            source_event_ids=[mail, member],
        )
        assert result.status_code == 200, result.text
        fact = result.json()["facts"][0]
        assert fact["provider_body_included"] is True
        assert len(fact["fact"]["body_snippet"]) == 2000
        assert fact["fact"]["body_snippet_truncated"] is True
        assert "body_html" not in str(
            result.json()
        ) and "HTML must stay private" not in str(result.json())
        # Source IDs are deliberately omitted: the trusted receipt carries both.
        note = _run_note(
            client,
            "mail-follow-up-run",
            "retain-mail-follow-up",
            description="Prepare travel options using the member timing.",
        )
        assert note.status_code == 201, note.text
        work = note.json()["work"]
        task_id = work["task_id"]
        assert work["sensitivity"] == "privileged"
        assert {source["source_event_id"] for source in work["source_authorities"]} == {
            mail,
            member,
        }
        event = client.portal.call(runtime.event_log.get, note.json()["event_id"])
        assert event["schema_version"] == 5
        assert note.json()["provider_effect_count"] == 0
        client.portal.call(runtime.projections.rebuild, "tasks")
        assert _read(client, "own-note-replay", task_id=task_id)["work"][0] == work
        receipt = (
            runtime.projections.connection("tasks")
            .execute(
                "SELECT COUNT(*) FROM cos_source_read_receipts WHERE source_event_id=?",
                (mail,),
            )
            .fetchone()[0]
        )
        assert receipt > 0
        assert runtime.fixture_adapter.delivered == []
        # Real member provenance cannot enter the private service tool.
        from tests.acceptance.test_cos_identity_delegation_kernel import _provenance

        assert (
            client.post(
                CONTEXT,
                headers=OPENCLAW_HEADERS,
                json={"provenance": _provenance(A, "member-not-service")},
            ).status_code
            == 403
        )
    # Replay receipts remain authoritative after restart. Default policy is
    # typed-facts again, so notes derived from snippets are no longer readable.
    with TestClient(create_application(settings)) as restarted:
        assert _read(restarted, "snippet-note-blocked-after-restart")["work"] == []
        _mail_policy(restarted, mail, monkeypatch, "typed_facts_safe_snippet")
        assert (
            _read(restarted, "snippet-note-restored", task_id=task_id)["work"][0][
                "task_id"
            ]
            == task_id
        )
        same_run = _run_note(
            restarted, "mail-follow-up-run", "retained-receipt-after-restart"
        )
        assert same_run.status_code == 201, same_run.text
        assert len(same_run.json()["work"]["source_authorities"]) == 2
        assert restarted.app.state.runtime.fixture_adapter.delivered == []


@pytest.mark.parametrize(
    "mode",
    ["none", "metadata_only", "typed_facts", "content_on_explicit_authorized_demand"],
)
def test_mail_snippet_downgrade_blocks_in_run_and_retained_note_laundering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        runtime = client.app.state.runtime
        mail = _own_mail(client, "policy-source", body="Restricted client plan")
        policy = _mail_policy(client, mail, monkeypatch, "typed_facts_safe_snippet")
        assert (
            _run_context(
                client,
                "sensitive-run",
                "consume-client-source",
                source_event_ids=[mail],
            ).status_code
            == 200
        )
        note = _run_note(
            client,
            "sensitive-run",
            "retain-client-source",
            description="Restricted client plan",
        )
        assert note.status_code == 201, note.text
        task_id = note.json()["task_id"]
        # A second run reading a note inherits its sources, including history.
        read = client.post(
            READ,
            headers=OPENCLAW_HEADERS,
            json={
                "provenance": _run_provenance("copy-run", "read-derived-note"),
                "task_id": task_id,
            },
        )
        assert read.status_code == 200, read.text
        policy["context_mode"] = mode
        # Re-reading a less permissive view cannot lower the durable run floor.
        _run_context(
            client, "sensitive-run", "consume-lower-view", source_event_ids=[mail]
        )
        assert (
            _run_note(client, "sensitive-run", "launder-by-omission").status_code == 404
        )
        assert _run_note(client, "copy-run", "launder-derived-note").status_code == 404
        assert (
            _run_note(
                client, "sensitive-run", "launder-lower-mode", source_event_ids=[mail]
            ).status_code
            == 404
        )
        assert _read(client, "withheld-retained-client-note")["work"] == []
        assert "Restricted client plan" not in str(
            _read(client, "withheld-client-history")
        )
        context = _context(client, "policy-current-context", source_event_ids=[mail])
        if mode in {"none", "content_on_explicit_authorized_demand"}:
            assert context.status_code == 404
        else:
            assert context.status_code == 200, context.text
            fact = context.json()["facts"][0]
            assert fact["provider_body_included"] is False
            assert "body_snippet" not in fact["fact"]
            if mode == "metadata_only":
                assert (
                    "subject" not in fact["fact"]
                    and "from_identifier" not in fact["fact"]
                )
        policy["context_mode"] = "typed_facts_safe_snippet"
        assert _read(client, "restored-client-note", task_id=task_id)["work"]
        assert runtime.fixture_adapter.delivered == []


def test_own_mail_currentness_is_exact_and_historical_note_keeps_original_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        runtime = client.app.state.runtime
        first = _own_mail(client, "original", native_id="one-native-message")
        _mail_policy(client, first, monkeypatch, "typed_facts_safe_snippet")
        note = _run_note(
            client,
            "original-run",
            "note-original-observation",
            source_event_ids=[first],
        )
        assert note.status_code == 201, note.text
        second = _own_mail(
            client,
            "revision",
            native_id="one-native-message",
            body="Revised travel request",
        )
        assert (
            _context(
                client, "superseded-own-mail", source_event_ids=[first]
            ).status_code
            == 404
        )
        current = _context(client, "current-own-mail")
        assert [fact["event_id"] for fact in current.json()["facts"]] == [second]
        assert (
            current.json()["facts"][0]["fact"]["body_snippet"]
            == "Revised travel request"
        )
        assert (
            _context(client, "superseded-self-cursor", after_event_id=first).status_code
            == 200
        )
        work = _read(client, "historical-mail-note", task_id=note.json()["task_id"])[
            "work"
        ][0]
        assert work["source_authorities"][0]["source_event_id"] == first
        client.portal.call(runtime.projections.rebuild, "interactions")
        assert (
            _context(client, "replayed-own-mail", source_event_ids=[second]).status_code
            == 200
        )
        assert runtime.fixture_adapter.delivered == []


def _gmail_thread(
    member: str,
    account: str,
    thread: str,
    native: str,
    **changes: Any,
) -> dict[str, Any]:
    return _message(
        member,
        **{
            "provider": "gmail",
            "provider_account_id": account,
            "source_channel": "gmail",
            "workspace": "personal",
            "thread_id": thread,
            "native_message_id": native,
            **changes,
        },
    )


def _mail_thread_selector(
    member: str, account: str, thread: str, **extra: Any
) -> dict[str, Any]:
    return {
        "member_id": member,
        "provider_account_id": account,
        "workspace": "personal",
        "thread_id": thread,
        **extra,
    }


def test_exact_member_gmail_thread_maps_native_coordinates_and_audits_canonical_sources(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "member-gmail-thread"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        runtime = client.app.state.runtime
        account = "gmail-account-fixture-a"
        thread = "gmail-thread-fixture-shared"
        first = _observed_source(
            client,
            A,
            "messaging.received",
            _gmail_thread(A, account, thread, "gmail-message-fixture-1"),
            version=4,
        )
        second = _observed_source(
            client,
            A,
            "messaging.received",
            _gmail_thread(A, account, thread, "gmail-message-fixture-2"),
            version=4,
        )
        # The exact projection locator is not itself authority.
        selector = _mail_thread_selector(A, account, thread)
        assert (
            _context(client, "thread-without-grant", mail_thread=selector).status_code
            == 404
        )
        for cursor in (first, second):
            _assert_mail_denied(
                client,
                _context(
                    client,
                    f"ungranted-{cursor}",
                    mail_thread=selector,
                    after_event_id=cursor,
                ),
            )
        grant = _grant_private(client, A, "exact-gmail-thread-grant")
        page = _context(client, "thread-page-one", mail_thread=selector, limit=1)
        assert page.status_code == 200, page.text
        body = page.json()
        assert [fact["event_id"] for fact in body["facts"]] == [first]
        assert body["next_cursor"] == first and body["scan_truncated"] is True
        metadata = body["facts"][0]["mail_read_metadata"]
        assert metadata["canonical"] == {
            "source_event_id": first,
            "member_id": A,
            "provider": "gmail",
            "provider_account_id": account,
            "workspace": "personal",
            "thread_id": thread,
            "native_message_id": "gmail-message-fixture-1",
        }
        assert metadata["latest_retained_revision"]["source_event_id"] == first
        assert metadata["provider_freshness"]["state"] == "unknown"
        assert metadata["effective_policy"]["context_mode"] == "typed_facts"
        assert metadata["effective_policy"]["acquisition_state"] == "active"
        assert metadata["effective_policy"]["origin"] == "recommended_default"
        assert isinstance(metadata["effective_policy"]["revision"], int)
        assert metadata["implementation"] == {
            "provider_body_included": False,
            "body_reason": "member_body_not_implemented_by_this_reader",
        }
        assert body["facts"][0]["provider_body_included"] is False
        assert "PROVIDER_MAIL_BODY" not in page.text
        later = _context(
            client,
            "thread-page-two",
            mail_thread=selector,
            limit=1,
            after_event_id=body["next_cursor"],
        )
        assert later.status_code == 200, later.text
        assert [fact["event_id"] for fact in later.json()["facts"]] == [second]
        assert later.json()["next_cursor"] is None
        terminal = _context(
            client, "thread-terminal", mail_thread=selector, after_event_id=second
        )
        assert terminal.status_code == 200, terminal.text
        assert terminal.json()["facts"] == []
        assert terminal.json()["next_cursor"] is None
        assert terminal.json()["scan_truncated"] is False
        assert terminal.json()["provider_effect_count"] == 0
        assert runtime.fixture_adapter.delivered == []
        receipts = (
            runtime.projections.connection("tasks")
            .execute(
                "SELECT source_event_id, authority_json FROM cos_source_read_receipts "
                "WHERE source_event_id IN (?, ?) ORDER BY source_event_id",
                (first, second),
            )
            .fetchall()
        )
        assert [row["source_event_id"] for row in receipts] == [first, second]
        assert all(
            json.loads(row["authority_json"])["authority_grant_id"] == grant
            for row in receipts
        )


def test_exact_member_gmail_thread_rejects_scope_collisions_stale_rows_and_bad_cursor(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "member-gmail-thread-isolation"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        runtime = client.app.state.runtime
        account = "gmail-account-fixture-a"
        other_account = "gmail-account-fixture-b"
        thread = "gmail-thread-collision-fixture"
        _grant_private(client, A, "gmail-thread-isolation-a")
        _grant_private(client, B, "gmail-thread-isolation-b")
        original = _observed_source(
            client,
            A,
            "messaging.received",
            _gmail_thread(
                A, account, thread, "gmail-message-revised", subject="Old header"
            ),
            version=4,
        )
        current = _observed_source(
            client,
            A,
            "messaging.received",
            _gmail_thread(
                A, account, thread, "gmail-message-revised", subject="Current header"
            ),
            version=4,
        )
        other_member = _observed_source(
            client,
            B,
            "messaging.received",
            _gmail_thread(B, other_account, thread, "gmail-message-other-member"),
            version=4,
        )
        exact = _context(
            client,
            "gmail-current-exact",
            mail_thread=_mail_thread_selector(
                A, account, thread, native_message_id="gmail-message-revised"
            ),
        )
        assert exact.status_code == 200, exact.text
        assert [fact["event_id"] for fact in exact.json()["facts"]] == [current]
        assert (
            _context(
                client,
                "gmail-wrong-account",
                mail_thread=_mail_thread_selector(A, other_account, thread),
            ).status_code
            == 404
        )
        assert (
            _context(
                client,
                "gmail-wrong-member",
                mail_thread=_mail_thread_selector(B, account, thread),
            ).status_code
            == 404
        )
        assert (
            _context(
                client,
                "gmail-stale-cursor",
                mail_thread=_mail_thread_selector(A, account, thread),
                after_event_id=original,
            ).status_code
            == 404
        )
        assert (
            _context(
                client,
                "gmail-other-thread-cursor",
                mail_thread=_mail_thread_selector(A, account, thread),
                after_event_id=other_member,
            ).status_code
            == 404
        )
        # Simulate projection lag without acquiring or querying the provider.
        runtime.projections.connection("interactions").execute(
            "DELETE FROM interactions WHERE tenant_id=? AND provider_account_id=? "
            "AND native_message_id=?",
            (runtime.settings.instance.tenant_id, account, "gmail-message-revised"),
        )
        assert (
            _context(
                client,
                "gmail-projection-lag",
                mail_thread=_mail_thread_selector(A, account, thread),
            ).status_code
            == 404
        )


@pytest.mark.parametrize("acquisition", ["active", "paused"])
@pytest.mark.parametrize("mode", ["typed_facts", "typed_facts_safe_snippet"])
def test_member_gmail_thread_reports_effective_mode_but_never_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    acquisition: str,
) -> None:
    from adminme.source_policy import source_coordinate_for_event

    instance = tmp_path / f"member-gmail-mode-{mode}"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        runtime = client.app.state.runtime
        account = "gmail-mode-account-fixture"
        thread = "gmail-mode-thread-fixture"
        source = _observed_source(
            client,
            A,
            "messaging.received",
            _gmail_thread(A, account, thread, "gmail-mode-message-fixture"),
            version=4,
        )
        _grant_private(client, A, f"gmail-mode-grant-{mode}")
        event = client.portal.call(runtime.event_log.get, source)
        coordinate = source_coordinate_for_event(
            event, provider_bindings=runtime.settings.provider_bindings
        )
        state = runtime.settings_control.read()
        policy = {
            **policy_for_preset("balanced").model_dump(mode="json"),
            "context_mode": mode,
            "acquisition_state": acquisition,
        }
        state["source_policies_v2"].setdefault(A, {})[coordinate.key] = {
            "policy": policy
        }
        state["source_policy_v2_revisions"][A] = 7
        monkeypatch.setattr(runtime.settings_control, "read", lambda: state)
        response = _context(
            client,
            f"gmail-mode-{mode}",
            mail_thread=_mail_thread_selector(A, account, thread),
        )
        assert response.status_code == 200, response.text
        fact = response.json()["facts"][0]
        assert fact["mail_read_metadata"]["effective_policy"]["context_mode"] == mode
        effective = fact["mail_read_metadata"]["effective_policy"]
        assert effective["origin"] == "exact_override"
        assert effective["acquisition_state"] == acquisition
        assert effective["revision"] == 7
        assert effective["policy_key"] == coordinate.key
        assert effective["exact_coordinate"] is coordinate.exact
        assert effective["inherited_from_policy_key"] is None
        assert (
            fact["mail_read_metadata"]["timestamps"]["received_at"]
            == event["payload"]["received_at"]
        )
        assert (
            fact["mail_read_metadata"]["timestamps"]["recorded_at"]
            == event["recorded_at"]
        )
        assert fact["mail_read_metadata"]["provider_freshness"]["state"] == "unknown"
        assert fact["provider_body_included"] is False
        assert (
            fact["mail_read_metadata"]["implementation"]["body_reason"]
            == "member_body_not_implemented_by_this_reader"
        )
        assert "body_snippet" not in fact["fact"]
        assert "PROVIDER_MAIL_BODY" not in response.text


def _mail_page_fixture(client: TestClient) -> tuple[dict[str, Any], list[str], str]:
    selector = _mail_thread_selector(A, "gmail-page-account", "gmail-page-thread")
    grant = _grant_private(client, A, "gmail-page-grant")
    ids = [
        _observed_source(
            client,
            A,
            "messaging.received",
            _gmail_thread(
                A,
                selector["provider_account_id"],
                selector["thread_id"],
                f"gmail-page-{index}",
            ),
            version=4,
        )
        for index in range(2)
    ]
    return selector, ids, grant


def _assert_mail_denied(client: TestClient, response: Any) -> None:
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "authorized member mail thread not found"}
    assert "PROVIDER_MAIL_BODY" not in response.text
    assert client.app.state.runtime.fixture_adapter.delivered == []


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize(
    "change",
    [
        "revoked",
        "none",
        "metadata_only",
        "content_on_explicit_authorized_demand",
        "stale",
        "deleted",
        "retracted",
        "account",
        "thread",
        "tenant",
    ],
)
def test_member_mail_pages_reauthorize_cursor_even_when_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal: bool,
    change: str,
) -> None:
    instance = tmp_path / "mail-page-authority"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        runtime = client.app.state.runtime
        selector, ids, grant = _mail_page_fixture(client)
        cursor = ids[-1] if terminal else ids[0]
        before = _context(
            client, "page-before", mail_thread=selector, after_event_id=cursor
        )
        assert before.status_code == 200, before.text
        assert len(before.json()["facts"]) == (0 if terminal else 1)
        if change == "revoked":
            revoked = client.request(
                "DELETE",
                f"/api/member/v1/cos/delegations/{grant}",
                headers=A_HEADERS,
                json={"request_id": "page-revoke", "reason": "fixture"},
            )
            assert revoked.status_code == 200, revoked.text
        elif change in {
            "none",
            "metadata_only",
            "content_on_explicit_authorized_demand",
        }:
            _mail_policy(client, cursor, monkeypatch, change)
        elif change == "stale":
            event = client.portal.call(runtime.event_log.get, cursor)
            _observed_source(
                client,
                A,
                "messaging.received",
                {
                    **event["payload"],
                    "subject": "New canonical revision",
                },
                version=4,
            )
        elif change in {"deleted", "retracted", "tenant"}:
            # Projection corruption/lag must not make retained history admissible.
            conn = runtime.projections.connection("interactions")
            predicate = "json_extract(raw_event_ids, '$[#-1]')=?"
            if change == "deleted":
                conn.execute(f"DELETE FROM interactions WHERE {predicate}", (cursor,))
            elif change == "retracted":
                conn.execute(
                    f"UPDATE interactions SET message_state='retracted' WHERE {predicate}",
                    (cursor,),
                )
            else:
                conn.execute(
                    f"UPDATE interactions SET tenant_id='foreign-fixture-tenant' WHERE {predicate}",
                    (cursor,),
                )
        else:
            selector["provider_account_id" if change == "account" else "thread_id"] = (
                "different-fixture-coordinate"
            )
        _assert_mail_denied(
            client,
            _context(client, "page-after", mail_thread=selector, after_event_id=cursor),
        )


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("stage", ["source_fetch", "audit_publication"])
@pytest.mark.parametrize("change", ["revoked", "policy", "revision"])
def test_member_mail_rechecks_current_authority_after_awaits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal: bool,
    stage: str,
    change: str,
) -> None:
    import httpx

    from adminme.products.openclaw import service_work

    instance = tmp_path / "mail-await-authority"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        runtime = client.app.state.runtime
        selector, ids, grant = _mail_page_fixture(client)
        cursor = ids[-1] if terminal else ids[0]
        policy = _mail_policy(client, cursor, monkeypatch, "typed_facts")
        changed = False
        original_get = runtime.event_log.get
        original_audit = service_work._audit

        async def change_authority() -> None:
            nonlocal changed
            if changed:
                return
            changed = True
            if change == "revoked":
                # Publish a real authenticated revocation while the original read awaits.
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=client.app),
                    base_url="http://fixture",
                ) as http:
                    revoked = await http.request(
                        "DELETE",
                        f"/api/member/v1/cos/delegations/{grant}",
                        headers=A_HEADERS,
                        json={"request_id": "await-revoke", "reason": "fixture"},
                    )
                    assert revoked.status_code == 200, revoked.text
            elif change == "policy":
                policy["context_mode"] = "metadata_only"
            else:
                old = await original_get(cursor)
                revised = EventEnvelope(
                    event_at_ms=1,
                    tenant_id=runtime.settings.instance.tenant_id,
                    type="messaging.received",
                    schema_version=4,
                    occurred_at=EventEnvelope.now_utc_iso(),
                    source_adapter=old["source_adapter"],
                    source_account_id=old["source_account_id"],
                    owner_scope=old["owner_scope"],
                    visibility_scope=old["visibility_scope"],
                    sensitivity=old["sensitivity"],
                    payload={**old["payload"], "subject": "Changed during read"},
                )
                new_id = await runtime.events.write(revised)
                await runtime.wait_for_projection("interactions", new_id)

        async def read_then_change(event_id: str) -> Any:
            event = await original_get(event_id)
            if event_id == cursor:
                await change_authority()
            return event

        async def audit_then_change(*args: Any, **kwargs: Any) -> None:
            await original_audit(*args, **kwargs)
            await change_authority()

        if stage == "source_fetch":
            monkeypatch.setattr(runtime.event_log, "get", read_then_change)
        else:
            monkeypatch.setattr(service_work, "_audit", audit_then_change)
        _assert_mail_denied(
            client,
            _context(client, "await-read", mail_thread=selector, after_event_id=cursor),
        )
        assert changed is True


@pytest.mark.parametrize(
    "invalid",
    [
        {"member_id": ""},
        {"provider_account_id": " account"},
        {"thread_id": "x\n"},
        {"workspace": "default"},
        {"native_message_id": ""},
        {"provider": "gmail"},
    ],
)
def test_member_mail_rejects_malformed_selector(
    tmp_path: Path, invalid: dict[str, Any]
) -> None:
    instance = tmp_path / "mail-malformed"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        selector, _, _ = _mail_page_fixture(client)
        response = _context(
            client, "malformed-selector", mail_thread={**selector, **invalid}
        )
        assert response.status_code == 422, response.text
        assert client.app.state.runtime.fixture_adapter.delivered == []


def test_member_mail_rejects_mixed_selection_and_unknown_cursor(tmp_path: Path) -> None:
    instance = tmp_path / "mail-mixed"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        selector, ids, _ = _mail_page_fixture(client)
        for other in (
            {"source_kind": "calendar"},
            {"source_event_ids": ids},
            {"after_event_id": ""},
        ):
            response = _context(
                client, "mixed-selection", mail_thread=selector, **other
            )
            assert response.status_code == 422, response.text
        _assert_mail_denied(
            client,
            _context(
                client,
                "unknown-cursor",
                mail_thread=selector,
                after_event_id="not-an-event",
            ),
        )
        assert client.app.state.runtime.fixture_adapter.delivered == []


def test_member_calendar_tool_reads_current_imported_calendar_without_internal_session(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from tests.acceptance.test_cos_identity_delegation_kernel import (
        A_HEADERS,
        _provenance,
    )

    instance = tmp_path / "member-calendar"
    instance.mkdir()
    settings = _settings(instance)
    settings = replace(
        settings,
        instance=replace(settings.instance, timezone="America/New_York"),
        openclaw_actor_identities={"imessage|account-a|actor-a": A},
    )
    app = create_application(settings)
    with TestClient(app) as client:
        runtime = app.state.runtime
        assert client.portal is not None
        grant = _grant_private(client, A, "calendar-standing-read")
        _observed_source(
            client,
            A,
            "calendar.source_observed",
            {
                "member_id": A,
                "provider": "google_calendar",
                "provider_account_id": f"calendar-{A}",
                "calendar_external_id": "fixture-calendar",
                "calendar_name": "Partner work busy/free",
                "access_role": "reader",
                "read_details": True,
                "read_free_busy": True,
                "write_events": False,
                "observed_at": "2026-10-22T12:00:00Z",
            },
        )
        original = _observed_source(
            client,
            A,
            "calendar.event_added",
            _calendar(
                A, calendar_name="Partner work busy/free", summary="Old revision"
            ),
            version=2,
        )
        current = _observed_source(
            client,
            A,
            "calendar.event_updated",
            _calendar(
                A,
                calendar_name="Partner work busy/free",
                summary="Busy",
                end="2026-11-02T18:00:00Z",
            ),
            version=2,
        )
        _observed_source(
            client,
            B,
            "calendar.event_added",
            _calendar(B, summary="OUTSIDE_CURRENT_SOURCE_AUTHORITY"),
            version=2,
        )
        _observed_source(
            client,
            A,
            "calendar.event_added",
            _calendar(
                A,
                external_event_id="out-of-range",
                start="2026-11-09T14:00:00Z",
                end="2026-11-09T15:00:00Z",
            ),
            version=2,
        )
        provenance = {**_provenance(A, "calendar-query"), "channel_id": "imessage"}
        body = {"provenance": provenance, "anchor_date": "2026-11-02", "view": "week"}
        response = client.post(
            "/api/openclaw/v1/tools/calendar", headers=OPENCLAW_HEADERS, json=body
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["timezone"] == "America/New_York"
        assert result["range"] == {
            "start_at": "2026-11-02T00:00:00-05:00",
            "end_at": "2026-11-09T00:00:00-05:00",
        }
        assert result["matching_event_count"] == 1
        assert result["events"][0]["summary"] == "Busy"
        assert result["events"][0]["calendar_name"] == "Partner work busy/free"
        assert result["events"][0]["source_event_id"] == current
        assert result["events"][0]["provider_account_id"] == f"calendar-{A}"
        retained = client.portal.call(runtime.event_log.get, current)
        assert result["events"][0]["retained_change_at"] == retained["recorded_at"]
        assert original not in str(result)
        assert "OUTSIDE_CURRENT_SOURCE_AUTHORITY" not in str(result)
        assert "PROVIDER_BODY_MUST_NOT_ENTER_CONTEXT" not in str(result)
        assert result["coverage"]["provider_live_read"] is False
        assert result["coverage"]["provider_last_successful_poll_at"] is None
        assert result["coverage"]["provider_range_completeness"] == "not_verified"
        assert result["provider_effect_count"] == 0
        catalog = client.post(
            "/api/openclaw/v1/tools/capabilities",
            headers=OPENCLAW_HEADERS,
            json={"provenance": provenance},
        )
        assert catalog.status_code == 200, catalog.text
        capability = next(
            item
            for item in catalog.json()["items"]
            if item["capability_id"] == "domain.household_calendar"
        )
        assert capability["openclaw"]["declared_tools"] == ["administrateme_calendar"]
        assert capability["openclaw"]["approval_required_for_side_effects"] is False
        assert result["sources"][0]["access_role"] == "reader"
        assert result["sources"][0]["provider_account_id"] == f"calendar-{A}"
        assert result["sources"][0]["observed_at"].startswith("2026-10-22")
        assert (
            result["coverage"]["source_observed_at_means"]
            == "source_metadata_change_not_last_poll"
        )
        replay = client.post(
            "/api/openclaw/v1/tools/calendar", headers=OPENCLAW_HEADERS, json=body
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["events"] == result["events"]
        conflicting_replay = client.post(
            "/api/openclaw/v1/tools/calendar",
            headers=OPENCLAW_HEADERS,
            json={**body, "calendar_external_ids": ["absent-calendar"]},
        )
        assert conflicting_replay.status_code == 409
        selected = client.post(
            "/api/openclaw/v1/tools/calendar",
            headers=OPENCLAW_HEADERS,
            json={
                **body,
                "calendar_external_ids": ["absent-calendar"],
                "provenance": {
                    **_provenance(A, "calendar-selected"),
                    "channel_id": "imessage",
                },
            },
        )
        assert selected.status_code == 200, selected.text
        assert selected.json()["events"] == []
        assert selected.json()["sources"] == []
        denied = client.post(
            "/api/openclaw/v1/tools/service-context",
            headers=OPENCLAW_HEADERS,
            json={"provenance": provenance, "source_kind": "calendar"},
        )
        assert denied.status_code == 403
        forged = {**body, "provenance": {**provenance, "actor_id": "unknown"}}
        assert (
            client.post(
                "/api/openclaw/v1/tools/calendar", headers=OPENCLAW_HEADERS, json=forged
            ).status_code
            == 403
        )
        for invalid in (
            {"limit": 201},
            {"view": "year"},
            {"anchor_date": "not-a-date"},
            {"member_id": B},
        ):
            assert (
                client.post(
                    "/api/openclaw/v1/tools/calendar",
                    headers=OPENCLAW_HEADERS,
                    json={**body, **invalid},
                ).status_code
                == 422
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
                "deleted_at": "2026-11-01T13:00:00Z",
            },
            version=2,
        )
        deleted = client.post(
            "/api/openclaw/v1/tools/calendar",
            headers=OPENCLAW_HEADERS,
            json={
                **body,
                "provenance": {
                    **_provenance(A, "calendar-after-delete"),
                    "channel_id": "imessage",
                },
            },
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["events"] == []
        revoke = client.request(
            "DELETE",
            f"/api/member/v1/cos/delegations/{grant}",
            headers=A_HEADERS,
            json={"request_id": "calendar-revoke", "reason": "fixture"},
        )
        assert revoke.status_code == 200, revoke.text
        assert (
            client.post(
                "/api/openclaw/v1/tools/calendar", headers=OPENCLAW_HEADERS, json=body
            ).status_code
            == 403
        )
        assert runtime.fixture_adapter.delivered == []


def test_member_calendar_tool_bounds_results_and_does_not_claim_empty_is_free(
    tmp_path: Path,
) -> None:
    from tests.acceptance.test_cos_identity_delegation_kernel import _provenance

    instance = tmp_path / "bounded-calendar"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        _grant_private(client, A, "bounded-calendar-standing")
        for number in range(2):
            _observed_source(
                client,
                A,
                "calendar.event_added",
                _calendar(A, external_event_id=f"busy-{number}", summary="x" * 600),
                version=2,
            )
        body = {
            "provenance": _provenance(A, "bounded-calendar"),
            "anchor_date": "2026-11-02",
            "view": "week",
            "limit": 1,
        }
        response = client.post(
            "/api/openclaw/v1/tools/calendar", headers=OPENCLAW_HEADERS, json=body
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert len(result["events"]) == 1
        assert result["matching_event_count"] == 2
        assert result["truncated"] is True
        assert len(result["events"][0]["summary"]) == 500
        assert result["events"][0]["text_truncated"] is True
        empty = client.post(
            "/api/openclaw/v1/tools/calendar",
            headers=OPENCLAW_HEADERS,
            json={
                **body,
                "anchor_date": "2026-12-01",
                "provenance": _provenance(A, "calendar-empty-window"),
            },
        )
        assert empty.status_code == 200, empty.text
        assert empty.json()["events"] == []
        assert (
            empty.json()["coverage"]["empty_result_means"]
            == "no_matching_retained_events_not_proven_free_time"
        )
