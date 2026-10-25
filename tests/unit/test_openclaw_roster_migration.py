from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from adminme.openclaw_roster_migration import (
    CONTRACT,
    canonical,
    classify,
    digest,
    retained_fallbacks,
    validate_record,
)
from adminme.openclaw_topology import (
    binding_fingerprints,
    inspect_runtime_topology,
    member_agent_id,
    member_workspace_path,
)
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.openclaw_activation import reconcile_member_topology


def fixture(
    tmp_path: Path,
) -> tuple[DeploymentPaths, dict[str, Any], dict[str, Any], dict[str, Any]]:
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    paths.create()
    root = paths.instance_dir / "openclaw"
    runtime = root / "runtime"
    runtime.mkdir(parents=True, mode=0o700)
    member = member_agent_id("member-a")
    workspace = member_workspace_path(root, "member-a")
    workspace.mkdir(parents=True, mode=0o700)
    actor = {"imessage|fixture-account|fixture-actor": "member-a"}
    peer = {
        "agentId": member,
        "match": {
            "channel": "imessage",
            "accountId": "fixture-account",
            "peer": {"kind": "direct", "id": "fixture-actor"},
        },
    }
    before = {
        "agents": {
            "list": [
                {
                    "id": member,
                    "default": True,
                    "workspace": str(workspace),
                    "model": "retained-model",
                    "tools": {"allow": ["typed-tool"]},
                },
                {
                    "id": "operator",
                    "default": False,
                    "workspace": str(root / "operator"),
                },
            ]
        },
        "channels": {
            "imessage": {
                "enabled": True,
                "accounts": {
                    "fixture-account": {
                        "enabled": True,
                        "dmPolicy": "allowlist",
                        "allowFrom": ["fixture-actor"],
                    }
                },
            }
        },
        "bindings": [peer],
        "session": {"dmScope": "per-account-channel-peer"},
        "plugins": {
            "entries": {
                "administrateme": {
                    "config": {
                        "memberAgentIds": [member],
                        "bindingFingerprints": list(
                            binding_fingerprints(actor, frozenset())
                        ),
                    }
                }
            }
        },
    }
    after = copy.deepcopy(before)
    after["agents"] = {
        "ownership": "explicit",
        "entries": {
            row["id"]: {
                key: copy.deepcopy(value)
                for key, value in row.items()
                if key not in {"id", "default"}
            }
            for row in before["agents"]["list"]
        },
        "defaults": {"systemAgent": {"agentId": member}},
    }
    after["talk"] = {"agentId": member}
    after["bindings"].append(
        {"agentId": member, "match": {"channel": "imessage", "accountId": "*"}}
    )
    record = make_record(before, after, runtime)
    atomic_write(runtime / "openclaw.json", canonical(after), mode=0o600)
    atomic_write(root / "native-roster-migration.json", canonical(record), mode=0o600)
    atomic_write(
        paths.instance_dir / "config/adapters.json",
        canonical({"openclaw_actor_identities": actor}),
        mode=0o600,
    )
    return paths, before, after, record


def make_record(
    before: dict[str, Any], after: dict[str, Any], runtime: Path
) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "kind": "doctor",
        "source": before,
        "result": after,
        "source_sha256": digest(before),
        "result_sha256": digest(after),
        "fallbacks": classify(before, after, runtime=runtime),
        "requested_entries": None,
        "native_commit": "1" * 40,
        "process_receipt_sha256": "2" * 64,
    }


def test_native_fallback_is_retained_through_real_member_reconciler_and_readiness(
    tmp_path: Path,
) -> None:
    paths, before, after, record = fixture(tmp_path)
    config_path = paths.instance_dir / "openclaw/runtime/openclaw.json"
    assert retained_fallbacks(config_path) == record["fallbacks"]
    outcome = reconcile_member_topology(paths)
    assert outcome["member_agent_count"] == 1
    selected = json.loads(config_path.read_text())
    assert selected["bindings"] == [after["bindings"][-1], before["bindings"][0]]
    assert selected["agents"] == after["agents"]
    inspected = inspect_runtime_topology(
        config_path,
        actor_identities={"imessage|fixture-account|fixture-actor": "member-a"},
        member_chat_enabled_members=frozenset(),
    )
    assert inspected["state"] == "healthy"


@pytest.mark.parametrize(
    "change",
    [
        "wrong_owner",
        "new_channel",
        "changed_peer",
        "widened_allowlist",
        "tools",
        "workspace",
        "default_role",
        "boolean",
        "duplicate_fallback",
    ],
)
def test_native_record_cannot_explain_unrelated_authority_or_identity_changes(
    tmp_path: Path, change: str
) -> None:
    paths, before, after, _ = fixture(tmp_path)
    member = member_agent_id("member-a")
    if change == "wrong_owner":
        after["bindings"][-1]["agentId"] = "operator"
    elif change == "new_channel":
        after["bindings"][-1]["match"]["channel"] = "another-channel"
    elif change == "changed_peer":
        after["bindings"][0]["match"]["peer"]["id"] = "another-actor"
    elif change == "widened_allowlist":
        after["channels"]["imessage"]["accounts"]["fixture-account"][
            "allowFrom"
        ].append("another-actor")
    elif change == "tools":
        after["agents"]["entries"][member]["tools"]["allow"].append(
            "raw-provider-write"
        )
    elif change == "workspace":
        after["agents"]["entries"][member]["workspace"] = str(
            paths.instance_dir / "somewhere-else"
        )
    elif change == "default_role":
        after["agents"]["defaults"]["systemAgent"]["agentId"] = "operator"
    elif change == "boolean":
        before["talk"] = {"enabled": True}
        after["talk"] = {"enabled": 1, "agentId": member}
    elif change == "duplicate_fallback":
        after["bindings"].append(copy.deepcopy(after["bindings"][-1]))
    with pytest.raises(ValueError):
        classify(before, after, runtime=paths.instance_dir / "openclaw/runtime")


def test_new_fallback_without_retained_native_evidence_is_still_blocked(
    tmp_path: Path,
) -> None:
    paths, _, _, _ = fixture(tmp_path)
    (paths.instance_dir / "openclaw/native-roster-migration.json").unlink()
    config_path = paths.instance_dir / "openclaw/runtime/openclaw.json"
    inspected = inspect_runtime_topology(
        config_path,
        actor_identities={"imessage|fixture-account|fixture-actor": "member-a"},
        member_chat_enabled_members=frozenset(),
    )
    assert inspected["state"] == "blocked"
    with pytest.raises(RuntimeError, match="routing requires explicit"):
        reconcile_member_topology(paths)


def test_removed_or_duplicated_recorded_fallback_is_drift(tmp_path: Path) -> None:
    paths, _, after, _ = fixture(tmp_path)
    config_path = paths.instance_dir / "openclaw/runtime/openclaw.json"
    for bindings in (
        [after["bindings"][0]],
        after["bindings"] + [after["bindings"][-1]],
    ):
        atomic_write(
            config_path, canonical({**after, "bindings": bindings}), mode=0o600
        )
        result = inspect_runtime_topology(
            config_path,
            actor_identities={"imessage|fixture-account|fixture-actor": "member-a"},
            member_chat_enabled_members=frozenset(),
        )
        assert result["state"] == "blocked"


def test_sole_expansion_requires_exact_requested_entries_and_retained_owner(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    before = {"agents": {"entries": {"main": {"tools": {"allow": ["read"]}}}}}
    entries = {
        "main": {"tools": {"allow": ["read"]}},
        "staff": {"workspace": str(tmp_path / "staff"), "tools": {"allow": ["typed"]}},
    }
    after = {
        "agents": {
            "ownership": "explicit",
            "entries": copy.deepcopy(entries),
            "defaults": {"systemAgent": {"agentId": "main"}},
        },
        "talk": {"agentId": "main"},
    }
    after["agents"]["entries"]["main"]["workspace"] = str(runtime / "workspace")
    assert classify(before, after, runtime=runtime, requested_entries=entries) == []
    after["agents"]["entries"]["staff"]["tools"]["allow"].append("unexpected")
    with pytest.raises(ValueError, match="permissions"):
        classify(before, after, runtime=runtime, requested_entries=entries)


def test_receipt_hash_or_row_tampering_is_not_new_authority(tmp_path: Path) -> None:
    paths, _, _, record = fixture(tmp_path)
    record["fallbacks"][0]["agentId"] = "operator"
    with pytest.raises(ValueError, match="fallback rows changed"):
        validate_record(record, runtime=paths.instance_dir / "openclaw/runtime")
