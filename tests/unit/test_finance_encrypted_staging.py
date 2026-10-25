"""Focused disposable-stage correctness; executed only in an admitted cohort."""

from dataclasses import replace
import fcntl
from importlib.metadata import version
import json
import os
from pathlib import Path
import secrets
import sys
from types import SimpleNamespace

import pytest

from adminme.events.log import EventLog
from adminme.finance.publication import PublicationError
from tests.fixtures.finance_staging import append_staged, fresh_controller_recovery
from tests.fixtures.finance_staging_store import (
    CHUNK,
    MAX_DB,
    StageError,
    StageStore,
    StagedBlobs,
    StagedEvents,
    cleanup,
    directory,
    identity,
    receipt,
)

pytestmark = pytest.mark.skipif(
    os.getenv("ADMINME_SYNTHETIC_STAGING_CORRECTNESS") != "1"
    or sys.platform != "linux",
    reason="explicitly admitted hosted synthetic correctness only",
)


@pytest.fixture
def stage(tmp_path):
    root = tmp_path / "synthetic-store-unit"
    root.mkdir(mode=0o700)
    header = {
        "operation": "a" * 32,
        "tenant_id": "synthetic-tenant",
        "owner_id": "synthetic-owner",
        "approval_id": "synthetic-approval",
        "manifest": {"binding": {"book_id": "synthetic-book"}},
    }
    store = StageStore(
        root,
        identity(header, "b" * 64),
        secrets.token_bytes(32),
        secrets.token_bytes(32),
    )
    try:
        yield store
    finally:
        store.close()
        cleanup(root, header["operation"])


def sealed(stage, count=2):
    stage.put("component", "index", b'{"synthetic":"secret-marker"}')
    events = StagedEvents(stage)
    for i in range(count):
        events.append(
            receipt(
                ("synthetic-tenant", "synthetic-owner", "synthetic-book"),
                "finance.publication_accepted",
                "accepted_books",
                "synthetic-event-" + str(i),
                json.dumps({"synthetic": i}).encode(),
            )
        )
    stage.seal(
        {"expected_heads": [None, None]},
        ["index"],
        ["finance.publication_accepted"] * count,
    )
    return events


def event_count(log):
    return log._conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_account_id='synthetic-book'"
    ).fetchone()[0]


_PROVIDER_ROW = object()


def observe_hmac_connections(monkeypatch, *, disabled=False, row=_PROVIDER_ROW):
    from tests.fixtures import finance_staging_store

    connect = finance_staging_store.sqlcipher3.connect
    connections, readings = [], []

    class ObservedConnection:
        def __init__(self, raw):
            self.raw, self.closed = raw, False

        def execute(self, sql, *args):
            cursor = self.raw.execute(sql, *args)
            if disabled and sql.startswith("PRAGMA key="):
                self.raw.execute("PRAGMA cipher_use_hmac=OFF").close()
            if sql == "PRAGMA cipher_use_hmac":
                actual = cursor.fetchone()
                cursor.close()
                readings.append(
                    {
                        "pragma": "cipher_use_hmac",
                        "row_type": type(actual).__name__,
                        "row_repr": repr(actual)[:128],
                        "value_types": [type(v).__name__ for v in actual]
                        if isinstance(actual, tuple)
                        else [],
                    }
                )
                return SimpleNamespace(
                    fetchone=lambda: actual if row is _PROVIDER_ROW else row
                )
            return cursor

        def close(self):
            self.raw.close()
            self.closed = True

    def observed_connect(*args, **kwargs):
        connection = ObservedConnection(connect(*args, **kwargs))
        connections.append(connection)
        return connection

    monkeypatch.setattr(finance_staging_store.sqlcipher3, "connect", observed_connect)
    return connections, readings


@pytest.mark.parametrize("disabled", [False, True], ids=["enabled", "disabled"])
def test_pinned_provider_hmac_and_constructor_cleanup(tmp_path, monkeypatch, disabled):
    root = tmp_path / "synthetic-store-provider"
    root.mkdir(mode=0o700)
    context = {"operation": "e" * 32}
    connections, readings = observe_hmac_connections(monkeypatch, disabled=disabled)
    store = None
    descriptors = set(os.listdir("/proc/self/fd"))
    try:
        if disabled:
            with pytest.raises(StageError, match="^stage_page_authentication$"):
                StageStore(
                    root, context, secrets.token_bytes(32), secrets.token_bytes(32)
                )
        else:
            store = StageStore(
                root, context, secrets.token_bytes(32), secrets.token_bytes(32)
            )
        assert readings == [
            {
                "pragma": "cipher_use_hmac",
                "row_type": "tuple",
                "row_repr": "('0',)" if disabled else "('1',)",
                "value_types": ["str"],
            }
        ]
    finally:
        if store is not None:
            store.close()
            cleanup(root, context["operation"])
        evidence = {
            "provider_package": version("sqlcipher3"),
            "disabled_by_test": disabled,
            "readings": readings,
            "connections_closed": all(c.closed for c in connections),
            "stage_removed": not list(root.iterdir()),
            "descriptors_restored": set(os.listdir("/proc/self/fd")) == descriptors,
        }
        output = Path(os.environ["FINANCE_STORAGE_ARTIFACT_DIR"])
        output.mkdir(parents=True, exist_ok=True)
        name = "stage-provider-" + ("disabled" if disabled else "enabled") + ".json"
        (output / name).write_text(json.dumps(evidence, indent=2) + "\n")
    assert evidence["connections_closed"]
    assert evidence["stage_removed"] and evidence["descriptors_restored"]


@pytest.mark.parametrize(
    "row",
    [
        (0,),
        ("0",),
        None,
        (),
        (None,),
        ("ON",),
        ("true",),
        ("01",),
        ("1 ",),
        (1,),
        (True,),
        (1.0,),
        (b"1",),
        ("1", "1"),
        ["1"],
    ],
)
def test_hmac_guard_rejects_disabled_unknown_and_missing_rows(
    tmp_path, monkeypatch, row
):
    root = tmp_path / "synthetic-store-hmac-rejection"
    root.mkdir(mode=0o700)
    connections, _ = observe_hmac_connections(monkeypatch, row=row)
    with pytest.raises(StageError, match="^stage_page_authentication$"):
        StageStore(
            root,
            {"operation": "f" * 32},
            secrets.token_bytes(32),
            secrets.token_bytes(32),
        )
    assert len(connections) == 1 and connections[0].closed
    assert not list(root.iterdir())


def test_encrypted_exact_roundtrip_seal_and_lazy_blobs(stage):
    raw = b"synthetic-secret-" + b"x" * (CHUNK + 17)
    stage.put("component", "blob:one", raw)
    stage.put("component", "blob:two", b"second")
    mapping = StagedBlobs(stage, ["one", "two"])
    assert list(mapping) == ["one", "two"] and mapping["one"] == raw
    assert stage.conn.execute(
        "SELECT ordinal,length(body) FROM chunks WHERE name='blob:one' ORDER BY ordinal"
    ).fetchall() == [(0, CHUNK), (1, len(raw) - CHUNK)]
    assert raw[:16] not in (stage.path / "stage.db").read_bytes()
    assert stage.conn.execute("PRAGMA cipher_use_hmac").fetchone() == ("1",)
    assert stage.conn.execute("PRAGMA max_page_count").fetchone()[0] * 4096 == MAX_DB
    assert stage.conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_wrong_key_and_context_rejected(stage):
    sealed(stage)
    stage.close_writer()
    key = stage.encryption_key
    stage.encryption_key = secrets.token_bytes(32)
    try:
        with pytest.raises(Exception):
            conn = None
            try:
                conn = stage.connect(readonly=True)
                stage.check_seal(conn)
            finally:
                if conn is not None:
                    conn.close()
    finally:
        stage.encryption_key = key
    conn = stage.connect(readonly=True)
    try:
        stage.binding = "c" * 64
        with pytest.raises(StageError):
            stage.check_seal(conn)
    finally:
        conn.close()


@pytest.mark.parametrize(
    "corruption",
    [
        "component_body",
        "event_body",
        "missing_component",
        "extra_object",
        "orphan_chunk",
        "missing_chunk",
        "extra_chunk",
        "chunk_ordinal",
        "chunk_length",
        "oversized_header",
        "seal_body",
        "seal_mac",
        "object_mac",
        "event_order",
    ],
)
def test_complete_inventory_and_after_seal_corruption_reject(stage, corruption):
    sealed(stage)
    sql, parameters = {
        "component_body": (
            "UPDATE chunks SET body=? WHERE kind='component'",
            (b"changed",),
        ),
        "event_body": (
            "UPDATE chunks SET body=? WHERE kind='event' AND name='1'",
            (b"changed",),
        ),
        "missing_component": ("DELETE FROM objects WHERE kind='component'", ()),
        "extra_object": (
            "INSERT INTO objects SELECT kind,'extra',size,digest,metadata,mac FROM objects WHERE kind='component'",
            (),
        ),
        "orphan_chunk": (
            "INSERT INTO chunks VALUES('component','orphan',0,?)",
            (b"x",),
        ),
        "missing_chunk": ("DELETE FROM chunks WHERE kind='event' AND name='1'", ()),
        "extra_chunk": ("INSERT INTO chunks VALUES('event','1',1,?)", (b"x",)),
        "chunk_ordinal": ("UPDATE chunks SET ordinal=7 WHERE kind='component'", ()),
        "chunk_length": (
            "UPDATE chunks SET body=zeroblob(?) WHERE kind='component'",
            (CHUNK + 1,),
        ),
        "oversized_header": (
            "UPDATE objects SET metadata=? WHERE kind='component'",
            ("x" * 20000,),
        ),
        "seal_body": ("UPDATE seal SET body='{}'", ()),
        "seal_mac": ("UPDATE seal SET mac=?", ("0" * 64,)),
        "object_mac": ("UPDATE objects SET mac=? WHERE kind='component'", ("0" * 64,)),
        "event_order": (
            "UPDATE objects SET name='9' WHERE kind='event' AND name='1'",
            (),
        ),
    }[corruption]
    stage.conn.execute(sql, parameters).close()
    with pytest.raises((StageError, KeyError)):
        stage.check_seal(stage.conn)


def test_seal_authenticates_both_inventories_and_domains(stage):
    sealed(stage)
    body, signature = stage.conn.execute("SELECT body,mac FROM seal").fetchone()
    value = json.loads(body)
    assert [r[1] for r in value["components"]] == ["index"]
    assert [r[1] for r in value["events"]] == ["0", "1"]
    assert signature == stage.mac(value, "seal")
    assert signature != stage.mac(value, "object")
    with pytest.raises(StageError):
        stage.put("component", "late", b"x")


@pytest.mark.parametrize("bad", ["missing", "duplicate", "wrong_type"])
def test_seal_requires_exact_component_and_event_contract(stage, bad):
    stage.put("component", "index", b"index")
    events = StagedEvents(stage)
    events.append(
        receipt(
            ("synthetic-tenant", "synthetic-owner", "synthetic-book"),
            "finance.publication_accepted",
            "accepted_books",
            "event",
            b"{}",
        )
    )
    names = (
        []
        if bad == "missing"
        else ["index", "index"]
        if bad == "duplicate"
        else ["index"]
    )
    kinds = (
        ["finance.projection_verified"]
        if bad == "wrong_type"
        else ["finance.publication_accepted"]
    )
    with pytest.raises(StageError):
        stage.seal({}, names, kinds)


@pytest.mark.parametrize("limit", ["count", "total", "single", "metadata", "disk"])
def test_explicit_containment_limits(stage, monkeypatch, limit):
    raw, metadata = b"x", None
    if limit == "count":
        stage.counts["component"] = 131
    elif limit == "total":
        stage.totals["component"] = 44 * 1024 * 1024
    elif limit == "single":
        raw = b"x" * (8 * 1024 * 1024 + 1)
    elif limit == "metadata":
        metadata = {"x": "x" * 16384}
    else:
        from tests.fixtures import finance_staging_store

        monkeypatch.setattr(finance_staging_store, "MAX_DISK", 1)
    with pytest.raises(StageError):
        stage.put("component", "limit", raw, metadata)
    assert stage.conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0] == 0


def test_duplicate_chunk_and_object_rejected(stage):
    stage.put("component", "index", b"x")
    with pytest.raises(Exception):
        stage.put("component", "index", b"y")
    with pytest.raises(Exception):
        stage.conn.execute(
            "INSERT INTO chunks VALUES('component','index',0,?)", (b"x",)
        )


def test_lock_and_path_ownership(stage, tmp_path):
    with pytest.raises(BlockingIOError):
        cleanup(stage.path.parent, stage.context["operation"])
    fd = os.open(stage.path / "owner.lock", os.O_RDWR)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)
    with pytest.raises(StageError):
        directory(stage.path.parent, "../escape")
    root = tmp_path / "synthetic-store-link"
    root.symlink_to(stage.path.parent, target_is_directory=True)
    with pytest.raises(StageError):
        directory(root, "a" * 32)


def test_constructor_failure_after_lock_closes_and_cleans(tmp_path, monkeypatch):
    root = tmp_path / "synthetic-store-failure"
    root.mkdir(mode=0o700)
    context = {"operation": "d" * 32}
    open_file = os.open
    observed = []

    def fail_db(path, flags, *args, **kwargs):
        if Path(path).name == "stage.db":
            raise OSError("synthetic creation fault")
        fd = open_file(path, flags, *args, **kwargs)
        observed.append(fd)
        return fd

    monkeypatch.setattr(os, "open", fail_db)
    with pytest.raises(OSError, match="synthetic creation fault"):
        StageStore(root, context, secrets.token_bytes(32), secrets.token_bytes(32))
    assert not list(root.iterdir())
    for fd in observed:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize(
    "fault", ["none", "revoke", "late_event", "late_component", "wrong_scope"]
)
async def test_streamed_atomic_append_guards_and_mid_append_corruption(
    stage, fault, monkeypatch
):
    events = sealed(stage)
    log = EventLog(stage.path.parent / "events.db", secrets.token_bytes(32))
    calls, yielded = [], []
    event = events.event

    def observed(index, conn=None):
        value = event(index, conn)
        if conn is not None:
            yielded.append(index)
            if index == 1 and fault in {"late_event", "late_component"}:
                # First row has been inserted, but nothing is committed. The
                # final seal recheck must catch even an already-consumed row.
                writer = stage.connect()
                try:
                    kind = "event" if fault == "late_event" else "component"
                    writer.execute(
                        "UPDATE chunks SET body=? WHERE kind=? AND name=?",
                        (b"changed", kind, "0" if kind == "event" else "index"),
                    ).close()
                finally:
                    writer.close()
        return value

    monkeypatch.setattr(events, "event", observed)

    def guard():
        calls.append(len(calls) + 1)
        if fault == "revoke" and len(calls) == 2:
            raise PublicationError("synthetic_revoked")

    scope = (
        "synthetic-tenant",
        "synthetic-owner",
        "wrong" if fault == "wrong_scope" else "synthetic-book",
    )
    try:
        if fault == "none":
            ids = await append_staged(log, scope, events, guard, None)
            assert len(ids) == 2 and calls == [1, 2] and yielded == [0, 1]
            assert await log._run_on_connection_thread(event_count, log) == 2
        else:
            with pytest.raises((ValueError, PublicationError)):
                await append_staged(log, scope, events, guard, None)
            assert await log._run_on_connection_thread(event_count, log) == 0
            if fault == "wrong_scope":
                assert not calls
            else:
                assert calls[0] == 1
            if fault == "revoke":
                assert calls == [1, 2]
    finally:
        await log.close()


def test_fresh_controller_without_durable_anchor_refuses_reconciliation():
    with pytest.raises(PublicationError, match="synthetic_controller_outcome_unknown"):
        fresh_controller_recovery()
    # Function takes no volatile refs or expired approval and performs no retry.


def test_cross_context_event_receipt_rejected(stage):
    event = receipt(
        ("synthetic-tenant", "synthetic-owner", "synthetic-book"),
        "finance.publication_accepted",
        "accepted_books",
        "event",
        b"{}",
    )
    with pytest.raises(PublicationError):
        StagedEvents(stage).append(replace(event, owner_id="other-owner"))
    for field in ("tenant_id", "owner_id", "book_id"):
        wrong = replace(event, **{field: "different-synthetic-scope"})
        wrong = replace(wrong, _receipt=wrong._signature())
        with pytest.raises(StageError, match="stage_event_scope"):
            StagedEvents(stage).append(wrong)


async def test_prepared_reference_lookup_keeps_stage_connection_on_owner_thread(
    stage, monkeypatch
):
    events = sealed(stage)
    references = events.reference_metadata()
    assert len(references) == 2
    assert all(
        set(row.__dict__)
        == {"tenant_id", "owner_id", "book_id", "type", "idempotency_key", "sha256"}
        for row in references
    )
    log = EventLog(stage.path.parent / "reference-events.db", secrets.token_bytes(32))

    def deny_stage_read(*args, **kwargs):
        raise AssertionError("payload/stage connection crossed into EventLog thread")

    monkeypatch.setattr(stage, "read", deny_stage_read)
    monkeypatch.setattr(stage, "headers", deny_stage_read)

    def lookup():
        return {
            row.idempotency_key: log._conn.execute(
                "SELECT event_id FROM events WHERE tenant_id=? AND idempotency_key=?",
                (row.tenant_id, row.idempotency_key),
            ).fetchone()
            for row in references
        }

    try:
        assert await log._run_on_connection_thread(lookup) == {
            "synthetic-event-0": None,
            "synthetic-event-1": None,
        }
    finally:
        await log.close()
