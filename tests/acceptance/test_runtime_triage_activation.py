"""Production launch defaults and retained shadow admission through the real app.

Only host topology, secret storage, and the HTTP listener are fixture boundaries.
RuntimeSettings, the composition root, durable bus, projections, and member routes
are real. The synthetic member-intent sources require no language/provider call.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.reconciliation import ReconciliationAdmissionEvaluator
from adminme.runtime.application import RuntimeSettings
from bootstrap import runtime_service
from bootstrap.contracts import DeploymentPaths


ROOT = Path(__file__).resolve().parents[2]
MEMBER = "fixture-launch-adult-a"  # fixture:tenant_data:ok
OTHER = "fixture-launch-adult-b"  # fixture:tenant_data:ok
HEADERS = {"Tailscale-User-Login": "launch-a.ts.net"}
OTHER_HEADERS = {"Tailscale-User-Login": "launch-b.ts.net"}
ADMISSION_ENV = "ADMINME_RECONCILIATION_ADMISSION_ENABLED"
HUMAN_ENV = "ADMINME_HUMAN_TRIAGE_MANAGEMENT_ENABLED"
RULE_API = "/api/member/v1/triage-learning/v2"


def _launch_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    config = paths.instance_dir / "config"
    config.mkdir(exist_ok=True)
    (config / "secret-references.json").write_text(
        json.dumps(
            {
                "references": {
                    "event_log": "fixture-log",
                    "openclaw_shared": "fixture-gateway",
                }
            }
        ),
        encoding="utf-8",
    )
    (config / "member-mapping.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "household_label": "Fixture household",
                "members": [
                    {
                        "member_id": MEMBER,
                        "role": "principal",
                        "label": "Fixture adult A",
                        "tailscale_identity": "launch-a.ts.net",
                    },
                    {
                        "member_id": OTHER,
                        "role": "principal",
                        "label": "Fixture adult B",
                        "tailscale_identity": "launch-b.ts.net",
                    },
                ],
                "memberships": [],
                "relationships": [],
                "parties": [],
            }
        ),
        encoding="utf-8",
    )
    (config / "adapters.json").write_text(
        json.dumps({"adapters": [], "adapter_supervisor_port": 0}),
        encoding="utf-8",
    )

    class FixtureSecrets:
        def get(self, reference: str) -> bytes:
            assert reference in {"fixture-log", "fixture-gateway"}
            return b"l" * 32 if reference == "fixture-log" else b"g" * 32

    monkeypatch.setenv("ADMINME_INSTANCE_ROOT", str(paths.root))
    monkeypatch.setenv("ADMINME_REPOSITORY_ROOT", str(ROOT))
    monkeypatch.setattr(
        runtime_service, "secret_store", lambda _paths: FixtureSecrets()
    )
    monkeypatch.setattr(
        runtime_service, "sample_host_topology", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        runtime_service, "model_provider_configured", lambda _paths: False
    )
    monkeypatch.setattr(runtime_service, "load_deployment_evidence", lambda _paths: {})
    monkeypatch.setattr(
        runtime_service,
        "capture_deployment_snapshot",
        lambda _paths: SimpleNamespace(
            predecessor_commit="a" * 40,
            instance_fingerprint="b" * 64,
            host_fingerprint="c" * 64,
            configuration_fingerprint="d" * 64,
        ),
    )
    captured: list[FastAPI] = []

    def capture_listener(app: FastAPI, **options: Any) -> None:
        assert options["host"] == "127.0.0.1"
        assert options["proxy_headers"] is False
        captured.append(app)

    monkeypatch.setattr(runtime_service.uvicorn, "run", capture_listener)
    # Do not replace either settings constructor or create_application: this
    # must fail if the real launchd target forgets to propagate either flag.
    runtime_service.main()
    assert len(captured) == 1
    return captured[0]


async def _settle(runtime: Any) -> None:
    deadline = asyncio.get_running_loop().time() + 60.0
    prior: str | None = None
    stable = 0
    statuses: dict[str, dict[str, Any]] = {}
    while asyncio.get_running_loop().time() < deadline:
        statuses = {
            name: await runtime.bus.subscriber_status(name)
            for name in sorted(runtime.bus._subscribers)  # noqa: SLF001
        }
        assert not any(value["degraded"] for value in statuses.values()), statuses
        latest = await runtime.event_log.latest_event_id()
        if (
            all(value["lag_count"] == 0 for value in statuses.values())
            and latest == prior
        ):
            stable += 1
        else:
            stable = 0
        prior = latest
        if stable >= 2:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"production-launch fixture did not settle: {statuses}")


def _triage(client: TestClient, *, other: bool = False) -> dict[str, Any]:
    response = client.get(
        "/api/member/v1/surfaces/triage",
        params={"triage_lens": "all_candidates", "triage_limit": 100},
        headers=OTHER_HEADERS if other else HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _receipts(runtime: Any) -> list[dict[str, Any]]:
    return [
        event
        async for event in runtime.event_log.read_since(
            types=["triage.reconciliation.decided"]
        )
    ]


async def _external_writes(runtime: Any) -> int:
    return await runtime.event_log.count_since(
        None, types=["external_sync.write_requested"]
    )


def _rows(client: TestClient) -> dict[str, dict[str, Any]]:
    conn = client.app.state.runtime.projections.connection("triage")
    return {
        str(row["proposal_id"]): dict(row)
        for row in conn.execute(
            "SELECT * FROM triage_items ORDER BY proposal_id"
        ).fetchall()
    }


def _seed_intent(client: TestClient, suffix: str) -> str:
    runtime = client.app.state.runtime
    now = datetime.now(timezone.utc).isoformat()
    proposal_id = f"fixture-launch-intent-{suffix}"
    client.portal.call(
        runtime.events.write,
        EventEnvelope(
            event_at_ms=time.time_ns() // 1_000_000,
            tenant_id=runtime.settings.instance.tenant_id,
            type="triage.intent.submitted",
            schema_version=1,
            occurred_at=now,
            source_adapter="direct-intake",
            source_account_id=MEMBER,
            owner_scope=f"private:{MEMBER}",
            visibility_scope=f"private:{MEMBER}",
            actor_identity=MEMBER,
            idempotency_key=f"fixture-launch-source:{suffix}",
            payload={
                "intent_id": proposal_id,
                "member_id": MEMBER,
                "text": f"Review fixture checklist {suffix}",
                "source_surface": "triage",
                "kind_hint": "task",
                "source_event_id": None,
                "submitted_at": now,
                "client_request_id": f"fixture-launch-capture-{suffix}",
            },
        ),
    )
    client.portal.call(_settle, runtime)
    return proposal_id


@pytest.mark.parametrize(
    ("admission_value", "human_value", "admission", "human"),
    [
        (None, None, True, True),
        ("0", "0", False, False),
        ("0", "1", False, True),
        ("1", "0", True, False),
    ],
)
def test_real_launchd_composition_defaults_and_independent_explicit_opt_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    admission_value: str | None,
    human_value: str | None,
    admission: bool,
    human: bool,
) -> None:
    for name, value in ((ADMISSION_ENV, admission_value), (HUMAN_ENV, human_value)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    with TestClient(_launch_app(tmp_path, monkeypatch)) as client:
        runtime = client.app.state.runtime
        assert runtime.settings.reconciliation_admission_enabled is admission
        assert runtime.settings.human_triage_management_enabled is human
        assert runtime.settings.deterministic_openclaw_fixture is False
        assert runtime.settings.deployment_evidence_required is True
        proposal_id = _seed_intent(client, "defaults")
        receipts = client.portal.call(_receipts, runtime)
        assert len(receipts) == 1
        assert receipts[0]["payload"]["admission_active"] is admission
        assert bool(_rows(client)[proposal_id]["reconciliation_active"]) is admission
        assert _triage(client)["human_triage_management"]["active"] is human
        rule = {
            "conditions": {
                "provider_account_id": MEMBER,
                "adapter_instance_id": "triage",
                "message_purpose": "personal_request",
            },
            "exceptions": [],
            "action": "review",
            "origin": "explicit",
        }
        preview = client.post(f"{RULE_API}/preview", headers=HEADERS, json=rule)
        assert preview.status_code == 200, preview.text
        assert preview.json()["activation_available"] is human
        saved = client.post(
            f"{RULE_API}/rules",
            headers=HEADERS,
            json={
                **rule,
                "request_id": "fixture-launch-rule-save",
                "preview_digest": preview.json()["preview_digest"],
                "mode": "active",
            },
        )
        assert saved.status_code == (200 if human else 409), saved.text
        if human:
            receipt = saved.json()
            undone = client.post(
                f"{RULE_API}/rules/{receipt['rule_id']}/review",
                headers=HEADERS,
                json={
                    "request_id": "fixture-launch-rule-undo",
                    "expected_last_event_id": receipt["event_id"],
                    "decision": "revoke",
                    "reason": "Member undid fixture preference",
                },
            )
            assert undone.status_code == 200, undone.text
        assert _triage(client, other=True)["items"] == []
        client.portal.call(_settle, runtime)
        assert client.portal.call(_external_writes, runtime) == 0

    isolated = RuntimeSettings.credential_free(
        tmp_path / "isolated", encryption_key=b"i" * 32, repository_root=ROOT
    )
    assert isolated.reconciliation_admission_enabled is False
    assert isolated.human_triage_management_enabled is False


def test_launch_upgrade_replaces_retained_shadow_receipts_without_reopening_member_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_version = ReconciliationAdmissionEvaluator.CONSUMER_VERSION
    assert current_version != "2.0.0"
    monkeypatch.setattr(ReconciliationAdmissionEvaluator, "CONSUMER_VERSION", "2.0.0")
    monkeypatch.setenv(ADMISSION_ENV, "0")
    monkeypatch.setenv(HUMAN_ENV, "0")
    with TestClient(_launch_app(tmp_path, monkeypatch)) as client:
        runtime = client.app.state.runtime
        open_id = _seed_intent(client, "open")
        dismissed_id = _seed_intent(client, "dismissed")
        edited_id = _seed_intent(client, "edited")
        before = _rows(client)
        dismissed = client.post(
            f"/api/member/v1/triage/{dismissed_id}/dismiss",
            headers=HEADERS,
            json={
                "request_id": "fixture-launch-member-dismissal",
                "expected_last_event_id": before[dismissed_id]["last_event_id"],
                "reason": "Member chose to dismiss",
            },
        )
        assert dismissed.status_code == 200, dismissed.text
        edited = client.post(
            f"/api/member/v1/triage/{edited_id}/enrich",
            headers=HEADERS,
            json={
                "request_id": "fixture-launch-member-edit",
                "expected_last_event_id": before[edited_id]["last_event_id"],
                "title": "Member corrected fixture checklist",
                "target_kind": "task",
            },
        )
        assert edited.status_code == 200, edited.text
        client.portal.call(_settle, runtime)
        before = _rows(client)
        shadows = client.portal.call(_receipts, runtime)
        assert len(shadows) == 3
        assert all(event["payload"]["admission_active"] is False for event in shadows)
        assert all(row["reconciliation_active"] == 0 for row in before.values())
        assert before[dismissed_id]["state"] == "dismissed"
        assert (
            before[edited_id]["content_revision_event_id"] == edited.json()["event_id"]
        )

    monkeypatch.setattr(
        ReconciliationAdmissionEvaluator, "CONSUMER_VERSION", current_version
    )
    monkeypatch.delenv(ADMISSION_ENV)
    monkeypatch.delenv(HUMAN_ENV)
    with TestClient(_launch_app(tmp_path, monkeypatch)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_settle, runtime)
        after = _rows(client)
        receipts = client.portal.call(_receipts, runtime)
        assert len(receipts) == 6
        assert receipts[:3] == shadows
        active = {event["payload"]["proposal_id"]: event for event in receipts[3:]}
        for shadow in shadows:
            payload = shadow["payload"]
            successor = active[payload["proposal_id"]]["payload"]
            assert successor["admission_active"] is True
            assert successor["decision_revision"] == payload["decision_revision"] + 1
            assert successor["supersedes_reconciliation_event_id"] == shadow["event_id"]
        assert after[open_id]["reconciliation_active"] == 1
        assert after[open_id]["admission_decision"] == "admit"
        assert after[open_id]["queue_lane"] == "member_priority"
        assert after[edited_id]["queue_lane"] == "member_priority"
        assert after[dismissed_id]["state"] == "dismissed"
        assert (
            after[dismissed_id]["dismissal_event_id"]
            == before[dismissed_id]["dismissal_event_id"]
        )
        assert after[dismissed_id]["resolved_by"] == MEMBER
        assert after[edited_id]["title"] == before[edited_id]["title"]
        assert (
            after[edited_id]["content_revision_event_id"]
            == before[edited_id]["content_revision_event_id"]
        )
        assert _triage(client)["human_triage_management"]["active"] is True
        assert _triage(client, other=True)["items"] == []
        client.portal.call(runtime.projections.rebuild, "triage")
        client.portal.call(runtime.projections.rebuild, "context_fabric")
        client.portal.call(_settle, runtime)
        assert _rows(client) == after
        assert client.portal.call(_receipts, runtime) == receipts

    # A same-version service restart reuses the active revisions and does not
    # append another decision or lose the member's content revision.
    with TestClient(_launch_app(tmp_path, monkeypatch)) as client:
        runtime = client.app.state.runtime
        client.portal.call(_settle, runtime)
        assert _rows(client) == after
        assert client.portal.call(_receipts, runtime) == receipts
        assert client.portal.call(_external_writes, runtime) == 0
