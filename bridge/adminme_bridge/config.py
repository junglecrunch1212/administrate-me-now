"""Bridge daemon configuration.

Reads ``~/.adminme-bridge/config.yaml`` (written by bootstrap §10
mini-wizard, 16-§10). Schema v1 includes explicit tenant/member identity,
central endpoint, opt-in built-in/installed packs, per-pack config, durable
cursor path, and log level. ``extra="forbid"`` per [D7]."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


LogLevel = Literal["debug", "info", "warning", "error"]


class BridgeConfig(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    enrollment_id: str = Field(min_length=1)
    tenant_id: str = Field(default="tenant-default", min_length=1)
    central_endpoint: str = Field(min_length=1)
    tailscale_identity: str = Field(min_length=1)
    packs_dir: str = "~/.adminme-bridge/packs"
    log_level: LogLevel = "info"
    cursor_db_path: str = "~/.adminme-bridge/adapter-cursors.db"
    configured_packs: list[str] = Field(default_factory=list)
    enabled_packs: list[str] = Field(default_factory=list)
    pack_config: dict[str, dict[str, object]] = Field(default_factory=dict)
    command_poll_interval_s: float = Field(default=5.0, ge=1.0, le=300.0)

    def model_post_init(self, __context: object) -> None:
        if len(set(self.configured_packs)) != len(self.configured_packs):
            raise ValueError("configured_packs must be unique")
        if len(set(self.enabled_packs)) != len(self.enabled_packs):
            raise ValueError("enabled_packs must be unique")
        if not set(self.enabled_packs) <= set(self.configured_packs):
            raise ValueError("enabled_packs must be a subset of configured_packs")
        if set(self.pack_config) != set(self.configured_packs):
            raise ValueError("pack_config must exactly match configured_packs")
        from adminme.bridge_authorization import validate_configured_pack_config

        validate_configured_pack_config(
            member_id=self.member_id,
            enrollment_id=self.enrollment_id,
            configured_packs=self.configured_packs,
            pack_config=self.pack_config,
        )

    def resolved_packs_dir(self) -> Path:
        return Path(self.packs_dir).expanduser()

    def resolved_cursor_db_path(self) -> Path:
        return Path(self.cursor_db_path).expanduser()


class BridgeConfigError(RuntimeError):
    """Bridge config missing / malformed / failed validation."""


def load_config(path: Path) -> BridgeConfig:
    if not path.exists():
        raise BridgeConfigError(f"bridge config not found at {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise BridgeConfigError(
            f"failed to read bridge config at {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise BridgeConfigError(
            f"bridge config at {path} did not parse to a mapping (got {type(data).__name__})"
        )
    try:
        return BridgeConfig.model_validate(data)
    except Exception as exc:
        raise BridgeConfigError(
            f"bridge config at {path} failed validation: {exc}"
        ) from exc


__all__ = ["BridgeConfig", "BridgeConfigError", "LogLevel", "load_config"]
