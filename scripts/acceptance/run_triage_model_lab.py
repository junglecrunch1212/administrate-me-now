"""Run one synthetic capture with a fresh, private, pinned lab Gateway.

The dedicated model key is supplied only to this hosted job. This launcher
never reads an installed instance, its configuration, or household credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL = "openai/gpt-5.6-sol"


def validate_host(*, require_model_authorization: bool = True) -> str:
    """Refuse local/self-hosted execution before starting any process."""
    if (
        platform.system() != "Linux"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("ADMINME_TEST_MODE")
    ):
        raise ValueError("actual model lab requires explicit hosted Linux execution")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
    ).strip()
    if require_model_authorization and (
        os.environ.get("ADMINME_MODEL_LAB_APPROVED_SHA") != commit
    ):
        raise ValueError("model lab revision has not been explicitly approved")
    if not os.environ.get("RUNNER_TEMP") or (
        require_model_authorization and not os.environ.get("OPENAI_API_KEY")
    ):
        raise ValueError("dedicated model key and disposable runner directory required")
    return commit


def gateway_config(state: Path, token: str) -> dict[str, Any]:
    """A single model route and tool, with no channels or background jobs."""
    return {
        "gateway": {
            "mode": "local",
            "bind": "loopback",
            "port": 18789,
            "auth": {"mode": "token", "token": token},
        },
        "agents": {
            "defaults": {
                "model": {"primary": MODEL},
                "workspace": str(state / "workspace"),
            },
            "list": [{"id": "main", "tools": {"allow": ["llm-task"]}}],
        },
        "plugins": {
            "allow": ["llm-task"],
            "entries": {
                "llm-task": {
                    "enabled": True,
                    "config": {
                        "defaultProvider": "openai",
                        "defaultModel": "gpt-5.6-sol",
                        "maxTokens": 6000,
                        "timeoutMs": 120000,
                    },
                }
            },
        },
        "tools": {"profile": "minimal", "alsoAllow": ["llm-task"]},
        "cron": {"enabled": False},
        "browser": {"enabled": False},
        "channels": {},
    }


def wait_for_gateway(process: subprocess.Popen[bytes]) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("isolated Gateway exited before readiness")
        try:
            with opener.open("http://127.0.0.1:18789/healthz", timeout=1) as reply:
                if reply.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.25)
    raise TimeoutError("isolated Gateway did not become ready within 60 seconds")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-gateway-only", action="store_true")
    args = parser.parse_args()
    commit = validate_host(require_model_authorization=not args.check_gateway_only)
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    if not source.is_relative_to(runner_temp) or not output.is_relative_to(runner_temp):
        raise ValueError("lab source and evidence must be inside the disposable runner")
    if output.exists():
        raise ValueError("refusing to overwrite a previous model capture")
    pin = json.loads((REPO_ROOT / "bootstrap/versions.json").read_text())["openclaw"]
    if source.name != "openclaw-" + pin["commit"]:
        raise ValueError("Gateway source does not match the reviewed commit")
    archive = runner_temp / "openclaw-source.tar.gz"
    if hashlib.sha256(archive.read_bytes()).hexdigest() != pin["source_archive_sha256"]:
        raise ValueError("Gateway source archive authentication failed")
    entrypoint = source / "openclaw.mjs"
    if not entrypoint.is_file():
        raise ValueError("pinned Gateway entrypoint is missing")
    with tempfile.TemporaryDirectory(prefix="triage-gateway-", dir=runner_temp) as name:
        state = Path(name)
        token = secrets.token_urlsafe(40)
        config_path = state / "openclaw.json"
        config_path.write_text(json.dumps(gateway_config(state, token)))
        config_path.chmod(0o600)
        env = {
            **os.environ,
            "OPENCLAW_STATE_DIR": str(state),
            "OPENCLAW_CONFIG_PATH": str(config_path),
            "OPENCLAW_GATEWAY_TOKEN": token,
        }
        if args.check_gateway_only:
            env.pop("OPENAI_API_KEY", None)
        command = ["node", str(entrypoint)]
        evidence: dict[str, Any] = {
            "commit": commit,
            "configured_model": MODEL,
            "openclaw_commit": pin["commit"],
            "openclaw_archive_sha256": pin["source_archive_sha256"],
            "runtime_registered_llm_task": False,
            "gateway_only": args.check_gateway_only,
            "doctor_invocation_limit": 0 if args.check_gateway_only else 1,
            "source_classifier_attempt_limit": 0 if args.check_gateway_only else 57,
        }
        exit_code = 1
        phase = "configuration_validation"
        log_path = state / "gateway.log"
        try:
            # No model credentials are written to a file or command-line argument.
            subprocess.run(
                [*command, "config", "validate"],
                env=env,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            phase = "tool_registration"
            inspected = subprocess.run(
                [*command, "plugins", "inspect", "llm-task", "--runtime", "--json"],
                env=env,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            plugin = json.loads(inspected.stdout).get("plugin", {})
            if not (
                plugin.get("status") == "loaded"
                and plugin.get("activated") is True
                and plugin.get("origin") == "bundled"
                and plugin.get("packageName") == "@openclaw/llm-task"
                and plugin.get("packageVersion") == pin["version"]
                and "llm-task" in plugin.get("toolNames", [])
            ):
                raise RuntimeError("the pinned bundled llm-task did not register")
            evidence["runtime_registered_llm_task"] = True
            with log_path.open("wb") as log:
                phase = "gateway_readiness"
                process = subprocess.Popen(
                    [*command, "gateway", "run"],
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                try:
                    wait_for_gateway(process)
                    if args.check_gateway_only:
                        evidence["model_calls"] = 0
                        exit_code = 0
                    else:
                        from bootstrap.openclaw_tools import probe_required_llm_tool

                        phase = "model_probe"
                        evidence["doctor_probe"] = probe_required_llm_tool(token)
                        if not evidence["doctor_probe"]["ok"]:
                            raise RuntimeError(
                                "isolated model probe failed; capture not run"
                            )
                        phase = "source_capture"
                        result = subprocess.run(
                            [
                                sys.executable,
                                "-m",
                                "tools.triage_model_lab",
                                "--output",
                                str(output),
                            ],
                            cwd=REPO_ROOT,
                            env=env,
                            check=False,
                            timeout=19 * 420 + 120,
                        )
                        exit_code = result.returncode
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
        except Exception as exc:
            # Retain the failure even if no classifier capture was started.
            # Diagnostics come only from the fresh synthetic Gateway.
            detail = str(exc)
            if isinstance(exc, subprocess.CalledProcessError):
                detail += "\n" + str(exc.stdout or "") + "\n" + str(exc.stderr or "")
            if log_path.exists():
                detail += "\n" + log_path.read_text(errors="replace")[-8000:]
            for secret in (token, os.environ.get("OPENAI_API_KEY", "")):
                if secret:
                    detail = detail.replace(secret, "[REDACTED]")
            evidence["failure"] = {
                "phase": phase,
                "type": type(exc).__name__,
                "detail": detail[-12000:],
            }
        report = json.loads(output.read_text()) if output.exists() else {}
        report["gateway_evidence"] = evidence
        report["capture_exit_code"] = exit_code
        output.write_text(json.dumps(report, indent=2))
        output.chmod(0o600)
        print(json.dumps({"commit": commit, "capture_exit_code": exit_code}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
