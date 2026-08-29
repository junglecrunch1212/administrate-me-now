"""Read-only, secret-safe Central Host topology sampling."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

from adminme.deployment_readiness import CENTRAL_HOST_TAG
from bootstrap.contracts import OPENCLAW_LABEL, RUNTIME_LABEL, DeploymentPaths


def _run(
    command: list[str], *, timeout: int = 10
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _tailscale_status() -> dict[str, Any]:
    result = _run(["tailscale", "status", "--json"])
    if result is None or result.returncode != 0:
        return {}
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _serve_is_sole_edge() -> bool:
    result = _run(["tailscale", "serve", "status", "--json"])
    if result is None or result.returncode != 0:
        return False
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        # Older clients return a secret-free text summary.  Keep the predicate
        # exact: one HTTPS edge to the reviewed loopback target and no Funnel.
        text = result.stdout.casefold()
        funnel_enabled = re.search(
            r"\bfunnel\b\s*(?::|=)?\s*(?:on|true|enabled|yes)\b", text
        )
        return (
            text.count("https://") == 1
            and text.count("127.0.0.1:3330") == 1
            and funnel_enabled is None
        )

    def values(node: Any) -> list[str]:
        if isinstance(node, dict):
            return [item for child in node.values() for item in values(child)]
        if isinstance(node, list):
            return [item for child in node for item in values(child)]
        return [node] if isinstance(node, str) else []

    def enabled(node: Any) -> bool:
        if node is True:
            return True
        if isinstance(node, dict):
            return any(enabled(child) for child in node.values())
        if isinstance(node, list):
            return any(enabled(child) for child in node)
        return False

    def has_truthy_key(node: Any, expected: str) -> bool:
        if isinstance(node, dict):
            return any(
                (str(key).casefold() == expected and enabled(child))
                or has_truthy_key(child, expected)
                for key, child in node.items()
            )
        if isinstance(node, list):
            return any(has_truthy_key(child, expected) for child in node)
        return False

    strings = [item.casefold() for item in values(value)]
    return (
        sum("127.0.0.1:3330" in item for item in strings) == 1
        and has_truthy_key(value, "https")
        and not has_truthy_key(value, "allowfunnel")
        and not has_truthy_key(value, "funnel")
    )


def _service_account_standard() -> bool:
    result = _run(["id", "-Gn"])
    if result is None or result.returncode != 0:
        return False
    return "admin" not in result.stdout.split()


def _launchd_job_running(label: str) -> bool:
    result = _run(["launchctl", "print", f"gui/{os.getuid()}/{label}"])
    if result is None or result.returncode != 0:
        return False
    return bool(
        re.search(r"^\s*state\s*=\s*running\s*$", result.stdout, re.MULTILINE)
        and re.search(r"^\s*pid\s*=\s*[1-9][0-9]*\s*$", result.stdout, re.MULTILINE)
    )


def _process_counts() -> tuple[int, int]:
    """Count the two unique launchd jobs, not mutable command-line text."""

    return (
        int(_launchd_job_running(RUNTIME_LABEL)),
        int(_launchd_job_running(OPENCLAW_LABEL)),
    )


def sample_host_topology(
    paths: DeploymentPaths,
    *,
    member_identities: dict[str, str],
) -> dict[str, Any]:
    """Sample only topology facts; never return a login, hostname, or address."""

    status = _tailscale_status()
    raw_self_node = status.get("Self")
    self_node: dict[str, Any] = raw_self_node if isinstance(raw_self_node, dict) else {}
    raw_tags = self_node.get("Tags")
    tags = (
        [tag for tag in raw_tags if isinstance(tag, str)]
        if isinstance(raw_tags, list)
        else []
    )
    tagged = CENTRAL_HOST_TAG in tags
    login = ""
    raw_users = status.get("User")
    users: dict[str, Any] = raw_users if isinstance(raw_users, dict) else {}
    user_id = self_node.get("UserID")
    user = users.get(str(user_id)) if user_id is not None else None
    if isinstance(user, dict) and isinstance(user.get("LoginName"), str):
        login = user["LoginName"].strip()
    normalized_members = {identity.casefold() for identity in member_identities}
    member_collision = bool(login and login.casefold() in normalized_members)
    python_count, gateway_count = _process_counts()
    return {
        "contract": "central-host-topology-v1",
        "tailscale_node_online": self_node.get("Online") is True,
        "central_host_tag": CENTRAL_HOST_TAG if tagged else None,
        "central_identity_kind": (
            "infrastructure_tag" if tagged else "untagged_user_identity"
        ),
        "central_identity_collides_with_member": member_collision,
        "serve_is_sole_edge": _serve_is_sole_edge(),
        "python_runtime_count": python_count,
        "openclaw_gateway_count": gateway_count,
        "service_account_standard": _service_account_standard(),
        "content_exposed": False,
    }


def load_deployment_evidence(paths: DeploymentPaths) -> dict[str, Any]:
    path = paths.instance_dir / "config" / "deployment-readiness.json"
    if not path.is_file():
        return {}
    value = json.loads(path.read_text("utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("contract") != "deployment-readiness-v1"
    ):
        raise RuntimeError("deployment readiness evidence contract is invalid")
    return value


__all__ = ["load_deployment_evidence", "sample_host_topology"]
