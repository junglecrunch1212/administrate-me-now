"""Gate 6 durable adapter cursor, lease, dedupe, and recovery contract."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from adminme.adapters.cursor_store import AdapterCursorStore


def test_cursor_lease_dedupe_circuit_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "adapter-cursors.db"
    store = AdapterCursorStore(path)
    assert store.claim("watcher", "worker-a", ttl_ms=100, now_ms=1_000)
    assert not store.claim("watcher", "worker-b", ttl_ms=100, now_ms=1_050)
    assert store.claim("watcher", "worker-b", ttl_ms=100, now_ms=1_101)
    assert not store.release("watcher", "worker-a")
    assert store.release("watcher", "worker-b")

    store.record_success(
        "watcher",
        provider_watermark="provider-page-7",
        external_id="external-1",
        fingerprint="sha-1",
        event_id="event-1",
    )
    assert store.dedupe_event("watcher", "external-1", "sha-1") == "event-1"
    assert store.latest_event("watcher", "external-1") == ("sha-1", "event-1")

    assert store.record_error("watcher").circuit_state == "closed"
    assert store.record_error("watcher").circuit_state == "closed"
    opened = store.record_error("watcher")
    assert opened.error_count == 3
    assert opened.circuit_state == "open"
    assert not store.claim("watcher", "worker-a", ttl_ms=100, now_ms=2_000)
    assert store.begin_recovery("watcher").circuit_state == "half_open"
    assert store.claim("watcher", "worker-a", ttl_ms=100, now_ms=2_001)
    store.record_success("watcher", provider_watermark="provider-page-8")
    assert store.get("watcher").circuit_state == "closed"
    assert store.get("watcher").error_count == 0
    store.close()

    reopened = AdapterCursorStore(path)
    try:
        cursor = reopened.get("watcher")
        assert cursor.provider_watermark == "provider-page-8"
        assert reopened.dedupe_event("watcher", "external-1", "sha-1") == "event-1"
        with sqlite3.connect(path) as inspection:
            columns = {
                row[1]
                for row in inspection.execute("PRAGMA table_info(adapter_cursors)")
            }
        assert not columns & {"body", "content", "credential", "payload", "token"}
    finally:
        reopened.close()


def test_cursor_success_requires_complete_dedupe_identity(tmp_path: Path) -> None:
    store = AdapterCursorStore(tmp_path / "cursor.db")
    try:
        try:
            store.record_success("watcher", external_id="only-one-field")
        except ValueError as exc:
            assert "supplied together" in str(exc)
        else:
            raise AssertionError("partial dedupe identity was accepted")
    finally:
        store.close()
