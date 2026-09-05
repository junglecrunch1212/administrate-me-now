"""Gate 8 release hardening at storage, process, and tenant boundaries."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlcipher3

from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config


KEY_A = b"a" * 32
KEY_B = b"b" * 32


def _key_pragma(key: bytes) -> str:
    return f"x'{key.hex()}'"


def _envelope(*, tenant_id: str, owner: str, title: str) -> EventEnvelope:
    return EventEnvelope(
        event_id="ev_01RELEASECANDIDATE",
        event_at_ms=1_786_000_000_000,
        tenant_id=tenant_id,
        type="task.created",
        schema_version=1,
        occurred_at="2026-08-04T12:00:00Z",
        source_adapter="gate8-fixture",
        source_account_id="credential-free",
        owner_scope=f"private:{owner}",
        visibility_scope=f"private:{owner}",
        sensitivity="normal",
        idempotency_key="gate8:identical-command",
        payload={
            "task_id": "task-identical",
            "title": title,
            "owner_member_id": owner,
        },
    )


def test_v1_encrypted_log_migrates_in_place_and_preserves_append_only_truth(
    tmp_path: Path,
) -> None:
    """A real pre-0002 SQLCipher file upgrades through every retained migration."""

    db_path = tmp_path / "legacy.db"
    connection = sqlcipher3.connect(str(db_path), isolation_level=None)
    connection.execute(f'PRAGMA key = "{_key_pragma(KEY_A)}"')
    migration = (
        Path(__file__).parents[2]
        / "adminme"
        / "events"
        / "migrations"
        / "0001_initial.sql"
    ).read_text(encoding="utf-8")
    connection.executescript(migration)
    connection.execute(
        "CREATE TABLE _schema_version (version INTEGER PRIMARY KEY, applied_at_ms INTEGER NOT NULL)"
    )
    connection.execute("INSERT INTO _schema_version VALUES (1, 1)")
    connection.execute(
        "INSERT INTO events "
        "(event_id, event_at_ms, tenant_id, owner_scope, type, version, "
        "correlation_id, source, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ev_legacy",
            1_700_000_000_000,
            "tenant-legacy",
            "private:legacy-member",
            "legacy.fixture",
            2,
            "cor_legacy",
            None,
            '{"sentinel":"legacy-preserved"}',
        ),
    )
    connection.close()

    raw = db_path.read_bytes()
    assert b"legacy-preserved" not in raw

    async def migrate_and_read() -> dict[str, object] | None:
        log = EventLog(db_path, KEY_A)
        try:
            return await log.get("ev_legacy")
        finally:
            await log.close()

    stored = asyncio.run(migrate_and_read())
    assert stored is not None
    assert stored["schema_version"] == 2
    assert stored["visibility_scope"] == "private:legacy-member"
    assert stored["payload"] == {"sentinel": "legacy-preserved"}

    migrated = sqlcipher3.connect(str(db_path))
    migrated.execute(f'PRAGMA key = "{_key_pragma(KEY_A)}"')
    assert [
        row[0] for row in migrated.execute("SELECT version FROM _schema_version")
    ] == [
        1,
        2,
            3,
            4,
            5,
        ]
    indexes = {row[1] for row in migrated.execute("PRAGMA index_list(events)")}
    assert "idx_events_type_id_time" in indexes
    with pytest.raises(sqlcipher3.DatabaseError, match="append-only"):
        migrated.execute("DELETE FROM events WHERE event_id='ev_legacy'")
    migrated.close()

    with pytest.raises(sqlcipher3.DatabaseError):
        EventLog(db_path, KEY_B)


def test_two_instances_accept_identical_ids_without_cross_tenant_leakage(
    tmp_path: Path,
) -> None:
    """Separate instance paths and keys form the tenant boundary, even for equal IDs."""

    instance_a = load_instance_config(tmp_path / "instance-a")
    instance_b = load_instance_config(tmp_path / "instance-b")

    async def exercise() -> tuple[dict[str, object] | None, dict[str, object] | None]:
        log_a = EventLog(instance_a, KEY_A)
        log_b = EventLog(instance_b, KEY_B)
        try:
            await asyncio.gather(
                log_a.append(
                    _envelope(
                        tenant_id=instance_a.tenant_id,
                        owner="member-a",
                        title="instance-a only",
                    )
                ),
                log_b.append(
                    _envelope(
                        tenant_id=instance_b.tenant_id,
                        owner="member-b",
                        title="instance-b only",
                    )
                ),
            )
            return await log_a.get("ev_01RELEASECANDIDATE"), await log_b.get(
                "ev_01RELEASECANDIDATE"
            )
        finally:
            await log_a.close()
            await log_b.close()

    event_a, event_b = asyncio.run(exercise())
    assert event_a is not None and event_b is not None
    assert event_a["tenant_id"] == instance_a.tenant_id
    assert event_b["tenant_id"] == instance_b.tenant_id
    assert event_a["payload"]["title"] == "instance-a only"  # type: ignore[index]
    assert event_b["payload"]["title"] == "instance-b only"  # type: ignore[index]
    assert instance_a.event_log_path != instance_b.event_log_path

    with pytest.raises(sqlcipher3.DatabaseError):
        EventLog(instance_a, KEY_B)
    with pytest.raises(sqlcipher3.DatabaseError):
        EventLog(instance_b, KEY_A)


def test_committed_append_survives_forced_process_exit(tmp_path: Path) -> None:
    """A hard exit after commit cannot erase the durable event or corrupt recovery."""

    db_path = tmp_path / "forced-exit.db"
    script = r"""
import asyncio
import os
import sys
from pathlib import Path
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog

async def main() -> None:
    log = EventLog(Path(sys.argv[1]), bytes.fromhex(sys.argv[2]))
    await log.append(EventEnvelope(
        event_id="ev_forced_exit",
        event_at_ms=1786000000000,
        tenant_id="tenant-crash",
        type="task.created",
        schema_version=1,
        occurred_at="2026-08-04T12:00:00Z",
        source_adapter="gate8-fixture",
        source_account_id="credential-free",
        owner_scope="private:member-crash",
        visibility_scope="private:member-crash",
        sensitivity="normal",
            payload={
                "task_id": "task-crash",
                "title": "survive forced exit",
                "owner_member_id": "member-crash",
        },
    ))
    os._exit(86)

asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(db_path), KEY_A.hex()],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2])},
    )
    assert result.returncode == 86, result.stderr

    async def recover() -> tuple[dict[str, object] | None, int]:
        log = EventLog(db_path, KEY_A)
        try:
            return await log.get("ev_forced_exit"), await log.count()
        finally:
            await log.close()

    stored, count = asyncio.run(recover())
    assert count == 1
    assert stored is not None
    assert stored["payload"] == {
        "task_id": "task-crash",
        "title": "survive forced exit",
        "owner_member_id": "member-crash",
    }
