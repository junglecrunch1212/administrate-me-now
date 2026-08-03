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
        for document in await provider.documents():
            content_sha = hashlib.sha256(document.body.encode("utf-8")).hexdigest()
            if self._cursor_store.dedupe_event(
                self.name, document.external_id, content_sha
            ):
                continue
            prior = self._cursor_store.latest_event(self.name, document.external_id)
            event_type = (
                "member_knowledge.note_corrected"
                if prior is not None
                else "member_knowledge.note_added"
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
            if prior is not None:
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
                schema_version=1,
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
        if watermark is not None:
            self._status.last_cursor_advance_at = datetime.fromisoformat(watermark)
        return event_ids

    async def stop(self) -> None:
        self._stop.set()
        self._status.state = "stopped"


__all__ = [
    "FilesystemKnowledgeProvider",
    "KnowledgeDocument",
    "KnowledgeDocumentProvider",
    "LocalKnowledgeSourceAdapter",
]
