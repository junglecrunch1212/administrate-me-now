"""Private provisioning changes only reviewed policy and a new preparation file."""

from datetime import datetime, timezone
from copy import deepcopy
import json
import base64
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from adminme.workstation_browser import BROWSER_POLICY_FILE
from adminme.workstation_inventory import INVENTORY_FILE
from bootstrap.workstation_browser import prepare_config, provision
from bootstrap import workstation_browser as browser
from bootstrap.candidate_readiness import (
    CandidateReadinessError,
    capture_deployment_snapshot,
)
from tests.unit.bootstrap.test_candidate_readiness import _deployment, _private_write
from tests.unit.test_workstation_inventory import document


def private_file(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def test_private_browser_preparation_preserves_existing_config_and_credentials(
    tmp_path,
):
    previous = tmp_path / "gateway.json"
    original = {
        "gateway": {"auth": {"token": "synthetic-test-only"}},
        "agents": {"list": [{"id": "service", "tools": {"allow": ["fixture"]}}]},
    }
    private_file(previous, original)
    output = tmp_path / "prepared.json"
    assert prepare_config(previous, output)["state"] == "prepared_not_activated"
    prepared = json.loads(output.read_text())
    assert json.loads(previous.read_text()) == original
    assert prepared["gateway"]["auth"] == original["gateway"]["auth"]
    assert prepared["agents"] == original["agents"]
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        prepare_config(previous, output)
    link = tmp_path / "output-link"
    link.symlink_to(previous)
    with pytest.raises(FileExistsError):
        prepare_config(previous, link)


def test_verified_tab_refresh_uses_current_revision_and_can_revoke_without_evidence(
    tmp_path,
):
    config = tmp_path / "config"
    config.mkdir(mode=0o700)
    private_file(config / INVENTORY_FILE, document(datetime.now(timezone.utc)))
    policy = {
        "revision": 1,
        "enabled": True,
        "resources": [
            {
                "resource_id": "own-page",
                "inventory_binding_id": "cos-browser",
                "inventory_binding_revision": 1,
                "native_target_id": "actual-native-target",
                "target_evidence_reference": "operator-observation:profile-and-tab",
                "url": "https://work.example.invalid/planning",
                "authorized_url_prefix": "https://work.example.invalid/",
                "identity_probes": {
                    key: {"selector": f"#{key}"}
                    for key in ("account", "workspace", "role")
                },
                "audience_member_ids": ["member-a"],
            }
        ],
    }
    assert provision(config, policy, expected_revision=0)["enabled"] is True
    assert (config / BROWSER_POLICY_FILE).stat().st_mode & 0o777 == 0o600
    policy["revision"] = 2
    policy["resources"][0]["native_target_id"] = "replacement-native-target"
    with pytest.raises(ValueError, match="revision"):
        provision(config, policy, expected_revision=0)
    assert provision(config, policy, expected_revision=1)["revision"] == 2
    (config / INVENTORY_FILE).unlink()
    with pytest.raises(FileNotFoundError):
        provision(config, {**policy, "revision": 3}, expected_revision=2)
    assert (
        provision(
            config, {**policy, "revision": 3, "enabled": False}, expected_revision=2
        )["enabled"]
        is False
    )
    config.chmod(0o755)
    with pytest.raises(ValueError, match="private"):
        provision(config, {**policy, "revision": 4}, expected_revision=3)


def test_native_patch_uses_hash_guard_and_only_browser_and_existing_cos_agent(tmp_path):
    from adminme.openclaw_topology import SERVICE_PRIVATE_TOOLS, service_agent_id
    from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
    from bootstrap.contracts import DeploymentPaths
    from bootstrap.workstation_browser import native_service_tools, prepare_native_patch
    from adminme.workstation_browser import prepare_browser_gateway

    paths = DeploymentPaths.from_root(tmp_path)
    directory = paths.instance_dir / "config"
    directory.mkdir(parents=True, mode=0o700)
    private_file(directory / INVENTORY_FILE, document(datetime.now(timezone.utc)))
    resource = {
        "resource_id": "planning",
        "inventory_binding_id": "cos-browser",
        "inventory_binding_revision": 1,
        "native_target_id": "observed-tab",
        "target_evidence_reference": "fixture:profile-tab",
        "url": "https://work.example.invalid/planning",
        "authorized_url_prefix": "https://work.example.invalid/",
        "identity_probes": {
            key: {"selector": f"#{key}"} for key in ("account", "workspace", "role")
        },
        "audience_member_ids": ["member-a"],
    }
    private_file(
        directory / BROWSER_POLICY_FILE,
        {"revision": 1, "enabled": True, "resources": [resource]},
    )
    release = tmp_path / "release"
    manifest = release / "openclaw" / "administrateme" / "openclaw.plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"contracts": {"agentToolResultMiddleware": ["openclaw", "codex"]}})
    )
    agent = service_agent_id(COS_SERVICE_PRINCIPAL_ID)
    original = {
        "plugins": {
            "entries": {
                "administrateme": {
                    "enabled": True,
                    "hooks": {
                        "allowPromptInjection": True,
                        "allowConversationAccess": True,
                    },
                }
            }
        },
        "agents": {
            "list": [
                {"id": agent, "tools": {"allow": list(SERVICE_PRIVATE_TOOLS)}},
                {"id": "member-agent", "tools": {"allow": ["administrateme_state"]}},
            ]
        },
        "gateway": {"auth": {"token": "synthetic-secret-never-in-patch"}},
    }
    snapshot = {"valid": True, "hash": "a" * 64, "config": original}
    params = prepare_native_patch(snapshot, paths, release)
    patch = json.loads(params["raw"])
    assert params["baseHash"] == snapshot["hash"]
    assert patch["agents"]["list"] == [
        {"id": agent, "tools": {"allow": [*SERVICE_PRIVATE_TOOLS, "browser"]}}
    ]
    assert "synthetic-secret" not in json.dumps(params)
    assert patch["browser"]["allowSystemProfileImport"] is False
    assert patch["plugins"] == {"entries": {"browser": {"enabled": True}}}
    assert "browser" not in original
    keyed = deepcopy(original)
    keyed["agents"] = {
        "defaults": {"tools": {"deny": ["arbitrary-tool"]}},
        "entries": {
            agent: {
                "tools": {"allow": list(SERVICE_PRIVATE_TOOLS), "deny": ["send"]},
                "workspace": "retained-workspace",
            },
            "member-agent": {
                "tools": {"allow": ["administrateme_state"]},
                "model": "retained-model",
            },
        },
    }
    unchanged = deepcopy(keyed)
    keyed_params = prepare_native_patch({**snapshot, "config": keyed}, paths, release)
    keyed_patch = json.loads(keyed_params["raw"])
    assert keyed_patch["agents"] == {
        "entries": {agent: {"tools": {"allow": [*SERVICE_PRIVATE_TOOLS, "browser"]}}}
    }
    assert {key: value for key, value in keyed_patch.items() if key != "agents"} == {
        key: value for key, value in patch.items() if key != "agents"
    }
    assert keyed == unchanged
    assert "synthetic-secret" not in json.dumps(keyed_params)
    for invalid_agents in (
        {"list": original["agents"]["list"], "entries": keyed["agents"]["entries"]},
        {"entries": []},
        {"entries": {"member-agent": {"tools": {"allow": []}}}},
        {"entries": {agent: {"id": "another-agent", "tools": {"allow": []}}}},
        {
            "entries": {
                agent: keyed["agents"]["entries"][agent],
                "duplicate": {"id": agent},
            }
        },
        {"list": [original["agents"]["list"][0], original["agents"]["list"][0]]},
        {"entries": {agent: {"tools": {"allow": "not-a-list"}}}},
    ):
        with pytest.raises(ValueError):
            prepare_native_patch(
                {**snapshot, "config": {**original, "agents": invalid_agents}},
                paths,
                release,
            )
    assert json.loads(
        prepare_native_patch(snapshot, paths, release, disable=True)["raw"]
    ) == {"browser": {"enabled": False}}
    with pytest.raises(ValueError, match="snapshot"):
        prepare_native_patch({**snapshot, "hash": ""}, paths, release)
    prepared = prepare_browser_gateway(original)
    prepared["plugins"]["entries"]["administrateme"]["hooks"][
        "allowPromptInjection"
    ] = False
    assert native_service_tools(prepared, paths, release) == []
    with pytest.raises(ValueError, match="finite worker policy hook"):
        prepare_native_patch({**snapshot, "config": prepared}, paths, release)
    assert json.loads(
        prepare_native_patch(
            {**snapshot, "config": prepared}, paths, release, disable=True
        )["raw"]
    ) == {"browser": {"enabled": False}}
    prepared["plugins"]["entries"]["administrateme"]["hooks"][
        "allowPromptInjection"
    ] = True
    prepared["plugins"]["entries"]["administrateme"]["hooks"][
        "allowConversationAccess"
    ] = False
    assert native_service_tools(prepared, paths, release) == []
    with pytest.raises(ValueError, match="finite worker policy hook"):
        prepare_native_patch({**snapshot, "config": prepared}, paths, release)
    prepared["plugins"]["entries"]["administrateme"]["enabled"] = False
    assert native_service_tools(prepared, paths, release) == []
    manifest.write_text(json.dumps({"contracts": {}}))
    with pytest.raises(ValueError, match="deployed release"):
        prepare_native_patch(snapshot, paths, release)


def _reconciliation_fixture(tmp_path, monkeypatch, *, admit=False, agents="entries"):
    from adminme.openclaw_topology import service_agent_id
    from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
    from adminme.workstation_browser import prepare_browser_gateway

    paths = _deployment(tmp_path)
    (paths.instance_dir / "openclaw").chmod(0o700)
    release = paths.app_dir / "releases" / ("a" * 40)
    _private_write(
        release / "openclaw/administrateme/openclaw.plugin.json",
        json.dumps(
            {"contracts": {"agentToolResultMiddleware": ["openclaw", "codex"]}}
        ).encode(),
    )
    source = {
        "controller_commit": "b" * 40,
        "installed_source": {"tracked_source_tree_matches": True},
    }
    monkeypatch.setattr(
        browser, "_browser_source_selection", lambda _paths: (release, deepcopy(source))
    )
    agent = service_agent_id(COS_SERVICE_PRINCIPAL_ID)
    own = {
        "tools": {"allow": ["administrateme_service_work"], "deny": ["send"]},
        "workspace": "retained-workspace",
    }
    other = {"tools": {"allow": ["administrateme_state"]}, "model": "retained-model"}
    previous = {
        "gateway": {"auth": {"token": "synthetic-private-credential"}},
        "plugins": {
            "entries": {
                "administrateme": {
                    "enabled": True,
                    "hooks": {
                        "allowPromptInjection": True,
                        "allowConversationAccess": True,
                    },
                }
            }
        },
        "agents": {
            "defaults": {"model": "retained-default"},
            agents: (
                {agent: own, "member-agent": other}
                if agents == "entries"
                else [{"id": agent, **own}, {"id": "member-agent", **other}]
            ),
        },
        "bindings": [{"agentId": "member-agent", "match": {"channel": "fixture"}}],
    }
    config = paths.instance_dir / "config"
    for name in (
        "disposable-test-dataset.json",
        "cos-channel-operation.json",
        "cos-imsg-guard.json",
    ):
        _private_write(
            config / name, json.dumps({"sentinel": name, "version": 1}).encode()
        )
    if admit:
        private_file(config / INVENTORY_FILE, document(datetime.now(timezone.utc)))
        private_file(
            config / BROWSER_POLICY_FILE,
            {
                "revision": 1,
                "enabled": True,
                "resources": [
                    {
                        "resource_id": "planning",
                        "inventory_binding_id": "cos-browser",
                        "inventory_binding_revision": 1,
                        "native_target_id": "observed-tab",
                        "target_evidence_reference": "fixture:profile-tab",
                        "url": "https://work.example.invalid/planning",
                        "authorized_url_prefix": "https://work.example.invalid/",
                        "identity_probes": {
                            key: {"selector": f"#{key}"}
                            for key in ("account", "workspace", "role")
                        },
                        "audience_member_ids": ["member-a"],
                    }
                ],
            },
        )
    gateway = paths.instance_dir / "openclaw/runtime/openclaw.json"
    prior_raw = json.dumps(previous, indent=2).encode() + b"\n"
    _private_write(gateway, prior_raw)
    before = capture_deployment_snapshot(paths)
    readiness_path = config / "deployment-readiness.json"
    original_readiness = json.loads(readiness_path.read_bytes())
    original_readiness["deployment_binding"] = browser._browser_binding(before)
    original_readiness["acceptance"] = {
        "checked_at": "2026-08-27T15:00:00Z",
        "receipt": "unchanged-private-receipt",
    }
    readiness_bytes = json.dumps(original_readiness, indent=2).encode() + b"\n"
    _private_write(readiness_path, readiness_bytes)
    artifacts = tmp_path / "private-artifacts"
    artifacts.mkdir(mode=0o700)
    prior_path = artifacts / "previous-native.json"
    _private_write(prior_path, prior_raw)
    old_snapshot = {
        "valid": True,
        "hash": hashlib.sha256(prior_raw).hexdigest(),
        "config": previous,
    }
    approved = browser.prepare_native_patch(old_snapshot, paths, release)
    patch_path = artifacts / "approved-patch.json"
    _private_write(patch_path, json.dumps(approved).encode())
    current = prepare_browser_gateway(previous)
    if admit:
        target = (
            current["agents"]["entries"][agent]
            if agents == "entries"
            else current["agents"]["list"][0]
        )
        target["tools"]["allow"].append("browser")
    _private_write(gateway, json.dumps(current, indent=4).encode() + b"\n")
    snapshot_path = artifacts / "current-snapshot.json"
    private_file(
        snapshot_path,
        {
            "valid": True,
            "hash": hashlib.sha256(gateway.read_bytes()).hexdigest(),
            "config": {"redacted": True},
        },
    )
    arguments = {
        "previous_gateway_path": prior_path,
        "approved_patch_path": patch_path,
        "approved_patch_sha256": hashlib.sha256(patch_path.read_bytes()).hexdigest(),
        "authorization_reference": "owner-request:synthetic-browser-repair",
        "native_snapshot_path": snapshot_path,
    }
    return SimpleNamespace(
        paths=paths,
        release=release,
        source=source,
        agent=agent,
        gateway=gateway,
        previous=previous,
        prior_raw=prior_raw,
        current=current,
        readiness_path=readiness_path,
        readiness_bytes=readiness_bytes,
        before=before,
        arguments=arguments,
        old_snapshot=old_snapshot,
        artifacts=artifacts,
    )


def _reconcile(fixture, **kwargs):
    return browser.reconcile_browser_readiness(
        fixture.paths, **(fixture.arguments | kwargs)
    )


@pytest.mark.parametrize(
    "admit,agents", [(False, "entries"), (True, "entries"), (True, "list")]
)
def test_browser_readiness_repairs_actual_maintenance_boundary_and_preserves_evidence(
    tmp_path, monkeypatch, admit, agents
):
    from bootstrap.cos_channel_activation import CoSActivationError, CoSChannelActivator

    fixture = _reconciliation_fixture(tmp_path, monkeypatch, admit=admit, agents=agents)
    activator = SimpleNamespace(
        paths=fixture.paths, config_dir=fixture.readiness_path.parent
    )
    with pytest.raises(CoSActivationError, match="stale"):
        CoSChannelActivator._bound_snapshot(activator, "a" * 40)
    before_gateway = fixture.gateway.read_bytes()
    existing = {
        path: path.read_bytes()
        for path in fixture.readiness_path.parent.iterdir()
        if path != fixture.readiness_path
    }
    preflight = _reconcile(fixture)
    assert fixture.readiness_path.read_bytes() == fixture.readiness_bytes
    result = _reconcile(fixture, plan_sha256=preflight["plan_sha256"], apply=True)
    assert result["state"] == "browser_readiness_reconciled"
    assert result["native_config_written"] is False
    assert result["acceptance_evidence_refreshed"] is False
    assert fixture.gateway.read_bytes() == before_gateway
    assert all(path.read_bytes() == data for path, data in existing.items())
    current = capture_deployment_snapshot(fixture.paths)
    assert CoSChannelActivator._bound_snapshot(activator, "a" * 40) == current.public()
    expected = json.loads(fixture.readiness_bytes)
    expected["deployment_binding"]["configuration_fingerprint"] = (
        current.configuration_fingerprint
    )
    assert json.loads(fixture.readiness_path.read_bytes()) == expected
    receipt = (
        fixture.paths.app_dir / f"browser-readiness-{preflight['plan_sha256']}.json"
    )
    record = json.loads(receipt.read_bytes())
    assert (
        base64.b64decode(record["plan"]["previous_readiness_base64"])
        == fixture.readiness_bytes
    )
    assert (
        record["plan"]["authorization_reference"]
        == fixture.arguments["authorization_reference"]
    )
    assert "synthetic-private-credential" not in receipt.read_text()
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert not (fixture.paths.app_dir / browser.READINESS_JOURNAL).exists()
    monkeypatch.setattr(
        browser,
        "_write_private_at",
        lambda *args, **kwargs: pytest.fail("completed verification must not write"),
    )
    assert (
        _reconcile(fixture, plan_sha256=preflight["plan_sha256"], apply=True) == result
    )


@pytest.mark.parametrize(
    "coordinate",
    [
        "adapters.json",
        "member-mapping.json",
        "secret-references.json",
        "disposable-test-dataset.json",
        "cos-channel-operation.json",
        "cos-imsg-guard.json",
    ],
)
def test_browser_reconciliation_rejects_unrelated_complete_coordinate_drift(
    tmp_path, monkeypatch, coordinate
):
    fixture = _reconciliation_fixture(tmp_path, monkeypatch)
    path = fixture.readiness_path.parent / coordinate
    _private_write(path, json.dumps({"unrelated": "changed"}).encode())
    with pytest.raises(ValueError, match="prior complete readiness"):
        _reconcile(fixture)
    assert fixture.readiness_path.read_bytes() == fixture.readiness_bytes


@pytest.mark.parametrize(
    "mutation",
    ["routing", "credential", "member-grant", "hooks", "additional-browser-setting"],
)
def test_browser_reconciliation_rejects_unapproved_native_deltas(
    tmp_path, monkeypatch, mutation
):
    fixture = _reconciliation_fixture(tmp_path, monkeypatch)
    current = fixture.current
    if mutation == "routing":
        current["bindings"].append({"agentId": "unapproved"})
    elif mutation == "credential":
        current["gateway"]["auth"]["token"] = "different-synthetic-credential"
    elif mutation == "member-grant":
        current["agents"]["entries"]["member-agent"]["tools"]["allow"].append("browser")
    elif mutation == "hooks":
        current["plugins"]["entries"]["administrateme"]["hooks"][
            "allowPromptInjection"
        ] = False
    else:
        current["browser"]["evaluateEnabled"] = False
    _private_write(fixture.gateway, json.dumps(current).encode())
    private_file(
        fixture.arguments["native_snapshot_path"],
        {
            "valid": True,
            "hash": hashlib.sha256(fixture.gateway.read_bytes()).hexdigest(),
        },
    )
    with pytest.raises(ValueError, match="exact reviewed browser"):
        _reconcile(fixture)


@pytest.mark.parametrize(
    "mutation",
    [
        "patch-hash",
        "prior-raw",
        "base-hash",
        "current-hash",
        "invalid-native",
        "unapproved-patch",
        "blank-authority",
    ],
)
def test_browser_reconciliation_requires_exact_native_patch_and_raw_hashes(
    tmp_path, monkeypatch, mutation
):
    fixture = _reconciliation_fixture(tmp_path, monkeypatch)
    arguments = fixture.arguments
    if mutation == "patch-hash":
        arguments["approved_patch_sha256"] = "0" * 64
    elif mutation == "prior-raw":
        _private_write(arguments["previous_gateway_path"], fixture.prior_raw + b" ")
    elif mutation in ("base-hash", "unapproved-patch"):
        patch = json.loads(arguments["approved_patch_path"].read_bytes())
        if mutation == "base-hash":
            patch["baseHash"] = "0" * 64
        else:
            patch["raw"] = json.dumps({"browser": {"enabled": True}})
        private_file(arguments["approved_patch_path"], patch)
        arguments["approved_patch_sha256"] = hashlib.sha256(
            arguments["approved_patch_path"].read_bytes()
        ).hexdigest()
    elif mutation == "blank-authority":
        arguments["authorization_reference"] = " "
    else:
        snapshot = json.loads(arguments["native_snapshot_path"].read_bytes())
        snapshot["hash" if mutation == "current-hash" else "valid"] = (
            "0" * 64 if mutation == "current-hash" else False
        )
        private_file(arguments["native_snapshot_path"], snapshot)
    with pytest.raises(ValueError):
        _reconcile(fixture)
    assert fixture.readiness_path.read_bytes() == fixture.readiness_bytes


@pytest.mark.parametrize(
    "pending",
    [
        "activation/active.json",
        "cos-channel-operation/active.json",
        "disposable-test-reset-active.json",
        "bridge-readiness-transition.json",
        "member-google-readiness-transition.json",
    ],
)
def test_browser_reconciliation_refuses_pending_operations(
    tmp_path, monkeypatch, pending
):
    fixture = _reconciliation_fixture(tmp_path, monkeypatch)
    _private_write(fixture.paths.app_dir / pending, b"{}")
    with pytest.raises((ValueError, CandidateReadinessError), match="pending"):
        _reconcile(fixture)
    assert fixture.readiness_path.read_bytes() == fixture.readiness_bytes


@pytest.mark.parametrize("phase", ["journal", "readiness", "receipt", "cleanup"])
def test_browser_reconciliation_recovers_every_publication_interruption(
    tmp_path, monkeypatch, phase
):
    fixture = _reconciliation_fixture(tmp_path, monkeypatch)
    preflight = _reconcile(fixture)
    digest = preflight["plan_sha256"]
    target = {
        "journal": browser.READINESS_JOURNAL,
        "readiness": fixture.readiness_path.name,
        "receipt": f"browser-readiness-{digest}.json",
    }.get(phase)
    write = browser._write_private_at
    unlink = browser.os.unlink

    def interrupted_write(descriptor, name, payload, *, replace):
        write(descriptor, name, payload, replace=replace)
        if name == target:
            raise RuntimeError("synthetic interruption")

    def interrupted_unlink(name, **kwargs):
        if name == browser.READINESS_JOURNAL:
            raise RuntimeError("synthetic interruption")
        return unlink(name, **kwargs)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(browser, "_write_private_at", interrupted_write)
        if phase == "cleanup":
            interrupted.setattr(browser.os, "unlink", interrupted_unlink)
        with pytest.raises(RuntimeError, match="interruption"):
            _reconcile(fixture, plan_sha256=digest, apply=True)
    with pytest.raises(CandidateReadinessError, match="browser readiness transition"):
        capture_deployment_snapshot(fixture.paths)
    with pytest.raises(ValueError, match="exact pending"):
        _reconcile(fixture)
    with pytest.raises(ValueError, match="authority or deployment changed"):
        _reconcile(
            fixture,
            plan_sha256=digest,
            apply=True,
            authorization_reference="different-owner-request",
        )
    assert (
        _reconcile(fixture, plan_sha256=digest, apply=True)["state"]
        == "browser_readiness_reconciled"
    )
    assert not (fixture.paths.app_dir / browser.READINESS_JOURNAL).exists()


def test_browser_completed_receipt_cannot_replay_old_readiness(tmp_path, monkeypatch):
    fixture = _reconciliation_fixture(tmp_path, monkeypatch)
    digest = _reconcile(fixture)["plan_sha256"]
    _reconcile(fixture, plan_sha256=digest, apply=True)
    _private_write(fixture.readiness_path, fixture.readiness_bytes)
    with pytest.raises(ValueError, match="cannot replay"):
        _reconcile(fixture, plan_sha256=digest, apply=True)
    assert fixture.readiness_path.read_bytes() == fixture.readiness_bytes


@pytest.mark.parametrize(
    "mutation", ["hash", "unknown-plan-field", "malformed-plan", "original-bytes"]
)
def test_browser_retained_journal_is_hash_and_schema_bound(
    tmp_path, monkeypatch, mutation
):
    fixture = _reconciliation_fixture(tmp_path, monkeypatch)
    digest = _reconcile(fixture)["plan_sha256"]
    _reconcile(fixture, plan_sha256=digest, apply=True)
    receipt = fixture.paths.app_dir / f"browser-readiness-{digest}.json"
    record = json.loads(receipt.read_bytes())
    if mutation == "hash":
        record["plan_sha256"] = "0" * 64
    else:
        if mutation == "unknown-plan-field":
            record["plan"]["invented"] = True
        elif mutation == "malformed-plan":
            record["plan"]["source"] = []
        else:
            record["plan"]["previous_readiness_base64"] = "not-base64"
        record["plan_sha256"] = browser._digest_domain(
            browser.READINESS_TRANSITION, record["plan"]
        )
        digest = record["plan_sha256"]
        receipt = fixture.paths.app_dir / f"browser-readiness-{digest}.json"
    _private_write(receipt, json.dumps(record).encode())
    with pytest.raises(ValueError):
        _reconcile(fixture, plan_sha256=digest, apply=True)


def test_browser_reconciliation_keeps_recovery_pending_on_post_write_drift(
    tmp_path, monkeypatch
):
    fixture = _reconciliation_fixture(tmp_path, monkeypatch)
    digest = _reconcile(fixture)["plan_sha256"]
    write = browser._write_private_at
    gateway = fixture.gateway.read_bytes()

    def drift(descriptor, name, payload, *, replace):
        write(descriptor, name, payload, replace=replace)
        if name == fixture.readiness_path.name:
            _private_write(fixture.gateway, gateway + b" ")

    with monkeypatch.context() as context:
        context.setattr(browser, "_write_private_at", drift)
        with pytest.raises(ValueError, match="current native hash"):
            _reconcile(fixture, plan_sha256=digest, apply=True)
    assert (fixture.paths.app_dir / browser.READINESS_JOURNAL).exists()
    assert not (fixture.paths.app_dir / f"browser-readiness-{digest}.json").exists()
    # Recovery is exact completion, never manufacturing acceptance for the drift.
    _private_write(fixture.gateway, gateway)
    _reconcile(fixture, plan_sha256=digest, apply=True)


def test_browser_reconciliation_rejects_source_change_and_requires_plan_before_writing(
    tmp_path, monkeypatch
):
    fixture = _reconciliation_fixture(tmp_path, monkeypatch)
    digest = _reconcile(fixture)["plan_sha256"]
    with pytest.raises(ValueError, match="preflight hash"):
        _reconcile(fixture, apply=True)
    fixture.source["controller_commit"] = "c" * 40
    with pytest.raises(ValueError, match="source, authority or deployment changed"):
        _reconcile(fixture, plan_sha256=digest, apply=True)
    assert fixture.readiness_path.read_bytes() == fixture.readiness_bytes


def test_browser_reconciliation_journal_size_is_recoverable_before_apply(
    tmp_path, monkeypatch
):
    fixture = _reconciliation_fixture(tmp_path, monkeypatch)
    evidence = json.loads(fixture.readiness_bytes)
    evidence["large_existing_receipt"] = "x" * (3 * 1024 * 1024)
    _private_write(fixture.readiness_path, json.dumps(evidence).encode())
    with pytest.raises(ValueError, match="recoverable journal"):
        _reconcile(fixture)
    assert not (fixture.paths.app_dir / browser.READINESS_JOURNAL).exists()


@pytest.mark.parametrize(
    "unsafe", ["symlink", "hardlink", "mode", "directory-mode", "lock"]
)
def test_browser_reconciliation_private_paths_and_operation_lock(
    tmp_path, monkeypatch, unsafe
):
    import fcntl

    fixture = _reconciliation_fixture(tmp_path, monkeypatch)
    path = fixture.arguments["previous_gateway_path"]
    if unsafe == "symlink":
        path.unlink()
        path.symlink_to(fixture.gateway)
    elif unsafe == "hardlink":
        os.link(path, fixture.artifacts / "extra-link.json")
    elif unsafe == "mode":
        path.chmod(0o644)
    elif unsafe == "directory-mode":
        path.parent.chmod(0o755)
    else:
        descriptor = os.open(
            fixture.paths.app_dir / ".upgrade-operation.lock",
            os.O_RDWR | os.O_CREAT,
            0o600,
        )
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(
            (ValueError, CandidateReadinessError, browser.ActivationJournalError)
        ):
            _reconcile(fixture)
    finally:
        if unsafe == "lock":
            os.close(descriptor)


@pytest.mark.parametrize("agents", ["entries", "list"])
def test_future_browser_patch_retains_exact_raw_and_reconciles_additive_tool_admission(
    tmp_path, monkeypatch, agents
):
    fixture = _reconciliation_fixture(tmp_path, monkeypatch, admit=True, agents=agents)
    _private_write(fixture.gateway, fixture.prior_raw)
    snapshot = fixture.arguments["native_snapshot_path"]
    private_file(snapshot, {**fixture.old_snapshot, "config": {"redacted": True}})
    patch = fixture.artifacts / "future-patch.json"
    prior = fixture.artifacts / "future-prior.json"
    result = browser.write_patch(
        snapshot, fixture.paths, patch, previous_config_output=prior, disable=False
    )
    assert prior.read_bytes() == fixture.prior_raw
    assert fixture.gateway.read_bytes() == fixture.prior_raw
    assert fixture.readiness_path.read_bytes() == fixture.readiness_bytes
    prepared = json.loads(patch.read_bytes())
    assert prepared["baseHash"] == hashlib.sha256(fixture.prior_raw).hexdigest()
    assert "synthetic-private-credential" not in patch.read_text()
    assert (
        browser._merge_browser_patch(fixture.previous, json.loads(prepared["raw"]))
        == fixture.current
    )
    _private_write(
        fixture.gateway, json.dumps(fixture.current, indent=4).encode() + b"\n"
    )
    private_file(
        snapshot,
        {
            "valid": True,
            "hash": hashlib.sha256(fixture.gateway.read_bytes()).hexdigest(),
        },
    )
    fixture.arguments.update(
        previous_gateway_path=prior,
        approved_patch_path=patch,
        approved_patch_sha256=result["approved_patch_sha256"],
    )
    digest = _reconcile(fixture)["plan_sha256"]
    _reconcile(fixture, plan_sha256=digest, apply=True)
    assert json.loads(fixture.gateway.read_bytes()) == fixture.current


def test_future_browser_patch_refuses_stale_readiness_and_native_snapshot(
    tmp_path, monkeypatch
):
    fixture = _reconciliation_fixture(tmp_path, monkeypatch)
    output = fixture.artifacts / "future-patch.json"
    prior = fixture.artifacts / "future-prior.json"
    with pytest.raises(ValueError, match="bound readiness"):
        browser.write_patch(
            fixture.arguments["native_snapshot_path"],
            fixture.paths,
            output,
            previous_config_output=prior,
            disable=False,
        )
    _private_write(fixture.gateway, fixture.prior_raw)
    with pytest.raises(ValueError, match="current raw configuration"):
        browser.write_patch(
            fixture.arguments["native_snapshot_path"],
            fixture.paths,
            output,
            previous_config_output=prior,
            disable=False,
        )
    assert not output.exists() and not prior.exists()


@pytest.mark.parametrize("mismatch", ["host", "instance", "release"])
def test_browser_reconciliation_requires_original_host_instance_and_release(
    tmp_path, monkeypatch, mismatch
):
    from bootstrap import candidate_readiness

    fixture = _reconciliation_fixture(tmp_path, monkeypatch)
    if mismatch == "host":
        monkeypatch.setattr(candidate_readiness.uuid, "getnode", lambda: 1)
    elif mismatch == "instance":
        _private_write(
            fixture.readiness_path.parent / "instance.yaml",
            b"schema_version: 1\ntenant_id: another_synthetic_tenant\n",
        )
    else:
        release = fixture.paths.app_dir / "releases" / ("c" * 40)
        release.mkdir(mode=0o700)
        _private_write(
            fixture.paths.app_dir / "current-release.json",
            json.dumps({"commit": "c" * 40, "path": str(release)}).encode(),
        )
    with pytest.raises(ValueError, match="prior complete readiness"):
        _reconcile(fixture)


@pytest.mark.parametrize("failure", [None, "main", "python", "pointer", "source"])
def test_browser_source_selection_requires_protected_controller_and_exact_installed_source(
    tmp_path, monkeypatch, failure
):
    paths = _deployment(tmp_path)
    release = paths.app_dir / "releases" / ("a" * 40)
    controller = "b" * 40
    monkeypatch.setattr(
        browser, "reviewed_candidate_commit", lambda repository: controller
    )
    monkeypatch.setattr(
        browser,
        "_run_git",
        lambda repository, arguments: SimpleNamespace(
            stdout=(("c" * 40 if failure == "main" else controller) + "\n").encode()
        ),
    )
    monkeypatch.setattr(
        browser.sys,
        "prefix",
        str(tmp_path if failure == "python" else release / ".venv"),
    )
    if failure == "pointer":
        private_file(
            paths.app_dir / "current-release.json",
            {"commit": "a" * 40, "path": str(tmp_path)},
        )

    def verify(path, **arguments):
        assert path == release
        assert arguments["commit"] == "a" * 40
        assert arguments["retained_by_commit"] == controller
        assert arguments["require_tracked_tree"] is True
        assert arguments["allow_legacy_tree"] is False
        if failure == "source":
            raise browser.SourceIdentityError("synthetic installed source drift")
        return {"tracked_source_tree_matches": True}

    monkeypatch.setattr(browser, "verify_release_source_identity", verify)
    if failure:
        with pytest.raises((ValueError, browser.SourceIdentityError)):
            browser._browser_source_selection(paths)
    else:
        selected, proof = browser._browser_source_selection(paths)
        assert selected == release
        assert proof == {
            "controller_commit": controller,
            "installed_source": {"tracked_source_tree_matches": True},
        }


def _computer_use_fixture(tmp_path, monkeypatch, *, disable=False):
    """Reuse the real deployment-coordinate fixture with synthetic Codex policy."""
    fixture = _reconciliation_fixture(tmp_path, monkeypatch)
    previous = deepcopy(fixture.previous)
    config = {
        "codexPlugins": {"enabled": False},
        "appServer": {"sandbox": "workspace-write"},
    }
    if disable:
        config["computerUse"] = deepcopy(browser.COMPUTER_USE_CONFIG)
    previous["plugins"]["entries"]["codex"] = {"enabled": True, "config": config}
    # Existing browser settings and all service/member grants must survive.
    previous["browser"] = {"enabled": True, "allowSystemProfileImport": False}
    prior_raw = json.dumps(previous, indent=2).encode() + b"\n"
    _private_write(fixture.gateway, prior_raw)
    before = capture_deployment_snapshot(fixture.paths)
    evidence = json.loads(fixture.readiness_bytes)
    evidence["deployment_binding"] = browser._browser_binding(before)
    evidence["provider_effect_window"] = {
        "started_at": "2026-08-27T10:00:00Z",
        "ended_at": "2026-08-27T10:30:00Z",
    }
    readiness_bytes = json.dumps(evidence, indent=2).encode() + b"\n"
    _private_write(fixture.readiness_path, readiness_bytes)
    _private_write(fixture.arguments["previous_gateway_path"], prior_raw)
    old_snapshot = {
        "valid": True,
        "hash": hashlib.sha256(prior_raw).hexdigest(),
        "config": previous,
    }
    prepared = browser.prepare_computer_use_patch(old_snapshot, disable=disable)
    private_file(fixture.arguments["approved_patch_path"], prepared)
    current = deepcopy(previous)
    current_config = current["plugins"]["entries"]["codex"]["config"]
    if disable:
        del current_config["computerUse"]
    else:
        current_config["computerUse"] = deepcopy(browser.COMPUTER_USE_CONFIG)
    _private_write(fixture.gateway, json.dumps(current, indent=4).encode() + b"\n")
    private_file(
        fixture.arguments["native_snapshot_path"],
        {
            "valid": True,
            "hash": hashlib.sha256(fixture.gateway.read_bytes()).hexdigest(),
        },
    )
    fixture.arguments.update(
        approved_patch_sha256=hashlib.sha256(
            fixture.arguments["approved_patch_path"].read_bytes()
        ).hexdigest(),
        authorization_reference="owner-request:synthetic-computer-use",
        component="computer-use",
    )
    fixture.previous = previous
    fixture.prior_raw = prior_raw
    fixture.current = current
    fixture.readiness_bytes = readiness_bytes
    fixture.before = before
    fixture.old_snapshot = old_snapshot
    return fixture


@pytest.mark.parametrize("disable", [False, True])
def test_computer_use_preparation_and_reconciliation_preserve_exact_other_authority(
    tmp_path, monkeypatch, disable
):
    fixture = _computer_use_fixture(tmp_path, monkeypatch, disable=disable)
    current_raw = fixture.gateway.read_bytes()
    original_other_files = {
        path: path.read_bytes()
        for path in fixture.readiness_path.parent.iterdir()
        if path != fixture.readiness_path
    }
    # Exercise the supported file preparation; resolved/redacted config is ignored.
    _private_write(fixture.gateway, fixture.prior_raw)
    native = fixture.arguments["native_snapshot_path"]
    private_file(native, {**fixture.old_snapshot, "config": {"redacted": True}})
    patch_path = fixture.artifacts / "computer-use-patch.json"
    prior_path = fixture.artifacts / "computer-use-prior.json"
    prepared = browser.write_patch(
        native,
        fixture.paths,
        patch_path,
        previous_config_output=prior_path,
        disable=disable,
        component="computer-use",
    )
    expected = None if disable else browser.COMPUTER_USE_CONFIG
    assert json.loads(json.loads(patch_path.read_bytes())["raw"]) == {
        "plugins": {"entries": {"codex": {"config": {"computerUse": expected}}}}
    }
    assert prior_path.read_bytes() == fixture.prior_raw
    assert fixture.gateway.read_bytes() == fixture.prior_raw
    assert fixture.readiness_path.read_bytes() == fixture.readiness_bytes
    assert "synthetic-private-credential" not in patch_path.read_text()
    assert patch_path.stat().st_mode & 0o777 == 0o600
    fixture.arguments.update(
        previous_gateway_path=prior_path,
        approved_patch_path=patch_path,
        approved_patch_sha256=prepared["approved_patch_sha256"],
    )
    _private_write(fixture.gateway, current_raw)
    private_file(
        native, {"valid": True, "hash": hashlib.sha256(current_raw).hexdigest()}
    )
    digest = _reconcile(fixture)["plan_sha256"]
    assert fixture.readiness_path.read_bytes() == fixture.readiness_bytes
    result = _reconcile(fixture, plan_sha256=digest, apply=True)
    assert result["state"] == "computer_use_readiness_reconciled"
    assert result["native_config_written"] is False
    assert result["acceptance_evidence_refreshed"] is False
    assert fixture.gateway.read_bytes() == current_raw
    assert all(path.read_bytes() == raw for path, raw in original_other_files.items())
    expected_evidence = json.loads(fixture.readiness_bytes)
    expected_evidence["deployment_binding"] = browser._browser_binding(
        capture_deployment_snapshot(fixture.paths)
    )
    assert json.loads(fixture.readiness_path.read_bytes()) == expected_evidence
    receipt = fixture.paths.app_dir / f"computer-use-readiness-{digest}.json"
    record = json.loads(receipt.read_bytes())
    assert record["plan"]["contract"] == browser.COMPUTER_USE_TRANSITION
    assert digest == browser._digest_domain(
        browser.COMPUTER_USE_TRANSITION, record["plan"]
    )
    assert digest != browser._digest_domain(
        browser.READINESS_TRANSITION, record["plan"]
    )
    assert (
        base64.b64decode(record["plan"]["previous_readiness_base64"])
        == fixture.readiness_bytes
    )
    assert "synthetic-private-credential" not in receipt.read_text()
    monkeypatch.setattr(
        browser,
        "_write_private_at",
        lambda *args, **kwargs: pytest.fail("completed reconciliation must not write"),
    )
    assert _reconcile(fixture, plan_sha256=digest, apply=True) == result


@pytest.mark.parametrize(
    "mutation",
    [
        "apps",
        "sandbox",
        "member",
        "route",
        "credential",
        "browser",
        "agent-shape",
        "numeric-boolean",
    ],
)
def test_computer_use_reconciliation_refuses_every_unrelated_gateway_change(
    tmp_path, monkeypatch, mutation
):
    fixture = _computer_use_fixture(tmp_path, monkeypatch)
    current = fixture.current
    config = current["plugins"]["entries"]["codex"]["config"]
    if mutation == "apps":
        config["codexPlugins"]["enabled"] = True
    elif mutation == "sandbox":
        config["appServer"]["sandbox"] = "danger-full-access"
    elif mutation == "member":
        current["agents"]["entries"]["member-agent"]["tools"]["allow"].append("browser")
    elif mutation == "route":
        current["bindings"].append({"agentId": "unapproved"})
    elif mutation == "credential":
        current["gateway"]["auth"]["token"] = "different-synthetic-credential"
    elif mutation == "browser":
        current["browser"]["allowSystemProfileImport"] = True
    elif mutation == "numeric-boolean":
        config["computerUse"]["enabled"] = 1
    else:
        current["agents"]["list"] = [
            {"id": key, **value}
            for key, value in current["agents"].pop("entries").items()
        ]
    _private_write(fixture.gateway, json.dumps(current).encode())
    private_file(
        fixture.arguments["native_snapshot_path"],
        {
            "valid": True,
            "hash": hashlib.sha256(fixture.gateway.read_bytes()).hexdigest(),
        },
    )
    with pytest.raises(ValueError, match="unrelated Computer Use"):
        _reconcile(fixture)
    assert fixture.readiness_path.read_bytes() == fixture.readiness_bytes


@pytest.mark.parametrize("disable", [False, True])
@pytest.mark.parametrize("phase", ["journal", "readiness", "receipt", "cleanup"])
def test_computer_use_exact_component_recovers_interrupted_enable_and_disable(
    tmp_path, monkeypatch, disable, phase
):
    fixture = _computer_use_fixture(tmp_path, monkeypatch, disable=disable)
    digest = _reconcile(fixture)["plan_sha256"]
    target = {
        "journal": browser.READINESS_JOURNAL,
        "readiness": fixture.readiness_path.name,
        "receipt": f"computer-use-readiness-{digest}.json",
    }.get(phase)
    write, unlink = browser._write_private_at, browser.os.unlink

    def interrupted_write(descriptor, name, payload, *, replace):
        write(descriptor, name, payload, replace=replace)
        if name == target:
            raise RuntimeError("synthetic interruption")

    def interrupted_unlink(name, **kwargs):
        if name == browser.READINESS_JOURNAL:
            raise RuntimeError("synthetic interruption")
        return unlink(name, **kwargs)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(browser, "_write_private_at", interrupted_write)
        if phase == "cleanup":
            interrupted.setattr(browser.os, "unlink", interrupted_unlink)
        with pytest.raises(RuntimeError, match="interruption"):
            _reconcile(fixture, plan_sha256=digest, apply=True)
    with pytest.raises(CandidateReadinessError, match="readiness transition"):
        capture_deployment_snapshot(fixture.paths)
    with pytest.raises(ValueError, match="journal is invalid"):
        _reconcile(fixture, plan_sha256=digest, apply=True, component="browser")
    with pytest.raises(ValueError, match="exact pending"):
        _reconcile(fixture)
    assert (
        _reconcile(fixture, plan_sha256=digest, apply=True)["state"]
        == "computer_use_readiness_reconciled"
    )
    assert not (fixture.paths.app_dir / browser.READINESS_JOURNAL).exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-plugin",
        "disabled-plugin",
        "apps-enabled",
        "apps-absent",
        "already-configured",
        "wrong-path",
        "extra-setting",
        "numeric-boolean",
    ],
)
def test_computer_use_preparation_requires_exact_closed_account_and_feature_policy(
    tmp_path, monkeypatch, mutation
):
    fixture = _computer_use_fixture(tmp_path, monkeypatch)
    snapshot = deepcopy(fixture.old_snapshot)
    entries = snapshot["config"]["plugins"]["entries"]
    config = entries["codex"]["config"]
    disable = mutation in {"wrong-path", "extra-setting", "numeric-boolean"}
    if mutation == "missing-plugin":
        del entries["codex"]
    elif mutation == "disabled-plugin":
        entries["codex"]["enabled"] = False
    elif mutation == "apps-enabled":
        config["codexPlugins"]["enabled"] = True
    elif mutation == "apps-absent":
        del config["codexPlugins"]
    elif mutation == "already-configured":
        config["computerUse"] = {}
    else:
        config["computerUse"] = deepcopy(browser.COMPUTER_USE_CONFIG)
        if mutation == "wrong-path":
            config["computerUse"]["marketplacePath"] = (
                "/tmp/unreviewed/marketplace.json"
            )
        elif mutation == "numeric-boolean":
            config["computerUse"]["enabled"] = 1
        else:
            config["computerUse"]["autoRepairExtra"] = True
    with pytest.raises(ValueError):
        browser.prepare_computer_use_patch(snapshot, disable=disable)


@pytest.mark.parametrize(
    "mutation",
    [
        "patch-hash",
        "prior-raw",
        "current-hash",
        "unapproved-patch",
        "blank-authority",
        "cutoff",
        "grant",
    ],
)
def test_computer_use_reconciliation_keeps_hash_evidence_and_grant_boundaries(
    tmp_path, monkeypatch, mutation
):
    fixture = _computer_use_fixture(tmp_path, monkeypatch)
    if mutation == "patch-hash":
        fixture.arguments["approved_patch_sha256"] = "0" * 64
    elif mutation == "prior-raw":
        _private_write(
            fixture.arguments["previous_gateway_path"], fixture.prior_raw + b" "
        )
    elif mutation == "current-hash":
        private_file(
            fixture.arguments["native_snapshot_path"], {"valid": True, "hash": "0" * 64}
        )
    elif mutation == "unapproved-patch":
        path = fixture.arguments["approved_patch_path"]
        approved = json.loads(path.read_bytes())
        patch = json.loads(approved["raw"])
        patch["plugins"]["entries"]["codex"]["config"]["codexPlugins"] = {
            "enabled": True
        }
        approved["raw"] = json.dumps(patch)
        private_file(path, approved)
        fixture.arguments["approved_patch_sha256"] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    elif mutation == "blank-authority":
        fixture.arguments["authorization_reference"] = " "
    elif mutation == "grant":
        _private_write(
            fixture.readiness_path.parent / "cos-channel-operation.json",
            b'{"changed":true}',
        )
    else:
        digest = _reconcile(fixture)["plan_sha256"]
        evidence = json.loads(fixture.readiness_bytes)
        evidence["provider_effect_window"]["started_at"] = "2026-08-28T10:00:00Z"
        private_file(fixture.readiness_path, evidence)
        with pytest.raises(ValueError, match="source, authority or deployment changed"):
            _reconcile(fixture, plan_sha256=digest, apply=True)
        return
    with pytest.raises(ValueError):
        _reconcile(fixture)
    assert fixture.readiness_path.read_bytes() == fixture.readiness_bytes
