"""Private, disposable saved views over existing authorized read models."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


ViewMode = Literal["carousel", "compressed", "child"]
ViewStatus = Literal["active", "expired", "archived"]

ALLOWED_QUERIES = frozenset(
    {"what_now", "tasks.open", "lists.open", "calendar.week", "finance.recent"}
)
ALLOWED_ACTIONS = frozenset(
    {"task.complete", "task.defer", "task.snooze", "task.block", "task.unblock"}
)
ALLOWED_CAPABILITIES = frozenset(
    {
        "projection:tasks",
        "projection:lists",
        "projection:calendars",
        "projection:money",
        "product:core",
    }
)
CHILD_QUERIES = frozenset({"what_now", "tasks.open"})
CHILD_ACTIONS = frozenset({"task.complete", "task.defer", "task.snooze", "task.block", "task.unblock"})


def _default_modes() -> list[ViewMode]:
    return ["carousel", "compressed"]


class TemporarySurfaceError(ValueError):
    """A saved-view declaration or transition violated the data-only contract."""


class SavedQuery(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def query_is_allowlisted(cls, value: str) -> str:
        if value not in ALLOWED_QUERIES:
            raise ValueError("query is outside the temporary-surface allowlist")
        return value

    @field_validator("params")
    @classmethod
    def params_are_data_only(cls, value: dict[str, Any]) -> dict[str, Any]:
        forbidden = {"sql", "code", "template", "script", "event_log", "raw_log"}
        if forbidden & set(value):
            raise ValueError("executable, template, and raw-log declarations are forbidden")
        encoded = json.dumps(value, sort_keys=True)
        if len(encoded) > 2_000:
            raise ValueError("query parameters are too large")
        return value


class CreateTemporarySurface(BaseModel):
    """Browser input intentionally omits owner, audience, status, and version."""

    model_config = {"extra": "forbid"}
    title: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=500)
    queries: list[SavedQuery] = Field(min_length=1, max_length=8)
    action_verbs: list[str] = Field(default_factory=list, max_length=8)
    required_capabilities: list[str] = Field(default_factory=list, max_length=8)
    compatible_modes: list[ViewMode] = Field(
        default_factory=_default_modes, min_length=1
    )
    expires_at: datetime | None = None

    @field_validator("action_verbs")
    @classmethod
    def actions_are_allowlisted(cls, value: list[str]) -> list[str]:
        if set(value) - ALLOWED_ACTIONS:
            raise ValueError("action verb is outside the temporary-surface allowlist")
        return list(dict.fromkeys(value))

    @field_validator("required_capabilities")
    @classmethod
    def capabilities_are_allowlisted(cls, value: list[str]) -> list[str]:
        if set(value) - ALLOWED_CAPABILITIES:
            raise ValueError("capability is outside the temporary-surface allowlist")
        return list(dict.fromkeys(value))

    @field_validator("compatible_modes")
    @classmethod
    def modes_are_unique_and_safe(cls, value: list[ViewMode]) -> list[ViewMode]:
        return list(dict.fromkeys(value))

    @field_validator("expires_at")
    @classmethod
    def expiry_has_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("expires_at must include a timezone offset")
        return value

    def validate_child_contract(self) -> None:
        if "child" not in self.compatible_modes:
            return
        if {query.name for query in self.queries} - CHILD_QUERIES:
            raise TemporarySurfaceError("child-compatible views contain an adult-only query")
        if set(self.action_verbs) - CHILD_ACTIONS:
            raise TemporarySurfaceError("child-compatible views contain an adult-only action")


@dataclass(frozen=True)
class TemporarySurfaceSpec:
    schema_version: int
    stable_id: str
    title: str
    purpose: str
    owner_member_id: str
    audience_member_ids: tuple[str, ...]
    queries: tuple[dict[str, Any], ...]
    action_verbs: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    compatible_modes: tuple[ViewMode, ...]
    created_version: int
    updated_version: int
    created_at: str
    updated_at: str
    expires_at: str | None
    status: ViewStatus

    def public(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "stable_id": self.stable_id,
            "title": self.title,
            "purpose": self.purpose,
            "owner_member_id": self.owner_member_id,
            "audience_member_ids": list(self.audience_member_ids),
            "queries": [dict(query) for query in self.queries],
            "action_verbs": list(self.action_verbs),
            "required_capabilities": list(self.required_capabilities),
            "compatible_modes": list(self.compatible_modes),
            "created_version": self.created_version,
            "updated_version": self.updated_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TemporarySurfaceSpec":
        try:
            spec = cls(
                schema_version=int(value["schema_version"]),
                stable_id=str(value["stable_id"]),
                title=str(value["title"]),
                purpose=str(value["purpose"]),
                owner_member_id=str(value["owner_member_id"]),
                audience_member_ids=tuple(str(item) for item in value["audience_member_ids"]),
                queries=tuple(dict(item) for item in value["queries"]),
                action_verbs=tuple(str(item) for item in value["action_verbs"]),
                required_capabilities=tuple(str(item) for item in value["required_capabilities"]),
                compatible_modes=tuple(value["compatible_modes"]),
                created_version=int(value["created_version"]),
                updated_version=int(value["updated_version"]),
                created_at=str(value["created_at"]),
                updated_at=str(value["updated_at"]),
                expires_at=(str(value["expires_at"]) if value.get("expires_at") else None),
                status=value["status"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TemporarySurfaceError("invalid temporary-surface store") from exc
        if spec.schema_version != 1 or spec.status not in {"active", "expired", "archived"}:
            raise TemporarySurfaceError("unsupported temporary-surface store schema")
        if set(spec.action_verbs) - ALLOWED_ACTIONS or set(spec.required_capabilities) - ALLOWED_CAPABILITIES:
            raise TemporarySurfaceError("stored temporary surface contains an unsafe declaration")
        if any(query.get("name") not in ALLOWED_QUERIES for query in spec.queries):
            raise TemporarySurfaceError("stored temporary surface contains an unsafe query")
        try:
            for query in spec.queries:
                SavedQuery.model_validate(query)
        except ValueError as exc:
            raise TemporarySurfaceError(
                "stored temporary surface contains unsafe query parameters"
            ) from exc
        return spec


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class TemporarySurfaceStore:
    """Atomic, server-owned presentation configuration for one household hub."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def _read(self) -> dict[str, TemporarySurfaceSpec]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TemporarySurfaceError("temporary-surface store is unreadable") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise TemporarySurfaceError("unsupported temporary-surface store schema")
        values = raw.get("surfaces")
        if not isinstance(values, list):
            raise TemporarySurfaceError("temporary-surface store is malformed")
        specs = [TemporarySurfaceSpec.from_dict(item) for item in values if isinstance(item, dict)]
        if len(specs) != len(values) or len({item.stable_id for item in specs}) != len(specs):
            raise TemporarySurfaceError("temporary-surface store contains invalid identifiers")
        return {item.stable_id: item for item in specs}

    def _write(self, specs: dict[str, TemporarySurfaceSpec]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "schema_version": 1,
            "surfaces": [specs[key].public() for key in sorted(specs)],
        }
        encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def create(self, owner_member_id: str, request: CreateTemporarySurface) -> TemporarySurfaceSpec:
        with self._lock:
            request.validate_child_contract()
            now = _now()
            if request.expires_at is not None and request.expires_at <= now:
                raise TemporarySurfaceError("expiry must be in the future")
            specs = self._read()
            stable_id = f"view_{uuid.uuid4().hex[:16]}"
            spec = TemporarySurfaceSpec(
                schema_version=1,
                stable_id=stable_id,
                title=request.title,
                purpose=request.purpose,
                owner_member_id=owner_member_id,
                audience_member_ids=(owner_member_id,),
                queries=tuple(query.model_dump(mode="json") for query in request.queries),
                action_verbs=tuple(request.action_verbs),
                required_capabilities=tuple(request.required_capabilities),
                compatible_modes=tuple(request.compatible_modes),
                created_version=1,
                updated_version=1,
                created_at=_iso(now),
                updated_at=_iso(now),
                expires_at=_iso(request.expires_at) if request.expires_at else None,
                status="active",
            )
            specs[stable_id] = spec
            self._write(specs)
            return spec

    def get(self, stable_id: str) -> TemporarySurfaceSpec | None:
        with self._lock:
            return self._read().get(stable_id)

    def visible_to(self, member_id: str) -> list[TemporarySurfaceSpec]:
        with self._lock:
            return [
                spec
                for spec in self._read().values()
                if spec.status == "active" and member_id in spec.audience_member_ids
            ]

    def promote(
        self,
        stable_id: str,
        *,
        actor_member_id: str,
        audience_member_ids: tuple[str, ...],
    ) -> TemporarySurfaceSpec:
        with self._lock:
            specs = self._read()
            spec = specs.get(stable_id)
            if spec is None or spec.status != "active":
                raise TemporarySurfaceError("temporary surface not found")
            if actor_member_id != spec.owner_member_id:
                raise TemporarySurfaceError("only the owner may request promotion")
            audience = tuple(sorted(set(audience_member_ids) | {spec.owner_member_id}))
            updated = TemporarySurfaceSpec(
                **{
                    **spec.__dict__,
                    "audience_member_ids": audience,
                    "updated_version": spec.updated_version + 1,
                    "updated_at": _iso(_now()),
                }
            )
            specs[stable_id] = updated
            self._write(specs)
            return updated

    def transition(
        self,
        stable_id: str,
        *,
        actor_member_id: str,
        status: Literal["expired", "archived"],
        force_expiry: bool = False,
    ) -> TemporarySurfaceSpec:
        with self._lock:
            specs = self._read()
            spec = specs.get(stable_id)
            if spec is None or spec.status != "active" or spec.owner_member_id != actor_member_id:
                raise TemporarySurfaceError("temporary surface not found")
            if status == "expired" and not force_expiry:
                if spec.expires_at is None or datetime.fromisoformat(spec.expires_at.replace("Z", "+00:00")) > _now():
                    raise TemporarySurfaceError("temporary surface is not due to expire")
            updated = TemporarySurfaceSpec(
                **{
                    **spec.__dict__,
                    "status": status,
                    "updated_version": spec.updated_version + 1,
                    "updated_at": _iso(_now()),
                }
            )
            specs[stable_id] = updated
            self._write(specs)
            return updated

    def expire_due(self) -> list[TemporarySurfaceSpec]:
        with self._lock:
            specs = self._read()
            changed: list[TemporarySurfaceSpec] = []
            now = _now()
            for stable_id, spec in list(specs.items()):
                if spec.status != "active" or spec.expires_at is None:
                    continue
                expires = datetime.fromisoformat(spec.expires_at.replace("Z", "+00:00"))
                if expires > now:
                    continue
                updated = TemporarySurfaceSpec(
                    **{
                        **spec.__dict__,
                        "status": "expired",
                        "updated_version": spec.updated_version + 1,
                        "updated_at": _iso(now),
                    }
                )
                specs[stable_id] = updated
                changed.append(updated)
            if changed:
                self._write(specs)
            return changed


__all__ = [
    "ALLOWED_ACTIONS",
    "ALLOWED_CAPABILITIES",
    "ALLOWED_QUERIES",
    "CHILD_ACTIONS",
    "CHILD_QUERIES",
    "CreateTemporarySurface",
    "SavedQuery",
    "TemporarySurfaceError",
    "TemporarySurfaceSpec",
    "TemporarySurfaceStore",
]
