"""Narrow emergency revocation; this file cannot grant provider authority."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

FILENAME = "request-actions-control.json"


def read_control(config_dir: Path) -> dict[str, Any]:
    path = config_dir / FILENAME
    if not path.exists():
        return {
            "contract": "request-actions-control-v1",
            "disabled": False,
            "revoked_before": None,
        }
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            raise ValueError("unsafe control file")
        value = json.loads(path.read_text())
        if (
            set(value) != {"contract", "disabled", "revoked_before"}
            or value["contract"] != "request-actions-control-v1"
            or type(value["disabled"]) is not bool
        ):
            raise ValueError("invalid recovery control")
        if value["revoked_before"] is not None:
            cutoff = datetime.fromisoformat(value["revoked_before"])
            if cutoff.tzinfo is None:
                raise ValueError("revocation requires an aware time")
        return value
    except (OSError, ValueError, TypeError):
        return {
            "contract": "request-actions-control-v1",
            "disabled": True,
            "revoked_before": datetime.now(timezone.utc).isoformat(),
        }


def request_actions_allowed(runtime: Any, *, issued_at: str | None = None) -> bool:
    control = read_control(runtime.settings.instance.config_dir)
    if control["disabled"]:
        return False
    if issued_at is not None and control["revoked_before"] is not None:
        try:
            return datetime.fromisoformat(issued_at) > datetime.fromisoformat(
                control["revoked_before"]
            )
        except (ValueError, TypeError):
            return False
    return True
