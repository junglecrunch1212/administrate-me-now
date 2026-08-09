"""Process proof that source facts cannot disappear behind an empty surface."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import RuntimeSettings, create_application


REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBER = "member_source_principal"
AUTH = {"Tailscale-User-Login": "source-principal.ts.net"}
KINDS = {
    "task",
    "commitment",
    "calendar_event",
    "reminder",
    "list",
    "list_item",
    "recurrence",
    "party_identifier",
    "party_candidate",
    "party_relationship",
    "relationship_fact",
}


def _settings(instance_dir: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=b"a" * 32,
        repository_root=REPO_ROOT,
        member_identities={"source-principal.ts.net": MEMBER},
        member_roles={MEMBER: "principal"},
        deterministic_openclaw_fixture=True,
    )


def _message(
    *, tenant_id: str, key: str, body: str, sensitivity: str = "normal"
) -> EventEnvelope:
    now = "2026-08-08T12:00:00Z"
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=tenant_id,
        type="messaging.received",
        schema_version=3,
        occurred_at=now,
        source_adapter="gmail",
        source_account_id="fixture-account",
        owner_scope=f"private:{MEMBER}",
        visibility_scope=f"private:{MEMBER}",
        sensitivity=sensitivity,  # type: ignore[arg-type]
        idempotency_key=f"source-assurance:{key}",
        payload={
            "provider": "gmail",
            "provider_account_id": "fixture-account",
            "member_id": MEMBER,
            "source_channel": "gmail",
            "native_message_id": key,
            "thread_id": f"thread-{key}",
            "from_identifier": "sender@example.invalid",
            "to_identifier": "member@example.invalid",
            "workspace": "personal",
            "subject": "Source assurance fixture",
            "body_text": body,
            "received_at": now,
            "provider_labels": ["INBOX"],
            "attachments": [],
        },
    )


def test_every_source_reaches_an_audited_disposition_in_production_composition(
    tmp_path: Path,
) -> None:
    app = create_application(_settings(tmp_path / "instance"))
    with TestClient(app) as client:
        runtime = app.state.runtime
        harmless_id = client.portal.call(
            runtime.events.write,
            _message(
                tenant_id=runtime.settings.instance.tenant_id,
                key="harmless",
                body="Thanks for the household update.",
            ),
        )
        privileged_id = client.portal.call(
            runtime.events.write,
            _message(
                tenant_id=runtime.settings.instance.tenant_id,
                key="privileged",
                body="Attorney-client material must stay outside model processing.",
                sensitivity="privileged",
            ),
        )
        for event_id in (harmless_id, privileged_id):
            client.portal.call(
                runtime.wait_for_subscriber,
                "pipeline:pipeline:noise_filtering",
                event_id,
            )

        for index, kind in enumerate(sorted(KINDS)):
            response = client.post(
                "/api/member/v1/triage/intents",
                headers=AUTH,
                json={
                    "text": f"Exact member submission for {kind}",
                    "source_surface": "today",
                    "kind_hint": kind,
                    "client_request_id": f"source-kind-{index:02d}",
                },
            )
            assert response.status_code == 202, response.text
            assert response.json()["state"] == "pending_review"

        list_event_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=runtime.settings.instance.tenant_id,
                type="list.created",
                schema_version=1,
                occurred_at="2026-08-08T12:00:00Z",
                source_adapter="google_tasks",
                source_account_id="fixture-account",
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key="source-assurance:list-created",
                payload={
                    "member_id": MEMBER,
                    "provider": "google_tasks",
                    "provider_account_id": "fixture-account",
                    "list_external_id": "fixture-list",
                    "list_name": "Fixture list",
                    "is_default": False,
                    "changed_at": "2026-08-08T12:00:00Z",
                },
            ),
        )
        client.portal.call(runtime.wait_for_projection, "lists", list_event_id)

        diagnostics = client.get("/api/system/ready").json()
        capability = diagnostics["capabilities"]["policy:source-assurance"]
        counts = capability["details"]["counts"]
        assert capability["state"] == "healthy"
        assert counts["missing"] == 0
        assert counts["failed"] == 0
        assert counts["pending"] == 0
        assert counts["no_candidate"] >= 1
        assert counts["quarantined"] >= 1
        assert counts["eligible_candidates"] >= len(KINDS)
        assert counts["accounted"] == counts["obligations"]

        triage = client.get("/api/member/v1/surfaces/triage", headers=AUTH).json()
        assert triage["processing_health"]["state"] == "healthy"
        triage_counts = triage["processing_health"]["details"]["source_assurance"][
            "counts"
        ]
        assert triage_counts["accounted"] == triage_counts["obligations"]
        assert KINDS <= {item["target_kind"] for item in triage["items"]}

        settings = client.get("/api/member/v1/surfaces/settings", headers=AUTH).json()
        source_capability = next(
            item
            for item in settings["system"]["capabilities"]
            if item["name"] == "source-assurance"
        )
        assert source_capability["state"] == "healthy"
        assert source_capability["details"]["content_exposed"] is False
