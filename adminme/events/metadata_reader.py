"""Committed event IDs/counts without queuing behind the durable writer.

This parent-process connection owns one thread and a small metadata page cache.
It never reads payloads, provides authorization, or holds a read transaction
across calls. Each query sees committed WAL state and releases its cursor.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from pathlib import Path
from typing import Any

import sqlcipher3

_log = logging.getLogger(__name__)
_SQLITE_OK: int = getattr(sqlcipher3, "SQLITE_OK")
_SQLITE_DENY: int = getattr(sqlcipher3, "SQLITE_DENY")
_SQLITE_SELECT: int = getattr(sqlcipher3, "SQLITE_SELECT")
_SQLITE_READ: int = getattr(sqlcipher3, "SQLITE_READ")
_SQLITE_FUNCTION: int = getattr(sqlcipher3, "SQLITE_FUNCTION")


class EventMetadataReader:
    """Private EventLog helper; no payload or write API."""

    def __init__(self, path: Path, key_pragma: str) -> None:
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="adminme-event-log-metadata"
        )
        self._close_call: concurrent.futures.Future[None] | None = None
        try:
            self._conn = self._executor.submit(
                self._open_connection, path, key_pragma
            ).result()
        except BaseException:
            self._executor.shutdown(wait=True, cancel_futures=False)
            raise

    @staticmethod
    def _open_connection(path: Path, key_pragma: str) -> sqlcipher3.Connection:
        conn: sqlcipher3.Connection | None = None
        try:
            conn = sqlcipher3.connect(
                f"{path.resolve().as_uri()}?mode=ro",
                uri=True,
                isolation_level=None,
                check_same_thread=True,
            )
            conn.execute(f'PRAGMA key = "{key_pragma}"').close()
            conn.execute("PRAGMA query_only=ON").close()
            # Counts/IDs use narrow indexes, never the large Finance payloads.
            conn.execute("PRAGMA cache_size=-256").close()
            conn.execute("SELECT count(*) FROM sqlite_master").close()
            conn.set_authorizer(EventMetadataReader._authorize)
            return conn
        except BaseException:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    _log.exception("failed to close rejected event metadata reader")
            raise

    @staticmethod
    def _authorize(
        action: int,
        first: str | None,
        second: str | None,
        database: str | None,
        _trigger: str | None,
    ) -> int:
        if action == _SQLITE_SELECT:
            return _SQLITE_OK
        if (
            action == _SQLITE_READ
            and database == "main"
            and first == "events"
            and second in {"event_id", "type", ""}
        ):
            return _SQLITE_OK
        if action == _SQLITE_FUNCTION and second == "count":
            return _SQLITE_OK
        return _SQLITE_DENY

    def _query(self, sql: str, params: tuple[str, ...]) -> Any:
        cursor = self._conn.cursor()
        try:
            return cursor.execute(sql, params).fetchone()
        finally:
            cursor.close()

    async def _run(self, sql: str, params: tuple[str, ...]) -> Any:
        if self._close_call is not None:
            raise RuntimeError("event metadata reader is closed")
        # Admission and submission have no await between them. Close is queued
        # behind accepted work, including a native read whose caller cancelled.
        call = self._executor.submit(self._query, sql, params)
        return await asyncio.wrap_future(call)

    async def latest_event_id(self, types: tuple[str, ...] | None) -> str | None:
        where = ""
        if types:
            where = " WHERE type IN (" + ",".join("?" for _ in types) + ")"
        row = await self._run(
            "SELECT event_id FROM events" + where + " ORDER BY event_id DESC LIMIT 1",
            types or (),
        )
        return str(row[0]) if row else None

    async def count_since(
        self, cursor: str | None, types: tuple[str, ...] | None
    ) -> int:
        sql, params = self._count_query(cursor, types)
        return int((await self._run(sql, params))[0])

    async def count_since_many(
        self, queries: tuple[tuple[str | None, tuple[str, ...] | None], ...]
    ) -> list[int]:
        if self._close_call is not None:
            raise RuntimeError("event metadata reader is closed")
        if not queries:
            return []
        # One dispatch for this registered subscriber group. Every query still
        # opens/closes its own cursor in autocommit and sees committed WAL state.
        call = self._executor.submit(self._count_many, queries)
        return await asyncio.wrap_future(call)

    def _count_many(
        self, queries: tuple[tuple[str | None, tuple[str, ...] | None], ...]
    ) -> list[int]:
        return [int(self._query(*self._count_query(*query))[0]) for query in queries]

    @staticmethod
    def _count_query(
        cursor: str | None, types: tuple[str, ...] | None
    ) -> tuple[str, tuple[str, ...]]:
        where: list[str] = []
        params: tuple[str, ...] = ()
        if cursor is not None:
            where.append("event_id > ?")
            params += (cursor,)
        if types:
            where.append("type IN (" + ",".join("?" for _ in types) + ")")
            params += types
        # An unfiltered COUNT(*) authorizes a table-only read. SQLite passes
        # the FROM clause's database qualifier for that callback, so name main
        # explicitly; do not broaden the authorizer to accept unknown schemas.
        sql = "SELECT count(*) FROM main.events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        return sql, params

    def submit_close(self) -> concurrent.futures.Future[None]:
        if self._close_call is None:
            self._close_call = self._executor.submit(self._conn.close)
        return self._close_call

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)
