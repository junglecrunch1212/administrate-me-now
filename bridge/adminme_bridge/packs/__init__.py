"""Connector-pack loader scaffolding for the bridge daemon.

Per memo 04 §3.1: bridge-side adapter packs declare ``kind: adapter`` with
one of two subkinds: ``knowledge-source`` (Cat-D) or ``state-mirror``
(Cat-B). The loader walks ``<packs_dir>/*/pack.yaml``, filters by that
discriminator, and yields :class:`PackHandle` records. 11c-i shipped the
Cat-D scaffold; 11b-ii-α-1 extends the filter to also accept Cat-B
state-mirror packs per [D19] (runtime substrate orthogonal to category)
and ``docs/10-split-memo-11b-ii-alpha.md`` §1. Concrete Cat-B packs land
in 11b-ii-α-2. Mirrors the shape of ``adminme.lib.pack_registry.parser``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import yaml


KNOWLEDGE_SOURCE_KIND = "adapter"
KNOWLEDGE_SOURCE_SUBKIND = "knowledge-source"
STATE_MIRROR_SUBKIND = "state-mirror"
_ALLOWED_SUBKINDS = frozenset({KNOWLEDGE_SOURCE_SUBKIND, STATE_MIRROR_SUBKIND})


@dataclass(frozen=True)
class PackHandle:
    pack_dir: Path
    name: str
    entrypoint: str
    config: dict


class PackLoadError(RuntimeError):
    """Pack manifest missing / malformed / failed discriminator check."""


def discover_packs(packs_dir: Path) -> Iterator[PackHandle]:
    """Yield PackHandle per bridge-runnable adapter pack found under packs_dir.

    Accepts ``kind: adapter`` packs whose ``subkind`` is either
    ``knowledge-source`` (Cat-D) or ``state-mirror`` (Cat-B per [D19] +
    ``docs/10-split-memo-11b-ii-alpha.md`` §1). Packs with any other
    ``kind``/``subkind`` are silently skipped per memo 04 §3.1.
    Malformed manifests raise PackLoadError immediately."""
    if not packs_dir.exists():
        return
    for child in sorted(packs_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest = child / "pack.yaml"
        if not manifest.exists():
            continue
        try:
            with manifest.open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise PackLoadError(f"failed to read {manifest}: {exc}") from exc
        if not isinstance(data, dict):
            raise PackLoadError(
                f"{manifest} did not parse to a mapping (got {type(data).__name__})"
            )
        if data.get("kind") != KNOWLEDGE_SOURCE_KIND or data.get("subkind") not in _ALLOWED_SUBKINDS:
            continue
        name = (data.get("pack") or {}).get("name")
        entrypoint = data.get("entrypoint")
        if not isinstance(name, str) or not name:
            raise PackLoadError(f"{manifest}: pack.name required")
        if not isinstance(entrypoint, str) or not entrypoint:
            raise PackLoadError(f"{manifest}: entrypoint required for bridge packs")
        yield PackHandle(
            pack_dir=child,
            name=name,
            entrypoint=entrypoint,
            config=data.get("config") or {},
        )


__all__ = [
    "KNOWLEDGE_SOURCE_KIND",
    "KNOWLEDGE_SOURCE_SUBKIND",
    "STATE_MIRROR_SUBKIND",
    "PackHandle",
    "PackLoadError",
    "discover_packs",
]
