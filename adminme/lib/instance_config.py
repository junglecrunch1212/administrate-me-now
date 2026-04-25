"""
InstanceConfig — the single source of truth for instance-directory paths.

Per SYSTEM_INVARIANTS.md §15 and DECISIONS.md §D15: no module under
adminme/, bootstrap/, profiles/, personas/, integrations/ hardcodes an
instance-directory path literal or any subpath of it. All instance-directory
paths resolve through an InstanceConfig object constructed at service-start
time from config files in the instance directory.

Three callers share this contract:
- Tests pass an isolated tmp path to ``load_instance_config()``.
- Production services resolve through ``resolve_instance_dir()`` →
  ``load_instance_config()`` at service-start time.
- The bootstrap wizard populates a fresh instance directory and hands the
  resulting path back to the same factory.

Prompt 02 shipped a stub. Prompt 05 fills in the real implementation because
prompt 05 is the first prompt where three projection databases need paths
that cannot be hardcoded (§15 invariant 2). Later prompts extend the dataclass
but do not change its contract.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class InstanceConfig:
    """Resolved instance-directory paths. Single source of truth per §15/D15."""

    instance_dir: Path
    tenant_id: str

    # Core event log
    event_log_path: Path
    bus_checkpoint_path: Path
    projection_checkpoint_path: Path

    # Projections directory (one .db file per projection lives under here)
    projections_dir: Path

    # Packs
    packs_dir: Path

    # Sidecars
    raw_events_dir: Path
    artifacts_dir: Path

    # Config + secrets
    config_dir: Path
    secrets_path: Path

    # xlsx workbook projections (prompt 07 uses these). The xlsx round-trip
    # state sidecar (`.xlsx-state/`) is a SIBLING of this directory, resolved
    # via ``xlsx_workbooks_dir.parent / ".xlsx-state"``. The sibling pathway
    # exists so a future xlsx watchdog scoped to ``xlsx_workbooks_dir`` cannot
    # self-trigger when the forward daemon writes the sidecar (see 07c-β).
    xlsx_workbooks_dir: Path

    def projection_db_path(self, projection_name: str) -> Path:
        """Return the SQLite path for a named projection. Does not create the file."""
        return self.projections_dir / f"{projection_name}.db"


def resolve_instance_dir() -> Path:
    """Resolve the active instance directory from ADMINME_INSTANCE_DIR.

    Raises RuntimeError if the env var is unset. Production code never
    falls back to a default path; bootstrap sets the env var for every
    service it starts (§15/D15 invariant 2).
    """
    value = os.environ.get("ADMINME_INSTANCE_DIR")
    if not value:
        raise RuntimeError(
            "ADMINME_INSTANCE_DIR is not set. "
            "Tests must pass an explicit path to load_instance_config(); "
            "production services have it set by bootstrap (see §15/D15)."
        )
    return Path(value)


def load_instance_config(instance_dir: Path) -> InstanceConfig:
    """Build an InstanceConfig from an instance directory.

    If <instance_dir>/config/instance.yaml exists, read tenant_id from it.
    Otherwise, synthesize a deterministic tenant_id from the directory name
    (test-friendly; production always has the file present).
    """
    instance_dir = Path(instance_dir)
    config_dir = instance_dir / "config"
    config_yaml = config_dir / "instance.yaml"
    if config_yaml.exists():
        with config_yaml.open() as f:
            data = yaml.safe_load(f) or {}
        tenant_id = data.get("tenant_id")
        if not tenant_id:
            raise RuntimeError(f"tenant_id missing from {config_yaml}")
    else:
        tenant_id = f"tenant-{instance_dir.name}"

    return InstanceConfig(
        instance_dir=instance_dir,
        tenant_id=tenant_id,
        event_log_path=instance_dir / "events" / "events.db",
        bus_checkpoint_path=instance_dir / "events" / "bus_checkpoints.db",
        projection_checkpoint_path=instance_dir / "projections" / "_checkpoints.db",
        projections_dir=instance_dir / "projections",
        packs_dir=instance_dir / "packs",
        raw_events_dir=instance_dir / "data" / "raw_events",
        artifacts_dir=instance_dir / "data" / "artifacts",
        config_dir=config_dir,
        secrets_path=instance_dir / "config" / "secrets.yaml.enc",
        xlsx_workbooks_dir=instance_dir / "projections" / "xlsx_workbooks",
    )
