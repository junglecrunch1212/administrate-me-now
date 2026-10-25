"""Disposable encrypted unpublished storage. Never imported by adminme.

SQLCipher supplies encrypted pages/journals. A separate fresh test MAC key binds
objects and the sealed inventory to one source/operation/approval/owner/book.
No stage key or unpublished-stage recovery format is a production decision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
from types import SimpleNamespace
import re
import stat

import sqlcipher3

from adminme.finance.publication import canonical_bytes
from adminme.finance.worker import ValidatedFinanceEvent

MIB = 1024 * 1024
CHUNK = 65536
MAX_INPUT = 44 * MIB
MAX_EVENT = 9 * MIB
MAX_EVENTS = 80 * MIB
MAX_DB = 192 * MIB
MAX_DISK = 400 * MIB
MAX_METADATA = 16384
MAX_OBJECTS = 262


class StageError(ValueError):
    """Safe fixed reason; never include a path, key or source payload."""


def identity(header, artifact):
    return {
        "contract": "synthetic-finance-unpublished-stage.v1",
        "artifact": artifact,
        "operation": header["operation"],
        "tenant_id": header["tenant_id"],
        "owner_id": header["owner_id"],
        "book_id": header["manifest"]["binding"]["book_id"],
        "approval_id": header["approval_id"],
        "manifest_sha256": hashlib.sha256(
            canonical_bytes(header["manifest"])
        ).hexdigest(),
    }


def directory(root, operation):
    if not re.fullmatch(r"[0-9a-f]{32}", operation):
        raise StageError("stage_operation")
    root = Path(root)
    if root.is_symlink() or not root.name.startswith("synthetic-store-"):
        raise StageError("stage_root")
    info = root.stat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise StageError("stage_root_mode")
    path = root / ("unpublished-" + operation)
    if path.is_symlink():
        raise StageError("stage_symlink")
    return path


def cleanup(root, operation):
    """Controller calls only after confirmed reap; flock also rejects an owner."""
    path = directory(root, operation)
    if not path.exists():
        return
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise StageError("stage_directory_mode")
    allowed = {"owner.lock", "stage.db", "stage.db-journal"}
    if any(
        p.name not in allowed or p.is_symlink() or not p.is_file()
        for p in path.iterdir()
    ):
        raise StageError("stage_cleanup_entries")
    fd = os.open(path / "owner.lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for child in path.iterdir():
            child.unlink()
        path.rmdir()
    finally:
        os.close(fd)


class StageStore:
    def __init__(self, root, context, encryption_key, mac_key):
        if len(encryption_key) != 32 or len(mac_key) != 32:
            raise StageError("stage_key")
        self.context = context
        self.binding = hashlib.sha256(canonical_bytes(context)).hexdigest()
        self.encryption_key, self.mac_key = encryption_key, mac_key
        self.path = directory(root, context["operation"])
        self.lock = self.conn = self.sealed = None
        self.counts = {"component": 0, "event": 0}
        self.totals = {"component": 0, "event": 0}
        created = False
        try:
            self.path.mkdir(mode=0o700)
            created = True
            self.lock = os.open(
                self.path / "owner.lock",
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd = os.open(
                self.path / "stage.db",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            os.close(fd)
            self.conn = self.connect()
            self.conn.execute(
                "CREATE TABLE objects(kind TEXT, name TEXT, size INTEGER, digest TEXT, metadata TEXT, mac TEXT, PRIMARY KEY(kind,name))"
            ).close()
            self.conn.execute(
                "CREATE TABLE chunks(kind TEXT, name TEXT, ordinal INTEGER, body BLOB, PRIMARY KEY(kind,name,ordinal))"
            ).close()
            self.conn.execute(
                "CREATE TABLE seal(singleton INTEGER PRIMARY KEY CHECK(singleton=1), body TEXT, mac TEXT)"
            ).close()
        except BaseException:
            self.close()
            if created:
                if (self.path / "owner.lock").exists():
                    cleanup(root, context["operation"])
                else:
                    self.path.rmdir()
            raise

    def connect(self, readonly=False):
        path = self.path / "stage.db"
        if (
            path.is_symlink()
            or path.stat().st_uid != os.getuid()
            or path.stat().st_mode & 0o077
        ):
            raise StageError("stage_file_mode")
        target = path.as_uri() + "?mode=ro" if readonly else str(path)
        conn = sqlcipher3.connect(target, uri=readonly, isolation_level=None, timeout=0)
        try:
            conn.execute(f"PRAGMA key=\"x'{self.encryption_key.hex()}'\"").close()
            # Pinned sqlcipher3 0.6.2 emits this PRAGMA as SQL TEXT, not INTEGER.
            if conn.execute("PRAGMA cipher_use_hmac").fetchone() != ("1",):
                raise StageError("stage_page_authentication")
            conn.execute("PRAGMA temp_store=MEMORY").close()
            conn.execute("PRAGMA cache_size=-512").close()
            if readonly:
                conn.execute("PRAGMA query_only=ON").close()
            else:
                conn.execute("PRAGMA journal_mode=DELETE").close()
                conn.execute("PRAGMA synchronous=FULL").close()
                conn.execute("PRAGMA journal_size_limit=0").close()
                if conn.execute("PRAGMA page_size").fetchone() != (4096,):
                    raise StageError("stage_page_size")
                conn.execute(f"PRAGMA max_page_count={MAX_DB // 4096}").close()
            return conn
        except BaseException:
            conn.close()
            raise

    def mac(self, value, domain="object"):
        encoded = canonical_bytes(
            {
                "domain": "synthetic-finance-stage.v1/" + domain,
                "binding": self.binding,
                "value": value,
            }
        )
        return hmac.new(self.mac_key, encoded, hashlib.sha256).hexdigest()

    def disk_check(self):
        total = 0
        for path in self.path.iterdir():
            if path.is_symlink() or path.name not in {
                "owner.lock",
                "stage.db",
                "stage.db-journal",
            }:
                raise StageError("stage_disk_entries")
            total += path.stat().st_size
        if total > MAX_DISK:
            raise StageError("stage_disk_limit")

    def put(self, kind, name, raw, metadata=None):
        maximum = 8 * MIB if kind == "component" else MAX_EVENT
        total_limit = MAX_INPUT if kind == "component" else MAX_EVENTS
        if (
            self.sealed is not None
            or self.conn is None
            or kind not in self.counts
            or not isinstance(raw, bytes)
            or not 0 < len(raw) <= maximum
            or not isinstance(name, str)
            or len(name) > 160
            or self.counts[kind] >= 131
            or self.totals[kind] + len(raw) > total_limit
        ):
            raise StageError("stage_object_limit")
        metadata = metadata or {}
        encoded = canonical_bytes(metadata)
        if len(encoded) > MAX_METADATA:
            raise StageError("stage_metadata_limit")
        record = [
            kind,
            name,
            len(raw),
            hashlib.sha256(raw).hexdigest(),
            encoded.decode(),
        ]
        self.conn.execute("BEGIN IMMEDIATE").close()
        try:
            self.conn.execute(
                "INSERT INTO objects VALUES(?,?,?,?,?,?)", (*record, self.mac(record))
            ).close()
            for ordinal, offset in enumerate(range(0, len(raw), CHUNK)):
                self.conn.execute(
                    "INSERT INTO chunks VALUES(?,?,?,?)",
                    (kind, name, ordinal, raw[offset : offset + CHUNK]),
                ).close()
                self.disk_check()
            self.conn.execute("COMMIT").close()
        except BaseException:
            self.conn.execute("ROLLBACK").close()
            raise
        self.counts[kind] += 1
        self.totals[kind] += len(raw)
        self.disk_check()

    def headers(self, conn, kind=None, name=None):
        # Bound every text column in SQL before Python allocates it. Even an
        # authenticated-encryption key holder cannot feed an unbounded header.
        query = "SELECT substr(kind,1,10),substr(name,1,161),size,substr(digest,1,65),substr(metadata,1,16385),substr(mac,1,65),length(kind),length(name),length(digest),length(metadata),length(mac) FROM objects"
        params = ()
        if kind is not None:
            query += " WHERE kind=? AND name=?"
            params = (kind, name)
        query += " ORDER BY kind,name LIMIT 263"
        cursor = conn.execute(query, params)
        rows, seen = [], set()
        try:
            for found in cursor:
                row = found[:6]
                if (
                    len(rows) >= MAX_OBJECTS
                    or not all(isinstance(row[i], str) for i in (0, 1, 3, 4, 5))
                    or row[0] not in {"component", "event"}
                    or found[6] != len(row[0])
                    or not 0 < found[7] <= 160
                    or found[8] != 64
                    or not 0 < found[9] <= MAX_METADATA
                    or not row[4].isascii()
                    or found[10] != 64
                    or type(row[2]) is not int
                    or not 0
                    < row[2]
                    <= (8 * MIB if row[0] == "component" else MAX_EVENT)
                    or (row[0], row[1]) in seen
                ):
                    raise StageError("stage_header_limit")
                if not hmac.compare_digest(row[5], self.mac(list(row[:5]))):
                    raise StageError("stage_object_authentication")
                seen.add((row[0], row[1]))
                rows.append(list(row))
        finally:
            cursor.close()
        return rows

    def inventory(self, conn):
        rows = self.headers(conn)
        expected_chunks = sum((row[2] + CHUNK - 1) // CHUNK for row in rows)
        cursor = conn.execute("SELECT COUNT(*) FROM (SELECT 1 FROM chunks LIMIT 2247)")
        try:
            actual_chunks = cursor.fetchone()[0]
        finally:
            cursor.close()
        if actual_chunks != expected_chunks:
            raise StageError("stage_chunk_inventory")
        # Each object's exact ordinal/length/hash check below accounts for every
        # row. The count equality therefore also excludes orphan/extra chunks.
        for row in rows:
            self.read(row[0], row[1], conn)
        return rows

    def read(self, kind, name, conn=None):
        conn = conn or self.conn
        if conn is None:
            raise StageError("stage_closed")
        rows = self.headers(conn, kind, name)
        if len(rows) != 1:
            raise StageError("stage_object_authentication")
        row = rows[0]
        raw, digest, expected = bytearray(), hashlib.sha256(), 0
        cursor = conn.execute(
            "SELECT ordinal,length(body),substr(body,1,?) FROM chunks WHERE kind=? AND name=? ORDER BY ordinal",
            (CHUNK + 1, kind, name),
        )
        try:
            for ordinal, size, body in cursor:
                if (
                    ordinal != expected
                    or size != min(CHUNK, row[2] - len(raw))
                    or not 0 < size <= CHUNK
                    or len(body) != size
                    or len(raw) + size > row[2]
                ):
                    raise StageError("stage_chunk_limit")
                raw.extend(body)
                digest.update(body)
                expected += 1
        finally:
            cursor.close()
        if len(raw) != row[2] or digest.hexdigest() != row[3]:
            raise StageError("stage_content_changed")
        return bytes(raw), json.loads(row[4])

    def seal(self, metadata, component_names, event_types):
        if self.sealed is not None or not self.counts["event"]:
            raise StageError("stage_seal_state")
        objects = self.inventory(self.conn)
        components = [row for row in objects if row[0] == "component"]
        events = [row for row in objects if row[0] == "event"]
        if (
            len(set(component_names)) != len(component_names)
            or {row[1] for row in components} != set(component_names)
            or {row[1] for row in events} != {str(i) for i in range(len(event_types))}
        ):
            raise StageError("stage_complete_inventory")
        events.sort(key=lambda row: int(row[1]))
        if [json.loads(row[4])["type"] for row in events] != event_types:
            raise StageError("stage_event_order")
        value = {
            "context": self.context,
            "components": components,
            "events": events,
            "metadata": metadata,
        }
        raw = canonical_bytes(value)
        if len(raw) > 128 * 1024:
            raise StageError("stage_seal_limit")
        signature = self.mac(value, "seal")
        self.conn.execute(
            "INSERT INTO seal VALUES(1,?,?)", (raw.decode(), signature)
        ).close()
        self.sealed = signature
        self.disk_check()

    def check_seal(self, conn):
        cursor = conn.execute(
            "SELECT length(body),substr(body,1,131073),substr(mac,1,65),length(mac) FROM seal LIMIT 2"
        )
        try:
            rows = cursor.fetchall()
        finally:
            cursor.close()
        if len(rows) != 1 or not 0 < rows[0][0] <= 131072 or rows[0][3] != 64:
            raise StageError("stage_seal_limit")
        row = rows[0]
        value = json.loads(row[1])
        if (
            value["context"] != self.context
            or row[2] != self.sealed
            or not hmac.compare_digest(row[2], self.mac(value, "seal"))
        ):
            raise StageError("stage_seal_changed")
        actual = self.inventory(conn)
        components = [r for r in actual if r[0] == "component"]
        events = [r for r in actual if r[0] == "event"]
        if {r[1] for r in events} != {str(i) for i in range(len(events))}:
            raise StageError("stage_event_order")
        events.sort(key=lambda r: int(r[1]))
        if value["components"] != components or value["events"] != events:
            raise StageError("stage_seal_changed")
        return value["metadata"]

    def close_writer(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def close(self):
        self.close_writer()
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None


class StagedBlobs(Mapping):
    def __init__(self, store, keys):
        self.store, self._keys = store, tuple(keys)

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)

    def __getitem__(self, key):
        if key not in self._keys:
            raise KeyError(key)
        return self.store.read("component", "blob:" + key)[0]


class StagedEvents(Sequence):
    def __init__(self, store):
        self.store = store

    def __len__(self):
        return self.store.counts["event"]

    def __getitem__(self, index):
        if not isinstance(index, int) or not 0 <= index < len(self):
            raise IndexError(index)
        return self.event(index)

    def event(self, index, conn=None):
        raw, metadata = self.store.read("event", str(index), conn)
        metadata["_receipt"] = bytes.fromhex(metadata["_receipt"])
        event = ValidatedFinanceEvent(payload_json=raw.decode(), **metadata)
        event.verify()
        return event

    def append(self, event):
        event.verify()
        context = self.store.context
        if (event.tenant_id, event.owner_id, event.book_id) != (
            context["tenant_id"],
            context["owner_id"],
            context["book_id"],
        ):
            raise StageError("stage_event_scope")
        metadata = {k: v for k, v in event.__dict__.items() if k != "payload_json"}
        metadata["_receipt"] = event._receipt.hex()
        self.store.put("event", str(len(self)), event.payload_json.encode(), metadata)

    def reference_metadata(self):
        # Read on the stage connection's owner thread. Only bounded immutable
        # identity fields cross into the EventLog thread for idempotency lookup.
        rows = [row for row in self.store.headers(self.store.conn) if row[0] == "event"]
        rows.sort(key=lambda row: int(row[1]))
        fields = {
            "tenant_id",
            "owner_id",
            "book_id",
            "type",
            "idempotency_key",
            "sha256",
        }
        return [
            SimpleNamespace(
                **{
                    key: value
                    for key, value in json.loads(row[4]).items()
                    if key in fields
                }
            )
            for row in rows
        ]

    def clear(self):
        # The durable batch has committed; controller deletes only after reap.
        self.store.close_writer()

    def validate_all(self):
        self.store.check_seal(self.store.conn)
        for event in self:
            event.verify()


def receipt(scope, kind, source, key, raw):
    event = ValidatedFinanceEvent(
        *scope, kind, source, key, raw.decode(), hashlib.sha256(raw).hexdigest(), b""
    )
    return replace(event, _receipt=event._signature())
