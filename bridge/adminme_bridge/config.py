"""Bridge daemon configuration.

Reads ``~/.adminme-bridge/config.yaml`` (written by bootstrap §10
mini-wizard, 16-§10). Schema v1 fields: ``member_id``,
``central_endpoint``, ``tailscale_identity``, ``packs_dir`` (optional),
``log_level`` (optional). ``extra="forbid"`` per [D7]."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


LogLevel = Literal["debug", "info", "warning", "error"]


class BridgeConfig(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    central_endpoint: str = Field(min_length=1)
    tailscale_identity: str = Field(min_length=1)
    packs_dir: str = "~/.adminme-bridge/packs"
    log_level: LogLevel = "info"

    def resolved_packs_dir(self) -> Path:
        return Path(self.packs_dir).expanduser()


class BridgeConfigError(RuntimeError):
    """Bridge config missing / malformed / failed validation."""


def load_config(path: Path) -> BridgeConfig:
    if not path.exists():
        raise BridgeConfigError(f"bridge config not found at {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise BridgeConfigError(f"failed to read bridge config at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise BridgeConfigError(
            f"bridge config at {path} did not parse to a mapping (got {type(data).__name__})"
        )
    try:
        return BridgeConfig.model_validate(data)
    except Exception as exc:
        raise BridgeConfigError(f"bridge config at {path} failed validation: {exc}") from exc


__all__ = ["BridgeConfig", "BridgeConfigError", "LogLevel", "load_config"]
