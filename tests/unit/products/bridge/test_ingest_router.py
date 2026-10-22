"""Unit tests for ``adminme.products.bridge.routers.ingest``.

Pins the F-3 hard rules end-to-end at the router seam:

* the resolved Tailscale identity is the SOLE source of truth for
  ``owner_scope`` (claim must equal ``private:<member_id>`` or hard 403);
* envelope validation surfaces as 422;
* batch ingest rejects the whole batch on the first bad envelope and
  reports the offset;
* the health endpoint exposes the atomic counter so operators can
  spot-check the bridge.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.products.bridge.app import create_app
from adminme.products.bridge.auth import (
    DictMemberIdResolver,
    TAILSCALE_IDENTITY_HEADER,
)

ENROLLMENT_ID = "bridge-enrollment-test"


def _headers(identity: str = "m1@example.ts.net") -> dict[str, str]:
    return {
        TAILSCALE_IDENTITY_HEADER: identity,
        "X-AdministrateMe-Bridge-Enrollment": ENROLLMENT_ID,
    }


def _active_bridge_record() -> dict[str, Any]:
    pack = "knowledge-obsidian"
    return {
        "status": "active",
        "member_id": "m1",
        "enrollment_id": ENROLLMENT_ID,
        "configured_packs": [pack],
        "enabled_packs": [pack],
        "authorized_packs": [pack],
        "pack_config": {pack: {"member_id": "m1"}},
        "pack_capability_states": {pack: "not_required"},
        "provider_capability_state": "not_required",
        "capability_attestations": {},
    }


class _FakeEventSink:
    """Async-compatible double for :class:`EventLog`.

    The bridge router only needs ``append`` / ``append_batch`` —
    everything else (read paths, migrations, sqlcipher) is out of scope
    at this seam."""

    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []
        self._counter = 0

    async def write(self, envelope: EventEnvelope, **_kwargs: Any) -> str:
        self._counter += 1
        eid = f"ev_test_{self._counter:04d}"
        self.events.append(envelope)
        return eid

    async def write_batch(
        self, envelopes: list[EventEnvelope], **_kwargs: Any
    ) -> list[str]:
        ids: list[str] = []
        for env in envelopes:
            ids.append(await self.write(env))
        return ids


class _FakeEventLog:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = list(events)

    async def read_since(
        self,
        _cursor: str | None = None,
        *,
        owner_scope: str | None = None,
        types: list[str] | None = None,
        **_kwargs: Any,
    ):
        for event in self.events:
            if owner_scope is not None and event.get("owner_scope") != owner_scope:
                continue
            if types is not None and event.get("type") not in types:
                continue
            yield event

    async def get(self, event_id: str) -> dict[str, Any] | None:
        return next(
            (event for event in self.events if event.get("event_id") == event_id),
            None,
        )

    async def get_by_idempotency(
        self, tenant_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        return next(
            (
                event
                for event in self.events
                if event.get("tenant_id") == tenant_id
                and event.get("idempotency_key") == idempotency_key
            ),
            None,
        )


def _client_with_state(
    *,
    log: _FakeEventSink | None = None,
    resolver: DictMemberIdResolver | None = None,
    bridge_record: dict[str, Any] | None = None,
) -> tuple[TestClient, _FakeEventSink]:
    """Build an app with wired-up state. TestClient triggers lifespan;
    pre-seeded state survives because the lifespan only injects if the
    attribute is unset."""
    app = create_app()
    app.state.event_sink = log or _FakeEventSink()
    app.state.member_id_resolver = resolver or DictMemberIdResolver(
        {"m1@example.ts.net": "m1"}
    )
    app.state.ingest_count = 0
    app.state.bridge_enrollments = {
        "m1@example.ts.net": bridge_record or _active_bridge_record()
    }
    client = TestClient(app)
    return client, app.state.event_sink


def _envelope_dict(
    *,
    owner_scope: str = "private:m1",
    event_type: str = "member_knowledge.note_added",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Synthesize a JSON-serializable envelope. Defaults satisfy
    :class:`EventEnvelope` validation; tests perturb individual fields."""
    return {
        "event_at_ms": int(time.time() * 1000),
        "tenant_id": "tenant-a",
        "type": event_type,
        "schema_version": 1,
        "occurred_at": EventEnvelope.now_utc_iso(),
        "source_adapter": "knowledge-obsidian",
        "source_account_id": "obsidian",
        "owner_scope": owner_scope,
        "visibility_scope": owner_scope,
        "sensitivity": "sensitive",
        "payload": payload
        if payload is not None
        else {
            "member_id": "m1",
            "knowledge_id": "knowledge-test",
            "title": "Test note",
            "body": "Bridge ingest contract fixture",
            "source_kind": "obsidian",
            "source_external_id": "note-1",
            "content_sha256": "a" * 64,
            "captured_at": "2026-05-18T12:00:00+00:00",
        },
    }


def _calendar_bridge_record(*, shared: bool) -> dict[str, Any]:
    pack = "calendaring-apple-calendar-bridge"
    account = "provider-account:" + "a" * 32
    calendar_id = "calendar-shared" if shared else "calendar-private"
    return {
        "status": "active",
        "member_id": "m1",
        "enrollment_id": ENROLLMENT_ID,
        "configured_packs": [pack],
        "enabled_packs": [pack],
        "authorized_packs": [pack],
        "pack_config": {
            pack: {
                "member_id": "m1",
                "provider_account_id": account,
                "deployment_target": "bridge",
                "workspace": "personal",
                "permission_scope": [
                    "eventkit.calendar.read",
                    "eventkit.calendar.write",
                ],
                "calendar_identifier": calendar_id,
                "allowed_container_ids": [calendar_id],
                "shared_container_ids": [calendar_id] if shared else [],
                "binding_provenance": "member-bridge-enrollment",
                "ownership_attested": True,
            }
        },
        "pack_capability_states": {pack: "attested"},
        "provider_capability_state": "attested",
        "capability_attestations": {pack: {"event_id": "ev-auth-calendar"}},
    }


def _calendar_envelope(*, calendar_id: str) -> dict[str, Any]:
    account = "provider-account:" + "a" * 32
    now = EventEnvelope.now_utc_iso()
    return {
        "event_at_ms": int(time.time() * 1000),
        "tenant_id": "tenant-a",
        "type": "calendar.event_added",
        "schema_version": 2,
        "occurred_at": now,
        "source_adapter": "calendaring-apple-calendar",
        "source_account_id": account,
        "owner_scope": "private:m1",
        "visibility_scope": "private:m1",
        "sensitivity": "normal",
        "payload": {
            "member_id": "m1",
            "provider": "apple_calendar",
            "provider_account_id": account,
            "calendar_external_id": calendar_id,
            "external_event_id": "event-1",
            "summary": "Shared fixture",
            "start": now,
            "end": now,
            "updated_at": now,
        },
    }


def _calendar_command(*, calendar_id: str) -> dict[str, Any]:
    account = "provider-account:" + "a" * 32
    return {
        "event_id": "ev-command-calendar",
        "tenant_id": "tenant-a",
        "type": "external_sync.write_requested",
        "schema_version": 2,
        "owner_scope": "private:m1",
        "payload": {
            "command_id": "command-calendar",
            "member_id": "m1",
            "provider": "apple_calendar",
            "provider_account_id": account,
            "operation": "update_event",
            "target_external_id": "event-1",
            "expected_provider_revision": "revision-1",
            "payload": {"calendar_external_id": calendar_id, "summary": "Updated"},
            "requested_at": "2026-08-27T12:00:00Z",
            "domain_approval_event_id": "ev-domain-approval",
            "openclaw_approval_event_id": "ev-openclaw-approval",
            "request_fingerprint": "b" * 64,
            "privacy_guard_version": "bridge-test-v1",
            "origin_proposal_id": None,
        },
    }


def _command_client(*, command: dict[str, Any]) -> tuple[TestClient, _FakeEventSink]:
    app = create_app()
    sink = _FakeEventSink()
    app.state.event_sink = sink
    app.state.event_log = _FakeEventLog([command])
    app.state.member_id_resolver = DictMemberIdResolver({"m1@example.ts.net": "m1"})
    app.state.ingest_count = 0
    app.state.bridge_enrollments = {
        "m1@example.ts.net": _calendar_bridge_record(shared=False)
    }

    class _Observation:
        async def is_active(self) -> bool:
            return False

    app.state.runtime = SimpleNamespace(
        settings_command_locks={},
        observation=_Observation(),
        settings=SimpleNamespace(
            instance=SimpleNamespace(tenant_id="tenant-a"), adapter_config={}
        ),
    )
    return TestClient(app), sink


def test_ingest_single_happy_path_returns_202_and_event_id() -> None:
    client, log = _client_with_state()
    resp = client.post(
        "/api/bridge/ingest",
        json=_envelope_dict(),
        headers=_headers(),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["event_id"].startswith("ev_test_")
    assert body["owner_scope"] == "private:m1"
    assert len(log.events) == 1


@pytest.mark.parametrize(
    ("shared", "calendar_id", "expected_scope"),
    [
        (False, "calendar-private", "private:m1"),
        (True, "calendar-shared", "shared:household"),
    ],
)
def test_calendar_sharing_is_promoted_only_by_reviewed_container_grant(
    shared: bool,
    calendar_id: str,
    expected_scope: str,
) -> None:
    client, log = _client_with_state(
        bridge_record=_calendar_bridge_record(shared=shared)
    )
    response = client.post(
        "/api/bridge/ingest",
        json=_calendar_envelope(calendar_id=calendar_id),
        headers=_headers(),
    )

    assert response.status_code == 202, response.text
    assert len(log.events) == 1
    assert log.events[0].owner_scope == expected_scope
    assert log.events[0].visibility_scope == expected_scope


def test_bridge_commands_require_exact_authorized_container() -> None:
    allowed_client, _ = _command_client(
        command=_calendar_command(calendar_id="calendar-private")
    )
    allowed = allowed_client.get(
        "/api/bridge/commands",
        params={"providers": "apple_calendar"},
        headers=_headers(),
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["count"] == 1

    denied_client, _ = _command_client(
        command=_calendar_command(calendar_id="calendar-not-reviewed")
    )
    denied = denied_client.get(
        "/api/bridge/commands",
        params={"providers": "apple_calendar"},
        headers=_headers(),
    )
    assert denied.status_code == 200, denied.text
    assert denied.json()["commands"] == []


def test_exact_v2_terminal_result_is_bound_to_real_command_and_container() -> None:
    command = _calendar_command(calendar_id="calendar-private")
    client, sink = _command_client(command=command)
    command_payload = command["payload"]
    result = {
        "event_at_ms": int(time.time() * 1000),
        "tenant_id": "tenant-a",
        "type": "external_sync.write_completed",
        "schema_version": 2,
        "occurred_at": EventEnvelope.now_utc_iso(),
        "source_adapter": "adminme-bridge-command-dispatcher",
        "source_account_id": command_payload["provider_account_id"],
        "owner_scope": "private:m1",
        "visibility_scope": "private:m1",
        "sensitivity": "normal",
        "causation_id": command["event_id"],
        "idempotency_key": "external-sync-result:command-calendar",
        "payload": {
            "command_id": "command-calendar",
            "member_id": "m1",
            "provider": "apple_calendar",
            "operation": "update_event",
            "external_id": "event-1",
            "provider_revision": "revision-2",
            "result": {},
            "completed_at": EventEnvelope.now_utc_iso(),
            "suppressed": False,
            "command_event_id": command["event_id"],
            "domain_approval_event_id": "ev-domain-approval",
            "openclaw_approval_event_id": "ev-openclaw-approval",
            "provider_account_id": command_payload["provider_account_id"],
            "request_fingerprint": "b" * 64,
            "provider_called": True,
            "origin_proposal_id": None,
        },
    }

    response = client.post("/api/bridge/ingest", json=result, headers=_headers())

    assert response.status_code == 202, response.text
    assert len(sink.events) == 1
    assert sink.events[0].payload["provider_called"] is True


def test_ingest_single_missing_tailscale_header_403() -> None:
    client, _ = _client_with_state()
    resp = client.post("/api/bridge/ingest", json=_envelope_dict())
    assert resp.status_code == 403
    assert resp.json()["detail"] == "missing_tailscale_identity"


def test_ingest_single_unknown_identity_403() -> None:
    """Memo 04 §7(d): an unbound Tailscale identity is a privileged-
    access violation, not a soft fallback."""
    client, _ = _client_with_state()
    resp = client.post(
        "/api/bridge/ingest",
        json=_envelope_dict(),
        headers=_headers("intruder@example.ts.net"),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "unknown_tailscale_identity"


def test_ingest_single_owner_scope_claim_mismatch_hard_refuse_403() -> None:
    """F-3 (HARD): if the envelope claims an ``owner_scope`` that doesn't
    match the resolved identity's ``private:<member_id>``, the router
    refuses with 403 — NOT silent overwrite. Memo 04 §7(d)."""
    client, log = _client_with_state()
    resp = client.post(
        "/api/bridge/ingest",
        json=_envelope_dict(owner_scope="private:m2"),  # claim disagrees
        headers=_headers(),
    )
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["error"] == "owner_scope_claim_mismatch"
    assert detail["claimed"] == "private:m2"
    assert detail["expected"] == "private:m1"
    # F-3 SILENT-OVERWRITE GUARD: nothing made it into the event log.
    assert log.events == []


def test_ingest_single_malformed_envelope_returns_422() -> None:
    """Missing required envelope fields → 422 from :class:`EventEnvelope`
    validation. [D7] envelope shape is closed."""
    client, _ = _client_with_state()
    bad = _envelope_dict()
    del bad["tenant_id"]
    resp = client.post(
        "/api/bridge/ingest",
        json=bad,
        headers=_headers(),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "envelope_validation_failed"


def test_ingest_batch_happy_path_returns_202_and_event_ids() -> None:
    client, log = _client_with_state()
    body = {
        "envelopes": [
            _envelope_dict(),
            _envelope_dict(),
            _envelope_dict(),
        ]
    }
    resp = client.post(
        "/api/bridge/ingest/batch",
        json=body,
        headers=_headers(),
    )
    assert resp.status_code == 202, resp.text
    payload = resp.json()
    assert payload["count"] == 3
    assert len(payload["event_ids"]) == 3
    assert len(log.events) == 3


def test_ingest_batch_one_bad_envelope_rejects_whole_batch_400_with_offset() -> None:
    """Per §7 c2 + F-3: a batch with a claim-mismatched envelope at
    offset 1 is rejected wholesale; nothing is appended."""
    client, log = _client_with_state()
    body = {
        "envelopes": [
            _envelope_dict(),
            _envelope_dict(owner_scope="private:m2"),  # bad — claim mismatch
            _envelope_dict(),
        ]
    }
    resp = client.post(
        "/api/bridge/ingest/batch",
        json=body,
        headers=_headers(),
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["error"] == "batch_envelope_rejected"
    assert detail["offset"] == 1
    # WHOLE-BATCH REJECT: nothing was appended, even the leading good envelope.
    assert log.events == []


def test_health_endpoint_returns_status_and_atomic_counter_increment() -> None:
    """The atomic counter advances by 1 per single ingest + by ``count``
    per batch ingest, and is observable through ``/api/bridge/health``."""
    client, _ = _client_with_state()

    health_before = client.get("/api/bridge/health").json()
    assert health_before["status"] == "ok"
    assert health_before["bridge_endpoint"] == "/api/bridge/ingest"
    assert health_before["ingest_count"] == 0

    client.post(
        "/api/bridge/ingest",
        json=_envelope_dict(),
        headers=_headers(),
    )
    client.post(
        "/api/bridge/ingest/batch",
        json={"envelopes": [_envelope_dict(), _envelope_dict()]},
        headers=_headers(),
    )
    health_after = client.get("/api/bridge/health").json()
    assert health_after["ingest_count"] == 3


def test_health_distinguishes_transport_from_provider_capability() -> None:
    transport_client, _ = _client_with_state(
        bridge_record={
            **_calendar_bridge_record(shared=False),
            "authorized_packs": [],
            "pack_capability_states": {
                "calendaring-apple-calendar-bridge": "not_attested"
            },
            "provider_capability_state": "not_attested",
            "capability_attestations": {},
        }
    )
    transport = transport_client.get("/api/bridge/health").json()
    assert transport["enrollment_state"] == "transport-active"
    assert transport["provider_capability_state"] == "not_attested"
    assert transport["provider_blocked_count"] == 1
    assert transport["provider_ready_count"] == 0

    invalid_client, _ = _client_with_state(
        bridge_record={
            **_calendar_bridge_record(shared=False),
            "pack_config": {},
        }
    )
    invalid = invalid_client.get("/api/bridge/health").json()
    assert invalid["provider_capability_state"] == "invalid"
    assert invalid["invalid_active_count"] == 1


def test_ingest_single_503_when_event_log_unavailable() -> None:
    """No event log wired → loud 503, NOT silent acceptance of writes
    that would otherwise be dropped."""
    app = create_app()
    app.state.member_id_resolver = DictMemberIdResolver({"m1@example.ts.net": "m1"})
    app.state.ingest_count = 0
    app.state.bridge_enrollments = {"m1@example.ts.net": _active_bridge_record()}
    # NB: app.state.event_log deliberately unset.
    client = TestClient(app)
    resp = client.post(
        "/api/bridge/ingest",
        json=_envelope_dict(),
        headers=_headers(),
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "event_sink_unavailable"


def test_ingest_batch_empty_envelopes_list_returns_422() -> None:
    """An empty batch is a client bug, not a no-op. ``min_length=1`` on
    the request model surfaces it as 422."""
    client, _ = _client_with_state()
    resp = client.post(
        "/api/bridge/ingest/batch",
        json={"envelopes": []},
        headers=_headers(),
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "scope_claim,expected_status",
    [
        ("private:m1", 202),
        ("private:m2", 403),
        ("shared:household", 403),
        ("org:external-operator", 403),
    ],
)
def test_ingest_single_owner_scope_claim_only_exact_match_accepted(
    scope_claim: str, expected_status: int
) -> None:
    """F-3 parametrized: the resolved identity ``m1`` admits ONLY
    ``private:m1``. Every other scope shape is a 403, regardless of
    whether it would have been semantically valid in another context."""
    client, _ = _client_with_state()
    resp = client.post(
        "/api/bridge/ingest",
        json=_envelope_dict(owner_scope=scope_claim),
        headers=_headers(),
    )
    assert resp.status_code == expected_status
