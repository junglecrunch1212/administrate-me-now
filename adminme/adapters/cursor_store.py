"""Durable provider cursors, leases, dedupe, and circuit state for adapters."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal


CircuitState = Literal["closed", "open", "half_open"]


@dataclass(frozen=True)
class AdapterCursor:
    adapter_id: str
    provider_watermark: str | None
    lease_owner: str | None
    lease_until_ms: int | None
    error_count: int
    circuit_state: CircuitState
    updated_at_ms: int

    def public(self) -> dict[str, object]:
        return asdict(self)


class AdapterCursorStore:
    """Small metadata-only SQLite store shared by central and bridge adapters.

    It never stores provider content or credentials. Lease claims and cursor
    updates use immediate transactions so two watcher processes cannot both
    advance the same provider stream.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.path, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS adapter_cursors (
                adapter_id TEXT PRIMARY KEY,
                provider_watermark TEXT,
                lease_owner TEXT,
                lease_until_ms INTEGER,
                error_count INTEGER NOT NULL DEFAULT 0,
                circuit_state TEXT NOT NULL DEFAULT 'closed'
                    CHECK (circuit_state IN ('closed','open','half_open')),
                updated_at_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS adapter_dedupe (
                adapter_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                event_id TEXT NOT NULL,
                seen_at_ms INTEGER NOT NULL,
                PRIMARY KEY (adapter_id, external_id, fingerprint)
            );
            CREATE INDEX IF NOT EXISTS idx_adapter_dedupe_seen
                ON adapter_dedupe(adapter_id, seen_at_ms);
            """
        )

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @classmethod
    def _next_updated_at_ms(cls, previous: int) -> int:
        """Return a process-independent monotonic row version.

        ``updated_at_ms`` is both public recency evidence and the existing
        durable compare-and-swap token.  Advancing by at least one preserves
        ordering when multiple transitions land inside the same wall-clock
        millisecond without adding a schema migration to installed stores.
        """

        return max(cls._now_ms(), int(previous) + 1)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _ensure(self, adapter_id: str, now_ms: int) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO adapter_cursors(adapter_id, updated_at_ms) "
            "VALUES (?, ?)",
            (adapter_id, now_ms),
        )

    def get(self, adapter_id: str) -> AdapterCursor:
        now_ms = self._now_ms()
        with self._lock:
            self._ensure(adapter_id, now_ms)
            row = self._conn.execute(
                "SELECT * FROM adapter_cursors WHERE adapter_id=?", (adapter_id,)
            ).fetchone()
        assert row is not None
        return AdapterCursor(**dict(row))

    def claim(
        self,
        adapter_id: str,
        lease_owner: str,
        *,
        ttl_ms: int,
        now_ms: int | None = None,
    ) -> bool:
        if ttl_ms <= 0:
            raise ValueError("ttl_ms must be positive")
        now = self._now_ms() if now_ms is None else now_ms
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure(adapter_id, now)
                row = self._conn.execute(
                    "SELECT lease_owner, lease_until_ms, circuit_state, updated_at_ms "
                    "FROM adapter_cursors WHERE adapter_id=?",
                    (adapter_id,),
                ).fetchone()
                assert row is not None
                available = row["circuit_state"] != "open" and (
                    row["lease_owner"] in {None, lease_owner}
                    or int(row["lease_until_ms"] or 0) <= now
                )
                if available:
                    updated_at = max(now, int(row["updated_at_ms"]) + 1)
                    self._conn.execute(
                        "UPDATE adapter_cursors SET lease_owner=?, lease_until_ms=?, "
                        "updated_at_ms=? WHERE adapter_id=?",
                        (lease_owner, now + ttl_ms, updated_at, adapter_id),
                    )
                self._conn.execute("COMMIT")
                return available
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def release(self, adapter_id: str, lease_owner: str) -> bool:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT updated_at_ms FROM adapter_cursors "
                    "WHERE adapter_id=? AND lease_owner=?",
                    (adapter_id, lease_owner),
                ).fetchone()
                if row is None:
                    self._conn.execute("COMMIT")
                    return False
                previous = int(row[0])
                now = self._next_updated_at_ms(previous)
                cur = self._conn.execute(
                    "UPDATE adapter_cursors SET lease_owner=NULL, "
                    "lease_until_ms=NULL, updated_at_ms=? "
                    "WHERE adapter_id=? AND lease_owner=? AND updated_at_ms=?",
                    (now, adapter_id, lease_owner, previous),
                )
                self._conn.execute("COMMIT")
                return cur.rowcount == 1
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def dedupe_event(
        self, adapter_id: str, external_id: str, fingerprint: str
    ) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT event_id FROM adapter_dedupe WHERE adapter_id=? "
                "AND external_id=? AND fingerprint=?",
                (adapter_id, external_id, fingerprint),
            ).fetchone()
        return str(row[0]) if row else None

    def latest_event(self, adapter_id: str, external_id: str) -> tuple[str, str] | None:
        """Return the latest ``(fingerprint, event_id)`` for one provider id."""

        with self._lock:
            row = self._conn.execute(
                "SELECT fingerprint, event_id FROM adapter_dedupe "
                "WHERE adapter_id=? AND external_id=? "
                "ORDER BY seen_at_ms DESC, rowid DESC LIMIT 1",
                (adapter_id, external_id),
            ).fetchone()
        return (str(row[0]), str(row[1])) if row else None

    def record_success(
        self,
        adapter_id: str,
        *,
        provider_watermark: str | None = None,
        external_id: str | None = None,
        fingerprint: str | None = None,
        event_id: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure(adapter_id, self._now_ms())
                row = self._conn.execute(
                    "SELECT updated_at_ms FROM adapter_cursors WHERE adapter_id=?",
                    (adapter_id,),
                ).fetchone()
                assert row is not None
                now = self._next_updated_at_ms(int(row[0]))
                self._conn.execute(
                    "UPDATE adapter_cursors SET provider_watermark=COALESCE(?, provider_watermark), "
                    "error_count=0, circuit_state='closed', updated_at_ms=? "
                    "WHERE adapter_id=?",
                    (provider_watermark, now, adapter_id),
                )
                supplied = (external_id, fingerprint, event_id)
                if any(value is not None for value in supplied) and not all(
                    value is not None for value in supplied
                ):
                    raise ValueError(
                        "external_id, fingerprint, and event_id must be supplied together"
                    )
                if external_id is not None:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO adapter_dedupe "
                        "(adapter_id, external_id, fingerprint, event_id, seen_at_ms) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (adapter_id, external_id, fingerprint, event_id, now),
                    )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def record_event(
        self,
        adapter_id: str,
        *,
        external_id: str,
        fingerprint: str,
        event_id: str,
    ) -> None:
        """Persist one idempotent fact identity without changing health.

        Observing one record is not proof that the provider poll, its cursor,
        or its circuit transition completed.  Calendar uses this narrower
        operation so a durable source fact cannot prematurely erase an open or
        half-open circuit.
        """

        now = self._now_ms()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure(adapter_id, now)
                self._conn.execute(
                    "INSERT OR REPLACE INTO adapter_dedupe "
                    "(adapter_id, external_id, fingerprint, event_id, seen_at_ms) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (adapter_id, external_id, fingerprint, event_id, now),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def complete_poll(
        self,
        adapter_id: str,
        *,
        provider_watermark: str,
        expected_updated_at_ms: int,
        lease_owner: str,
    ) -> AdapterCursor:
        """Atomically persist a complete poll and close its exact circuit.

        The caller captures ``expected_updated_at_ms`` after acquiring its
        lease.  A newer failure, recovery transition, lease transfer, or other
        durable state change invalidates that success instead of letting stale
        work erase it.  Provider facts must already be durable before this
        terminal metadata transaction is attempted.
        """

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT updated_at_ms, lease_owner, circuit_state "
                    "FROM adapter_cursors WHERE adapter_id=?",
                    (adapter_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("adapter cursor disappeared during poll")
                if (
                    int(row["updated_at_ms"]) != int(expected_updated_at_ms)
                    or row["lease_owner"] != lease_owner
                    or row["circuit_state"] not in {"closed", "half_open"}
                ):
                    raise RuntimeError(
                        "adapter circuit changed while provider poll was in progress"
                    )
                now = self._next_updated_at_ms(int(row["updated_at_ms"]))
                cursor_update = self._conn.execute(
                    "UPDATE adapter_cursors SET provider_watermark=?, "
                    "updated_at_ms=? WHERE adapter_id=? AND updated_at_ms=? "
                    "AND lease_owner=?",
                    (
                        provider_watermark,
                        now,
                        adapter_id,
                        expected_updated_at_ms,
                        lease_owner,
                    ),
                )
                if cursor_update.rowcount != 1:
                    raise RuntimeError(
                        "adapter cursor changed before provider watermark persisted"
                    )
                circuit_update = self._conn.execute(
                    "UPDATE adapter_cursors SET error_count=0, "
                    "circuit_state='closed' WHERE adapter_id=? AND updated_at_ms=? "
                    "AND lease_owner=?",
                    (adapter_id, now, lease_owner),
                )
                if circuit_update.rowcount != 1:
                    raise RuntimeError(
                        "adapter circuit could not close after cursor persistence"
                    )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return self.get(adapter_id)

    def record_error(self, adapter_id: str, *, open_after: int = 3) -> AdapterCursor:
        if open_after <= 0:
            raise ValueError("open_after must be positive")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure(adapter_id, self._now_ms())
                row = self._conn.execute(
                    "SELECT updated_at_ms FROM adapter_cursors WHERE adapter_id=?",
                    (adapter_id,),
                ).fetchone()
                assert row is not None
                now = self._next_updated_at_ms(int(row[0]))
                self._conn.execute(
                    "UPDATE adapter_cursors SET error_count=error_count+1, "
                    "circuit_state=CASE WHEN error_count+1>=? THEN 'open' "
                    "ELSE circuit_state END, updated_at_ms=? WHERE adapter_id=?",
                    (open_after, now, adapter_id),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return self.get(adapter_id)

    def begin_recovery(self, adapter_id: str) -> AdapterCursor:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure(adapter_id, self._now_ms())
                row = self._conn.execute(
                    "SELECT updated_at_ms FROM adapter_cursors WHERE adapter_id=?",
                    (adapter_id,),
                ).fetchone()
                assert row is not None
                now = self._next_updated_at_ms(int(row[0]))
                self._conn.execute(
                    "UPDATE adapter_cursors SET circuit_state='half_open', "
                    "lease_owner=NULL, lease_until_ms=NULL, updated_at_ms=? "
                    "WHERE adapter_id=?",
                    (now, adapter_id),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return self.get(adapter_id)


__all__ = ["AdapterCursor", "AdapterCursorStore", "CircuitState"]
