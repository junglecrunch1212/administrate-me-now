"""Server-side, secret-safe activation of the pinned OpenClaw provider boundary."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from adminme.openclaw_topology import (
    SERVICE_PRIVATE_TOOLS,
    binding_fingerprints,
    inspect_runtime_topology,
    member_agent_id,
    member_workspace_path,
    service_agent_id,
    service_workspace_path,
)
from adminme.deployment_readiness import REQUIRED_DM_SCOPE
from adminme.service_identities import (
    COS_SERVICE_PRINCIPAL_ID,
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
from bootstrap.workstation_browser import native_service_tools


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


def prepare_existing_member_topology(
    config: dict[str, Any],
    *,
    adapter_config: dict[str, Any],
    openclaw_root: Path,
    known_members: set[str],
) -> dict[str, Any]:
    """Prepare routes for enrollment without creating or changing workspaces.

    Operational channel enrollment may add actor routes for already provisioned
    members. Missing agents, changed workspace coordinates or unprovisioned
    members fail before the activation journal or any live configuration write.
    """
    actors = adapter_config.get("openclaw_actor_identities") or {}
    chat = adapter_config.get("member_chat_enabled_members") or []
    if (
        not isinstance(actors, dict)
        or not all(
            isinstance(key, str) and isinstance(value, str) and value
            for key, value in actors.items()
        )
        or not isinstance(chat, list)
        or not all(isinstance(member, str) and member for member in chat)
    ):
        raise RuntimeError("OpenClaw prepared actor identity map is invalid")
    members = sorted(set(actors.values()) | set(chat))
    if not set(members) <= known_members:
        raise RuntimeError("OpenClaw prepared actor map names an unknown member")
    expected = {
        member_agent_id(member): str(member_workspace_path(openclaw_root, member))
        for member in members
    }
    result = copy.deepcopy(config)
    agents = (result.get("agents") or {}).get("list")
    if not isinstance(agents, list) or not all(isinstance(row, dict) for row in agents):
        raise RuntimeError("OpenClaw prepared member agents are unavailable")
    member_rows = [
        row for row in agents if str(row.get("id", "")).startswith("adminme-")
    ]
    actual = {row["id"]: row.get("workspace") for row in member_rows}
    if len(actual) != len(member_rows) or actual != expected:
        raise RuntimeError("OpenClaw prepared member workspace coordinates changed")
    for workspace in expected.values():
        path = Path(workspace)
        if path.is_symlink() or not path.is_dir() or path.resolve() != path:
            raise RuntimeError("OpenClaw prepared member workspace is unavailable")

    service_config = adapter_config.get("service_identities")
    if isinstance(service_config, dict):
        service_config = {
            **service_config,
            "channels": [
                {**row, "routes": []} if isinstance(row, dict) else row
                for row in service_config.get("channels") or []
            ],
        }
    _, channels = validate_service_identity_config(
        service_config, member_ids=set(members), household_party_ids=set()
    )
    existing_bindings = result.get("bindings") or []
    if not isinstance(existing_bindings, list) or not all(
        isinstance(row, dict) for row in existing_bindings
    ):
        raise RuntimeError("OpenClaw prepared bindings are invalid")
    bindings = [
        row
        for row in existing_bindings
        if not str(row.get("agentId", "")).startswith("adminme-")
    ]
    accounts: dict[tuple[str, str], tuple[str, str, str]] = {}
    for route, member in sorted(actors.items()):
        parts = route.split("|", 2)
        if len(parts) != 3 or not all(parts):
            raise RuntimeError("OpenClaw actor route must be channel|account|actor")
        channel, account, actor = parts
        key = (channel.casefold(), account.casefold())
        prior = accounts.setdefault(key, (channel, account, member))
        if prior[:2] != (channel, account):
            raise RuntimeError("OpenClaw prepared account casing is ambiguous")
        if prior[2] != member:
            service = cos_message_channel_for_account(
                channels, transport_channel_id=channel, account_id=account
            )
            if service is None or service.intake_mode != "member_submission":
                raise RuntimeError("OpenClaw prepared account is shared across members")
        bindings.append(
            {
                "agentId": member_agent_id(member),
                "match": {
                    "channel": channel,
                    "accountId": account,
                    "peer": {"kind": "direct", "id": actor},
                },
            }
        )
    result["bindings"] = bindings
    result.setdefault("session", {})["dmScope"] = "per-account-channel-peer"
    plugin = (
        result.setdefault("plugins", {})
        .setdefault("entries", {})
        .setdefault("administrateme", {})
    )
    plugin.setdefault("config", {}).update(
        {
            "dmScope": REQUIRED_DM_SCOPE,
            "memberAgentIds": [member_agent_id(member) for member in members],
            "bindingFingerprints": list(binding_fingerprints(actors, frozenset(chat))),
        }
    )
    return result


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
    existing_member_defaults = {
        row["id"]: row["default"]
        for row in existing_agents
        if isinstance(row, dict)
        and str(row.get("id") or "").startswith("adminme-")
        and "default" in row
    }
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
        if member_agents[-1]["id"] in existing_member_defaults:
            member_agents[-1]["default"] = existing_member_defaults[
                member_agents[-1]["id"]
            ]
        if isinstance(adapter_config.get("cos_channel_acquisition"), dict):
            from bootstrap.cos_imessage import MEMBER_TOOLS

            member_agents[-1]["tools"] = {"allow": list(MEMBER_TOOLS)}
    # A service agent must not displace the member default on the next upgrade.
    # Also repair the prior reconciler's lost flag when the only retained agent
    # is the canonical CoS and nobody has an explicit default selection.
    only_service_retained = all(
        isinstance(row, dict)
        and row.get("id") == service_agent_id(COS_SERVICE_PRINCIPAL_ID)
        for row in retained_agents
    )
    has_default = any(
        isinstance(row, dict) and row.get("default") is True
        for row in [*retained_agents, *member_agents]
    )
    if only_service_retained and member_agents and not has_default:
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


def _service_release_workspace(release: Path) -> dict[str, bytes] | None:
    plugin_root = release / "openclaw" / "administrateme"
    manifest_path = plugin_root / "openclaw.plugin.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError("service topology requires the reviewed plugin manifest")
    manifest = json.loads(manifest_path.read_text("utf-8"))
    if not set(SERVICE_PRIVATE_TOOLS) <= set(
        manifest.get("contracts", {}).get("tools", [])
    ):
        return None
    # Legacy artifacts retain their exact old workspace and rollback contract.
    filenames = manifest.get("contracts", {}).get("serviceWorkspaceFiles")
    if filenames is None:
        filenames = ["AGENTS.md"]
    elif filenames != ["AGENTS.md", "IDENTITY.md"]:
        raise RuntimeError("release service workspace declaration is invalid")
    files = {}
    for filename in filenames:
        source = plugin_root / "workspace" / "service-private" / filename
        if (
            source.is_symlink()
            or not source.is_file()
            or not source.resolve().is_relative_to(plugin_root.resolve())
        ):
            raise RuntimeError("release service workspace files are unavailable")
        content = source.read_bytes()
        if not 0 < len(content) <= 65536:
            raise RuntimeError("release service workspace files exceed their bound")
        files[filename] = content
    return files


def _prepare_service_topology(
    gateway: dict[str, Any],
    paths: DeploymentPaths,
    files: dict[str, bytes] | None,
    native_tools: list[str] | None = None,
) -> dict[str, Any]:
    config = copy.deepcopy(gateway)
    agents = config.get("agents", {}).get("list", [])
    bindings = config.get("bindings", [])
    if not isinstance(agents, list) or not isinstance(bindings, list):
        raise RuntimeError("service topology is invalid")
    expected_agent = service_agent_id(COS_SERVICE_PRINCIPAL_ID)
    retained = [
        row
        for row in agents
        if not (isinstance(row, dict) and row.get("id") == expected_agent)
    ]
    retained_bindings = [
        row
        for row in bindings
        if not (isinstance(row, dict) and row.get("agentId") == expected_agent)
    ]
    if files is not None:
        adapter_config = json.loads(
            (paths.instance_dir / "config" / "adapters.json").read_text("utf-8")
        )
        members = set((adapter_config.get("openclaw_actor_identities") or {}).values())
        members.update(adapter_config.get("member_chat_enabled_members") or [])
        service_config = copy.deepcopy(adapter_config.get("service_identities"))
        if isinstance(service_config, dict):
            for channel in service_config.get("channels") or []:
                if isinstance(channel, dict):
                    channel["routes"] = []
        principals, _ = validate_service_identity_config(
            service_config, member_ids=members, household_party_ids=set()
        )
        service = [
            principal
            for principal in principals
            if principal.service_principal_id == COS_SERVICE_PRINCIPAL_ID
            and principal.kind == "chief_of_staff"
        ]
        if len(service) != 1:
            raise RuntimeError(
                "service topology requires one configured Chief of Staff"
            )
        workspace = service_workspace_path(
            paths.instance_dir / "openclaw",
            COS_SERVICE_PRINCIPAL_ID,
            instructions=files["AGENTS.md"],
            identity=files.get("IDENTITY.md", b""),
        )
        for directory in [workspace, *list(workspace.parents)[:2]]:
            if directory.is_symlink() or (
                directory.exists()
                and (not directory.is_dir() or directory.resolve() != directory)
            ):
                raise RuntimeError("service workspace must be a private directory")
        for filename, content in files.items():
            target = workspace / filename
            if target.is_symlink() or (
                target.exists()
                and (not target.is_file() or target.read_bytes() != content)
            ):
                raise RuntimeError("immutable service workspace files changed")
        retained.append(
            {
                "id": expected_agent,
                "workspace": str(workspace),
                "tools": {"allow": [*SERVICE_PRIVATE_TOOLS, *(native_tools or [])]},
            }
        )
    config.setdefault("agents", {})["list"] = retained
    config["bindings"] = retained_bindings
    return config


def prepare_service_topology(
    gateway: dict[str, Any], paths: DeploymentPaths, release: Path
) -> dict[str, Any]:
    """Derive the exact reviewed target topology without writing any live state.

    Maintenance records this complete expected configuration before downtime.
    The installer uses the same transformation; no agent, tool or grant field
    is omitted from the later compatibility comparison.
    """
    return _prepare_service_topology(
        gateway,
        paths,
        _service_release_workspace(release),
        native_service_tools(gateway, paths, release),
    )


def reconcile_service_topology(paths: DeploymentPaths, release: Path) -> dict[str, Any]:
    """Select an immutable service profile and preserve member state/history."""
    files = _service_release_workspace(release)
    config_path = paths.instance_dir / "openclaw" / "runtime" / "openclaw.json"
    if config_path.is_symlink() or not config_path.is_file():
        raise RuntimeError("service topology requires the runtime configuration")
    previous = json.loads(config_path.read_text("utf-8"))
    native_tools = native_service_tools(previous, paths, release)
    config = _prepare_service_topology(previous, paths, files, native_tools)
    if files is not None:
        workspace = service_workspace_path(
            paths.instance_dir / "openclaw",
            COS_SERVICE_PRINCIPAL_ID,
            instructions=files["AGENTS.md"],
            identity=files.get("IDENTITY.md", b""),
        )
        for directory in reversed([workspace, *list(workspace.parents)[:2]]):
            if directory.is_symlink():
                raise RuntimeError("service workspace cannot be a symlink")
            directory.mkdir(exist_ok=True, mode=0o700)
            if not directory.is_dir() or directory.resolve() != directory:
                raise RuntimeError("service workspace must be a private directory")
            directory.chmod(0o700)
        for filename, content in files.items():
            target = workspace / filename
            if target.is_symlink() or (
                target.exists()
                and (not target.is_file() or target.read_bytes() != content)
            ):
                raise RuntimeError("immutable service workspace files changed")
            if not target.exists():
                atomic_write(target, content, mode=0o600)
    atomic_write(
        config_path, json.dumps(config, indent=2, sort_keys=True).encode(), mode=0o600
    )
    return {
        "service_agent_count": int(files is not None),
        "service_tool_count": len(SERVICE_PRIVATE_TOOLS) + len(native_tools)
        if files is not None
        else 0,
        "member_authority": False,
        "provider_authority": False,
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
    reconcile_service_topology(paths, root or repository_root())
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
    "reconcile_service_topology",
    "prepare_service_topology",
    "remove_unsupported_member_topology",
]
