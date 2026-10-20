"""Synthetic schema files only; no real Messages database or provider process."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from bootstrap import cos_messages_database as database


@pytest.fixture
def service_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(database.sys, "platform", "darwin")
    monkeypatch.setattr(
        database.pwd, "getpwuid", lambda _uid: SimpleNamespace(pw_dir=str(tmp_path))
    )
    monkeypatch.setenv("HOME", "/not-the-service-account")
    (tmp_path / "Library" / "Messages").mkdir(parents=True)
    return tmp_path / "Library" / "Messages" / "chat.db"


def test_reads_only_fixed_account_schema_without_rows_or_writes(
    service_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with sqlite3.connect(service_home) as connection:
        for table in ("message", "chat", "handle"):
            connection.execute(f"CREATE TABLE {table}(private_text TEXT)")
            connection.execute(f"INSERT INTO {table} VALUES ('synthetic-private-row')")
    service_home.chmod(0o600)
    before = service_home.read_bytes()
    queries: list[str] = []
    original = sqlite3.connect

    def connect(database_uri: str, **kwargs: object) -> sqlite3.Connection:
        assert database_uri == service_home.as_uri() + "?mode=ro"
        assert kwargs == {"uri": True, "timeout": 2.0}
        result = original(database_uri, uri=True, timeout=2.0)
        result.set_trace_callback(queries.append)
        return result

    monkeypatch.setattr(database.sqlite3, "connect", connect)
    result = database.probe()
    assert result == {
        "contract": database.CONTRACT,
        "target": "service_messages_database",
        "status": "ready",
        "ready": True,
    }
    assert len(queries) == 4
    assert queries[0] == "PRAGMA query_only=ON"
    assert all("FROM sqlite_master WHERE type='table'" in row for row in queries[1:])
    assert "synthetic-private-row" not in str(result)
    assert str(service_home) not in str(result)
    assert service_home.read_bytes() == before


@pytest.mark.parametrize("kind", ["missing", "invalid", "linked", "writable"])
def test_missing_or_untrusted_schema_never_reports_ready(
    service_home: Path, kind: str
) -> None:
    if kind != "missing":
        with sqlite3.connect(service_home) as connection:
            connection.execute("CREATE TABLE synthetic_unrelated(value TEXT)")
        service_home.chmod(0o600)
    if kind == "linked":
        destination = service_home.with_name("other.db")
        service_home.rename(destination)
        service_home.symlink_to(destination)
    if kind == "writable":
        service_home.chmod(0o666)
    result = database.probe()
    assert result["ready"] is False
    assert (
        result["status"]
        == {
            "missing": "not_found",
            "invalid": "invalid_schema",
            "linked": "unavailable",
            "writable": "unavailable",
        }[kind]
    )
    assert str(service_home) not in str(result)


def test_sqlite_errors_are_content_free(
    service_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_home.write_bytes(b"not a database")
    assert database.probe()["status"] == "unavailable"
    monkeypatch.setattr(database.sys, "platform", "linux")
    assert database.probe()["status"] == "unsupported"
