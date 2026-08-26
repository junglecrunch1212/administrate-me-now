"""Durable provider cursors, leases, dedupe, and circuit state for adapters."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, overload


CircuitState = Literal["closed", "open", "half_open"]


@dataclass(frozen=True)
class AdapterCursor:
    adapter_id: str
    provider_watermark: str | None
    lease_owner: str | None
    lease_until_ms: int | None
    error_count: int
    circuit_state: CircuitState
    retry_not_before_ms: int | None
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
            CREATE TABLE IF NOT EXISTS adapter_retry_state (
                adapter_id TEXT PRIMARY KEY,
                retry_not_before_ms INTEGER
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
        columns = {
            str(row[1])
            for row in self._conn.execute("PRAGMA table_info(adapter_cursors)")
        }
        if "retry_not_before_ms" in columns:
            # Generation 21 added retry timing to the predecessor-owned cursor
            # table.  Its ``SELECT *`` reader cannot tolerate that extra column
            # after rollback.  Move only the content-free timing value into a
            # companion table and restore the predecessor-readable schema in one
            # transaction before any adapter can use the store.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "INSERT INTO adapter_retry_state "
                    "(adapter_id, retry_not_before_ms) "
                    "SELECT adapter_id, retry_not_before_ms FROM adapter_cursors "
                    "WHERE retry_not_before_ms IS NOT NULL "
                    "ON CONFLICT(adapter_id) DO UPDATE SET "
                    "retry_not_before_ms=excluded.retry_not_before_ms"
                )
                self._conn.execute(
                    "ALTER TABLE adapter_cursors DROP COLUMN retry_not_before_ms"
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

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
                "SELECT c.adapter_id, c.provider_watermark, c.lease_owner, "
                "c.lease_until_ms, c.error_count, c.circuit_state, "
                "r.retry_not_before_ms, c.updated_at_ms "
                "FROM adapter_cursors AS c "
                "LEFT JOIN adapter_retry_state AS r USING (adapter_id) "
                "WHERE c.adapter_id=?",
                (adapter_id,),
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
                    "SELECT c.lease_owner, c.lease_until_ms, c.circuit_state, "
                    "r.retry_not_before_ms "
                    "FROM adapter_cursors AS c "
                    "LEFT JOIN adapter_retry_state AS r USING (adapter_id) "
                    "WHERE c.adapter_id=?",
                    (adapter_id,),
                ).fetchone()
                assert row is not None
                lease_available = (
                    row["lease_owner"] in {None, lease_owner}
                    or int(row["lease_until_ms"] or 0) <= now
                )
                circuit_state = str(row["circuit_state"])
                recovery_due = (
                    circuit_state != "open"
                    or row["retry_not_before_ms"] is None
                    or int(row["retry_not_before_ms"]) <= now
                )
                available = lease_available and recovery_due
                if available:
                    next_circuit_state = (
                        "half_open" if circuit_state == "open" else circuit_state
                    )
                    self._conn.execute(
                        "UPDATE adapter_cursors SET lease_owner=?, lease_until_ms=?, "
                        "circuit_state=?, updated_at_ms=? WHERE adapter_id=?",
                        (
                            lease_owner,
                            now + ttl_ms,
                            next_circuit_state,
                            now,
                            adapter_id,
                        ),
                    )
                self._conn.execute("COMMIT")
                return available
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def release(self, adapter_id: str, lease_owner: str) -> bool:
        now = self._now_ms()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE adapter_cursors SET lease_owner=NULL, lease_until_ms=NULL, "
                "updated_at_ms=? WHERE adapter_id=? AND lease_owner=?",
                (now, adapter_id, lease_owner),
            )
            return cur.rowcount == 1

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
        lease_owner: str | None = None,
        now_ms: int | None = None,
        mark_healthy: bool = True,
        provider_watermark: str | None = None,
        external_id: str | None = None,
        fingerprint: str | None = None,
        event_id: str | None = None,
    ) -> bool:
        """Atomically commit provider progress and optional dedupe identity.

        When ``lease_owner`` is supplied, the mutation is fenced by both the
        current owner and the unexpired lease. A worker that finishes after a
        takeover cannot advance the provider watermark or dedupe ledger.
        """

        now = self._now_ms() if now_ms is None else now_ms
        supplied = (external_id, fingerprint, event_id)
        if any(value is not None for value in supplied) and not all(
            value is not None for value in supplied
        ):
            raise ValueError(
                "external_id, fingerprint, and event_id must be supplied together"
            )
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure(adapter_id, now)
                if lease_owner is not None:
                    lease = self._conn.execute(
                        "SELECT lease_owner, lease_until_ms FROM adapter_cursors "
                        "WHERE adapter_id=?",
                        (adapter_id,),
                    ).fetchone()
                    assert lease is not None
                    if (
                        lease["lease_owner"] != lease_owner
                        or int(lease["lease_until_ms"] or 0) <= now
                    ):
                        self._conn.execute("ROLLBACK")
                        return False
                if mark_healthy:
                    self._conn.execute(
                        "UPDATE adapter_cursors SET "
                        "provider_watermark=COALESCE(?, provider_watermark), "
                        "error_count=0, circuit_state='closed', updated_at_ms=? "
                        "WHERE adapter_id=?",
                        (provider_watermark, now, adapter_id),
                    )
                    self._conn.execute(
                        "DELETE FROM adapter_retry_state WHERE adapter_id=?",
                        (adapter_id,),
                    )
                else:
                    self._conn.execute(
                        "UPDATE adapter_cursors SET "
                        "provider_watermark=COALESCE(?, provider_watermark), "
                        "updated_at_ms=? WHERE adapter_id=?",
                        (provider_watermark, now, adapter_id),
                    )
                if external_id is not None:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO adapter_dedupe "
                        "(adapter_id, external_id, fingerprint, event_id, seen_at_ms) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (adapter_id, external_id, fingerprint, event_id, now),
                    )
                self._conn.execute("COMMIT")
                return True
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def clear_watermark(
        self,
        adapter_id: str,
        *,
        lease_owner: str,
        now_ms: int | None = None,
    ) -> bool:
        """Clear an invalid provider watermark under an active lease."""

        now = self._now_ms() if now_ms is None else now_ms
        with self._lock:
            cur = self._conn.execute(
                "UPDATE adapter_cursors SET provider_watermark=NULL, updated_at_ms=? "
                "WHERE adapter_id=? AND lease_owner=? AND lease_until_ms>?",
                (now, adapter_id, lease_owner, now),
            )
            return cur.rowcount == 1

    @overload
    def record_error(
        self,
        adapter_id: str,
        *,
        open_after: int = 3,
        recovery_after_ms: int = 30_000,
        lease_owner: None = None,
        now_ms: int | None = None,
    ) -> AdapterCursor: ...

    @overload
    def record_error(
        self,
        adapter_id: str,
        *,
        open_after: int = 3,
        recovery_after_ms: int = 30_000,
        lease_owner: str,
        now_ms: int | None = None,
    ) -> AdapterCursor | Literal[False]: ...

    def record_error(
        self,
        adapter_id: str,
        *,
        open_after: int = 3,
        recovery_after_ms: int = 30_000,
        lease_owner: str | None = None,
        now_ms: int | None = None,
    ) -> AdapterCursor | Literal[False]:
        if open_after <= 0:
            raise ValueError("open_after must be positive")
        if recovery_after_ms < 0:
            raise ValueError("recovery_after_ms must not be negative")
        now = self._now_ms() if now_ms is None else now_ms
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure(adapter_id, now)
                row = self._conn.execute(
                    "SELECT error_count, circuit_state, lease_owner, lease_until_ms "
                    "FROM adapter_cursors "
                    "WHERE adapter_id=?",
                    (adapter_id,),
                ).fetchone()
                assert row is not None
                if lease_owner is not None and (
                    row["lease_owner"] != lease_owner
                    or int(row["lease_until_ms"] or 0) <= now
                ):
                    self._conn.execute("ROLLBACK")
                    return False
                error_count = int(row["error_count"]) + 1
                should_open = (
                    str(row["circuit_state"]) == "half_open"
                    or error_count >= open_after
                )
                self._conn.execute(
                    "UPDATE adapter_cursors SET error_count=?, circuit_state=?, "
                    "lease_owner=CASE WHEN ? THEN NULL ELSE lease_owner END, "
                    "lease_until_ms=CASE WHEN ? THEN NULL ELSE lease_until_ms END, "
                    "updated_at_ms=? WHERE adapter_id=?",
                    (
                        error_count,
                        "open" if should_open else str(row["circuit_state"]),
                        should_open,
                        should_open,
                        now,
                        adapter_id,
                    ),
                )
                if should_open:
                    self._conn.execute(
                        "INSERT INTO adapter_retry_state "
                        "(adapter_id, retry_not_before_ms) VALUES (?, ?) "
                        "ON CONFLICT(adapter_id) DO UPDATE SET "
                        "retry_not_before_ms=excluded.retry_not_before_ms",
                        (adapter_id, now + recovery_after_ms),
                    )
                else:
                    self._conn.execute(
                        "DELETE FROM adapter_retry_state WHERE adapter_id=?",
                        (adapter_id,),
                    )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return self.get(adapter_id)

    def begin_recovery(self, adapter_id: str) -> AdapterCursor:
        """Make an open circuit immediately probe-eligible.

        The next :meth:`claim` still performs the atomic open-to-half-open
        transition and assigns the sole probe lease owner.
        """

        now = self._now_ms()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._ensure(adapter_id, now)
                self._conn.execute(
                    "UPDATE adapter_cursors SET updated_at_ms=? "
                    "WHERE adapter_id=? AND circuit_state='open'",
                    (now, adapter_id),
                )
                self._conn.execute(
                    "INSERT INTO adapter_retry_state "
                    "(adapter_id, retry_not_before_ms) "
                    "SELECT adapter_id, ? FROM adapter_cursors "
                    "WHERE adapter_id=? AND circuit_state='open' "
                    "ON CONFLICT(adapter_id) DO UPDATE SET "
                    "retry_not_before_ms=excluded.retry_not_before_ms",
                    (now, adapter_id),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return self.get(adapter_id)


__all__ = ["AdapterCursor", "AdapterCursorStore", "CircuitState"]
