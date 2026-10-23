"""Read a locally published helper for the exact running central release.

Publication is an operator build step, never an HTTP upload or build endpoint.
Downloads contain public program bytes only; pairing secrets remain separate.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any


CONTRACT = "administrateme-bridge-helper-download-v1"
ARCHIVE = "AdministrateMe-Bridge-Helper.zip"
MAX_BYTES = 128 * 1024 * 1024


def distribution_root(instance_dir: Path) -> Path:
    return instance_dir.parent / "app" / "bridge-helper-downloads"


def _safe(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError("Helper publication is unsafe")


def read_distribution(instance_dir: Path, commit: str) -> tuple[dict[str, Any], bytes]:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Helper release is unavailable")
    root = distribution_root(instance_dir)
    for directory in (instance_dir.parent, root.parent, root, root / commit):
        _safe(directory, directory=True)
    metadata = root / commit / "manifest.json"
    archive = root / commit / ARCHIVE
    _safe(metadata)
    _safe(archive)
    if metadata.stat().st_size > 4096 or not 0 < archive.stat().st_size <= MAX_BYTES:
        raise ValueError("Helper publication has an invalid size")
    manifest = json.loads(metadata.read_bytes())
    if not isinstance(manifest, dict) or (
        manifest.get("contract") != CONTRACT
        or manifest.get("release_commit") != commit
        or manifest.get("filename") != ARCHIVE
        or manifest.get("architecture") != "arm64"
        or manifest.get("minimum_macos") != "15.0"
    ):
        raise ValueError("Helper publication does not match this release")
    payload = archive.read_bytes()
    if (
        not 0 < len(payload) <= MAX_BYTES
        or manifest.get("size_bytes") != len(payload)
        or manifest.get("sha256") != hashlib.sha256(payload).hexdigest()
    ):
        raise ValueError("Helper publication integrity check failed")
    return manifest, payload
