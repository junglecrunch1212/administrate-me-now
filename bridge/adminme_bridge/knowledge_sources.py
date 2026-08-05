"""Loadable Cat-D adapters for member-owned local knowledge sources."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Protocol

from adminme.adapters.cursor_store import AdapterCursorStore
from adminme.events.envelope import EventEnvelope, Sensitivity
from adminme_bridge.adapters.base import AdapterStatus, BridgeContext
from adminme_bridge.adapters.sinks import IngestClientBridgeSink


@dataclass(frozen=True)
class KnowledgeDocument:
    external_id: str
    title: str | None
    body: str
    captured_at: datetime
    sensitivity: Sensitivity = "normal"
    body_format: str = "plain"
    account_external_id: str | None = None
    account_name: str | None = None
    folder_external_id: str | None = None
    folder_path: str | None = None
    created_at: datetime | None = None
    provider_modified_at: datetime | None = None
    provider_revision: str | None = None
    locked: bool = False
    shared: bool = False
    pinned: bool | None = None
    tags: list[str] | None = None
    attachments: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None
    capability_warnings: list[str] | None = None


@dataclass(frozen=True)
class KnowledgeFolder:
    external_id: str
    path: str
    account_external_id: str | None = None
    parent_external_id: str | None = None
    changed_at: datetime | None = None


class KnowledgeDocumentProvider(Protocol):
    async def documents(self) -> list[KnowledgeDocument]: ...


class FilesystemKnowledgeProvider:
    """Read exported/transcribed text without requiring cloud credentials."""

    def __init__(self, root: Path, *, suffixes: tuple[str, ...]) -> None:
        self.root = Path(root)
        self.suffixes = tuple(suffix.casefold() for suffix in suffixes)

    async def documents(self) -> list[KnowledgeDocument]:
        return await asyncio.to_thread(self._read)

    def _read(self) -> list[KnowledgeDocument]:
        if not self.root.is_dir():
            raise FileNotFoundError(f"knowledge source directory missing: {self.root}")
        documents: list[KnowledgeDocument] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.casefold() not in self.suffixes:
                continue
            relative = path.relative_to(self.root).as_posix()
            body = path.read_text(encoding="utf-8")
            if not body.strip():
                continue
            stat = path.stat()
            documents.append(
                KnowledgeDocument(
                    external_id=relative,
                    title=path.stem,
                    body=body,
                    captured_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                )
            )
        return documents


class LocalKnowledgeSourceAdapter:
    """Common bridge watcher for Apple exports, transcripts, and vault files."""

    subkind: ClassVar[str] = "knowledge-source"
    source_kind: ClassVar[str]
    name: ClassVar[str]
    suffixes: ClassVar[tuple[str, ...]] = (".md", ".txt")
    sensitivity_default: ClassVar[Sensitivity] = "normal"

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        provider: KnowledgeDocumentProvider | None = None,
        cursor_store: AdapterCursorStore | None = None,
    ) -> None:
        self._config = dict(config or {})
        self._provider = provider
        self._cursor_store = cursor_store
        self._owns_cursor_store = cursor_store is None
        self._status = AdapterStatus()
        self._stop = asyncio.Event()
        self._lease_owner = f"bridge:{uuid.uuid4()}"
        self._known_external_ids: set[str] = set()
        self._known_folders: dict[str, KnowledgeFolder] = {}
        self._initial_scan_complete = False

    def status(self) -> AdapterStatus:
        return self._status

    async def start(self, *, ctx: BridgeContext) -> None:
        root = self._config.get("root")
        if self._provider is None and not root:
            self._status.state = "stopped"
            self._status.extra = {
                "capability_state": "disabled",
                "reason": "optional member source not configured",
            }
            return
        provider = self._provider or FilesystemKnowledgeProvider(
            Path(str(root)).expanduser(), suffixes=self.suffixes
        )
        cursor_path = Path(
            str(
                self._config.get("cursor_db_path")
                or "~/.adminme-bridge/adapter-cursors.db"
            )
        ).expanduser()
        store = self._cursor_store or AdapterCursorStore(cursor_path)
        self._cursor_store = store
        poll_interval_s = float(self._config.get("poll_interval_s", 30.0))
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be positive")
        lease_ttl_ms = max(60_000, int(poll_interval_s * 3_000))
        if not store.claim(self.name, self._lease_owner, ttl_ms=lease_ttl_ms):
            self._status.state = "backoff"
            self._status.extra = {"reason": "cursor lease held or circuit open"}
            return
        self._status.state = "running"
        self._status.started_at = ctx.now_factory()
        try:
            while not self._stop.is_set():
                await self.scan_once(ctx, provider)
                if not store.claim(self.name, self._lease_owner, ttl_ms=lease_ttl_ms):
                    raise RuntimeError("adapter cursor lease renewal failed")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=poll_interval_s)
                except TimeoutError:
                    pass
        except Exception:
            cursor = store.record_error(self.name)
            self._status.state = "crashed"
            self._status.restart_count = cursor.error_count
            raise
        finally:
            store.release(self.name, self._lease_owner)
            if self._owns_cursor_store:
                store.close()

    async def scan_once(
        self, ctx: BridgeContext, provider: KnowledgeDocumentProvider
    ) -> list[str]:
        assert self._cursor_store is not None
        sink = ctx.sink or IngestClientBridgeSink(ctx.ingest_client)
        event_ids: list[str] = []
        watermark: str | None = None
        documents = await provider.documents()
        observed_ids = {document.external_id for document in documents}
        schema_version = int(self._config.get("emit_schema_version", 1))
        if schema_version >= 2 and callable(getattr(provider, "folders", None)):
            await self._sync_folders(
                ctx,
                sink,
                await provider.folders(),  # type: ignore[attr-defined]
            )
        for document in documents:
            hash_material = document.body
            if schema_version >= 2:
                hash_material += repr(
                    (
                        document.title,
                        document.folder_external_id,
                        document.folder_path,
                        document.provider_revision,
                        document.locked,
                        document.shared,
                    )
                )
            content_sha = hashlib.sha256(hash_material.encode("utf-8")).hexdigest()
            if self._cursor_store.dedupe_event(
                self.name, document.external_id, content_sha
            ):
                continue
            prior = self._cursor_store.latest_event(self.name, document.external_id)
            event_type = "member_knowledge.note_added"
            if prior is not None:
                event_type = (
                    "member_knowledge.note_updated"
                    if schema_version >= 2
                    else "member_knowledge.note_corrected"
                )
            knowledge_seed = (
                f"{ctx.member_id}|{self.source_kind}|{document.external_id}"
            )
            knowledge_id = (
                "knowledge_"
                + hashlib.sha256(knowledge_seed.encode("utf-8")).hexdigest()[:32]
            )
            payload: dict[str, Any] = {
                "knowledge_id": knowledge_id,
                "member_id": ctx.member_id,
                "title": document.title,
                "body": document.body,
                "source_kind": self.source_kind,
                "source_external_id": document.external_id,
                "content_sha256": content_sha,
                "captured_at": document.captured_at.isoformat(),
            }
            if schema_version >= 2:
                payload.update(
                    {
                        "body_format": document.body_format,
                        "account_external_id": document.account_external_id,
                        "account_name": document.account_name,
                        "folder_external_id": document.folder_external_id,
                        "folder_path": document.folder_path,
                        "created_at": document.created_at.isoformat()
                        if document.created_at
                        else None,
                        "provider_modified_at": document.provider_modified_at.isoformat()
                        if document.provider_modified_at
                        else None,
                        "provider_revision": document.provider_revision,
                        "locked": document.locked,
                        "shared": document.shared,
                        "pinned": document.pinned,
                        "tags": document.tags,
                        "attachments": document.attachments or [],
                        "metadata": document.metadata or {},
                        "capability_warnings": document.capability_warnings or [],
                    }
                )
            if prior is not None and schema_version == 1:
                payload["supersedes_event_id"] = prior[1]
            sensitivity_order = {"normal": 0, "sensitive": 1, "privileged": 2}
            effective_sensitivity = (
                document.sensitivity
                if sensitivity_order[document.sensitivity]
                >= sensitivity_order[self.sensitivity_default]
                else self.sensitivity_default
            )
            envelope = EventEnvelope(
                event_at_ms=int(document.captured_at.timestamp() * 1000),
                tenant_id=ctx.tenant_id,
                type=event_type,
                schema_version=schema_version,
                occurred_at=document.captured_at.isoformat(),
                source_adapter=self.name,
                source_account_id=self.source_kind,
                owner_scope=f"private:{ctx.member_id}",
                visibility_scope=f"private:{ctx.member_id}",
                sensitivity=effective_sensitivity,
                raw_ref=f"{self.source_kind}://{document.external_id}",
                correlation_id=ctx.correlation_id_factory(),
                idempotency_key=(
                    f"{self.name}:{ctx.member_id}:{document.external_id}:{content_sha}"
                ),
                payload=payload,
            )
            result = await sink.emit(envelope)
            if not result.ok or result.event_id is None:
                raise RuntimeError(result.error or "bridge ingest failed")
            self._cursor_store.record_success(
                self.name,
                provider_watermark=document.captured_at.isoformat(),
                external_id=document.external_id,
                fingerprint=content_sha,
                event_id=result.event_id,
            )
            event_ids.append(result.event_id)
            watermark = document.captured_at.isoformat()
        if self._initial_scan_complete and schema_version >= 2:
            for external_id in self._known_external_ids - observed_ids:
                knowledge_seed = f"{ctx.member_id}|{self.source_kind}|{external_id}"
                knowledge_id = (
                    "knowledge_"
                    + hashlib.sha256(knowledge_seed.encode("utf-8")).hexdigest()[:32]
                )
                now = ctx.now_factory()
                envelope = EventEnvelope(
                    event_at_ms=int(now.timestamp() * 1000),
                    tenant_id=ctx.tenant_id,
                    type="member_knowledge.note_deleted",
                    schema_version=1,
                    occurred_at=now.isoformat(),
                    source_adapter=self.name,
                    source_account_id=self.source_kind,
                    owner_scope=f"private:{ctx.member_id}",
                    visibility_scope=f"private:{ctx.member_id}",
                    sensitivity=self.sensitivity_default,
                    correlation_id=ctx.correlation_id_factory(),
                    idempotency_key=f"{self.name}:{ctx.member_id}:{external_id}:deleted",
                    payload={
                        "knowledge_id": knowledge_id,
                        "member_id": ctx.member_id,
                        "source_kind": "apple_notes",
                        "source_external_id": external_id,
                        "deleted_at": now.isoformat(),
                    },
                )
                result = await sink.emit(envelope)
                if not result.ok or result.event_id is None:
                    raise RuntimeError(result.error or "bridge delete ingest failed")
                event_ids.append(result.event_id)
        self._known_external_ids = observed_ids
        self._initial_scan_complete = True
        if watermark is not None:
            self._status.last_cursor_advance_at = datetime.fromisoformat(watermark)
        return event_ids

    async def _sync_folders(
        self, ctx: BridgeContext, sink: Any, folders: list[KnowledgeFolder]
    ) -> None:
        current = {
            folder.external_id: folder for folder in folders if folder.external_id
        }
        for folder_id, folder in current.items():
            if self._known_folders.get(folder_id) == folder:
                continue
            changed_at = folder.changed_at or ctx.now_factory()
            event_type = (
                "member_knowledge.folder_updated"
                if folder_id in self._known_folders
                else "member_knowledge.folder_created"
            )
            envelope = self._folder_envelope(
                ctx,
                event_type,
                folder,
                changed_at,
                idempotency_suffix=changed_at.isoformat(),
            )
            result = await sink.emit(envelope)
            if not result.ok:
                raise RuntimeError(result.error or "bridge folder ingest failed")
        if self._initial_scan_complete:
            for folder_id in set(self._known_folders) - set(current):
                prior = self._known_folders[folder_id]
                result = await sink.emit(
                    self._folder_envelope(
                        ctx,
                        "member_knowledge.folder_deleted",
                        prior,
                        ctx.now_factory(),
                        idempotency_suffix="deleted",
                    )
                )
                if not result.ok:
                    raise RuntimeError(
                        result.error or "bridge folder deletion ingest failed"
                    )
        self._known_folders = current

    def _folder_envelope(
        self,
        ctx: BridgeContext,
        event_type: str,
        folder: KnowledgeFolder,
        changed_at: datetime,
        *,
        idempotency_suffix: str,
    ) -> EventEnvelope:
        return EventEnvelope(
            event_at_ms=int(changed_at.timestamp() * 1000),
            tenant_id=ctx.tenant_id,
            type=event_type,
            schema_version=1,
            occurred_at=changed_at.isoformat(),
            source_adapter=self.name,
            source_account_id=self.source_kind,
            owner_scope=f"private:{ctx.member_id}",
            visibility_scope=f"private:{ctx.member_id}",
            sensitivity=self.sensitivity_default,
            correlation_id=ctx.correlation_id_factory(),
            idempotency_key=(
                f"{self.name}:{ctx.member_id}:folder:{folder.external_id}:"
                f"{idempotency_suffix}"
            ),
            payload={
                "member_id": ctx.member_id,
                "source_kind": "apple_notes",
                "account_external_id": folder.account_external_id,
                "folder_external_id": folder.external_id,
                "folder_path": folder.path,
                "parent_folder_external_id": folder.parent_external_id,
                "changed_at": changed_at.isoformat(),
            },
        )

    async def stop(self) -> None:
        self._stop.set()
        self._status.state = "stopped"


__all__ = [
    "FilesystemKnowledgeProvider",
    "KnowledgeDocument",
    "KnowledgeFolder",
    "KnowledgeDocumentProvider",
    "LocalKnowledgeSourceAdapter",
]
