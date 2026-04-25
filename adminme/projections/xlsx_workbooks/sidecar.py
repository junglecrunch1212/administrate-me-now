"""
xlsx round-trip sidecar I/O.

Per ADMINISTRATEME_BUILD.md §3.11 lines 1009 / 1015 / 1054 and
SYSTEM_INVARIANTS.md §10 invariant 2: the forward daemon writes a per-sheet
sidecar inside the same lock as the xlsx write so the reverse daemon (07c-β)
has a stable read baseline. Reverse rewrites the sidecar at the end of each
detection cycle to capture post-emit state.

Sidecar tree lives at ``InstanceConfig.xlsx_workbooks_dir.parent /
".xlsx-state"`` — a SIBLING of the workbooks dir, not a child. This is
deliberate: 07c-β's watchdog is scoped to ``xlsx_workbooks_dir`` and the
sidecar must not be inside that tree or every forward write would trigger
the reverse daemon on its own sidecar updates.

Per-sheet payload shapes:
- Bidirectional sheets store ``{"rows": [{...row keyed by header...}, ...]}``.
- Read-only sheets store ``{"content_hash": "<sha256-hex>"}`` so principal
  edits to read-only sheets are detectable for WARN logging without
  persisting full row data.

This module is pure-functional: no global state, no async. Atomic writes use
``.tmp.<pid>`` + ``os.replace`` so a watchdog or a parallel forward write
never observes partial JSON.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def sidecar_dir(xlsx_workbooks_dir: Path) -> Path:
    """Return the ``.xlsx-state/`` directory — sibling of workbooks dir."""
    return xlsx_workbooks_dir.parent / ".xlsx-state"


def sidecar_path(
    xlsx_workbooks_dir: Path, workbook_name: str, sheet_name: str
) -> Path:
    """Resolve the per-sheet sidecar JSON path."""
    return sidecar_dir(xlsx_workbooks_dir) / workbook_name / f"{sheet_name}.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, sort_keys=True, separators=(",", ":"), default=str)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return path


def write_sheet_state(
    xlsx_workbooks_dir: Path,
    workbook_name: str,
    sheet_name: str,
    rows: list[dict[str, Any]],
) -> Path:
    """Write a bidirectional sheet's row list as ``{"rows": [...]}``."""
    path = sidecar_path(xlsx_workbooks_dir, workbook_name, sheet_name)
    return _atomic_write_json(path, {"rows": list(rows)})


def hash_readonly_sheet(rows: list[list[Any]]) -> str:
    """Stable SHA-256 hex of a read-only sheet's raw row matrix."""
    blob = json.dumps(
        rows, default=str, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def write_readonly_state(
    xlsx_workbooks_dir: Path,
    workbook_name: str,
    sheet_name: str,
    rows: list[list[Any]],
) -> Path:
    """Write a read-only sheet's content hash as ``{"content_hash": "..."}``."""
    path = sidecar_path(xlsx_workbooks_dir, workbook_name, sheet_name)
    return _atomic_write_json(path, {"content_hash": hash_readonly_sheet(rows)})


def read_sheet_state(
    xlsx_workbooks_dir: Path, workbook_name: str, sheet_name: str
) -> list[dict[str, Any]] | None:
    """Read a bidirectional sheet's row list. None if missing or shape wrong."""
    path = sidecar_path(xlsx_workbooks_dir, workbook_name, sheet_name)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    rows = data.get("rows")
    if not isinstance(rows, list):
        return None
    return [r for r in rows if isinstance(r, dict)]


def read_readonly_state(
    xlsx_workbooks_dir: Path, workbook_name: str, sheet_name: str
) -> str | None:
    """Read a read-only sheet's content hash. None if missing or shape wrong."""
    path = sidecar_path(xlsx_workbooks_dir, workbook_name, sheet_name)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    h = data.get("content_hash")
    if not isinstance(h, str):
        return None
    return h


__all__ = [
    "sidecar_dir",
    "sidecar_path",
    "write_sheet_state",
    "write_readonly_state",
    "read_sheet_state",
    "read_readonly_state",
    "hash_readonly_sheet",
]
