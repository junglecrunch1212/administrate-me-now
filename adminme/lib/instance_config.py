"""
InstanceConfig — the single source of truth for instance-directory paths.

Per SYSTEM_INVARIANTS.md §15 and DECISIONS.md §D15: no module under adminme/
hardcodes an instance-directory path literal or any subpath of it. All
instance-directory paths resolve through an InstanceConfig object populated
at service-start time from config files in the instance directory.

This module will expose (in a later prompt — likely prompt 03 alongside the
event log, since the event log is the first module that needs resolved paths):
- `InstanceConfig`: dataclass/pydantic model holding resolved paths for:
    instance_dir, event_log_path, projection_db_path, packs_dir,
    xlsx_workbooks_dir, raw_events_dir, artifacts_dir, config_dir, secrets_path
- `load_instance_config(instance_dir: Path) -> InstanceConfig`: factory
    reading the yaml config under instance_dir/config/.
- `resolve_instance_dir() -> Path`: picks from ADMINME_INSTANCE_DIR env var
    first, then defaults to the conventional instance directory at bootstrap
    time (and ONLY at bootstrap time — production code never sees the default).

Three callers use the same InstanceConfig contract:
- Tests pass an isolated tmp path.
- Production code resolves through the real config.
- The bootstrap wizard populates a fresh instance directory.

Stub for now. Prompt 03 implements.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InstanceConfig:
    """Resolved instance-directory paths. Populated by load_instance_config()."""

    instance_dir: Path
    # Later prompts add: event_log_path, projection_db_path, packs_dir, etc.


def load_instance_config(instance_dir: Path) -> InstanceConfig:
    """Load instance config from the given directory. Stub — prompt 03 implements."""
    raise NotImplementedError("Implemented in prompt 03 per SYSTEM_INVARIANTS.md §15")


def resolve_instance_dir() -> Path:
    """Resolve the active instance directory. Stub — prompt 03 implements."""
    raise NotImplementedError("Implemented in prompt 03 per SYSTEM_INVARIANTS.md §15")
