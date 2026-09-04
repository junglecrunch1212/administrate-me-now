"""Shared, tenant-neutral contracts for the macOS operations package."""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECTION_NAMES = (
    "parties",
    "interactions",
    "artifacts",
    "commitments",
    "tasks",
    "recurrences",
    "calendars",
    "places_assets_accounts",
    "money",
    "vector_search",
    "xlsx_workbooks",
    "member_knowledge",
    "lists",
    "triage",
    "context_fabric",
)

ADAPTER_CATEGORIES = (
    "communication",
    "external_state_mirror",
    "inbound_data",
    "personal_knowledge",
    "outbound_action",
)

SECTION_NAMES = (
    "environment_preflight",
    "assistant_persona",
    "household_composition",
    "profile_assignment",
    "credential_references",
    "plaid_sandbox",
    "household_seed_data",
    "channels_and_adapters",
    "observation_briefing",
    "member_bridge_enrollment",
)

BOOTSTRAP_SCHEMA_VERSION = 1
BACKUP_FORMAT_VERSION = 2
RUNTIME_LABEL = "com.administrateme.runtime"
OPENCLAW_LABEL = "com.administrateme.openclaw"


def service_account_home() -> Path:
    """Resolve the durable login home independently of a controller's HOME."""

    try:
        declared = Path(pwd.getpwuid(os.getuid()).pw_dir)
        if not declared.is_absolute():
            raise ValueError("service-account home must be absolute")
        resolved = declared.resolve(strict=True)
    except (KeyError, OSError) as exc:
        raise ValueError("service-account home is unavailable") from exc
    if resolved == Path(resolved.anchor) or not resolved.is_dir():
        raise ValueError("service-account home is unsafe")
    return resolved


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_versions(root: Path | None = None) -> dict[str, Any]:
    base = root or repository_root()
    return json.loads((base / "bootstrap" / "versions.json").read_text("utf-8"))


@dataclass(frozen=True)
class DeploymentPaths:
    """All mutable paths are derived from one explicit deployment root."""

    root: Path
    app_dir: Path
    instance_dir: Path
    log_dir: Path
    backup_dir: Path
    rollback_dir: Path
    launch_agent_dir: Path
    run_dir: Path

    @classmethod
    def from_root(cls, root: Path, *, production: bool = False) -> "DeploymentPaths":
        expanded = root.expanduser()
        if not expanded.is_absolute():
            raise ValueError("deployment root must be absolute")
        resolved = expanded.resolve()
        account_home = service_account_home() if production else None
        broad_homes = {Path.home().resolve()}
        if account_home is not None:
            broad_homes.add(account_home)
        if resolved == Path(resolved.anchor) or resolved in broad_homes:
            raise ValueError("refusing a broad deployment root")
        if production:
            assert account_home is not None
            home = account_home
            launch_agents = home / "Library" / "LaunchAgents"
            logs = home / "Library" / "Logs" / "AdministrateMe"
        else:
            launch_agents = resolved / "launchd"
            logs = resolved / "logs"
        return cls(
            root=resolved,
            app_dir=resolved / "app",
            instance_dir=resolved / "state",
            log_dir=logs,
            backup_dir=resolved / "backups",
            rollback_dir=resolved / "rollback",
            launch_agent_dir=launch_agents,
            run_dir=resolved / "run",
        )

    @classmethod
    def default(cls) -> "DeploymentPaths":
        root = (
            service_account_home()
            / "Library"
            / "Application Support"
            / "AdministrateMe"
        )
        return cls.from_root(root, production=True)

    def create(self) -> None:
        for path in (
            self.root,
            self.app_dir,
            self.instance_dir,
            self.log_dir,
            self.backup_dir,
            self.rollback_dir,
            self.launch_agent_dir,
            self.run_dir,
        ):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                path.chmod(0o700)
            except OSError:
                pass

    @property
    def state_path(self) -> Path:
        return self.instance_dir / "config" / "bootstrap-state.json.enc"

    @property
    def public_config_path(self) -> Path:
        return self.instance_dir / "config" / "deployment.json"

    @property
    def service_state_path(self) -> Path:
        return self.run_dir / "services.json"

    def keychain_account(self) -> str:
        digest = hashlib.sha256(str(self.root).encode()).hexdigest()[:16]
        return f"instance-{digest}"


def atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """Write one file atomically without following an existing symlink."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise RuntimeError(f"refusing to replace symlink: {path}")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def ensure_within(path: Path, parent: Path) -> Path:
    resolved = path.resolve()
    base = parent.resolve()
    if resolved != base and base not in resolved.parents:
        raise RuntimeError(f"path escapes deployment root: {resolved}")
    return resolved


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
