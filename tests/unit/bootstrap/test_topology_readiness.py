"""Secret-safe Central Host topology and owner-attestation checks."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from adminme.deployment_readiness import CENTRAL_HOST_TAG, REQUIRED_DM_SCOPE
from adminme.openclaw_topology import binding_fingerprints, member_agent_id
from bootstrap.attest_deployment_readiness import (
    ReadinessAttestationError,
    build_attestation,
    record_attestation,
)
from bootstrap.contracts import DeploymentPaths
from bootstrap.openclaw_activation import reconcile_member_topology
from bootstrap.preflight import PreflightFailure, run_preflight
from bootstrap.topology_readiness import sample_host_topology
from bootstrap import topology_readiness


def _paths(tmp_path: Path) -> DeploymentPaths:
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    return paths


def test_topology_sample_accepts_only_tagged_nonmember_singletons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(
        "bootstrap.topology_readiness._tailscale_status",
        lambda: {
            "Self": {"Online": True, "Tags": [CENTRAL_HOST_TAG], "UserID": 7},
            "User": {"7": {"LoginName": "central-service.ts.net"}},
        },
    )
    monkeypatch.setattr(
        "bootstrap.topology_readiness._serve_is_sole_edge", lambda: True
    )
    monkeypatch.setattr(
        "bootstrap.topology_readiness._process_counts", lambda _paths: (1, 1)
    )
    monkeypatch.setattr(
        "bootstrap.topology_readiness._service_account_standard", lambda: True
    )

    value = sample_host_topology(
        paths, member_identities={"member-a.ts.net": "member-a"}
    )

    assert value == {
        "contract": "central-host-topology-v1",
        "tailscale_node_online": True,
        "central_host_tag": CENTRAL_HOST_TAG,
        "central_identity_kind": "infrastructure_tag",
        "central_identity_collides_with_member": False,
        "serve_is_sole_edge": True,
        "python_runtime_count": 1,
        "openclaw_gateway_count": 1,
        "service_account_standard": True,
        "content_exposed": False,
    }
    assert "member-a" not in json.dumps(value)


def test_topology_sample_exposes_collision_only_as_a_boolean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(
        "bootstrap.topology_readiness._tailscale_status",
        lambda: {
            "Self": {"Online": True, "Tags": [], "UserID": 7},
            "User": {"7": {"LoginName": "MEMBER-A.TS.NET"}},
        },
    )
    monkeypatch.setattr(
        "bootstrap.topology_readiness._serve_is_sole_edge", lambda: False
    )
    monkeypatch.setattr(
        "bootstrap.topology_readiness._process_counts", lambda _paths: (2, 0)
    )
    monkeypatch.setattr(
        "bootstrap.topology_readiness._service_account_standard", lambda: False
    )

    value = sample_host_topology(
        paths, member_identities={"member-a.ts.net": "member-a"}
    )

    assert value["central_identity_kind"] == "untagged_user_identity"
    assert value["central_identity_collides_with_member"] is True
    assert "member-a.ts.net" not in json.dumps(value).casefold()


def test_tagged_host_still_rejects_a_member_owned_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(
        "bootstrap.topology_readiness._tailscale_status",
        lambda: {
            "Self": {"Online": True, "Tags": [CENTRAL_HOST_TAG], "UserID": 7},
            "User": {"7": {"LoginName": "member-a.ts.net"}},
        },
    )
    monkeypatch.setattr(
        "bootstrap.topology_readiness._serve_is_sole_edge", lambda: True
    )
    monkeypatch.setattr(
        "bootstrap.topology_readiness._process_counts", lambda _paths: (1, 1)
    )
    monkeypatch.setattr(
        "bootstrap.topology_readiness._service_account_standard", lambda: True
    )

    value = sample_host_topology(
        paths, member_identities={"member-a.ts.net": "member-a"}
    )

    assert value["central_identity_kind"] == "infrastructure_tag"
    assert value["central_identity_collides_with_member"] is True


def test_serve_status_accepts_disabled_funnel_and_rejects_enabled_funnel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def result(value: dict[str, object]) -> object:
        return type(
            "Result",
            (),
            {"returncode": 0, "stdout": json.dumps(value), "stderr": ""},
        )()

    base = {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {
            "central.example.ts.net:443": {
                "Handlers": {"/": {"Proxy": "http://127.0.0.1:3330"}}
            }
        },
        "AllowFunnel": {"central.example.ts.net:443": False},
    }
    monkeypatch.setattr(topology_readiness, "_run", lambda _command: result(base))
    assert topology_readiness._serve_is_sole_edge() is True

    enabled = {**base, "AllowFunnel": {"central.example.ts.net:443": True}}
    monkeypatch.setattr(topology_readiness, "_run", lambda _command: result(enabled))
    assert topology_readiness._serve_is_sole_edge() is False


def test_owner_attestation_requires_every_physical_confirmation_and_is_mode_600(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    config = paths.instance_dir / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "member-mapping.json").write_text(
        json.dumps(
            {
                "members": [
                    {
                        "member_id": "member-a",
                        "tailscale_identity": "member-a.ts.net",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (paths.app_dir / "current-release.json").write_text(
        json.dumps({"commit": "a" * 40, "path": "/reviewed/release"}),
        encoding="utf-8",
    )
    confirmations = {
        "distinct_identities": True,
        "separate_admin": True,
        "filevault_unlock": True,
        "automatic_login_disabled": True,
        "backup_authenticated": True,
        "restore_rehearsed": True,
        "prior_release_retained": True,
        "automatic_rollback_eligible": True,
    }
    with pytest.raises(ReadinessAttestationError, match="filevault_unlock"):
        build_attestation(
            paths, confirmations={**confirmations, "filevault_unlock": False}
        )

    result = record_attestation(paths, confirmations=confirmations)
    target = config / "deployment-readiness.json"
    stored = json.loads(target.read_text("utf-8"))
    assert result == {
        "status": "recorded",
        "contract": "deployment-readiness-v1",
        "portal_member_count": 1,
        "content_exposed": False,
    }
    assert stored["identity_acceptance"]["member_ids"] == ["member-a"]
    assert stored["deployment_binding"]["release_commit"] == "a" * 40
    assert stored["provider_effect_window"]["started_at"]
    assert "openclaw" not in stored
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_openclaw_reconciliation_is_per_member_and_preserves_other_agents(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    config_dir = paths.instance_dir / "config"
    runtime_dir = paths.instance_dir / "openclaw" / "runtime"
    config_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    actors = {
        "discord|account-a|actor-a": "member-a",
        "discord|account-b|actor-b": "member-b",
    }
    (config_dir / "adapters.json").write_text(
        json.dumps(
            {
                "openclaw_actor_identities": actors,
                "member_chat_enabled_members": ["member-a", "member-b"],
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "openclaw.json").write_text(
        json.dumps(
            {
                "agents": {"list": [{"id": "operator", "default": True}]},
                "bindings": [{"agentId": "operator", "match": {"channel": "cli"}}],
                "plugins": {"entries": {"administrateme": {"config": {}}}},
            }
        ),
        encoding="utf-8",
    )

    result = reconcile_member_topology(paths)
    stored = json.loads((runtime_dir / "openclaw.json").read_text("utf-8"))
    agent_ids = {row["id"] for row in stored["agents"]["list"]}
    assert agent_ids == {
        "operator",
        member_agent_id("member-a"),
        member_agent_id("member-b"),
    }
    assert stored["session"]["dmScope"] == "per-account-channel-peer"
    plugin = stored["plugins"]["entries"]["administrateme"]["config"]
    assert plugin == {
        "dmScope": REQUIRED_DM_SCOPE,
        "memberAgentIds": [
            member_agent_id("member-a"),
            member_agent_id("member-b"),
        ],
        "bindingFingerprints": list(
            binding_fingerprints(actors, frozenset({"member-a", "member-b"}))
        ),
    }
    assert result == {
        "member_agent_count": 2,
        "binding_count": 4,
        "dm_scope": REQUIRED_DM_SCOPE,
        "content_exposed": False,
    }


def test_openclaw_reconciliation_rejects_a_shared_channel_account_before_write(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    config_dir = paths.instance_dir / "config"
    runtime_dir = paths.instance_dir / "openclaw" / "runtime"
    config_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    (config_dir / "adapters.json").write_text(
        json.dumps(
            {
                "openclaw_actor_identities": {
                    "discord|shared|actor-a": "member-a",
                    "discord|shared|actor-b": "member-b",
                },
                "member_chat_enabled_members": [],
            }
        ),
        encoding="utf-8",
    )
    target = runtime_dir / "openclaw.json"
    original = {"agents": {"list": []}, "bindings": []}
    target.write_text(json.dumps(original), encoding="utf-8")

    with pytest.raises(RuntimeError, match="shared across members"):
        reconcile_member_topology(paths)
    assert json.loads(target.read_text("utf-8")) == original


def test_preflight_rejects_an_admin_service_account(tmp_path: Path) -> None:
    with pytest.raises(PreflightFailure) as captured:
        run_preflight(
            tmp_path / "deployment",
            system="Darwin",
            machine="arm64",
            macos_version="26.0",
            require_dependencies=False,
            check_filevault=False,
            group_names=frozenset({"staff", "admin"}),
        )
    dedicated = next(
        check for check in captured.value.checks if check.name == "dedicated_account"
    )
    assert dedicated.ok is False
    assert "admin group" in dedicated.detail
