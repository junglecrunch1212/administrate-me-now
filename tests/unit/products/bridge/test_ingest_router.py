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
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.products.bridge.app import create_app
from adminme.products.bridge.auth import (
    DictMemberIdResolver,
    TAILSCALE_IDENTITY_HEADER,
)


class _FakeEventLog:
    """Async-compatible double for :class:`EventLog`.

    The bridge router only needs ``append`` / ``append_batch`` —
    everything else (read paths, migrations, sqlcipher) is out of scope
    at this seam."""

    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []
        self._counter = 0

    async def append(self, envelope: EventEnvelope) -> str:
        self._counter += 1
        eid = f"ev_test_{self._counter:04d}"
        self.events.append(envelope)
        return eid

    async def append_batch(self, envelopes: list[EventEnvelope]) -> list[str]:
        ids: list[str] = []
        for env in envelopes:
            ids.append(await self.append(env))
        return ids


def _client_with_state(
    *,
    log: _FakeEventLog | None = None,
    resolver: DictMemberIdResolver | None = None,
) -> tuple[TestClient, _FakeEventLog]:
    """Build an app with wired-up state. TestClient triggers lifespan;
    pre-seeded state survives because the lifespan only injects if the
    attribute is unset."""
    app = create_app()
    app.state.event_log = log or _FakeEventLog()
    app.state.member_id_resolver = resolver or DictMemberIdResolver(
        {"m1@example.ts.net": "m1"}
    )
    app.state.ingest_count = 0
    client = TestClient(app)
    return client, app.state.event_log


def _envelope_dict(
    *,
    owner_scope: str = "private:m1",
    event_type: str = "bridge.enrolled",
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
        "source_adapter": "bridge",
        "source_account_id": "bridge-node-001",
        "owner_scope": owner_scope,
        "visibility_scope": owner_scope,
        "sensitivity": "normal",
        "payload": payload
        if payload is not None
        else {
            "member_id": "m1",
            "bridge_node_id": "bridge-node-001",
            "tailscale_identity": "m1@example.ts.net",
            "adapters_active": ["apple-notes"],
            "enrolled_at": "2026-05-18T12:00:00+00:00",
        },
    }


def test_ingest_single_happy_path_returns_202_and_event_id() -> None:
    client, log = _client_with_state()
    resp = client.post(
        "/api/bridge/ingest",
        json=_envelope_dict(),
        headers={TAILSCALE_IDENTITY_HEADER: "m1@example.ts.net"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["event_id"].startswith("ev_test_")
    assert body["owner_scope"] == "private:m1"
    assert len(log.events) == 1


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
        headers={TAILSCALE_IDENTITY_HEADER: "intruder@example.ts.net"},
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
        headers={TAILSCALE_IDENTITY_HEADER: "m1@example.ts.net"},
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
        headers={TAILSCALE_IDENTITY_HEADER: "m1@example.ts.net"},
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
        headers={TAILSCALE_IDENTITY_HEADER: "m1@example.ts.net"},
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
        headers={TAILSCALE_IDENTITY_HEADER: "m1@example.ts.net"},
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
        headers={TAILSCALE_IDENTITY_HEADER: "m1@example.ts.net"},
    )
    client.post(
        "/api/bridge/ingest/batch",
        json={"envelopes": [_envelope_dict(), _envelope_dict()]},
        headers={TAILSCALE_IDENTITY_HEADER: "m1@example.ts.net"},
    )
    health_after = client.get("/api/bridge/health").json()
    assert health_after["ingest_count"] == 3


def test_ingest_single_503_when_event_log_unavailable() -> None:
    """No event log wired → loud 503, NOT silent acceptance of writes
    that would otherwise be dropped."""
    app = create_app()
    app.state.member_id_resolver = DictMemberIdResolver(
        {"m1@example.ts.net": "m1"}
    )
    app.state.ingest_count = 0
    # NB: app.state.event_log deliberately unset.
    client = TestClient(app)
    resp = client.post(
        "/api/bridge/ingest",
        json=_envelope_dict(),
        headers={TAILSCALE_IDENTITY_HEADER: "m1@example.ts.net"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "event_log_unavailable"


def test_ingest_batch_empty_envelopes_list_returns_422() -> None:
    """An empty batch is a client bug, not a no-op. ``min_length=1`` on
    the request model surfaces it as 422."""
    client, _ = _client_with_state()
    resp = client.post(
        "/api/bridge/ingest/batch",
        json={"envelopes": []},
        headers={TAILSCALE_IDENTITY_HEADER: "m1@example.ts.net"},
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "scope_claim,expected_status",
    [
        ("private:m1", 202),
        ("private:m2", 403),
        ("shared:household", 403),
        ("org:morningside", 403),
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
        headers={TAILSCALE_IDENTITY_HEADER: "m1@example.ts.net"},
    )
    assert resp.status_code == expected_status
