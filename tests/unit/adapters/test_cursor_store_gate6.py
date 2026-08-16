"""Gate 6 durable adapter cursor, lease, dedupe, and recovery contract."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from adminme.adapters.cursor_store import AdapterCursorStore, AdapterLeaseLost


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


def test_poll_completion_is_fenced_from_newer_failure(tmp_path: Path) -> None:
    store = AdapterCursorStore(tmp_path / "cursor.db")
    try:
        assert store.claim("calendar", "worker-a", ttl_ms=30_000)
        claimed = store.get("calendar")
        store.record_event(
            "calendar",
            external_id="calendar-source:fixture",
            fingerprint="fixture-fingerprint",
            event_id="event-1",
        )
        unchanged = store.get("calendar")
        assert unchanged.updated_at_ms == claimed.updated_at_ms
        assert unchanged.circuit_state == "closed"

        raced = store.record_error("calendar", open_after=1)
        assert raced.circuit_state == "open"
        with pytest.raises(RuntimeError, match="circuit changed"):
            store.complete_poll(
                "calendar",
                provider_watermark='{"version":1}',
                expected_updated_at_ms=claimed.updated_at_ms,
                lease_owner="worker-a",
            )
        durable = store.get("calendar")
        assert durable.provider_watermark is None
        assert durable.circuit_state == "open"
    finally:
        store.close()


def test_half_open_poll_closes_only_after_watermark_persists(tmp_path: Path) -> None:
    store = AdapterCursorStore(tmp_path / "cursor.db")
    try:
        for _ in range(3):
            store.record_error("calendar")
        store.begin_recovery("calendar")
        assert store.claim("calendar", "worker-a", ttl_ms=30_000)
        claimed = store.get("calendar")

        completed = store.complete_poll(
            "calendar",
            provider_watermark='{"version":1}',
            expected_updated_at_ms=claimed.updated_at_ms,
            lease_owner="worker-a",
        )
        assert completed.provider_watermark == '{"version":1}'
        assert completed.circuit_state == "closed"
        assert completed.error_count == 0
    finally:
        store.close()


def test_release_preserves_a_newer_cross_process_circuit_transition(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cursor.db"
    poller = AdapterCursorStore(path)
    recovery = AdapterCursorStore(path)
    try:
        assert poller.claim("calendar", "worker-a", ttl_ms=30_000)
        claimed = poller.get("calendar")
        opened = recovery.record_error("calendar", open_after=1)
        assert opened.updated_at_ms > claimed.updated_at_ms

        assert poller.release("calendar", "worker-a")
        durable = recovery.get("calendar")
        assert durable.updated_at_ms > opened.updated_at_ms
        assert durable.circuit_state == "open"
        assert durable.error_count == 1
        assert durable.lease_owner is None
    finally:
        recovery.close()
        poller.close()


def test_expired_lease_cannot_commit_success_or_failure(tmp_path: Path) -> None:
    store = AdapterCursorStore(tmp_path / "cursor.db")
    try:
        assert store.claim("calendar", "worker-a", ttl_ms=100, now_ms=1_000)
        claimed = store.get("calendar")
        with pytest.raises(AdapterLeaseLost, match="lease or circuit changed"):
            store.complete_poll(
                "calendar",
                provider_watermark='{"version":1}',
                expected_updated_at_ms=claimed.updated_at_ms,
                lease_owner="worker-a",
                now_ms=1_101,
            )
        with pytest.raises(AdapterLeaseLost, match="lost its lease"):
            store.record_poll_error(
                "calendar",
                expected_updated_at_ms=claimed.updated_at_ms,
                lease_owner="worker-a",
                now_ms=1_101,
            )
        durable = store.get("calendar")
        assert durable.provider_watermark is None
        assert durable.error_count == 0
        assert durable.circuit_state == "closed"
    finally:
        store.close()


def test_poll_failure_is_fenced_to_holder_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "cursor.db"
    winner = AdapterCursorStore(path)
    try:
        assert winner.claim("calendar", "worker-a", ttl_ms=30_000)
        claimed = winner.get("calendar")
    finally:
        winner.close()

    restarted = AdapterCursorStore(path)
    try:
        before = restarted.get("calendar")
        with pytest.raises(AdapterLeaseLost, match="lost its lease"):
            restarted.record_poll_error(
                "calendar",
                expected_updated_at_ms=claimed.updated_at_ms,
                lease_owner="worker-b",
            )
        assert restarted.get("calendar") == before
        failed = restarted.record_poll_error(
            "calendar",
            expected_updated_at_ms=claimed.updated_at_ms,
            lease_owner="worker-a",
        )
        assert failed.error_count == 1
        assert failed.circuit_state == "closed"
    finally:
        restarted.close()
