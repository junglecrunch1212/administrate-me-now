"""Server-side, secret-safe activation of the pinned OpenClaw provider boundary."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from adminme.openclaw_topology import (
    binding_fingerprints,
    inspect_runtime_topology,
    member_agent_id,
    member_workspace_path,
)
from adminme.deployment_readiness import REQUIRED_DM_SCOPE
from adminme.service_identities import (
    cos_message_channel_for_account,
    validate_service_identity_config,
)

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
    # Actor topology needs service ownership/enrollment, not source-routing
    # scope. The runtime separately validates the full routes against household
    # Parties; never infer household Parties here from arbitrary route targets.
    service_config = adapter_config.get("service_identities")
    if isinstance(service_config, dict):
        service_config = {
            **service_config,
            "channels": [
                {**row, "routes": []} if isinstance(row, dict) else row
                for row in service_config.get("channels") or []
            ],
        }
    try:
        _, service_channels = validate_service_identity_config(
            service_config, member_ids=set(members), household_party_ids=set()
        )
    except ValueError as exc:
        raise RuntimeError("OpenClaw service channel identity is invalid") from exc
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
    openclaw_root = paths.instance_dir / "openclaw"
    template_workspace = openclaw_root / "workspace"
    member_agents: list[dict[str, Any]] = []
    for member in members:
        member_workspace = member_workspace_path(openclaw_root, member)
        if not member_workspace.exists():
            if template_workspace.is_dir() and not template_workspace.is_symlink():
                shutil.copytree(template_workspace, member_workspace, symlinks=True)
            else:
                member_workspace.mkdir(parents=True, mode=0o700)
        if not member_workspace.is_dir() or member_workspace.is_symlink():
            raise RuntimeError("OpenClaw member workspace is not a private directory")
        member_workspace.chmod(0o700)
        member_agents.append(
            {
                "id": member_agent_id(member),
                "workspace": str(member_workspace),
            }
        )
        if isinstance(adapter_config.get("cos_channel_acquisition"), dict):
            from bootstrap.cos_imessage import MEMBER_TOOLS

            member_agents[-1]["tools"] = {"allow": list(MEMBER_TOOLS)}
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
    account_routes: dict[tuple[str, str], tuple[str, str]] = {}
    for route, member in sorted(actor_identities.items()):
        parts = route.split("|", 2)
        if len(parts) != 3 or not all(parts):
            raise RuntimeError("OpenClaw actor route must be channel|account|actor")
        channel, account, actor = parts
        account_key = (channel.casefold(), account.casefold())
        original_account = account_routes.setdefault(account_key, (channel, account))
        owner = account_owners.setdefault(account_key, member)
        if original_account != (channel, account):
            raise RuntimeError(
                "OpenClaw channel account is shared across members with ambiguous casing"
            )
        if owner != member:
            service_channel = cos_message_channel_for_account(
                service_channels, transport_channel_id=channel, account_id=account
            )
            if (
                service_channel is None
                or service_channel.intake_mode != "member_submission"
            ):
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


def remove_unsupported_member_topology(paths: DeploymentPaths) -> dict[str, Any]:
    """Remove only topology rows unsupported by a retained legacy plugin."""

    config_path = paths.instance_dir / "openclaw" / "runtime" / "openclaw.json"
    if not config_path.is_file() or config_path.is_symlink():
        raise RuntimeError("OpenClaw configuration is unavailable")
    try:
        config = json.loads(config_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("OpenClaw configuration is invalid") from exc
    if not isinstance(config, dict):
        raise RuntimeError("OpenClaw configuration root is invalid")

    agents = config.get("agents") or {}
    if not isinstance(agents, dict):
        raise RuntimeError("OpenClaw agents configuration is invalid")
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
    agents["list"] = retained_agents
    config["agents"] = agents

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
    config["bindings"] = retained_bindings

    plugins = config.get("plugins") or {}
    entries = plugins.get("entries") if isinstance(plugins, dict) else None
    plugin = entries.get("administrateme") if isinstance(entries, dict) else None
    if not isinstance(plugin, dict):
        raise RuntimeError("AdministrateMe plugin configuration is invalid")
    if "config" in plugin:
        plugin_config = plugin["config"]
        if not isinstance(plugin_config, dict):
            raise RuntimeError("AdministrateMe plugin configuration is invalid")
        for key in ("bindingFingerprints", "dmScope", "memberAgentIds"):
            plugin_config.pop(key, None)

    atomic_write(
        config_path,
        json.dumps(config, indent=2, sort_keys=True).encode("utf-8"),
        mode=0o600,
    )
    return {
        "member_agent_count": 0,
        "binding_count": 0,
        "dm_scope": "legacy_plugin_unmanaged",
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
    "remove_unsupported_member_topology",
]
