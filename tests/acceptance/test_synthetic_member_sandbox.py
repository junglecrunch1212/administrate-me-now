"""Process-level acceptance for the isolated production-composed member."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.runtime.application import RuntimeSettings, create_application
from adminme.synthetic_member import (
    build_synthetic_member_settings,
    prepare_synthetic_instance,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


async def _events(runtime: Any, *types: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    async for event in runtime.event_log.read_since(types=list(types) or None):
        rows.append(event)
    return rows


def test_synthetic_member_runs_real_triage_learning_and_openclaw_composition(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "synthetic-instance"
    settings = build_synthetic_member_settings(instance_dir, REPOSITORY_ROOT)
    app = create_application(settings)

    with TestClient(app) as client:
        bootstrap = client.get("/api/member/v1/bootstrap")
        assert bootstrap.status_code == 200, bootstrap.text
        assert bootstrap.json()["synthetic"] == {
            "active": True,
            "scenario_id": "member-life-v1",
            "scenario_version": 1,
            "data_policy": "fictional_behavioral_fixture",
            "provider_effects": "synthetic_only",
            "model_transport": "deterministic_openclaw_fixture",
        }
        assert bootstrap.json()["member"]["authentication_method"] == (
            "Synthetic loopback"
        )
        assert client.get(
            "/api/member/v1/bootstrap",
            headers={"Tailscale-User-Login": "unknown.local.invalid"},
        ).status_code == 401

        settings_surface = client.get("/api/member/v1/surfaces/settings").json()
        connections = settings_surface["control"]["connections"]
        assert len(connections) == 9
        assert {row["product"] for row in connections} == {
            "Calendar",
            "Communications",
            "Finance",
            "Notes",
            "People",
            "Reminders",
        }
        assert all(row["setup"] == "connected" for row in connections)
        assert all(row["runtime"] == "running" for row in connections)
        assert all(row["binding"]["state"] == "synthetic" for row in connections)

        triage = client.get("/api/member/v1/surfaces/triage").json()
        assert len(triage["items"]) == 6
        assert len(triage["triage"]["matters"]) == 5
        assert len(
            [
                item
                for item in triage["items"]
                if item["title"].startswith("Review the new sign-in detected")
            ]
        ) == 1
        recurrence = next(
            item for item in triage["items"] if item["target_kind"] == "recurrence"
        )
        assert recurrence["policy_disposition"] == "route_to_low_signal"
        assert recurrence["policy_shadow_only"] is True
        assert recurrence["source_context"]["provider_category"]["id"] == "updates"
        assert len(triage["triage"]["policy_rules"]) == 1

        source_request = next(
            item
            for item in triage["items"]
            if item["title"].startswith("Please send the signed household form")
        )
        approval = client.post(
            f"/api/member/v1/triage/{source_request['proposal_id']}/approve",
            json={
                "request_id": "synthetic-approve-form-0001",
                "expected_last_event_id": source_request["last_event_id"],
                "reason": None,
                "feedback_reason": None,
                "context_correction": None,
            },
        )
        assert approval.status_code == 200, approval.text
        assert approval.json()["state"] == "confirmed"
        assert approval.json()["destination"]["surface"] == "reminders"
        assert len(approval.json()["materialized_event_ids"]) == 1

        sign_in = next(
            item
            for item in client.get("/api/member/v1/surfaces/triage").json()["items"]
            if item["title"].startswith("Review the new sign-in detected")
        )
        dismissal = client.post(
            f"/api/member/v1/triage/{sign_in['proposal_id']}/dismiss",
            json={
                "request_id": "synthetic-dismiss-signin-0001",
                "expected_last_event_id": sign_in["last_event_id"],
                "reason": "Handled in the synthetic provider",
                "feedback_reason": "already_done",
                "context_correction": None,
            },
        )
        assert dismissal.status_code == 200, dismissal.text
        assert dismissal.json()["feedback_event_id"]

        with client.stream(
            "POST",
            "/api/member/v1/chat/stream",
            json={
                "message": "What should I review first?",
                "request_id": "synthetic-chat-request-0001",
            },
        ) as response:
            transcript = "".join(response.iter_text())
        assert response.status_code == 200
        assert '"phase":"starting_model"' in transcript
        assert '"kind":"final"' in transcript

        runtime = app.state.runtime
        model_calls = client.portal.call(_events, runtime, "skill.call.recorded")
        assert model_calls
        assert any(
            event["payload"]["skill_name"] == "skill:classify_message_nature"
            and event["payload"]["provider"] == "fixture/deterministic-v1"
            for event in model_calls
        )
        assert client.portal.call(
            _events, runtime, "external_sync.write_requested"
        ) == []

    restarted = create_application(
        build_synthetic_member_settings(instance_dir, REPOSITORY_ROOT)
    )
    with TestClient(restarted) as client:
        runtime = restarted.state.runtime
        sources = client.portal.call(_events, runtime, "messaging.received")
        assert len(
            [
                event
                for event in sources
                if str(event.get("idempotency_key") or "").startswith(
                    "synthetic-member:v1:message:"
                )
            ]
        ) == 9
        triage = client.get("/api/member/v1/surfaces/triage").json()
        assert len(triage["items"]) == 4
        recent = client.get(
            "/api/member/v1/surfaces/triage",
            params={"triage_lens": "all_candidates"},
        ).json()["recent_items"]
        assert {item["state"] for item in recent} >= {"confirmed", "dismissed"}
        feedback = client.portal.call(_events, runtime, "triage.feedback.recorded")
        assert len(feedback) == 3


def test_synthetic_instance_and_identity_fail_closed(tmp_path: Path) -> None:
    ordinary_instance = tmp_path / "ordinary"
    config = ordinary_instance / "config" / "instance.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "tenant_id: tenant-ordinary\ntimezone: Etc/UTC\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="scenario marker"):
        prepare_synthetic_instance(ordinary_instance)

    version_mismatch = tmp_path / "synthetic-version-mismatch"
    mismatch_config = version_mismatch / "config" / "instance.yaml"
    mismatch_config.parent.mkdir(parents=True)
    mismatch_config.write_text(
        "tenant_id: tenant-synthetic-member-life-v1\n"
        "timezone: America/New_York\n"
        "synthetic_scenario_id: member-life-v1\n"
        "synthetic_scenario_version: 999\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="incompatible synthetic scenario version"):
        prepare_synthetic_instance(version_mismatch)

    settings = RuntimeSettings.credential_free(
        tmp_path / "no-synthetic-fallback",
        encryption_key=b"s" * 32,
        repository_root=REPOSITORY_ROOT,
        member_identities={"ordinary-member.ts.net": "ordinary-member"},
        member_roles={"ordinary-member": "principal"},
    )
    with TestClient(create_application(settings)) as client:
        response = client.get("/api/member/v1/bootstrap")
        assert response.status_code == 401
        assert response.json()["detail"] == "missing or unknown Tailscale identity"

    synthetic = build_synthetic_member_settings(
        tmp_path / "synthetic-validation", REPOSITORY_ROOT
    )
    with pytest.raises(ValueError, match="metadata requires"):
        replace(synthetic, synthetic_member_mode=False)
