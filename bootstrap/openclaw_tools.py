"""Configure and verify OpenClaw tools required by AdministrateMe."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from bootstrap.contracts import DeploymentPaths, load_versions
from bootstrap.openclaw_install import resolve_reviewed_openclaw


REQUIRED_LLM_TOOL = "llm-task"
REQUIRED_LLM_AGENT = "main"


def _mapping(value: Any) -> dict[str, Any]:
    """Treat malformed OpenClaw config nodes as absent, never as healthy."""

    return value if isinstance(value, dict) else {}


def _environment(paths: DeploymentPaths) -> dict[str, str]:
    environment = os.environ.copy()
    state_dir = paths.instance_dir / "openclaw" / "runtime"
    environment["OPENCLAW_STATE_DIR"] = str(state_dir)
    environment["OPENCLAW_CONFIG_PATH"] = str(state_dir / "openclaw.json")
    return environment


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
    "REQUIRED_LLM_AGENT",
    "REQUIRED_LLM_TOOL",
    "configure_required_llm_tool",
    "probe_required_llm_tool",
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
