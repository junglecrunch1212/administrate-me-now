"""Explicit forward -> durable extraction -> Triage -> governed bridge proof.

Every transport, model response, approval secret, and provider is a fixture.
The production composition root, event bus, APIs, projections, and observation
barrier run unchanged. No connected account or live member device is used.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import time
from threading import Event
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.events.envelope import EventEnvelope
from adminme.direct_intake import DirectIntakeProcessor
from adminme.openclaw_approvals import sign_approval_receipt
from adminme.runtime.application import create_application
from tests.acceptance.test_service_owned_cos_sources_direct_intake import (
    CHANNEL_ID,
    ROUTE_A,
    _fixture_config,
    _route_result,
    _service_message,
)
from tests.acceptance.test_triage_vertical_slice import (
    APPLE_CALENDAR_ACCOUNT,
    AUTH,
    MEMBER,
    OPENCLAW_AUTH,
    OPENCLAW_COMMIT,
    OPENCLAW_VERSION,
    SECRET,
    _events,
    _grant_cos_private_context,
    _provenance,
    _settings as triage_settings,
)


def _settings(path: Path) -> Any:
    settings = triage_settings(path)
    fixture = _fixture_config()
    channels = fixture["service_identities"]["channels"]
    channel = next(row for row in channels if row["channel_id"] == CHANNEL_ID)
    channel["provider_account_reference"] = (
        "provider-account:" + hashlib.sha256(b"cos-service@example.invalid").hexdigest()
    )
    channel["routes"] = [
        {"route_key": ROUTE_A, "scope": "member_private", "target_id": MEMBER}
    ]
    fixture["service_identities"]["channels"] = [channel]
    return replace(
        settings,
        legacy_personal_obligation_compatibility=False,
        adapter_config={
            "adapter_supervisor_port": 0,
            "adapters": [],
            "service_identities": fixture["service_identities"],
        },
    )


def _forward(
    client: TestClient, key: str, text: str, *, issues: list[str] | None = None
) -> tuple[str, dict[str, Any]]:
    runtime = client.app.state.runtime
    source = _service_message(
        runtime,
        key=key,
        trusted_route_key=ROUTE_A,
        from_identifier="member-a@example.invalid",
        subject="Forward for review",
        body=text,
        event_at_ms=int(time.time() * 1000),
    )
    source.payload["submission_evidence"] = {
        "text": text,
        "complete": not issues,
        "issues": issues or [],
    }
    source_id = client.portal.call(runtime.events.write, source)
    route = _route_result(client, source_id)
    cue_id = route["payload"]["cue_event_id"]
    assert cue_id
    client.portal.call(
        runtime.wait_for_subscriber, "member-submissions:interpretation", cue_id
    )
    cue = client.portal.call(runtime.event_log.get, cue_id)
    assert cue is not None
    return source_id, cue


def _items(client: TestClient) -> list[dict[str, Any]]:
    result = client.get(
        "/api/member/v1/surfaces/triage?triage_lens=all_candidates", headers=AUTH
    )
    assert result.status_code == 200, result.text
    return result.json()["items"]


def _allow_once(client: TestClient, domain_id: str) -> str:
    fingerprint = hashlib.sha256(
        json.dumps(
            {"domain_approval_event_id": domain_id},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    issued = int(time.time() * 1000)
    receipt = "forwarded-calendar-approval-0001"
    payload = {
        "receipt_id": receipt,
        "tool_name": "administrateme_execute_pending_provider_action",
        "request_fingerprint": fingerprint,
        "issued_at_ms": issued,
        "expires_at_ms": issued + 120_000,
        "openclaw_version": OPENCLAW_VERSION,
        "openclaw_commit": OPENCLAW_COMMIT,
    }
    signature = sign_approval_receipt(SECRET, **payload)
    response = client.post(
        "/api/openclaw/v1/approvals/resolve",
        headers=OPENCLAW_AUTH,
        json={**payload, "decision": "allow-once", "signature": signature},
    )
    assert response.status_code == 200, response.text
    return receipt


def _calendar_destination(client: TestClient) -> None:
    runtime = client.app.state.runtime
    observed = client.portal.call(
        runtime.events.write,
        EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=runtime.settings.instance.tenant_id,
            type="calendar.source_observed",
            schema_version=1,
            occurred_at="2031-01-01T12:00:00Z",
            source_adapter="calendaring-apple-calendar",
            source_account_id=APPLE_CALENDAR_ACCOUNT,
            owner_scope=f"private:{MEMBER}",
            visibility_scope=f"private:{MEMBER}",
            sensitivity="normal",
            payload={
                "member_id": MEMBER,
                "provider": "apple_calendar",
                "provider_account_id": APPLE_CALENDAR_ACCOUNT,
                "calendar_external_id": "calendar-primary",
                "calendar_name": "Fixture calendar",
                "access_role": "owner",
                "primary": True,
                "selected": True,
                "read_details": True,
                "read_free_busy": True,
                "write_events": True,
                "observed_at": "2031-01-01T12:00:00Z",
            },
        ),
    )
    client.portal.call(runtime.wait_for_projection, "calendars", observed)
    result = client.post(
        "/api/member/v1/calendar/onboarding",
        headers=AUTH,
        json={
            "request_id": "forward-destination-0001",
            "member_id": MEMBER,
            "timezone": "Etc/UTC",
            "rules": [
                {
                    "source_member_id": MEMBER,
                    "provider": "apple_calendar",
                    "provider_account_id": APPLE_CALENDAR_ACCOUNT,
                    "calendar_external_id": "calendar-primary",
                    "purpose": "personal",
                    "effect": "hard_block",
                    "write_target": True,
                    "destination_kind": "personal",
                }
            ],
            "windows": [],
            "buffer_minutes": {},
            "accepted_chores_required": True,
            "one_confirm_write": True,
        },
    )
    assert result.status_code == 202, result.text


def _calendar_bridge_headers(client: TestClient) -> dict[str, str]:
    """Bind the fixture request to its existing active enrollment coordinate."""
    identity = "bridge-triage.ts.net"
    enrollment = client.app.state.bridge_enrollments[identity]
    assert enrollment["member_id"] == MEMBER
    assert enrollment["status"] == "active"
    return {
        "Tailscale-User-Login": identity,
        "X-AdministrateMe-Bridge-Enrollment": enrollment["enrollment_id"],
    }


def test_full_forward_after_long_evidence_routes_only_after_both_approvals(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "full-forward"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        _grant_cos_private_context(client, "forwarded-obligation-cos-context")
        calls: list[str] = []
        appointment = (
            "appointment on 2031-01-05T14:00:00+00:00 until 2031-01-05T15:00:00+00:00"
        )
        ending = "\nCall the school office; " + appointment
        background = "Preserved background details\n" * 1200
        text = background[: 32_000 - len(ending)] + ending

        async def language(
            skill: str, inputs: dict[str, Any], *args: Any, **kwargs: Any
        ) -> Any:
            assert skill == "skill:extract_note_semantics"
            assert inputs["body_text"] == text
            assert len(inputs["body_text"]) == 32_000
            calls.append(text)
            return SimpleNamespace(
                output={
                    "candidates": [
                        {
                            "target_kind": "task",
                            "evidence_quote": "Call the school office",
                            "candidate": {},
                            "confidence": 0.9,
                        },
                        {
                            "target_kind": "calendar_event",
                            "evidence_quote": appointment,
                            "candidate": {
                                "start": "2031-01-05T14:00:00+00:00",
                                "end": "2031-01-05T15:00:00+00:00",
                            },
                            "confidence": 0.9,
                        },
                    ]
                }
            )

        runtime.run_skill_fn = language
        source_id, cue = _forward(client, "full", text)
        assert cue["schema_version"] == 2
        assert cue["payload"]["text"] == text
        assert cue["payload"]["evidence_complete"] is True
        assert cue["payload"]["source_event_id"] == source_id
        items = [
            row
            for row in _items(client)
            if row.get("parent_intent_id") == cue["payload"]["intent_id"]
        ]
        assert {row["target_kind"] for row in items} == {"task", "calendar_event"}
        assert len(calls) == 1
        assert (
            client.portal.call(
                _events, runtime, "task.created", "external_sync.write_requested"
            )
            == []
        )

        # An updated provider observation for the same native delivery is not a
        # second member instruction and cannot rerun extraction.
        original = client.portal.call(runtime.event_log.get, source_id)
        revised = EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=original["tenant_id"],
            type="messaging.received",
            schema_version=8,
            occurred_at=original["occurred_at"],
            source_adapter=original["source_adapter"],
            source_account_id=original["source_account_id"],
            owner_scope=original["owner_scope"],
            visibility_scope=original["visibility_scope"],
            sensitivity=original["sensitivity"],
            idempotency_key="forward-full-provider-label-revision",
            payload={**original["payload"], "unread": False},
        )
        revised_id = client.portal.call(runtime.events.write, revised)
        assert (
            _route_result(client, revised_id)["payload"]["cue_event_id"]
            == cue["event_id"]
        )
        assert len(calls) == 1

        _calendar_destination(client)
        item = next(row for row in items if row["target_kind"] == "calendar_event")
        enriched = client.post(
            f"/api/member/v1/triage/{item['proposal_id']}/enrich",
            headers=AUTH,
            json={
                "request_id": "forward-route-calendar-0001",
                "expected_last_event_id": item["last_event_id"],
                "route": {
                    "member_id": MEMBER,
                    "provider": "apple_calendar",
                    "provider_account_id": APPLE_CALENDAR_ACCOUNT,
                    "container_external_id": "calendar-primary",
                },
            },
        )
        assert enriched.status_code == 200, enriched.text
        approved = client.post(
            f"/api/member/v1/triage/{item['proposal_id']}/approve",
            headers=AUTH,
            json={
                "request_id": "forward-approve-calendar-0001",
                "expected_last_event_id": enriched.json()["triage"]["last_event_id"],
            },
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["state"] == "approved_awaiting_openclaw"
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )
        domain_id = approved.json()["pending_provider_action_event_id"]
        execute_body = {
            "provenance": _provenance(),
            "domain_approval_event_id": domain_id,
        }
        denied = client.post(
            "/api/openclaw/v1/tools/execute-pending-provider-action",
            headers=OPENCLAW_AUTH,
            json=execute_body,
        )
        assert denied.status_code in {403, 409}
        execute_body["openclaw_approval_receipt_id"] = _allow_once(client, domain_id)
        executed = client.post(
            "/api/openclaw/v1/tools/execute-pending-provider-action",
            headers=OPENCLAW_AUTH,
            json=execute_body,
        )
        assert executed.status_code == 200, executed.text
        queued = client.get(
            "/api/bridge/commands?providers=apple_calendar",
            headers=_calendar_bridge_headers(client),
        )
        assert queued.status_code == 200, queued.text
        assert queued.json()["commands"] == []
        [terminal] = client.portal.call(
            _events, runtime, "external_sync.write_completed"
        )
        assert terminal["payload"]["suppressed"] is True
        assert terminal["payload"]["provider_called"] is False
        assert terminal["payload"]["origin_proposal_id"] == item["proposal_id"]
        client.portal.call(runtime.wait_for_projection, "triage", terminal["event_id"])
        client.portal.call(runtime.projections.rebuild, "triage")
        row = (
            runtime.projections.connection("triage")
            .execute(
                "SELECT state,resolution_reason FROM triage_items WHERE proposal_id=?",
                (item["proposal_id"],),
            )
            .fetchone()
        )
        assert tuple(row) == ("confirmed", "observation_suppressed")
        assert len(calls) == 1


@pytest.mark.parametrize(
    "text,issues",
    [
        ("Call the school office", ["attachment_unavailable"]),
        ("BEGIN:VCALENDAR\nMETHOD:CANCEL\nEND:VCALENDAR", []),
        ("BEGIN:VCALENDAR\nDTSTART:20310105T140000\nEND:VCALENDAR", []),
        ("Submit the school form by Friday", []),
    ],
)
def test_incomplete_or_ambiguous_calendar_forward_requires_clarification(
    tmp_path: Path, text: str, issues: list[str]
) -> None:
    instance = tmp_path / "incomplete-forward"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime

        async def forbidden(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("incomplete submission must not invoke the model")

        runtime.run_skill_fn = forbidden
        _, cue = _forward(client, "incomplete", text, issues=issues)
        rows = [
            row
            for row in _items(client)
            if row["proposal_id"] == cue["payload"]["intent_id"]
        ]
        assert len(rows) == 1
        assert rows[0]["state"] == "pending_interpretation"
        assert (
            rows[0]["candidate"]["submission_status"]["assessment_outcome"]
            == "quarantined"
        )
        assert (
            client.portal.call(
                _events,
                runtime,
                "triage.item.proposed",
                "external_sync.write_requested",
            )
            == []
        )
        client.portal.call(runtime.projections.rebuild, "triage")
        assert (
            next(
                row
                for row in _items(client)
                if row["proposal_id"] == cue["payload"]["intent_id"]
            )["candidate"]["submission_status"]["assessment_outcome"]
            == "quarantined"
        )


def test_candidate_limit_retains_whole_forward_for_clarification(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "candidate-limit"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime

        async def language(*args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(output={"candidates": []})

        runtime.run_skill_fn = language
        text = "; ".join(f"Call the office about form {index}" for index in range(21))
        _, cue = _forward(client, "candidate-limit", text)
        assert cue["payload"]["text"] == text
        assert client.portal.call(_events, runtime, "triage.item.proposed") == []
        [feature] = client.portal.call(
            _events, runtime, "triage.intent.features_extracted"
        )
        assert feature["payload"]["assessment_outcome"] == "quarantined"
        assert (
            "submission_candidate_limit_requires_review"
            in feature["payload"]["reason_codes"]
        )


def test_captured_submission_resumes_after_restart_without_http_retry(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "capture-recovery"
    instance.mkdir()
    settings = _settings(instance)
    app = create_application(settings)
    with TestClient(app) as client:
        runtime = app.state.runtime
        # Stop only this durable subscriber to model a crash after capture.
        runtime.bus._subscribers["member-submissions:interpretation"].degraded = True
        event_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=runtime.settings.instance.tenant_id,
                type="triage.intent.submitted",
                schema_version=2,
                occurred_at="2031-01-01T12:00:00Z",
                source_adapter="member-triage",
                source_account_id=MEMBER,
                actor_identity=MEMBER,
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key="forward-recovery-capture-0001",
                payload={
                    "intent_id": "forward-recovery-intent",
                    "member_id": MEMBER,
                    "text": "Call the school office; buy the school folder",
                    "source_surface": "triage",
                    "source_event_id": None,
                    "submitted_at": "2031-01-01T12:00:00Z",
                    "client_request_id": "forward-recovery-0001",
                    "submission_kind": "manual",
                    "evidence_complete": True,
                    "evidence_issues": [],
                    "intake_channel": "member",
                },
            ),
        )
        client.portal.call(runtime.wait_for_projection, "triage", event_id)
        assert client.portal.call(_events, runtime, "triage.item.proposed") == []
    restored = create_application(settings)
    with TestClient(restored) as client:
        runtime = restored.state.runtime
        client.portal.call(
            runtime.wait_for_subscriber, "member-submissions:interpretation", event_id
        )
        proposals = client.portal.call(_events, runtime, "triage.item.proposed")
        assert len(proposals) == 2
        assert {event["payload"]["parent_intent_id"] for event in proposals} == {
            "forward-recovery-intent"
        }
        feature = client.portal.call(
            _events, runtime, "triage.intent.features_extracted"
        )
        assert len(feature) == 1
        event = client.portal.call(runtime.event_log.get, event_id)
        client.portal.call(runtime.submission_interpreter.handle, event)
        assert (
            len(
                client.portal.call(_events, runtime, "triage.intent.features_extracted")
            )
            == 1
        )
        assert len(client.portal.call(_events, runtime, "triage.item.proposed")) == 2


def test_native_provider_observation_reconciles_exact_receipt_and_replays(
    tmp_path: Path,
) -> None:
    """Reconstruct fixture provider facts through the real bus and projection."""
    instance = tmp_path / "native-observation"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        timestamp = "2031-01-01T12:00:00Z"
        proposal_id = "forward-fixture-observation-proposal"
        scope = f"private:{MEMBER}"

        def append(
            kind: str,
            version: int,
            payload: dict[str, Any],
            *,
            causation: str | None = None,
            source: str = "fixture-provider-history",
        ) -> str:
            return client.portal.call(
                runtime.events.write,
                EventEnvelope(
                    event_at_ms=int(time.time() * 1000),
                    tenant_id=runtime.settings.instance.tenant_id,
                    type=kind,
                    schema_version=version,
                    occurred_at=timestamp,
                    source_adapter=source,
                    source_account_id=APPLE_CALENDAR_ACCOUNT,
                    owner_scope=scope,
                    visibility_scope=scope,
                    sensitivity="normal",
                    actor_identity="fixture:provider-history",
                    causation_id=causation,
                    payload=payload,
                ),
            )

        proposal = append(
            "triage.item.proposed",
            1,
            {
                "proposal_id": proposal_id,
                "target_kind": "calendar_event",
                "owner_member_id": MEMBER,
                "title": "Fixture calendar obligation",
                "candidate": {},
                "source_event_id": "fixture-source-event",
            },
        )
        domain = append(
            "triage.item.confirmed",
            2,
            {
                "proposal_id": proposal_id,
                "confirmed_at": timestamp,
                "confirmed_by_member_id": MEMBER,
                "expected_last_event_id": proposal,
                "candidate_fingerprint": "b" * 64,
            },
            causation=proposal,
        )
        command_fields = {
            "command_id": "forward-fixture-observation-command",
            "member_id": MEMBER,
            "provider": "apple_calendar",
            "provider_account_id": APPLE_CALENDAR_ACCOUNT,
            "operation": "create_event",
            "domain_approval_event_id": domain,
            "openclaw_approval_event_id": "fixture-approved-tool-receipt",
            "request_fingerprint": "b" * 64,
            "origin_proposal_id": proposal_id,
        }
        command = append(
            "external_sync.write_requested",
            2,
            {
                **command_fields,
                "requested_at": timestamp,
                "privacy_guard_version": "fixture-verified-replay",
                "payload": {
                    "summary": "Fixture calendar obligation",
                    "calendar_id": "calendar-primary",
                    "start": "2031-01-05T14:00:00Z",
                    "end": "2031-01-05T15:00:00Z",
                },
            },
            causation=domain,
        )
        terminal = append(
            "external_sync.write_completed",
            2,
            {
                **command_fields,
                "command_event_id": command,
                "completed_at": timestamp,
                "external_id": "native-fixture-calendar-event",
                "provider_revision": "fixture-revision-1",
                "provider_called": True,
                "suppressed": False,
                "result": {"applied": True, "fixture_only": True},
            },
            causation=command,
        )
        observation_payload = {
            "member_id": MEMBER,
            "provider": "apple_calendar",
            "provider_account_id": APPLE_CALENDAR_ACCOUNT,
            "calendar_external_id": "calendar-primary",
            "external_event_id": "native-fixture-calendar-event",
            "summary": "Fixture calendar obligation",
            "start": "2031-01-05T14:00:00Z",
            "end": "2031-01-05T15:00:00Z",
            "updated_at": timestamp,
        }
        # Similar title/native ID on another account cannot prove delivery.
        wrong = append(
            "calendar.event_added",
            2,
            {
                **observation_payload,
                "provider_account_id": "provider-account:" + "f" * 32,
            },
            source="fixture-independent-calendar-poll",
        )
        client.portal.call(
            runtime.wait_for_subscriber, "provider-writes:reconciliation", wrong
        )
        assert (
            client.portal.call(_events, runtime, "external_sync.write_reconciled") == []
        )
        observed = append(
            "calendar.event_added",
            2,
            observation_payload,
            source="fixture-independent-calendar-poll",
        )
        client.portal.call(
            runtime.wait_for_subscriber, "provider-writes:reconciliation", observed
        )
        [receipt] = client.portal.call(
            _events, runtime, "external_sync.write_reconciled"
        )
        assert receipt["payload"]["command_event_id"] == command
        assert receipt["payload"]["terminal_event_id"] == terminal
        assert receipt["payload"]["observation_event_id"] == observed
        assert receipt["payload"]["outcome"] == "matched"
        client.portal.call(runtime.wait_for_projection, "triage", receipt["event_id"])
        row = (
            runtime.projections.connection("triage")
            .execute(
                "SELECT state,resolution_reason FROM triage_items WHERE proposal_id=?",
                (proposal_id,),
            )
            .fetchone()
        )
        assert tuple(row) == ("confirmed", "provider_observed_matched")
        source_event = client.portal.call(runtime.event_log.get, observed)
        client.portal.call(runtime.provider_write_reconciler.handle, source_event)
        assert (
            len(client.portal.call(_events, runtime, "external_sync.write_reconciled"))
            == 1
        )
        client.portal.call(runtime.projections.rebuild, "triage")
        replayed = (
            runtime.projections.connection("triage")
            .execute(
                "SELECT state,resolution_reason FROM triage_items WHERE proposal_id=?",
                (proposal_id,),
            )
            .fetchone()
        )
        assert tuple(replayed) == tuple(row)
        assert runtime.settings.adapter_config["adapters"] == []
        assert (
            client.portal.call(
                _events, runtime, "external_sync.write_attempted", "messaging.sent"
            )
            == []
        )


def test_route_reassignment_cannot_recopy_original_private_submission(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "route-reassignment"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        source_id, cue = _forward(client, "route-original", "Call the school office")
        original = client.portal.call(runtime.event_log.get, source_id)
        channel = runtime.direct_intake._channels[CHANNEL_ID]
        reassigned = replace(
            channel,
            routes=tuple(
                replace(route, target_id="member_other_principal")
                for route in channel.routes
            ),
        )
        processor = DirectIntakeProcessor(
            event_log=runtime.event_log,
            event_sink=runtime.events,
            channels=(reassigned,),
        )
        # Exact-source replay consults the immutable decision before current config.
        client.portal.call(processor.handle, original)
        assert len(client.portal.call(_events, runtime, "triage.intent.submitted")) == 1
        runtime.direct_intake._channels[CHANNEL_ID] = reassigned
        revised = _service_message(
            runtime,
            key="route-revised",
            trusted_route_key=ROUTE_A,
            from_identifier="member-a@example.invalid",
            subject="Forward revision",
            body="Call the school office",
            event_at_ms=int(time.time() * 1000),
        )
        revised.payload["native_message_id"] = original["payload"]["native_message_id"]
        revision_id = client.portal.call(runtime.events.write, revised)
        route = _route_result(client, revision_id)
        assert route["payload"]["routing_scope"] == "unresolved"
        assert route["payload"]["cue_event_id"] is None
        [captured] = client.portal.call(_events, runtime, "triage.intent.submitted")
        assert captured["event_id"] == cue["event_id"]
        assert captured["owner_scope"] == f"private:{MEMBER}"


def test_member_dismissal_during_extraction_prevents_late_children(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "decide-during-extraction"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        started, release = Event(), Event()

        async def language(*args: Any, **kwargs: Any) -> Any:
            started.set()
            assert await asyncio.to_thread(release.wait, 10)
            return SimpleNamespace(
                output={
                    "candidates": [
                        {
                            "target_kind": "task",
                            "evidence_quote": "Call the school office",
                            "candidate": {},
                            "confidence": 0.9,
                        }
                    ]
                }
            )

        runtime.run_skill_fn = language
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(
                _forward, client, "during-extraction", "Call the school office"
            )
            try:
                assert started.wait(10)
                [holding] = [
                    item
                    for item in _items(client)
                    if item["state"] == "pending_interpretation"
                ]
                response = client.post(
                    f"/api/member/v1/triage/{holding['proposal_id']}/dismiss",
                    headers=AUTH,
                    json={
                        "request_id": "forward-dismiss-during-extraction",
                        "expected_last_event_id": holding["last_event_id"],
                        "reason": "No longer needed",
                    },
                )
                assert response.status_code == 200, response.text
            finally:
                release.set()
            _, cue = pending.result(timeout=10)
        assert client.portal.call(_events, runtime, "triage.item.proposed") == []
        client.portal.call(runtime.projections.rebuild, "triage")
        row = (
            runtime.projections.connection("triage")
            .execute(
                "SELECT state FROM triage_items WHERE proposal_id=?",
                (cue["payload"]["intent_id"],),
            )
            .fetchone()
        )
        assert row["state"] == "dismissed"
        client.portal.call(runtime.submission_interpreter.handle, cue)
        assert client.portal.call(_events, runtime, "triage.item.proposed") == []


def test_possible_provider_contact_and_terminal_are_not_automatically_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crash-recovery history is synthetic; the real dispatcher cannot contact a provider."""
    instance = tmp_path / "provider-retry-boundary"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        runtime.bus._subscribers["external-sync:central-providers"].degraded = True
        timestamp = "2031-01-01T12:00:00Z"
        fields = {
            "command_id": "forward-contact-recovery",
            "member_id": MEMBER,
            "provider": "google_tasks",
            "provider_account_id": "fixture-google-task-account",
            "operation": "create_task",
            "request_fingerprint": "c" * 64,
        }
        command_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=runtime.settings.instance.tenant_id,
                type="external_sync.write_requested",
                schema_version=2,
                occurred_at=timestamp,
                source_adapter="fixture-command-recovery",
                source_account_id=fields["provider_account_id"],
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                payload={
                    **fields,
                    "domain_approval_event_id": "fixture-domain-approval",
                    "openclaw_approval_event_id": "fixture-openclaw-approval",
                    "privacy_guard_version": "fixture-history",
                    "requested_at": timestamp,
                    "payload": {
                        "title": "Fixture original command",
                        "tasklist_id": "fixture-list",
                    },
                },
            ),
        )
        client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=runtime.settings.instance.tenant_id,
                type="external_sync.write_attempted",
                schema_version=1,
                occurred_at=timestamp,
                source_adapter="external-sync-central-dispatcher",
                source_account_id=fields["provider_account_id"],
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                causation_id=command_id,
                idempotency_key=f"external-sync-attempt:{fields['command_id']}",
                payload={
                    **fields,
                    "command_event_id": command_id,
                    "attempted_at": timestamp,
                },
            ),
        )

        async def forbidden(**kwargs: Any) -> Any:
            raise AssertionError("ambiguous provider creation cannot be retried")

        monkeypatch.setattr(runtime.adapters, "dispatch_write", forbidden)
        command = client.portal.call(runtime.event_log.get, command_id)
        client.portal.call(runtime._dispatch_external_write, command)
        [terminal] = client.portal.call(_events, runtime, "external_sync.write_failed")
        assert "ambiguous_after_possible_contact" in terminal["payload"]["message"]
        assert terminal["payload"]["retryable"] is False
        assert terminal["payload"]["provider_called"] is True
        client.portal.call(runtime._dispatch_external_write, command)
        assert (
            len(client.portal.call(_events, runtime, "external_sync.write_failed")) == 1
        )
        assert (
            len(client.portal.call(_events, runtime, "external_sync.write_attempted"))
            == 1
        )
