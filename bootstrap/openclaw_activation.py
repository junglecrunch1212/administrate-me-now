"""Server-side, secret-safe activation of the pinned OpenClaw provider boundary."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from adminme.openclaw_topology import (
    binding_fingerprints,
    inspect_runtime_topology,
    member_agent_id,
)
from adminme.deployment_readiness import REQUIRED_DM_SCOPE

from bootstrap.contracts import (
    DeploymentPaths,
    atomic_write,
    load_versions,
    repository_root,
)
from bootstrap.openclaw_cron import OpenClawCronReconciler
from bootstrap.openclaw_install import resolve_reviewed_openclaw
from bootstrap.secrets import secret_store


def activation_path(paths: DeploymentPaths) -> Path:
    return paths.instance_dir / "config" / "openclaw-activation.json"


def model_provider_configured(paths: DeploymentPaths) -> bool:
    path = activation_path(paths)
    if not path.is_file() or path.is_symlink():
        return False
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        data.get("schema_version") == 1
        and data.get("configured") is True
        and data.get("openclaw_version") == load_versions()["openclaw"]["version"]
        and data.get("probe") == "passed"
        and data.get("workspace_configured") is True
        and data.get("cron_reconciled") is True
    )


def reconcile_member_topology(paths: DeploymentPaths) -> dict[str, Any]:
    """Write exact per-member agents, actor bindings, and DM isolation."""

    adapter_config = json.loads(
        (paths.instance_dir / "config" / "adapters.json").read_text("utf-8")
    )
    actor_identities = adapter_config.get("openclaw_actor_identities") or {}
    chat_members = frozenset(adapter_config.get("member_chat_enabled_members") or [])
    if not isinstance(actor_identities, dict) or not all(
        isinstance(route, str) and isinstance(member, str)
        for route, member in actor_identities.items()
    ):
        raise RuntimeError("OpenClaw actor identity map is invalid")
    members = sorted(set(actor_identities.values()) | set(chat_members))
    config_path = paths.instance_dir / "openclaw" / "runtime" / "openclaw.json"
    config = json.loads(config_path.read_text("utf-8")) if config_path.is_file() else {}
    if not isinstance(config, dict):
        raise RuntimeError("OpenClaw configuration root is invalid")
    agents = config.setdefault("agents", {})
    existing_agents = agents.get("list") or []
    if not isinstance(existing_agents, list):
        raise RuntimeError("OpenClaw agents.list is invalid")
    retained_agents = [
        row
        for row in existing_agents
        if not (
            isinstance(row, dict) and str(row.get("id") or "").startswith("adminme-")
        )
    ]
    workspace = str(paths.instance_dir / "openclaw" / "workspace")
    member_agents: list[dict[str, Any]] = [
        {"id": member_agent_id(member), "workspace": workspace} for member in members
    ]
    if not retained_agents and member_agents:
        member_agents[0]["default"] = True
    agents["list"] = retained_agents + member_agents

    existing_bindings = config.get("bindings") or []
    if not isinstance(existing_bindings, list):
        raise RuntimeError("OpenClaw bindings are invalid")
    retained_bindings = [
        row
        for row in existing_bindings
        if not (
            isinstance(row, dict)
            and str(row.get("agentId") or "").startswith("adminme-")
        )
    ]
    member_bindings: list[dict[str, Any]] = []
    account_owners: dict[tuple[str, str], str] = {}
    for route, member in sorted(actor_identities.items()):
        parts = route.split("|", 2)
        if len(parts) != 3 or not all(parts):
            raise RuntimeError("OpenClaw actor route must be channel|account|actor")
        channel, account, actor = parts
        owner = account_owners.setdefault(
            (channel.casefold(), account.casefold()), member
        )
        if owner != member:
            raise RuntimeError("OpenClaw channel account is shared across members")
        member_bindings.append(
            {
                "agentId": member_agent_id(member),
                "match": {
                    "channel": channel,
                    "accountId": account,
                    "peer": {"kind": "direct", "id": actor},
                },
            }
        )
    config["bindings"] = retained_bindings + member_bindings
    config.setdefault("session", {})["dmScope"] = "per-account-channel-peer"

    plugins = config.setdefault("plugins", {})
    entries = plugins.setdefault("entries", {})
    plugin = entries.setdefault("administrateme", {})
    plugin_config = plugin.setdefault("config", {})
    plugin_config.update(
        {
            "dmScope": REQUIRED_DM_SCOPE,
            "memberAgentIds": [member_agent_id(member) for member in members],
            "bindingFingerprints": list(
                binding_fingerprints(actor_identities, chat_members)
            ),
        }
    )
    atomic_write(
        config_path,
        json.dumps(config, indent=2, sort_keys=True).encode("utf-8"),
        mode=0o600,
    )
    inspected = inspect_runtime_topology(
        config_path,
        actor_identities=actor_identities,
        member_chat_enabled_members=chat_members,
    )
    if inspected.get("state") != "healthy":
        raise RuntimeError("OpenClaw member topology did not reconcile exactly")
    return {
        "member_agent_count": len(member_agents),
        "binding_count": len(member_bindings) + len(chat_members),
        "dm_scope": REQUIRED_DM_SCOPE,
        "content_exposed": False,
    }


def activate(
    paths: DeploymentPaths,
    *,
    root: Path | None = None,
    test_mode: bool = False,
) -> dict[str, Any]:
    """Run OpenClaw-owned interactive auth, probe it, then write metadata only."""

    executable = resolve_reviewed_openclaw(paths, test_mode=test_mode)
    environment = os.environ.copy()
    environment["OPENCLAW_STATE_DIR"] = str(paths.instance_dir / "openclaw" / "runtime")
    environment["OPENCLAW_CONFIG_PATH"] = str(
        paths.instance_dir / "openclaw" / "runtime" / "openclaw.json"
    )
    expected = load_versions()["openclaw"]["version"]
    version = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout
    if expected not in version:
        raise RuntimeError(
            "OpenClaw version is outside the tested compatibility contract"
        )

    print("OpenClaw will request provider credentials in this server terminal.")
    print(
        "Never enter them in the member portal or an AdministrateMe-owned form; a provider-owned OAuth page is allowed."
    )
    subprocess.run(
        [executable, "configure", "--section", "model", "--section", "channels"],
        check=True,
        env=environment,
    )
    workspace = paths.instance_dir / "openclaw" / "workspace"
    required_workspace_files = ("AGENTS.md", "SOUL.md", "IDENTITY.md", "USER.md")
    if not all((workspace / name).is_file() for name in required_workspace_files):
        raise RuntimeError(
            "the compiled AdministrateMe OpenClaw workspace is incomplete; rerun bootstrap before provider activation"
        )
    subprocess.run(
        [
            executable,
            "config",
            "set",
            "agents.defaults.workspace",
            str(workspace),
        ],
        check=True,
        env=environment,
    )
    topology = reconcile_member_topology(paths)
    subprocess.run([executable, "config", "validate"], check=True, env=environment)
    subprocess.run(
        [executable, "models", "status", "--probe"], check=True, env=environment
    )

    references = json.loads(
        (paths.instance_dir / "config" / "secret-references.json").read_text("utf-8")
    )["references"]
    gateway_token = (
        secret_store(paths, test_mode=test_mode)
        .get(references["openclaw_shared"])
        .decode("ascii")
    )
    # The AdministrateMe-owned LaunchAgent intentionally does not use OpenClaw's
    # generic service label.  Authenticate the RPC probe explicitly so the CLI
    # validates the running managed gateway instead of rejecting its URL.
    environment["OPENCLAW_GATEWAY_TOKEN"] = gateway_token
    subprocess.run(
        [executable, "gateway", "status", "--require-rpc"],
        check=True,
        env=environment,
    )

    instance = yaml.safe_load(
        (paths.instance_dir / "config" / "instance.yaml").read_text("utf-8")
    )
    cron = OpenClawCronReconciler(
        paths,
        root=(root or repository_root()),
        gateway_token=gateway_token,
        test_mode=test_mode,
    ).reconcile(instance["timezone"])

    marker = {
        "schema_version": 1,
        "configured": True,
        "openclaw_version": expected,
        "probe": "passed",
        "workspace_configured": True,
        "cron_reconciled": cron["status"] == "reconciled",
        "cron_jobs": len(cron["jobs"]),
        "configured_at": datetime.now(timezone.utc).isoformat(),
        "contains_credentials": False,
        "member_topology": topology,
    }
    atomic_write(
        activation_path(paths),
        json.dumps(marker, indent=2, sort_keys=True).encode("utf-8"),
        mode=0o600,
    )
    return marker


__all__ = [
    "activate",
    "activation_path",
    "model_provider_configured",
    "reconcile_member_topology",
]
