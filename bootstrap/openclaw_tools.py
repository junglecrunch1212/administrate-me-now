"""Configure and verify OpenClaw tools required by AdministrateMe."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from bootstrap.contracts import DeploymentPaths, atomic_write, load_versions
from bootstrap.openclaw_activation import (
    reconcile_service_topology,
    prepare_native_roster_expansion,
    reconcile_member_topology,
    remove_unsupported_member_topology,
)
from bootstrap.openclaw_install import resolve_reviewed_openclaw


REQUIRED_LLM_TOOL = "llm-task"
REQUIRED_LLM_AGENT = "main"
ADMINISTRATEME_PLUGIN = "administrateme"
OPENCLAW_PLUGIN_COMMAND_TIMEOUT_SECONDS = 300
_MAX_OPENCLAW_CONFIG_BYTES = 16 * 1024 * 1024
_MEMBER_TOPOLOGY_CONFIG_KEYS = frozenset(
    {"bindingFingerprints", "dmScope", "memberAgentIds"}
)


def _mapping(value: Any) -> dict[str, Any]:
    """Treat malformed OpenClaw config nodes as absent, never as healthy."""

    return value if isinstance(value, dict) else {}


def _environment(paths: DeploymentPaths) -> dict[str, str]:
    environment = os.environ.copy()
    state_dir = paths.instance_dir / "openclaw" / "runtime"
    environment["OPENCLAW_STATE_DIR"] = str(state_dir)
    environment["OPENCLAW_SUPERVISOR_MODE"] = "external"
    environment["OPENCLAW_SERVICE_REPAIR_POLICY"] = "external"
    environment["OPENCLAW_CONFIG_PATH"] = str(state_dir / "openclaw.json")
    return environment


def _supports_member_topology(manifest: Path) -> bool:
    """Require an all-or-none reviewed schema for managed member topology."""

    try:
        document = json.loads(manifest.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "reviewed AdministrateMe plugin manifest is invalid"
        ) from exc
    schema = document.get("configSchema") if isinstance(document, dict) else None
    properties = schema.get("properties") if isinstance(schema, dict) else None
    property_names = set(properties) if isinstance(properties, dict) else set()
    declared = property_names & _MEMBER_TOPOLOGY_CONFIG_KEYS
    if declared and declared != _MEMBER_TOPOLOGY_CONFIG_KEYS:
        raise RuntimeError("reviewed plugin declares a partial member-topology schema")
    return declared == _MEMBER_TOPOLOGY_CONFIG_KEYS


def _openclaw_config_snapshot(
    paths: DeploymentPaths,
) -> tuple[Path, bytes, int]:
    """Read the private config without following links for exact restoration."""

    config_path = paths.instance_dir / "openclaw" / "runtime" / "openclaw.json"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(config_path, flags)
    except OSError as exc:
        raise RuntimeError("OpenClaw configuration is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or mode & 0o077
            or metadata.st_size > _MAX_OPENCLAW_CONFIG_BYTES
        ):
            raise RuntimeError("OpenClaw configuration is not a bounded private file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(_MAX_OPENCLAW_CONFIG_BYTES + 1)
        if len(payload) != metadata.st_size:
            raise RuntimeError("OpenClaw configuration changed while being captured")
    finally:
        os.close(descriptor)
    return config_path, payload, mode


def administrateme_plugin_status(
    paths: DeploymentPaths,
    release: Path,
    *,
    test_mode: bool = False,
) -> dict[str, Any]:
    """Prove that OpenClaw loaded the plugin from the selected release."""

    if test_mode:
        return {
            "ok": True,
            "discovered": True,
            "loaded": True,
            "release_root_valid": True,
            "release_path_current": True,
            "runtime_source_current": True,
        }
    candidate_plugin = release / "openclaw" / "administrateme"
    try:
        expected = candidate_plugin.resolve()
        release_root_valid = (
            not candidate_plugin.is_symlink()
            and expected.is_relative_to(release.resolve())
        )
    except (OSError, RuntimeError):
        expected = candidate_plugin.absolute()
        release_root_valid = False
    try:
        completed = subprocess.run(
            [
                resolve_reviewed_openclaw(paths),
                "plugins",
                "inspect",
                ADMINISTRATEME_PLUGIN,
                "--runtime",
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=OPENCLAW_PLUGIN_COMMAND_TIMEOUT_SECONDS,
            env=_environment(paths),
        )
        inspected = json.loads(completed.stdout) if completed.returncode == 0 else {}
    except (
        OSError,
        RuntimeError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ):
        inspected = {}
    document = _mapping(inspected)
    plugin = _mapping(document.get("plugin"))
    install = _mapping(document.get("install"))
    discovered = plugin.get("id") == ADMINISTRATEME_PLUGIN
    loaded = plugin.get("status") == "loaded" and plugin.get("activated") is True

    def exact_path(value: Any) -> bool:
        if not isinstance(value, str) or not value:
            return False
        try:
            return Path(value).resolve() == expected
        except (OSError, RuntimeError):
            return False

    source = plugin.get("source")
    try:
        runtime_source_current = isinstance(source, str) and Path(
            source
        ).resolve().is_relative_to(expected)
    except (OSError, RuntimeError):
        runtime_source_current = False
    release_path_current = exact_path(install.get("sourcePath")) and exact_path(
        install.get("installPath")
    )
    return {
        "ok": (
            discovered
            and loaded
            and release_path_current
            and runtime_source_current
            and release_root_valid
        ),
        "discovered": discovered,
        "loaded": loaded,
        "release_root_valid": release_root_valid,
        "release_path_current": release_path_current,
        "runtime_source_current": runtime_source_current,
    }


def reconcile_administrateme_plugin(
    paths: DeploymentPaths,
    release: Path,
    *,
    test_mode: bool = False,
) -> dict[str, Any]:
    """Select the reviewed plugin and its exact adapter-derived member topology."""

    if test_mode:
        return administrateme_plugin_status(paths, release, test_mode=True)
    candidate_plugin = release / "openclaw" / "administrateme"
    plugin = candidate_plugin.resolve()
    if candidate_plugin.is_symlink() or not plugin.is_relative_to(release.resolve()):
        raise RuntimeError("reviewed AdministrateMe plugin path escapes the release")
    manifest = plugin / "openclaw.plugin.json"
    entrypoint = plugin / "dist" / "index.js"
    if (
        not manifest.is_file()
        or manifest.is_symlink()
        or not entrypoint.is_file()
        or entrypoint.is_symlink()
    ):
        raise RuntimeError("reviewed AdministrateMe plugin build is incomplete")
    supports_member_topology = _supports_member_topology(manifest)
    executable = resolve_reviewed_openclaw(paths)
    environment = _environment(paths)
    config_path, original_config, original_mode = _openclaw_config_snapshot(paths)
    topology: dict[str, Any] | None = None

    def run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [executable, *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=OPENCLAW_PLUGIN_COMMAND_TIMEOUT_SECONDS,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("bounded OpenClaw plugin command timed out") from exc

    try:
        # A rollback to the preceding plugin cannot retain a setting that its
        # schema does not recognize. Preserve every unrelated config field.
        properties = (
            json.loads(manifest.read_text("utf-8"))
            .get("configSchema", {})
            .get("properties", {})
        )
        if "standingSelfReplyBindings" not in properties:
            current = json.loads(config_path.read_text("utf-8"))
            plugin_config = (
                current.get("plugins", {})
                .get("entries", {})
                .get(ADMINISTRATEME_PLUGIN, {})
                .get("config", {})
            )
            if "standingSelfReplyBindings" in plugin_config:
                plugin_config.pop("standingSelfReplyBindings")
                atomic_write(
                    config_path,
                    json.dumps(current, indent=2, sort_keys=True).encode(),
                    mode=original_mode,
                )
        if not supports_member_topology:
            # OpenClaw validates the existing entry against the incoming manifest
            # before installation. Remove only candidate-owned fields/rows first so
            # a retained predecessor can be installed and then checked again.
            topology = remove_unsupported_member_topology(paths)

        # Native uninstall removes the entire entry, including enrollment,
        # grants and explicit hook denials. Retain the schema-compatible entry
        # independently of registration metadata written by the installer.
        retained_entries = (
            json.loads(config_path.read_text("utf-8"))
            .get("plugins", {})
            .get("entries", {})
        )
        for _ in range(20):
            inspected = run(["plugins", "inspect", ADMINISTRATEME_PLUGIN, "--json"])
            if inspected.returncode:
                break
            removed = run(
                [
                    "plugins",
                    "uninstall",
                    ADMINISTRATEME_PLUGIN,
                    "--force",
                    "--keep-files",
                ]
            )
            if removed.returncode:
                raise RuntimeError(
                    "stale AdministrateMe plugin registration could not be removed"
                )
        else:
            raise RuntimeError(
                "stale AdministrateMe plugin registrations exceeded the safety bound"
            )

        installed = run(["plugins", "install", "--link", "--force", str(plugin)])
        if installed.returncode:
            raise RuntimeError("reviewed AdministrateMe plugin registration failed")
        if ADMINISTRATEME_PLUGIN in retained_entries:
            current = json.loads(config_path.read_text("utf-8"))
            current.setdefault("plugins", {}).setdefault("entries", {})[
                ADMINISTRATEME_PLUGIN
            ] = retained_entries[ADMINISTRATEME_PLUGIN]
            atomic_write(
                config_path,
                json.dumps(current, indent=2, sort_keys=True).encode(),
                mode=original_mode,
            )
        prepare_native_roster_expansion(paths, release)
        topology = (
            reconcile_member_topology(paths)
            if supports_member_topology
            else remove_unsupported_member_topology(paths)
        )
        service_topology = reconcile_service_topology(paths, release)
        validated = run(["config", "validate"])
        if validated.returncode:
            raise RuntimeError("OpenClaw rejected the reviewed plugin configuration")
        status = administrateme_plugin_status(paths, release)
        if not status["ok"]:
            raise RuntimeError(
                "OpenClaw did not load AdministrateMe from the selected release"
            )
        status["member_topology"] = topology
        status["service_topology"] = service_topology
        return status
    except Exception:
        try:
            atomic_write(config_path, original_config, mode=original_mode)
        except Exception as restoration_error:
            raise RuntimeError(
                "OpenClaw plugin reconciliation failed and exact configuration "
                "restoration also failed"
            ) from restoration_error
        raise


def required_llm_tool_status(
    paths: DeploymentPaths, *, test_mode: bool = False
) -> dict[str, Any]:
    """Return secret-free configuration and runtime registration evidence."""

    if test_mode:
        return {
            "ok": True,
            "plugin_enabled": True,
            "tool_allowed": True,
            "runtime_registered": True,
            "bundled_origin": True,
            "version_matches": True,
            "tool": REQUIRED_LLM_TOOL,
        }
    config_path = paths.instance_dir / "openclaw" / "runtime" / "openclaw.json"
    try:
        if config_path.is_symlink():
            raise RuntimeError("OpenClaw config must not be a symlink")
        config = json.loads(config_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError, RuntimeError):
        return {
            "ok": False,
            "plugin_enabled": False,
            "tool_allowed": False,
            "runtime_registered": False,
            "bundled_origin": False,
            "version_matches": False,
            "tool": REQUIRED_LLM_TOOL,
        }
    config = _mapping(config)
    plugins = _mapping(config.get("plugins"))
    entries = _mapping(plugins.get("entries"))
    plugin = _mapping(entries.get(REQUIRED_LLM_TOOL))
    plugin_enabled = plugin.get("enabled") is True
    tools = _mapping(config.get("tools"))
    allowed = tools.get("alsoAllow", [])
    tool_allowed = isinstance(allowed, list) and REQUIRED_LLM_TOOL in allowed
    runtime_registered = False
    bundled_origin = False
    version_matches = False
    if plugin_enabled and tool_allowed:
        try:
            completed = subprocess.run(
                [
                    resolve_reviewed_openclaw(paths),
                    "plugins",
                    "inspect",
                    REQUIRED_LLM_TOOL,
                    "--runtime",
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                env=_environment(paths),
            )
            inspected = (
                json.loads(completed.stdout) if completed.returncode == 0 else {}
            )
            inspected_plugin = _mapping(_mapping(inspected).get("plugin"))
            tool_names = inspected_plugin.get("toolNames", [])
            runtime_registered = (
                inspected_plugin.get("status") == "loaded"
                and inspected_plugin.get("activated") is True
                and isinstance(tool_names, list)
                and (REQUIRED_LLM_TOOL in tool_names)
            )
            bundled_origin = (
                inspected_plugin.get("origin") == "bundled"
                and inspected_plugin.get("packageName") == "@openclaw/llm-task"
            )
            version_matches = inspected_plugin.get("packageVersion") == str(
                load_versions()["openclaw"]["version"]
            )
        except (
            OSError,
            RuntimeError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
        ):
            runtime_registered = False
    return {
        "ok": (
            plugin_enabled
            and tool_allowed
            and runtime_registered
            and bundled_origin
            and version_matches
        ),
        "plugin_enabled": plugin_enabled,
        "tool_allowed": tool_allowed,
        "runtime_registered": runtime_registered,
        "bundled_origin": bundled_origin,
        "version_matches": version_matches,
        "tool": REQUIRED_LLM_TOOL,
    }


def configure_required_llm_tool(
    paths: DeploymentPaths, *, test_mode: bool = False
) -> dict[str, Any]:
    """Enable only the reviewed bundled JSON-only model tool.

    Existing tool policy entries are preserved. OpenClaw's own validated
    config command remains the writer for its configuration.
    """

    if test_mode:
        return required_llm_tool_status(paths, test_mode=True)
    config_path = paths.instance_dir / "openclaw" / "runtime" / "openclaw.json"
    if config_path.is_symlink() or not config_path.is_file():
        raise RuntimeError("OpenClaw config is missing or symlinked")
    try:
        config = json.loads(config_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("OpenClaw config is unreadable") from exc
    config = _mapping(config)
    tools = _mapping(config.get("tools"))
    existing = tools.get("alsoAllow", [])
    if not isinstance(existing, list) or not all(
        isinstance(value, str) and value for value in existing
    ):
        raise RuntimeError("OpenClaw tools.alsoAllow is not a string list")
    allow = sorted({*existing, REQUIRED_LLM_TOOL})
    executable = resolve_reviewed_openclaw(paths)
    environment = _environment(paths)
    commands = (
        [
            executable,
            "config",
            "set",
            f"plugins.entries.{REQUIRED_LLM_TOOL}.enabled",
            "true",
            "--strict-json",
        ],
        [
            executable,
            "config",
            "set",
            "tools.alsoAllow",
            json.dumps(allow, separators=(",", ":")),
            "--strict-json",
            "--replace",
        ],
        [executable, "config", "validate"],
    )
    for command in commands:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )
        if completed.returncode:
            raise RuntimeError(
                f"OpenClaw required-tool configuration failed at {command[1:3]}"
            )
    status = required_llm_tool_status(paths)
    if not status["ok"]:
        raise RuntimeError("OpenClaw llm-task did not register after configuration")
    return status


def probe_required_llm_tool(
    gateway_token: str | None,
    *,
    test_mode: bool = False,
    timeout_seconds: int = 45,
) -> dict[str, Any]:
    """Execute one bounded, credential-free JSON task through the live seam.

    Registration is insufficient evidence: the pinned host must authenticate,
    route to its configured model, execute the bundled tool, and return JSON
    that satisfies the requested schema.  Response bodies and bearer material
    are deliberately excluded from diagnostic evidence.
    """

    if test_mode:
        return {
            "ok": True,
            "state": "fixture_passed",
            "http_status": 200,
            "gateway_ok": True,
            "schema_result_ok": True,
            "credentials_in_report": False,
        }
    if not gateway_token:
        return {
            "ok": False,
            "state": "credential_unavailable",
            "http_status": None,
            "gateway_ok": False,
            "schema_result_ok": False,
            "credentials_in_report": False,
        }
    body = {
        "tool": REQUIRED_LLM_TOOL,
        "action": "json",
        "args": {
            "prompt": "Return the supplied boolean in the required JSON schema.",
            "input": {"ok": True},
            "schema": {
                "type": "object",
                "required": ["ok"],
                "properties": {"ok": {"type": "boolean"}},
                "additionalProperties": False,
            },
            "maxTokens": 32,
            "timeoutMs": 30_000,
            "temperature": 0,
        },
        "agentId": REQUIRED_LLM_AGENT,
        "sessionKey": (f"agent:{REQUIRED_LLM_AGENT}:adminme:doctor:synthetic:llm-task"),
        "dryRun": False,
    }
    request = urllib.request.Request(
        "http://127.0.0.1:18789/tools/invoke",
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {gateway_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "state": "http_error",
            "http_status": int(exc.code),
            "gateway_ok": False,
            "schema_result_ok": False,
            "credentials_in_report": False,
        }
    except (OSError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
        return {
            "ok": False,
            "state": "transport_or_response_error",
            "http_status": None,
            "gateway_ok": False,
            "schema_result_ok": False,
            "credentials_in_report": False,
        }
    gateway_ok = isinstance(payload, dict) and payload.get("ok") is True
    result = _mapping(payload.get("result")) if isinstance(payload, dict) else {}
    details = _mapping(result.get("details"))
    schema_result_ok = details.get("json") == {"ok": True}
    ok = status == 200 and gateway_ok and schema_result_ok
    return {
        "ok": ok,
        "state": "passed" if ok else "schema_or_envelope_mismatch",
        "http_status": status,
        "gateway_ok": gateway_ok,
        "schema_result_ok": schema_result_ok,
        "credentials_in_report": False,
    }


__all__ = [
    "ADMINISTRATEME_PLUGIN",
    "REQUIRED_LLM_AGENT",
    "REQUIRED_LLM_TOOL",
    "administrateme_plugin_status",
    "configure_required_llm_tool",
    "probe_required_llm_tool",
    "reconcile_administrateme_plugin",
    "required_llm_tool_status",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="administrateme-openclaw-tools")
    parser.add_argument("--instance-root", required=True, type=Path)
    args = parser.parse_args(argv)
    paths = DeploymentPaths.from_root(
        args.instance_root,
        production=os.environ.get("ADMINME_TEST_MODE") != "1",
    )
    result = configure_required_llm_tool(
        paths, test_mode=os.environ.get("ADMINME_TEST_MODE") == "1"
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
