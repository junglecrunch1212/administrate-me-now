"""Process-level contract tests for the AdministrateMe composition root."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.daemons.adapter_supervisor import AdapterSupervisor
from adminme.events.envelope import EventEnvelope
from adminme.events.bus import EventBus
from adminme.events.log import AppendValidationError, EventLog
from adminme.lib.session import Session
from adminme.lib.skill_runner import wrapper as skill_wrapper
from adminme.lib.skill_runner.wrapper import SkillContext
from adminme.openclaw_topology import binding_fingerprints, member_agent_id
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.runtime.application import (
    AdministrateMeRuntime,
    RuntimeSettings,
    create_application,
)
from adminme.source_assessment_recovery import latest_source_assessment
from adminme.source_assessment_recovery import (
    SourceAssessmentRecoveryBatch,
    SourceAssessmentRecoveryTarget,
    build_recovery_request_envelopes,
)
from bootstrap.contracts import PROJECTION_NAMES

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_KEY = b"r" * 32

PROJECTIONS = set(PROJECTION_NAMES)

REACTIVE_PIPELINES = {
    "pipeline:commitment_extraction",
    "pipeline:identity_resolution",
    "pipeline:member_knowledge_indexing",
    "pipeline:noise_filtering",
    "pipeline:reward_dispatch",
    "pipeline:thank_you_detection",
    "pipeline:deterministic_source_candidates",
    "pipeline:lifecycle_maintenance",
}


def _settings(tmp_path: Path) -> RuntimeSettings:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"member-a.ts.net": "member-a"},
    )


def _bridge_envelope() -> dict[str, object]:
    return EventEnvelope(
        event_at_ms=1_780_000_000_000,
        tenant_id="tenant-instance",
        type="bridge.enrolled",
        schema_version=1,
        occurred_at="2026-05-28T12:00:00Z",
        source_adapter="bridge-test",
        source_account_id="bridge-node-a",
        owner_scope="private:member-a",
        visibility_scope="private:member-a",
        sensitivity="normal",
        payload={
            "member_id": "member-a",
            "bridge_node_id": "bridge-node-a",
            "tailscale_identity": "member-a.ts.net",
            "adapters_active": ["apple-notes"],
            "enrolled_at": "2026-05-28T12:00:00Z",
        },
    ).model_dump(mode="json")


def _strict_settings(
    tmp_path: Path,
    *,
    backup_ready: bool = True,
    adapter_config: dict[str, Any] | None = None,
    bridge_enrollments: dict[str, dict[str, Any]] | None = None,
) -> RuntimeSettings:
    instance_dir = tmp_path / "strict-instance"
    instance_dir.mkdir()
    now = datetime.now(timezone.utc).isoformat()
    openclaw_config_path = instance_dir / "openclaw" / "runtime" / "openclaw.json"
    openclaw_config_path.parent.mkdir(parents=True)
    openclaw_config_path.write_text(
        json.dumps(
            {
                "agents": {"list": [{"id": member_agent_id("member-a")}]},
                "bindings": [],
                "session": {"dmScope": "per-account-channel-peer"},
                "plugins": {
                    "entries": {
                        "administrateme": {
                            "config": {
                                "memberAgentIds": [member_agent_id("member-a")],
                                "bindingFingerprints": list(
                                    binding_fingerprints({}, frozenset({"member-a"}))
                                ),
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    deployment_binding = {
        "release_commit": "a" * 40,
        "instance_fingerprint": "b" * 64,
        "host_fingerprint": "c" * 64,
        "configuration_fingerprint": "d" * 64,
    }
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"member-a.ts.net": "member-a"},
        member_roles={"member-a": "principal"},
        adapter_config=adapter_config or {"adapter_supervisor_port": 0, "adapters": []},
        bridge_enrollments=bridge_enrollments or {},
        openclaw_enabled=True,
        openclaw_shared_secret="strict-test-secret-at-least-24-characters",
        member_chat_enabled_members=frozenset({"member-a"}),
        openclaw_runtime_config_path=openclaw_config_path,
        host_topology={
            "tailscale_node_online": True,
            "central_host_tag": "tag:administrateme-central",
            "central_identity_kind": "infrastructure_tag",
            "central_identity_collides_with_member": False,
            "serve_is_sole_edge": True,
            "python_runtime_count": 1,
            "openclaw_gateway_count": 1,
            "service_account_standard": True,
            "deployment_binding": deployment_binding,
        },
        deployment_evidence={
            "contract": "deployment-readiness-v1",
            "deployment_binding": deployment_binding,
            "identity_acceptance": {
                "member_ids": ["member-a"],
                "distinct_identities": True,
                "tested_at": now,
            },
            "recovery": {
                "separate_admin_available": True,
                "filevault_unlock_tested": True,
                "automatic_login_disabled": True,
                "tested_at": now,
            },
            "backup_rollback": {
                "encrypted_backup_authenticated": backup_ready,
                "restore_rehearsed": True,
                "prior_release_retained": True,
                "automatic_rollback_eligible": True,
                "tested_at": now,
            },
            "provider_effect_window": {"started_at": now},
        },
        deployment_evidence_required=True,
    )


def _strict_apple_adapter(
    kind: str,
    *,
    deployment_target: str,
    service_owned: bool = False,
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "provider_account_id": "provider-account:" + "a" * 32,
        "deployment_target": deployment_target,
        "workspace": "service" if service_owned else "personal",
        "permission_scope": ["eventkit.read", "eventkit.write"],
        "binding_provenance": (
            "oauth-subject" if service_owned else "member-bridge-enrollment"
        ),
        "ownership_attested": True,
    }
    if service_owned:
        config["service_principal_id"] = "service-principal:chief-of-staff"
    else:
        config["member_id"] = "member-a"
    return {"id": f"{kind}-fixture", "kind": kind, "config": config}


def _report_strict_openclaw(
    client: TestClient,
    *,
    members: frozenset[str] = frozenset({"member-a"}),
) -> None:
    response = client.post(
        "/api/openclaw/v1/health/report",
        headers={"Authorization": "Bearer strict-test-secret-at-least-24-characters"},
        json={
            "state": "healthy",
            "reason": "process fixture",
            "openclaw_version": OPENCLAW_VERSION,
            "openclaw_commit": OPENCLAW_COMMIT,
            "dm_scope": "per_account_channel_peer",
            "member_agent_ids": [member_agent_id(member) for member in sorted(members)],
            "binding_fingerprints": list(binding_fingerprints({}, members)),
            "unique_agents": True,
            "unique_accounts": True,
        },
    )
    assert response.status_code == 200, response.text


def test_strict_deployment_readiness_is_shared_by_ready_and_settings(
    tmp_path: Path,
) -> None:
    app = create_application(_strict_settings(tmp_path))
    with TestClient(app) as client:
        _report_strict_openclaw(client)
        ready = client.get("/api/system/ready")
        assert ready.status_code == 200, ready.text
        diagnostics = ready.json()
        assert diagnostics["ready"] is True
        assert diagnostics["activation_eligible"] is True
        assert diagnostics["deployment_readiness"]["ready"] is True
        service_channel = diagnostics["deployment_readiness"]["dimensions"][
            "service_channel_identities"
        ]
        assert service_channel["state"] == "planned"
        assert service_channel["required"] is False
        settings = client.get(
            "/api/member/v1/surfaces/settings",
            headers={"Tailscale-User-Login": "member-a.ts.net"},
        ).json()
        assert settings["control"]["readiness"] == diagnostics["deployment_readiness"]
        assert (
            settings["system"]["runtime"]["deployment_readiness"]
            == diagnostics["deployment_readiness"]
        )
        assert settings["control"]["service_channels"]["state_counts"] == {"planned": 1}
        assert (
            settings["control"]["service_channels"]["channels"][0]["display_name"]
            == "CoS Apple service identity"
        )
        assert app.state.runtime.settings.openclaw_runtime_config_path is not None
        app.state.runtime.settings.openclaw_runtime_config_path.write_text(
            json.dumps(
                {
                    "agents": {"list": []},
                    "bindings": [],
                    "session": {"dmScope": "per-account-channel-peer"},
                    "plugins": {"entries": {}},
                }
            ),
            encoding="utf-8",
        )
        drift = client.get("/api/system/ready")
        assert drift.status_code == 503
        assert "openclaw" in drift.json()["deployment_readiness"]["blocking_dimensions"]


def test_strict_readiness_identifies_stale_source_refresh_as_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_application(_strict_settings(tmp_path))
    with TestClient(app) as client:
        _report_strict_openclaw(client)
        runtime: AdministrateMeRuntime = app.state.runtime
        for _ in range(100):
            baseline = client.get("/api/system/ready").json()["capabilities"][
                "policy:source-assurance"
            ]["details"]["freshness"]
            if (
                baseline["revision_current"] is True
                and baseline["refresh_in_progress"] is False
            ):
                break
            client.portal.call(asyncio.sleep, 0.01)
        assert baseline["revision_current"] is True
        assert baseline["refresh_in_progress"] is False
        runtime._source_assurance_revision = ("ev_outdated", ())
        runtime._source_assurance_refreshed_monotonic = (
            time.monotonic() - runtime.SOURCE_ASSURANCE_MAX_STALE_SECONDS - 1
        )

        async def hung_refresh(*_args: object, **_kwargs: object) -> dict[str, object]:
            await asyncio.sleep(60)
            return {}

        monkeypatch.setattr(
            "adminme.runtime.application.audit_source_assurance", hung_refresh
        )

        response = client.get("/api/system/ready")
        assert response.status_code == 503
        diagnostics = response.json()
        assert diagnostics["ready"] is False
        assert diagnostics["service_ready"] is True
        assert diagnostics["safe_reads"] is True
        assert diagnostics["event_ingest_ready"] is True
        assert diagnostics["deployment_readiness"]["blocking_dimensions"] == [
            "observation_effects"
        ]
        effects = diagnostics["deployment_readiness"]["dimensions"][
            "observation_effects"
        ]
        assert effects["state"] == "blocked"
        assert effects["evidence"]["source_assurance_state"] == "degraded"
        assert effects["evidence"]["source_assurance_freshness"] == "stale"
        assert effects["evidence"]["source_assurance_revision_current"] is False
        assert effects["evidence"]["source_assurance_refresh_in_progress"] is True
        assert effects["evidence"]["source_assurance_refresh_error"] is None


@pytest.mark.parametrize(
    "kind",
    [
        "calendaring-apple-calendar",
        "contacts-apple-contacts",
        "reminders-apple-eventkit",
    ],
)
def test_strict_runtime_rejects_current_apple_central_ownership(
    tmp_path: Path,
    kind: str,
) -> None:
    with pytest.raises(ValueError, match="bridge-only"):
        _strict_settings(
            tmp_path,
            adapter_config={
                "adapter_supervisor_port": 0,
                "adapters": [_strict_apple_adapter(kind, deployment_target="central")],
            },
        )


def test_planned_cos_apple_identity_authorizes_no_provider_row(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="service-owned provider authority"):
        _strict_settings(
            tmp_path,
            adapter_config={
                "adapter_supervisor_port": 0,
                "adapters": [
                    {
                        "id": "planned-cos-fixture",
                        "kind": "messaging-fixture-provider",
                        "config": {
                            "service_principal_id": "service-principal:chief-of-staff",
                            "provider_account_id": "provider-account:" + "c" * 32,
                            "workspace": "service",
                            "permission_scope": ["fixture.read"],
                            "binding_provenance": "oauth-subject",
                            "ownership_attested": True,
                        },
                    }
                ],
            },
        )


def test_active_bridge_metadata_never_constructs_central_apple_adapter(
    tmp_path: Path,
) -> None:
    bridge_records = {
        "member-a-bridge.ts.net": {
            "member_id": "member-a",
            "enrollment_id": "bridge-enrollment-fixture",
            "status": "active",
            "allowed_packs": ["calendaring-apple-calendar-bridge"],
            "configured_packs": ["calendaring-apple-calendar-bridge"],
            "enabled_packs": [],
            "authorized_packs": [],
            "adapters_active": [],
            "provider_capability_state": "not_attested",
            "pack_capability_states": {
                "calendaring-apple-calendar-bridge": "not_attested"
            },
            "capability_attestations": {},
            "pack_config": {
                "calendaring-apple-calendar-bridge": {
                    "member_id": "member-a",
                    "provider_account_id": "provider-account:" + "a" * 32,
                    "deployment_target": "bridge",
                    "workspace": "personal",
                    "permission_scope": [
                        "eventkit.calendar.read",
                        "eventkit.calendar.write",
                    ],
                    "binding_provenance": "member-bridge-enrollment",
                    "ownership_attested": True,
                    "calendar_identifier": "calendar-personal",
                    "allowed_container_ids": ["calendar-personal"],
                    "shared_container_ids": [],
                }
            },
        }
    }
    app = create_application(
        _strict_settings(tmp_path, bridge_enrollments=bridge_records)
    )
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        assert runtime.adapters is not None
        assert runtime.adapters._managed == {}  # noqa: SLF001
        _report_strict_openclaw(client)
        readiness = client.get("/api/system/ready")
        assert readiness.status_code == 503
        bridge_dimension = readiness.json()["deployment_readiness"]["dimensions"][
            "bridges"
        ]
        assert bridge_dimension["state"] == "blocked"
        assert bridge_dimension["evidence"]["active_count"] == 1
        assert bridge_dimension["evidence"]["provider_blocked_count"] == 1
        settings = client.get(
            "/api/member/v1/surfaces/settings",
            headers={"Tailscale-User-Login": "member-a.ts.net"},
        ).json()
        assert settings["control"]["bridges"][0]["provider_capability_state"] == (
            "not_attested"
        )


def test_attested_bridge_provider_is_ready_but_never_centrally_constructed(
    tmp_path: Path,
) -> None:
    pack = "calendaring-apple-calendar-bridge"
    record = {
        "member_id": "member-a",
        "enrollment_id": "bridge-enrollment-fixture",
        "status": "active",
        "allowed_packs": [pack],
        "configured_packs": [pack],
        "enabled_packs": [pack],
        "authorized_packs": [pack],
        "adapters_active": [pack],
        "provider_capability_state": "attested",
        "pack_capability_states": {pack: "attested"},
        "capability_attestations": {pack: {"event_id": "event-auth-calendar"}},
        "pack_config": {
            pack: {
                "member_id": "member-a",
                "provider_account_id": "provider-account:" + "a" * 32,
                "deployment_target": "bridge",
                "workspace": "personal",
                "permission_scope": [
                    "eventkit.calendar.read",
                    "eventkit.calendar.write",
                ],
                "binding_provenance": "member-bridge-enrollment",
                "ownership_attested": True,
                "calendar_identifier": "calendar-personal",
                "allowed_container_ids": ["calendar-personal"],
                "shared_container_ids": [],
            }
        },
    }
    app = create_application(
        _strict_settings(
            tmp_path, bridge_enrollments={"member-a-bridge.ts.net": record}
        )
    )
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        assert runtime.adapters is not None
        assert runtime.adapters._managed == {}  # noqa: SLF001
        _report_strict_openclaw(client)
        readiness = client.get("/api/system/ready")
        assert readiness.status_code == 200, readiness.text
        bridge_dimension = readiness.json()["deployment_readiness"]["dimensions"][
            "bridges"
        ]
        assert bridge_dimension["state"] == "healthy"
        assert bridge_dimension["evidence"]["provider_blocked_count"] == 0


def test_concurrent_http_projection_reads_serialize_on_native_owner_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_application(
        replace(_settings(tmp_path), member_roles={"member-a": "principal"})
    )
    native_threads: set[int] = set()
    callback_started = threading.Event()
    release_callback = threading.Event()
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        assert runtime.projections is not None
        assert runtime.events is not None
        projection = runtime.projections._projections["tasks"]  # noqa: SLF001
        original_apply = projection.apply
        original_execute = runtime.projections._execute_statement  # noqa: SLF001

        def _held_apply(envelope: dict[str, Any], connection: Any) -> None:
            native_threads.add(threading.get_ident())
            if envelope["type"] == "task.created":
                callback_started.set()
                if not release_callback.wait(timeout=5):
                    raise RuntimeError("test did not release projection callback")
            original_apply(envelope, connection)

        def _tracked_execute(*args: Any, **kwargs: Any) -> Any:
            native_threads.add(threading.get_ident())
            return original_execute(*args, **kwargs)

        monkeypatch.setattr(projection, "apply", _held_apply)
        monkeypatch.setattr(runtime.projections, "_execute_statement", _tracked_execute)
        client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=1_780_000_000_000,
                tenant_id=runtime.settings.instance.tenant_id,
                type="task.created",
                schema_version=1,
                occurred_at="2026-05-28T12:00:00Z",
                source_adapter="concurrent-http-read-test",
                source_account_id="fixture",
                owner_scope="private:member-a",
                visibility_scope="private:member-a",
                sensitivity="normal",
                payload={
                    "task_id": "task-concurrent-http",
                    "title": "Concurrent HTTP projection proof",
                    "owner_member_id": "member-a",
                },
            ),
        )
        assert callback_started.wait(timeout=2)

        def _read_reminders() -> Any:
            return client.get(
                "/api/member/v1/surfaces/reminders",
                headers={"Tailscale-User-Login": "member-a.ts.net"},
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            pending = [executor.submit(_read_reminders) for _ in range(8)]
            time.sleep(0.05)
            release_callback.set()
            responses = [future.result(timeout=10) for future in pending]

        assert all(response.status_code == 200 for response in responses), [
            (response.status_code, response.text) for response in responses
        ]
        assert all(
            "task-concurrent-http" in json.dumps(response.json(), sort_keys=True)
            for response in responses
        )
        assert len(native_threads) == 1
        assert threading.get_ident() not in native_threads

    assert not any(
        thread.name.startswith(("adminme-projections", "adminme-projection-snapshot"))
        for thread in threading.enumerate()
    )


def test_missing_backup_blocks_activation_but_preserves_safe_reads(
    tmp_path: Path,
) -> None:
    app = create_application(_strict_settings(tmp_path, backup_ready=False))
    with TestClient(app) as client:
        _report_strict_openclaw(client)
        response = client.get("/api/system/ready")
        assert response.status_code == 503
        diagnostics = response.json()
        assert diagnostics["ready"] is False
        assert diagnostics["service_ready"] is True
        assert diagnostics["safe_reads"] is True
        assert diagnostics["write_admission"] == "blocked"
        assert (
            "backup_rollback"
            in diagnostics["deployment_readiness"]["blocking_dimensions"]
        )


def test_two_principals_have_distinct_ingress_agents_and_sessions(
    tmp_path: Path,
) -> None:
    base = _strict_settings(tmp_path)
    members = frozenset({"member-a", "member-b"})
    assert base.openclaw_runtime_config_path is not None
    base.openclaw_runtime_config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "list": [
                        {"id": member_agent_id(member)} for member in sorted(members)
                    ]
                },
                "bindings": [],
                "session": {"dmScope": "per-account-channel-peer"},
                "plugins": {
                    "entries": {
                        "administrateme": {
                            "config": {
                                "memberAgentIds": [
                                    member_agent_id(member)
                                    for member in sorted(members)
                                ],
                                "bindingFingerprints": list(
                                    binding_fingerprints({}, members)
                                ),
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    settings = replace(
        base,
        member_identities={
            "member-a.ts.net": "member-a",
            "member-b.ts.net": "member-b",
        },
        member_roles={"member-a": "principal", "member-b": "principal"},
        member_chat_enabled_members=members,
        deployment_evidence={
            **base.deployment_evidence,
            "identity_acceptance": {
                **base.deployment_evidence["identity_acceptance"],
                "member_ids": ["member-a", "member-b"],
            },
        },
    )
    app = create_application(settings)
    with TestClient(app) as client:
        _report_strict_openclaw(client, members=members)
        ready = client.get("/api/system/ready")
        assert ready.status_code == 200, ready.text
        for identity in ("member-a.ts.net", "member-b.ts.net"):
            response = client.get(
                "/api/member/v1/surfaces/settings",
                headers={"Tailscale-User-Login": identity},
            )
            assert response.status_code == 200, response.text
        unknown = client.get(
            "/api/member/v1/surfaces/settings",
            headers={"Tailscale-User-Login": "unknown.ts.net"},
        )
        assert unknown.status_code == 401
        runtime: AdministrateMeRuntime = app.state.runtime
        agent_a, session_a = runtime.member_chat_binding("member-a")
        agent_b, session_b = runtime.member_chat_binding("member-b")
        assert agent_a != agent_b
        assert session_a != session_b
        assert member_agent_id("member-a") in session_a
        assert member_agent_id("member-b") in session_b


def test_blocked_readiness_terminalizes_command_before_provider_dispatch(
    tmp_path: Path,
) -> None:
    app = create_application(_strict_settings(tmp_path, backup_ready=False))
    with TestClient(app) as client:
        _report_strict_openclaw(client)
        runtime: AdministrateMeRuntime = app.state.runtime
        provider_calls = 0

        async def forbidden_dispatch(**_kwargs: object) -> object:
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("provider dispatch must remain unreachable")

        assert runtime.adapters is not None
        runtime.adapters.dispatch_write = forbidden_dispatch  # type: ignore[method-assign,assignment]

        async def append_and_wait() -> dict[str, object]:
            assert runtime.events is not None
            command_id = "strict-readiness-command"
            request_id = await runtime.events.write(
                EventEnvelope(
                    event_at_ms=int(time.time() * 1000),
                    tenant_id=runtime.settings.instance.tenant_id,
                    type="external_sync.write_requested",
                    schema_version=2,
                    occurred_at=EventEnvelope.now_utc_iso(),
                    source_adapter="strict-process-fixture",
                    source_account_id="provider-account-fixture",
                    owner_scope="private:member-a",
                    visibility_scope="private:member-a",
                    sensitivity="normal",
                    payload={
                        "command_id": command_id,
                        "member_id": "member-a",
                        "provider": "google_tasks",
                        "provider_account_id": "provider-account-fixture",
                        "operation": "task.create",
                        "target_external_id": None,
                        "expected_provider_revision": None,
                        "payload": {"fixture": True},
                        "requested_at": EventEnvelope.now_utc_iso(),
                        "domain_approval_event_id": "domain-approval-fixture",
                        "openclaw_approval_event_id": "openclaw-approval-fixture",
                        "request_fingerprint": "a" * 64,
                        "privacy_guard_version": "fixture-v1",
                        "origin_proposal_id": None,
                    },
                )
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                assert runtime.event_log is not None
                async for event in runtime.event_log.read_since(
                    types=["external_sync.write_failed"]
                ):
                    if (event.get("payload") or {}).get("command_id") == command_id:
                        assert event.get("causation_id") == request_id
                        return event
                await asyncio.sleep(0.01)
            raise AssertionError("blocked command did not receive a terminal result")

        terminal = client.portal.call(append_and_wait)
        assert provider_calls == 0
        assert terminal["payload"]["error_kind"] == "permission"  # type: ignore[index]
        assert terminal["payload"]["provider_called"] is False  # type: ignore[index]
        assert (
            terminal["payload"]["domain_approval_event_id"] == "domain-approval-fixture"
        )  # type: ignore[index]
        assert "backup_rollback" in terminal["payload"]["message"]  # type: ignore[index]


def test_strict_identity_configuration_fails_closed_before_startup(
    tmp_path: Path,
) -> None:
    base = _strict_settings(tmp_path)
    with pytest.raises(ValueError, match="central infrastructure identity"):
        replace(
            base,
            host_topology={
                **base.host_topology,
                "central_identity_collides_with_member": True,
            },
        )
    with pytest.raises(ValueError, match="explicit member role"):
        replace(base, member_roles={})
    with pytest.raises(ValueError, match="ambiguous after normalization"):
        replace(
            base,
            member_identities={
                "member-a.ts.net": "member-a",
                "MEMBER-A.TS.NET": "member-b",
            },
            member_roles={"member-a": "principal", "member-b": "principal"},
        )
    with pytest.raises(ValueError, match="exactly one trusted ingress identity"):
        replace(
            base,
            member_identities={
                "member-a.ts.net": "member-a",
                "member-a-alias.ts.net": "member-a",
            },
        )


def test_unknown_view_target_and_missing_role_never_default_to_principal(
    tmp_path: Path,
) -> None:
    app = create_application(_strict_settings(tmp_path))
    with TestClient(app) as client:
        unknown = client.get(
            "/api/member/v1/surfaces/today",
            headers={
                "Tailscale-User-Login": "member-a.ts.net",
                "X-AdminMe-View-Member": "unknown-member",
            },
        )
        assert unknown.status_code == 403
        assert unknown.json()["detail"] == (
            "view target is not an interactive household member"
        )


def test_service_channel_messages_require_explicit_scope_before_member_visibility(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "service-channel-instance"
    instance_dir.mkdir()
    settings = RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={
            "member-a.ts.net": "member-a",
            "member-b.ts.net": "member-b",
        },
        member_roles={"member-a": "principal", "member-b": "principal"},
    )
    app = create_application(settings)
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime

        async def append(scope: str, routing_scope: str, target: str | None) -> str:
            assert runtime.events is not None
            return await runtime.events.write(
                EventEnvelope(
                    event_at_ms=1_788_000_000_000,
                    tenant_id=settings.instance.tenant_id,
                    type="messaging.received",
                    schema_version=5,
                    occurred_at="2026-08-26T12:00:00Z",
                    source_adapter="service-channel-fixture",
                    source_account_id="cos-apple-account:" + "3" * 32,
                    owner_scope=scope,
                    visibility_scope=scope,
                    sensitivity="normal",
                    payload={
                        "source_channel": "service_channel",
                        "from_identifier": "provider-account:" + "1" * 32,
                        "to_identifier": "cos-channel-account:" + "2" * 32,
                        "thread_id": "thread-fixture",
                        "subject": None,
                        "body_text": None,
                        "body_html": None,
                        "received_at": "2026-08-26T12:00:00Z",
                        "attachments": [],
                        "native_message_id": f"message-{routing_scope}",
                        "member_id": None,
                        "service_principal_id": "service-principal:chief-of-staff",
                        "provider": "bluebubbles",
                        "provider_account_id": "cos-apple-account:" + "3" * 32,
                        "workspace": "service",
                        "routing_scope": routing_scope,
                        "routing_target_id": target,
                    },
                )
            )

        unresolved_id = client.portal.call(
            append, "unresolved:service-channel", "unresolved", None
        )
        private_id = client.portal.call(
            append, "private:member-a", "member_private", "member-a"
        )
        with pytest.raises(
            AppendValidationError,
            match="messaging v5 envelope contradicts payload route: owner_scope, visibility_scope",
        ):
            client.portal.call(append, "shared:household", "member_private", "member-a")
        activity_a = client.get(
            "/api/core/v1/activity",
            headers={"Tailscale-User-Login": "member-a.ts.net"},
        ).json()
        activity_b = client.get(
            "/api/core/v1/activity",
            headers={"Tailscale-User-Login": "member-b.ts.net"},
        ).json()
        ids_a = {item["event_id"] for item in activity_a["items"]}
        ids_b = {item["event_id"] for item in activity_b["items"]}
        assert private_id in ids_a
        assert private_id not in ids_b
        assert unresolved_id not in ids_a | ids_b


def test_application_boots_with_complete_truthful_inventory_and_routes(
    tmp_path: Path,
) -> None:
    app = create_application(_settings(tmp_path))
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        assert set(runtime.projections.projection_names()) == PROJECTIONS
        assert set(runtime.pipelines.registered_pack_ids()) == REACTIVE_PIPELINES

        diagnostics = client.get("/api/system/ready")
        assert diagnostics.status_code == 503
        body = diagnostics.json()
        assert body["status"] == "blocked"
        assert body["ready"] is False
        assert body["observation_active"] is True
        assert {
            key.removeprefix("projection:")
            for key in body["capabilities"]
            if key.startswith("projection:")
        } == PROJECTIONS

        assert client.get("/api/system/live").json() == {
            "status": "alive",
            "runtime_started": True,
        }
        for product in ("core", "comms", "capture", "automation"):
            response = client.get(f"/api/{product}/status")
            assert response.status_code == 200
            assert response.json()["name"] == product
        assert client.get("/api/bridge/health").status_code == 200


def test_optional_unconfigured_chat_does_not_block_runtime_readiness(
    tmp_path: Path,
) -> None:
    (tmp_path / "instance").mkdir()
    settings = RuntimeSettings.credential_free(
        tmp_path / "instance",
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"member-a.ts.net": "member-a"},
        member_chat_enabled_members={"member-a"},
    )
    settings = replace(
        settings,
        openclaw_enabled=True,
        openclaw_shared_secret="fixture-secret-at-least-24-characters",
        member_roles={"member-a": "principal"},
    )
    app = create_application(settings)
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        runtime.openclaw_state = "unavailable"
        response = client.get("/api/system/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["ready"] is True
        assert body["service_ready"] is True
        assert body["release_ready"] is False
        assert body["activation_eligible"] is False
        assert body["safe_reads"] is True
        assert body["write_admission"] == "observation_suppressed"
        assert body["status"] == "degraded"
        assert body["capabilities"]["openclaw:member-chat"]["state"] == "blocked"

        ingest = client.post(
            "/api/bridge/ingest",
            json=_bridge_envelope(),
            headers={"Tailscale-User-Login": "member-a.ts.net"},
        )
        assert ingest.status_code == 403
        assert ingest.json()["detail"] == "unknown_tailscale_identity"
        health = client.get("/api/bridge/health").json()
        assert health["endpoint_available"] is True
        assert health["enrollment_state"] == "none"
        assert health["enrollment_count"] == 0


def test_source_assurance_refresh_never_blocks_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_application(_settings(tmp_path))
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        assert runtime._source_assurance_cache is not None
        cached = dict(runtime._source_assurance_cache)
        runtime._source_assurance_revision = ("ev_outdated", ())
        runtime._source_assurance_refreshed_monotonic = (
            time.monotonic() - runtime.SOURCE_ASSURANCE_REFRESH_SECONDS - 1
        )
        refresh_calls = 0

        async def slow_refresh(*_args: object, **_kwargs: object) -> dict[str, object]:
            nonlocal refresh_calls
            refresh_calls += 1
            await asyncio.sleep(0.5)
            return cached

        monkeypatch.setattr(
            "adminme.runtime.application.audit_source_assurance", slow_refresh
        )

        started = time.perf_counter()
        first = client.get("/api/system/ready")
        elapsed = time.perf_counter() - started
        second = client.get("/api/system/ready")

        assert elapsed < 0.2
        assert (
            first.json()["capabilities"]["policy:source-assurance"]["details"][
                "freshness"
            ]["state"]
            == "fresh"
        )
        assert (
            first.json()["capabilities"]["policy:source-assurance"]["details"][
                "freshness"
            ]["refresh_in_progress"]
            is True
        )
        assert (
            first.json()["capabilities"]["policy:source-assurance"]["details"][
                "freshness"
            ]["revision_current"]
            is False
        )
        assert second.status_code == first.status_code
        assert refresh_calls == 1

        client.portal.call(asyncio.sleep, 0.55)
        refreshed = client.get("/api/system/ready").json()
        assert (
            refreshed["capabilities"]["policy:source-assurance"]["details"][
                "freshness"
            ]["state"]
            == "fresh"
        )
        assert (
            refreshed["capabilities"]["policy:source-assurance"]["details"][
                "freshness"
            ]["refresh_in_progress"]
            is False
        )
        assert (
            refreshed["capabilities"]["policy:source-assurance"]["details"][
                "freshness"
            ]["revision_current"]
            is True
        )


def test_settled_current_source_assurance_proof_does_not_rescan_on_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_application(_settings(tmp_path))
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        for _ in range(100):
            freshness = client.get("/api/system/ready").json()["capabilities"][
                "policy:source-assurance"
            ]["details"]["freshness"]
            if (
                freshness["revision_current"] is True
                and freshness["refresh_in_progress"] is False
            ):
                break
            client.portal.call(asyncio.sleep, 0.01)
        assert freshness["revision_current"] is True
        runtime._source_assurance_refreshed_monotonic = (
            time.monotonic() - runtime.SOURCE_ASSURANCE_MAX_STALE_SECONDS - 1
        )
        refresh_calls = 0

        async def unexpected_refresh(
            *_args: object, **_kwargs: object
        ) -> dict[str, object]:
            nonlocal refresh_calls
            refresh_calls += 1
            return {}

        monkeypatch.setattr(
            "adminme.runtime.application.audit_source_assurance", unexpected_refresh
        )

        details = client.get("/api/system/ready").json()["capabilities"][
            "policy:source-assurance"
        ]["details"]
        assert refresh_calls == 0
        assert details["freshness"]["state"] == "fresh"
        assert details["freshness"]["settled_revision"] is True
        assert details["freshness"]["refresh_in_progress"] is False


def test_pending_source_assurance_proof_rechecks_on_grace_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_application(_settings(tmp_path))
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        for _ in range(100):
            freshness = client.get("/api/system/ready").json()["capabilities"][
                "policy:source-assurance"
            ]["details"]["freshness"]
            if (
                freshness["revision_current"] is True
                and freshness["refresh_in_progress"] is False
            ):
                break
            client.portal.call(asyncio.sleep, 0.01)
        assert freshness["revision_current"] is True
        assert runtime._source_assurance_cache is not None
        cached = dict(runtime._source_assurance_cache)
        counts = dict(cached["counts"])
        counts["pending"] = 1
        cached["counts"] = counts
        runtime._source_assurance_cache = cached
        runtime._source_assurance_refreshed_monotonic = (
            time.monotonic() - runtime.SOURCE_ASSURANCE_REFRESH_SECONDS - 1
        )
        refresh_calls = 0

        async def slow_refresh(*_args: object, **_kwargs: object) -> dict[str, object]:
            nonlocal refresh_calls
            refresh_calls += 1
            await asyncio.sleep(0.5)
            return cached

        monkeypatch.setattr(
            "adminme.runtime.application.audit_source_assurance", slow_refresh
        )

        details = client.get("/api/system/ready").json()["capabilities"][
            "policy:source-assurance"
        ]["details"]
        assert refresh_calls == 1
        assert details["freshness"]["pending_recheck_required"] is True
        assert details["freshness"]["refresh_in_progress"] is True
        assert details["freshness"]["settled_revision"] is False


def test_over_age_source_assurance_proof_degrades_during_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_application(_settings(tmp_path))
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        runtime._source_assurance_revision = ("ev_outdated", ())
        runtime._source_assurance_refreshed_monotonic = (
            time.monotonic() - runtime.SOURCE_ASSURANCE_MAX_STALE_SECONDS - 1
        )

        async def hung_refresh(*_args: object, **_kwargs: object) -> dict[str, object]:
            await asyncio.sleep(60)
            return {}

        monkeypatch.setattr(
            "adminme.runtime.application.audit_source_assurance", hung_refresh
        )

        response = client.get("/api/system/ready").json()
        capability = response["capabilities"]["policy:source-assurance"]
        assert capability["state"] == "degraded"
        assert capability["details"]["freshness"]["state"] == "stale"
        assert capability["details"]["freshness"]["refresh_in_progress"] is True


def test_failed_source_assurance_refresh_degrades_without_retry_storm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_application(_settings(tmp_path))
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        runtime._source_assurance_revision = ("ev_outdated", ())
        runtime._source_assurance_refreshed_monotonic = (
            time.monotonic() - runtime.SOURCE_ASSURANCE_REFRESH_SECONDS - 1
        )
        refresh_calls = 0

        async def failed_refresh(
            *_args: object, **_kwargs: object
        ) -> dict[str, object]:
            nonlocal refresh_calls
            refresh_calls += 1
            raise RuntimeError("content-free fixture failure")

        monkeypatch.setattr(
            "adminme.runtime.application.audit_source_assurance", failed_refresh
        )

        client.get("/api/system/ready")
        client.portal.call(asyncio.sleep, 0.01)
        first_failed = client.get("/api/system/ready").json()
        second_failed = client.get("/api/system/ready").json()

        assert refresh_calls == 1
        for diagnostics in (first_failed, second_failed):
            capability = diagnostics["capabilities"]["policy:source-assurance"]
            assert capability["state"] == "degraded"
            assert capability["details"]["freshness"]["state"] == "failed"
            assert capability["details"]["freshness"]["refresh_error_type"] == (
                "RuntimeError"
            )


def test_production_composition_injects_skill_gateway_auth_without_ambient_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "composition-gateway-token-at-least-24-characters"
    (tmp_path / "instance").mkdir()
    settings = RuntimeSettings.credential_free(
        tmp_path / "instance",
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"member-a.ts.net": "member-a"},
        openclaw_enabled=True,
        openclaw_shared_secret=token,
        openclaw_actor_identities={"portal|central|member-a": "member-a"},
        openclaw_gateway_token=token,
        member_roles={"member-a": "principal"},
    )
    captured: dict[str, object] = {}

    async def invoke(**kwargs: object) -> skill_wrapper._OpenClawCallOutcome:
        captured.update(kwargs)
        return skill_wrapper._OpenClawCallOutcome(
            kind="ok",
            raw_result={"is_thing": True, "confidence": 0.9},
            invocation_id="composition-invocation",
            provider_model="fixture/composition",
        )

    monkeypatch.delenv("OPENCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr(skill_wrapper, "_post_one_provider", invoke)
    app = create_application(settings)
    with TestClient(app) as client:
        runtime: AdministrateMeRuntime = app.state.runtime
        session = Session(
            tenant_id=runtime.settings.instance.tenant_id,
            auth_member_id="member-a",
            auth_role="principal",
            view_member_id="member-a",
            view_role="principal",
            dm_scope="per_channel_peer",
            source="product_api_internal",
        )
        result = client.portal.call(
            runtime.run_skill_fn,
            "skill:classify_test",
            {"text": "hi"},
            SkillContext(session=session),
        )

    assert result.output == {"is_thing": True, "confidence": 0.9}
    assert captured["token"] == token


def test_runtime_recovery_endpoint_is_scoped_append_only_and_human_gated(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    settings = RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={
            "member-a.ts.net": "member-a",
            "child.ts.net": "member-child",
        },
        member_roles={"member-a": "principal", "member-child": "child"},
        deterministic_openclaw_fixture=True,
        fixture_member_id="member-a",
    )

    async def preload() -> tuple[str, str]:
        log = EventLog(settings.instance, TEST_KEY)
        try:
            source_id = await log.append(
                EventEnvelope(
                    event_at_ms=1_786_410_000_000,
                    tenant_id=settings.instance.tenant_id,
                    type="messaging.received",
                    schema_version=3,
                    occurred_at="2026-08-11T15:00:00Z",
                    source_adapter="gmail",
                    source_account_id="fixture-account",
                    owner_scope="private:member-a",
                    visibility_scope="private:member-a",
                    sensitivity="normal",
                    idempotency_key="runtime-recovery-source",
                    payload={
                        "provider": "gmail",
                        "provider_account_id": "fixture-account",
                        "member_id": "member-a",
                        "source_channel": "gmail",
                        "native_message_id": "runtime-recovery-source",
                        "thread_id": "runtime-recovery-thread",
                        "from_identifier": "sender@example.invalid",
                        "to_identifier": "member@example.invalid",
                        "workspace": "personal",
                        "subject": "Fixture recovery",
                        "body_text": "A private fixture update without an action.",
                        "received_at": "2026-08-11T15:00:00Z",
                        "provider_labels": ["INBOX"],
                        "attachments": [],
                    },
                )
            )
            failed_id = await log.append(
                EventEnvelope(
                    event_at_ms=1_786_410_000_001,
                    tenant_id=settings.instance.tenant_id,
                    type="triage.intent.features_extracted",
                    schema_version=3,
                    occurred_at="2026-08-11T15:00:01Z",
                    source_adapter="deterministic_candidate_eligibility",
                    source_account_id="pipeline",
                    owner_scope="private:member-a",
                    visibility_scope="private:member-a",
                    sensitivity="normal",
                    idempotency_key=f"triage:message-features:v19:{source_id}",
                    payload={
                        "intent_id": f"source:{source_id}",
                        "source_event_id": source_id,
                        "skill_call_event_id": None,
                        "extractor_id": "fixture-source-assessor",
                        "language": None,
                        "candidates": [],
                        "extraction_state": "unavailable",
                        "extracted_at": "2026-08-11T15:00:01Z",
                        "assessment_outcome": "failed",
                        "reason_codes": [
                            "language_seam_OpenClawUnreachable",
                            "no_eligible_candidate",
                        ],
                    },
                ),
                causation_id=source_id,
            )
            bus = EventBus(log, settings.instance.bus_checkpoint_path)

            async def noop(_event: dict[str, object]) -> None:
                return None

            bus.subscribe(
                "pipeline:pipeline:noise_filtering",
                ["messaging.received", "messaging.sent", "telephony.sms_received"],
                noop,
                consumer_version="19.0.0",
            )
            await bus.set_checkpoint("pipeline:pipeline:noise_filtering", failed_id)
            await bus.stop()
            return source_id, failed_id
        finally:
            await log.close()

    source_id, failed_id = asyncio.run(preload())
    app = create_application(settings)
    with TestClient(app) as client:
        body = {
            "request_id": "runtime-recovery-request-1",
            "targets": [
                {
                    "source_event_id": source_id,
                    "failed_assessment_event_id": failed_id,
                    "expected_failure_reason": ("language_seam_OpenClawUnreachable"),
                }
            ],
        }
        child = client.post(
            "/api/system/source-assessment-recoveries",
            json=body,
            headers={"Tailscale-User-Login": "child.ts.net"},
        )
        assert child.status_code == 403
        cross_scope = client.post(
            "/api/system/source-assessment-recoveries",
            json=body,
            headers={
                "Tailscale-User-Login": "member-a.ts.net",
                "X-AdminMe-View-Member": "member-child",
            },
        )
        assert cross_scope.status_code == 409
        assert cross_scope.json()["detail"] == "request_scope_mismatch"

        accepted = client.post(
            "/api/system/source-assessment-recoveries",
            json=body,
            headers={"Tailscale-User-Login": "member-a.ts.net"},
        )
        assert accepted.status_code == 202, accepted.text
        receipt = accepted.json()
        assert receipt["content_exposed"] is False
        assert receipt["canonical_objects_created"] == 0
        assert receipt["external_write_requests"] == 0
        assert "private fixture update" not in accepted.text.lower()

        runtime: AdministrateMeRuntime = app.state.runtime
        deadline = time.monotonic() + 5
        latest = None
        while time.monotonic() < deadline:
            latest = client.portal.call(
                latest_source_assessment, runtime.event_log, source_id
            )
            if latest and latest["schema_version"] == 4:
                break
            time.sleep(0.02)
        assert latest is not None
        assert latest["schema_version"] == 4
        assert latest["payload"]["assessment_outcome"] == "no_candidate"
        assert latest["payload"]["supersedes_assessment_event_id"] == failed_id

        duplicate = client.post(
            "/api/system/source-assessment-recoveries",
            json=body,
            headers={"Tailscale-User-Login": "member-a.ts.net"},
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["duplicate"] is True
        assert duplicate.json()["request_event_ids"] == receipt["request_event_ids"]

        all_events = client.portal.call(_event_types, runtime.event_log)
        assert not {
            "triage.item.confirmed",
            "triage.item.materialized",
            "task.created",
            "commitment.created",
            "external_sync.write_requested",
            "external_sync.write_completed",
        }.intersection(all_events)


async def test_runtime_startup_terminalizes_checkpointed_recovery_command(
    tmp_path: Path,
) -> None:
    """Production startup repairs a command ledger without moving checkpoints."""

    settings = _settings(tmp_path)
    log = EventLog(settings.instance, TEST_KEY)
    try:
        source_id = await log.append(
            EventEnvelope(
                event_at_ms=1_786_420_000_000,
                tenant_id=settings.instance.tenant_id,
                type="messaging.received",
                schema_version=3,
                occurred_at="2026-08-11T18:00:00Z",
                source_adapter="gmail",
                source_account_id="fixture-account",
                owner_scope="private:member-a",
                visibility_scope="private:member-a",
                sensitivity="normal",
                idempotency_key="startup-recovery-source",
                payload={
                    "provider": "gmail",
                    "provider_account_id": "fixture-account",
                    "member_id": "member-a",
                    "source_channel": "gmail",
                    "native_message_id": "startup-recovery-source",
                    "thread_id": "startup-recovery-thread",
                    "from_identifier": "sender@example.invalid",
                    "to_identifier": "member@example.invalid",
                    "workspace": "personal",
                    "subject": "Fixture startup recovery",
                    "body_text": "A private fixture update without an action.",
                    "received_at": "2026-08-11T18:00:00Z",
                    "provider_labels": ["INBOX"],
                    "attachments": [],
                },
            )
        )
        failed_id = await log.append(
            EventEnvelope(
                event_at_ms=1_786_420_000_001,
                tenant_id=settings.instance.tenant_id,
                type="triage.intent.features_extracted",
                schema_version=3,
                occurred_at="2026-08-11T18:00:01Z",
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                owner_scope="private:member-a",
                visibility_scope="private:member-a",
                sensitivity="normal",
                idempotency_key=f"startup-recovery-failed:{source_id}",
                payload={
                    "intent_id": f"source:{source_id}",
                    "source_event_id": source_id,
                    "skill_call_event_id": None,
                    "extractor_id": "fixture-source-assessor",
                    "language": None,
                    "candidates": [],
                    "extraction_state": "unavailable",
                    "extracted_at": "2026-08-11T18:00:01Z",
                    "assessment_outcome": "failed",
                    "reason_codes": [
                        "language_seam_OpenClawUnreachable",
                        "no_eligible_candidate",
                    ],
                },
            ),
            causation_id=source_id,
        )
        [request_envelope] = await build_recovery_request_envelopes(
            log,
            SourceAssessmentRecoveryBatch(
                request_id="startup-recovery-request-1",
                targets=[
                    SourceAssessmentRecoveryTarget(
                        source_event_id=source_id,
                        failed_assessment_event_id=failed_id,
                        expected_failure_reason="language_seam_OpenClawUnreachable",
                    )
                ],
            ),
            tenant_id=settings.instance.tenant_id,
            requested_by_actor="member-a",
            authority="member_principal",
            required_owner_scope="private:member-a",
        )
        request_id = await log.append(request_envelope)
        request = await log.get(request_id)
        assert request is not None
        current_id = await log.append(
            EventEnvelope(
                event_at_ms=int(request["event_at_ms"]) + 1,
                tenant_id=settings.instance.tenant_id,
                type="triage.intent.features_extracted",
                schema_version=3,
                occurred_at=EventEnvelope.now_utc_iso(),
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                owner_scope="private:member-a",
                visibility_scope="private:member-a",
                sensitivity="normal",
                idempotency_key=f"startup-recovery-current:{source_id}",
                payload={
                    "intent_id": f"source:{source_id}",
                    "source_event_id": source_id,
                    "skill_call_event_id": None,
                    "extractor_id": "fixture-source-assessor",
                    "language": None,
                    "candidates": [],
                    "extraction_state": "complete",
                    "extracted_at": EventEnvelope.now_utc_iso(),
                    "assessment_outcome": "no_candidate",
                    "reason_codes": ["language_assessment_completed"],
                },
            ),
            causation_id=source_id,
        )
        checkpoint_bus = EventBus(log, settings.instance.bus_checkpoint_path)

        async def noop(_event: dict[str, object]) -> None:
            return None

        checkpoint_bus.subscribe(
            "pipeline:pipeline:noise_filtering",
            [
                "messaging.received",
                "messaging.sent",
                "telephony.sms_received",
                "source.assessment.recovery_requested",
            ],
            noop,
            consumer_version="22.0.0",
        )
        await checkpoint_bus.set_checkpoint(
            "pipeline:pipeline:noise_filtering", current_id
        )
        await checkpoint_bus.stop()
    finally:
        await log.close()

    runtime = AdministrateMeRuntime(settings)
    await runtime.start()
    try:
        assert runtime.event_log is not None
        refusals = [
            event
            async for event in runtime.event_log.read_since(
                types=["source.assessment.recovery_refused"]
            )
            if event["payload"]["request_event_id"] == request_id
        ]
        assert len(refusals) == 1
        assert refusals[0]["payload"]["reason"] == "failed_assessment_not_current"
        assert runtime._source_assurance_cache is not None
        assert runtime._source_assurance_cache["recovery"]["counts"]["pending"] == 0
    finally:
        await runtime.stop()


async def _event_types(log: EventLog) -> list[str]:
    return [str(event["type"]) async for event in log.read_since()]


def test_reviewed_household_enrollment_becomes_idempotent_people_truth(
    tmp_path: Path,
) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    mapping = {
        "schema_version": 2,
        "household_label": "Fixture household",
        "members": [
            {"member_id": "member-a", "label": "Member A", "role": "principal"},
            {"member_id": "member-b", "label": "Member B", "role": "principal"},
        ],
        "parties": [
            {
                "party_id": "party-contact",
                "label": "Outside Contact",
                "kind": "person",
                "visibility_scope": "shared:household",
            }
        ],
        "memberships": [
            {
                "membership_id": "membership-a",
                "party_id": "member-a",
                "role": "principal",
            },
            {
                "membership_id": "membership-b",
                "party_id": "member-b",
                "role": "principal",
            },
        ],
        "relationships": [
            {
                "relationship_id": "relationship-partners",
                "from_party_id": "member-a",
                "to_party_id": "member-b",
                "kind": "spouse",
                "direction": "mutual",
            },
            {
                "relationship_id": "relationship-contact",
                "from_party_id": "member-b",
                "to_party_id": "party-contact",
                "kind": "coparent",
                "direction": "mutual",
            },
        ],
    }
    settings = RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=TEST_KEY,
        repository_root=REPO_ROOT,
        member_identities={"member-a.ts.net": "member-a"},
        member_roles={"member-a": "principal", "member-b": "principal"},
        household_mapping=mapping,
    )

    enrollment_event_ids: list[str] = []
    for run in range(2):
        app = create_application(settings)
        with TestClient(app) as client:
            response = client.get(
                "/api/member/v1/surfaces/people",
                headers={"Tailscale-User-Login": "member-a.ts.net"},
            )
            assert response.status_code == 200, response.text
            cockpit = response.json()["cockpit"]
            assert cockpit["contract"] == "deterministic-people-cockpit-v1"
            assert {person["party_id"] for person in cockpit["directory"]} == {
                "member-a",
                "member-b",
                "party-contact",
            }
            assert cockpit["lens_counts"]["household"] == 3
            assert (
                next(
                    person
                    for person in cockpit["directory"]
                    if person["party_id"] == "member-a"
                )["relationship_label"]
                == "You"
            )
            assert (
                next(
                    person
                    for person in cockpit["directory"]
                    if person["party_id"] == "member-b"
                )["relationship_label"]
                == "Spouse"
            )
            assert cockpit["sources"] == []

            search = client.get(
                "/api/member/v1/people/search?q=Outside",
                headers={"Tailscale-User-Login": "member-a.ts.net"},
            )
            assert search.status_code == 200
            assert [item["party_id"] for item in search.json()["results"]] == [
                "party-contact"
            ]

            async def enrollment_ids() -> list[str]:
                assert app.state.runtime.event_log is not None
                return [
                    event["event_id"]
                    async for event in app.state.runtime.event_log.read_since()
                    if event["source_adapter"] == "bootstrap-household-enrollment"
                ]

            current_ids = client.portal.call(enrollment_ids)
            if run == 0:
                enrollment_event_ids = current_ids
                assert len(current_ids) == 8
            else:
                assert current_ids == enrollment_event_ids


async def test_repeated_shutdown_is_safe_and_log_reopens(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    runtime = AdministrateMeRuntime(settings)
    await runtime.start()
    await runtime.stop()
    await runtime.stop()
    assert runtime.started is False
    assert runtime.events is not None and runtime.events.accepting is False

    reopened = EventLog(settings.instance, TEST_KEY)
    try:
        events = [event async for event in reopened.read_since()]
        assert any(event["type"] == "runtime.started" for event in events)
    finally:
        await reopened.close()
        await reopened.close()


async def test_cancelled_runtime_stop_settles_real_bus_callback_and_native_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    runtime = AdministrateMeRuntime(settings)
    await runtime.start()
    assert runtime.projections is not None
    assert runtime.events is not None
    assert runtime.bus is not None
    projection = runtime.projections._projections["tasks"]  # noqa: SLF001
    original_apply = projection.apply
    callback_started = threading.Event()
    release_callback = threading.Event()

    def _held_apply(envelope: dict[str, Any], connection: Any) -> None:
        if envelope["type"] == "task.created":
            callback_started.set()
            if not release_callback.wait(timeout=5):
                raise RuntimeError("test did not release projection callback")
        original_apply(envelope, connection)

    monkeypatch.setattr(projection, "apply", _held_apply)
    await runtime.events.write(
        EventEnvelope(
            event_at_ms=1_780_000_000_000,
            tenant_id=settings.instance.tenant_id,
            type="task.created",
            schema_version=1,
            occurred_at="2026-05-28T12:00:00Z",
            source_adapter="runtime-shutdown-test",
            source_account_id="fixture",
            owner_scope="private:member-a",
            visibility_scope="private:member-a",
            sensitivity="normal",
            payload={
                "task_id": "task-runtime-cancelled-stop",
                "title": "Shutdown settles callback",
                "owner_member_id": "member-a",
            },
        )
    )
    assert await asyncio.to_thread(callback_started.wait, 2)

    stopping = asyncio.create_task(runtime.stop())
    deadline = time.monotonic() + 2
    while not runtime.bus._stopping.is_set():  # noqa: SLF001
        if time.monotonic() >= deadline:
            release_callback.set()
            raise AssertionError("runtime did not enter EventBus shutdown")
        await asyncio.sleep(0.01)
    stopping.cancel()
    await asyncio.sleep(0)
    assert not stopping.done()
    release_callback.set()

    with pytest.raises(asyncio.CancelledError):
        await stopping

    assert runtime.bus._closed is True  # noqa: SLF001
    assert runtime.bus._tasks == {}  # noqa: SLF001
    with pytest.raises(sqlite3.ProgrammingError):
        runtime.bus._ckpt_conn.execute("SELECT 1")  # noqa: SLF001
    assert runtime.projections._closed is True  # noqa: SLF001
    assert runtime.projections._conns == {}  # noqa: SLF001
    assert runtime.event_log is not None and runtime.event_log._closed is True  # noqa: SLF001
    assert not [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and task.get_name().startswith("eventbus:")
    ]
    assert not any(
        thread.name.startswith("adminme-projections")
        for thread in threading.enumerate()
    )
    await runtime.stop()


async def test_startup_failure_unwinds_all_open_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_start(_self: AdapterSupervisor) -> None:
        raise RuntimeError("simulated adapter supervisor startup failure")

    monkeypatch.setattr(AdapterSupervisor, "start", fail_start)
    settings = _settings(tmp_path)
    runtime = AdministrateMeRuntime(settings)
    with pytest.raises(RuntimeError, match="simulated adapter supervisor"):
        await runtime.start()

    assert runtime.started is False
    assert runtime.startup_error is not None
    assert runtime.events is not None and runtime.events.accepting is False
    await runtime.stop()

    reopened = EventLog(settings.instance, TEST_KEY)
    await reopened.close()
