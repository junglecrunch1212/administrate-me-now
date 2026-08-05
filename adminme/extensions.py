"""Versioned, data-only profile and persona extension catalog.

Profiles shape member-facing ranking and presentation. Personas shape the
instance-level OpenClaw identity. Neither extension kind may grant authority,
select a model provider, or contain tenant/member identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


ExtensionKind = Literal["profile", "persona"]
_FORBIDDEN_KEYS = {
    "authority",
    "credentials",
    "member_id",
    "model",
    "model_route",
    "permissions",
    "provider",
    "tenant_id",
}


@dataclass(frozen=True)
class ExtensionPack:
    pack_id: str
    kind: ExtensionKind
    version: str
    data: dict[str, Any]
    path: Path
    sha256: str


class ExtensionError(ValueError):
    """A pack or lifecycle transition violates the extension contract."""


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _walk_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            found.add(str(key))
            found.update(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_walk_keys(child))
    return found


class ExtensionCatalog:
    """Discover and activate data packs using an atomic lifecycle journal."""

    def __init__(self, repository_root: Path, instance_dir: Path) -> None:
        self.repository_root = Path(repository_root)
        self.instance_dir = Path(instance_dir)
        self.state_path = self.instance_dir / "config" / "extensions-state.json"
        self._packs: dict[tuple[ExtensionKind, str], ExtensionPack] = {}
        self._discover_root(self.repository_root / "packs")
        self._discover_root(self.instance_dir / "packs")

    def _discover_root(self, root: Path) -> None:
        for kind, directory in (("profile", "profiles"), ("persona", "personas")):
            pack_root = root / directory
            if not pack_root.is_dir():
                continue
            for path in sorted(pack_root.glob("*/pack.json")):
                raw = path.read_bytes()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ExtensionError(
                        f"invalid extension JSON at {path}: {exc}"
                    ) from exc
                if not isinstance(data, dict):
                    raise ExtensionError(f"extension pack must be an object: {path}")
                if data.get("kind") != kind:
                    raise ExtensionError(f"extension kind mismatch at {path}")
                pack_id = data.get("id")
                version = data.get("version")
                if not isinstance(pack_id, str) or not pack_id:
                    raise ExtensionError(f"extension id missing at {path}")
                if not isinstance(version, str) or not version:
                    raise ExtensionError(f"extension version missing at {path}")
                pack = ExtensionPack(
                    pack_id=pack_id,
                    kind=kind,  # type: ignore[arg-type]
                    version=version,
                    data=data,
                    path=path,
                    sha256=hashlib.sha256(raw).hexdigest(),
                )
                self._validate(pack)
                self._packs[(pack.kind, pack.pack_id)] = pack

    @staticmethod
    def _validate(pack: ExtensionPack) -> None:
        forbidden = _walk_keys(pack.data) & _FORBIDDEN_KEYS
        if forbidden:
            raise ExtensionError(
                f"{pack.kind} {pack.pack_id} contains forbidden keys: "
                + ", ".join(sorted(forbidden))
            )
        if pack.kind == "profile":
            behavior = pack.data.get("behavior")
            if not isinstance(behavior, dict):
                raise ExtensionError(f"profile {pack.pack_id} requires behavior")
            if not isinstance(behavior.get("available_minutes"), int):
                raise ExtensionError(
                    f"profile {pack.pack_id} requires integer available_minutes"
                )
        elif not isinstance(pack.data.get("soul"), str):
            raise ExtensionError(f"persona {pack.pack_id} requires soul text")

    def packs(self, kind: ExtensionKind | None = None) -> list[ExtensionPack]:
        values = self._packs.values()
        return sorted(
            (pack for pack in values if kind is None or pack.kind == kind),
            key=lambda pack: (pack.kind, pack.pack_id),
        )

    def get(self, kind: ExtensionKind, pack_id: str) -> ExtensionPack:
        try:
            return self._packs[(kind, pack_id)]
        except KeyError as exc:
            raise ExtensionError(f"unknown {kind} pack: {pack_id}") from exc

    def _state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {
                "schema_version": 1,
                "active": {"persona": "quiet_assistant", "profiles": {}},
                "previous": None,
                "staged": {},
            }
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ExtensionError("unsupported extension state journal")
        return value

    def stage(self, kind: ExtensionKind, pack_id: str) -> str:
        pack = self.get(kind, pack_id)
        token = hashlib.sha256(
            f"{kind}:{pack_id}:{pack.version}:{pack.sha256}".encode("utf-8")
        ).hexdigest()[:24]
        state = self._state()
        state.setdefault("staged", {})[token] = {
            "kind": kind,
            "id": pack_id,
            "version": pack.version,
            "sha256": pack.sha256,
            "tested": False,
        }
        _atomic_json(self.state_path, state)
        return token

    def test_stage(self, token: str) -> dict[str, Any]:
        state = self._state()
        staged = state.get("staged", {}).get(token)
        if not isinstance(staged, dict):
            raise ExtensionError("unknown extension stage token")
        pack = self.get(staged["kind"], staged["id"])
        self._validate(pack)
        if pack.sha256 != staged.get("sha256"):
            raise ExtensionError("staged extension changed after validation")
        staged["tested"] = True
        _atomic_json(self.state_path, state)
        return {
            "id": pack.pack_id,
            "kind": pack.kind,
            "version": pack.version,
            "sha256": pack.sha256,
            "authority_changes": False,
            "model_routing_changes": False,
        }

    def activate(self, token: str, *, member_id: str | None = None) -> None:
        state = self._state()
        staged = state.get("staged", {}).get(token)
        if not isinstance(staged, dict) or not staged.get("tested"):
            raise ExtensionError("extension must pass stage tests before activation")
        state["previous"] = deepcopy(state["active"])
        if staged["kind"] == "persona":
            if member_id is not None:
                raise ExtensionError("persona activation is instance-level")
            state["active"]["persona"] = staged["id"]
        else:
            if not member_id:
                raise ExtensionError("profile activation requires a member id")
            state["active"].setdefault("profiles", {})[member_id] = staged["id"]
        del state["staged"][token]
        _atomic_json(self.state_path, state)

    def rollback(self) -> None:
        state = self._state()
        previous = state.get("previous")
        if not isinstance(previous, dict):
            raise ExtensionError("no extension activation to roll back")
        state["active"] = previous
        state["previous"] = None
        _atomic_json(self.state_path, state)

    def active_persona(self) -> dict[str, Any]:
        pack_id = str(self._state()["active"].get("persona") or "quiet_assistant")
        return deepcopy(self.get("persona", pack_id).data)

    def profile_for(self, member_id: str) -> dict[str, Any] | None:
        active = self._state()["active"].setdefault("profiles", {})
        pack_id = active.get(member_id)
        if pack_id is None:
            legacy_path = self.instance_dir / "config" / "profiles.json"
            if legacy_path.is_file():
                legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
                selected = legacy.get(member_id) if isinstance(legacy, dict) else None
                if isinstance(selected, dict):
                    pack_id = selected.get("profile")
                elif isinstance(selected, str):
                    pack_id = selected
        if not isinstance(pack_id, str):
            return None
        return deepcopy(self.get("profile", pack_id).data)


__all__ = [
    "ExtensionCatalog",
    "ExtensionError",
    "ExtensionKind",
    "ExtensionPack",
]
