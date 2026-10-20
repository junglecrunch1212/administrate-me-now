"""Exact wrapper-only configuration transition between reviewed app releases."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from bootstrap.cos_imessage import validate_prepared_channel, verify_guard_files

CONTRACT = "cos-guard-release-rebind-v1"
WRAPPER = Path("openclaw/administrateme/bin/cos-imsg-guard.mjs")
COORDINATES = {
    "guard": Path("config/cos-imsg-guard.json"),
    "gateway": Path("openclaw/runtime/openclaw.json"),
}


def _bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _snapshot(payload: bytes) -> dict[str, str]:
    return {
        "bytes": base64.b64encode(payload).decode(),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def snapshot_bytes(value: Any) -> bytes:
    if not isinstance(value, dict) or set(value) != {"bytes", "sha256"}:
        raise ValueError("CoS guard configuration snapshot is invalid")
    payload = base64.b64decode(value["bytes"], validate=True)
    if hashlib.sha256(payload).hexdigest() != value["sha256"]:
        raise ValueError("CoS guard configuration snapshot changed")
    if not isinstance(json.loads(payload), dict):
        raise ValueError("CoS guard configuration is not an object")
    return payload


def validate_rebind(value: Any, *, source_release: Path, target_release: Path) -> None:
    """Validate journal bytes without trusting a file path from the journal."""
    if (
        not isinstance(value, dict)
        or set(value)
        != {"contract", "source_release", "target_release", "guard", "gateway"}
        or value["contract"] != CONTRACT
        or value["source_release"] != str(source_release)
        or value["target_release"] != str(target_release)
        or not source_release.is_absolute()
        or not target_release.is_absolute()
    ):
        raise ValueError("CoS guard release transition is invalid")
    decoded: dict[str, dict[str, Any]] = {}
    for name in COORDINATES:
        if not isinstance(value[name], dict) or set(value[name]) != {"before", "after"}:
            raise ValueError("CoS guard release transition snapshots are invalid")
        decoded[name] = {
            side: json.loads(snapshot_bytes(value[name][side]))
            for side in ("before", "after")
        }
    prior, selected = decoded["guard"]["before"], decoded["guard"]["after"]
    if (
        prior.get("wrapper_path") != str(source_release / WRAPPER)
        or selected.get("wrapper_path") != str(target_release / WRAPPER)
        or re.fullmatch(r"[0-9a-f]{64}", str(selected.get("wrapper_sha256"))) is None
    ):
        raise ValueError("CoS guard release transition wrapper is invalid")
    expected = {
        **prior,
        "wrapper_path": selected["wrapper_path"],
        "wrapper_sha256": selected["wrapper_sha256"],
    }
    if selected != expected:
        raise ValueError("CoS guard release transition broadens provider configuration")
    gateway = copy.deepcopy(decoded["gateway"]["before"])
    account = gateway["channels"]["imessage"]["accounts"][prior["account_id"]]
    if account["cliPath"] != prior["wrapper_path"]:
        raise ValueError("CoS guard release transition source routing changed")
    account["cliPath"] = selected["wrapper_path"]
    if gateway != decoded["gateway"]["after"]:
        raise ValueError(
            "CoS guard release transition changes unrelated gateway fields"
        )


def prepare_rebind(
    *,
    guard_payload: bytes,
    gateway_payload: bytes,
    adapter_config: dict[str, Any],
    source_release: Path,
    target_release: Path,
    historical_target_guard: bytes | None = None,
) -> dict[str, Any]:
    """Read immutable executables and prepare a transition without any writes."""
    guard = json.loads(guard_payload)
    gateway = json.loads(gateway_payload)
    validate_prepared_channel(gateway, guard, adapter_config=adapter_config)
    verify_guard_files(guard, reviewed_release=source_release)
    selected_guard = {
        **guard,
        "wrapper_path": str(target_release / WRAPPER),
        "wrapper_sha256": hashlib.sha256(
            (target_release / WRAPPER).read_bytes()
        ).hexdigest(),
    }
    selected_gateway = copy.deepcopy(gateway)
    selected_gateway["channels"]["imessage"]["accounts"][guard["account_id"]][
        "cliPath"
    ] = selected_guard["wrapper_path"]
    verify_guard_files(selected_guard, reviewed_release=target_release)
    validate_prepared_channel(
        selected_gateway, selected_guard, adapter_config=adapter_config
    )
    selected_guard_bytes = _bytes(selected_guard)
    # A rollback keeps the original guard bytes, including their formatting,
    # only when they represent exactly the same narrow transition.
    if historical_target_guard is not None:
        if json.loads(historical_target_guard) != selected_guard:
            raise ValueError("CoS rollback guard differs from its reviewed predecessor")
        selected_guard_bytes = historical_target_guard
    result = {
        "contract": CONTRACT,
        "source_release": str(source_release),
        "target_release": str(target_release),
        "guard": {
            "before": _snapshot(guard_payload),
            "after": _snapshot(selected_guard_bytes),
        },
        "gateway": {
            "before": _snapshot(gateway_payload),
            "after": _snapshot(_bytes(selected_gateway)),
        },
    }
    validate_rebind(
        result, source_release=source_release, target_release=target_release
    )
    return result
