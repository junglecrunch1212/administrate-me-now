"""Actual protected predecessor and new readers share one encrypted event log."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

from adminme.events.log import EventLog


ROOT = Path(__file__).resolve().parents[2]
PREDECESSOR = "edc6713edd120a4256cf9cc273774e79755b1d0b"
KEY = b"m" * 32
SOURCE = "fixture.index.source"
TERMINAL = "fixture.index.terminal"


def _archive_predecessor(tmp_path: Path) -> Path:
    # Protected CI retains Git history; missing rollback source fails the proof.
    archived = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(ROOT), "archive", PREDECESSOR],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout
    destination = tmp_path / "protected-predecessor"
    destination.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(archived), mode="r:") as archive:
        archive.extractall(destination, filter="data")
    expected = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(ROOT),
            "show",
            f"{PREDECESSOR}:adminme/events/log.py",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout
    assert (destination / "adminme/events/log.py").read_bytes() == expected
    return destination


def _predecessor(archive: Path, db: Path, mode: str) -> dict:
    output = db.parent / f"archived-{mode}.json"
    environment = {
        key: value
        for key, value in os.environ.items()
        if key != "PYTHONPATH" and not key.startswith(("ADMINME_", "OPENCLAW_"))
    }
    environment["PYTHONPATH"] = os.pathsep.join((str(archive), str(archive / "bridge")))
    environment["PYTHONNOUSERSITE"] = "1"
    worker = (ROOT / "tests/fixtures/source_assurance_index_worker.py").read_text(
        "utf-8"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            worker,
            str(archive),
            str(db),
            str(output),
            mode,
        ],
        cwd=archive,
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(output.read_text("utf-8"))


async def _current_read(db: Path, *, read_only: bool) -> dict:
    log = EventLog(db, KEY, read_only=read_only)
    try:
        full = [event async for event in log.read_since()]
        metadata = [
            event
            async for event in log.read_metadata_since(
                types=[SOURCE, TERMINAL], include_payload_for=[TERMINAL], limit=500
            )
        ]

        def inventory() -> dict:
            connection = log._conn  # noqa: SLF001 - migration inventory
            return {
                "versions": [
                    row[0]
                    for row in connection.execute(
                        "SELECT version FROM _schema_version ORDER BY version"
                    )
                ],
                "indexes": [
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='index' ORDER BY name"
                    )
                ],
            }

        return {
            "event_count": len(full),
            "event_digest": hashlib.sha256(
                json.dumps(full, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "metadata": metadata,
            **await log._run_locked(inventory),  # noqa: SLF001
        }
    finally:
        await log.close()


async def test_old_and_new_readers_preserve_encrypted_metadata_across_index_migration(
    tmp_path: Path,
) -> None:
    archive = _archive_predecessor(tmp_path)
    database = tmp_path / "synthetic-events.db"
    original = _predecessor(archive, database, "seed")
    assert original["imports_from_archive"] is True
    assert original["versions"] == [1, 2, 3, 4, 5]
    assert "idx_events_type_id_time" in original["indexes"]
    assert "idx_events_type_id_time_causation" not in original["indexes"]
    expected = {
        key: value for key, value in original.items() if key != "imports_from_archive"
    }
    # Current read-only tooling must not require or install a future index.
    assert await _current_read(database, read_only=True) == expected
    unchanged = _predecessor(archive, database, "read-only")
    assert unchanged == original
    # A normal open migrates once; every immutable row and selected payload is
    # byte-equivalent, and the predecessor's forced index remains available.
    upgraded = await _current_read(database, read_only=False)
    assert upgraded["versions"] == [1, 2, 3, 4, 5, 6]
    assert set(upgraded["indexes"]) == set(original["indexes"]) | {
        "idx_events_type_id_time_causation"
    }
    for field in ("event_count", "event_digest", "metadata"):
        assert upgraded[field] == original[field]
    assert await _current_read(database, read_only=False) == upgraded
    assert await _current_read(database, read_only=True) == upgraded
    for mode in ("read-only", "read-write"):
        predecessor = _predecessor(archive, database, mode)
        assert predecessor["imports_from_archive"] is True
        assert {
            key: value
            for key, value in predecessor.items()
            if key != "imports_from_archive"
        } == upgraded
    assert await _current_read(database, read_only=True) == upgraded
