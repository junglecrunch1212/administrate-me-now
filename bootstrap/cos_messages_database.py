"""Read only the current service user's Messages database schema.

Executed as a Gateway child with isolated Python. No provider CLI, Contacts,
Apple Events, message rows, copies, or immutable SQLite snapshots are used.
"""

from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import pwd
import sqlite3
import sys
from typing import Any

CONTRACT = "cos-messages-database-preflight-v1"
TARGET = "service_messages_database"


def probe() -> dict[str, Any]:
    result: dict[str, Any] = {
        "contract": CONTRACT,
        "target": TARGET,
        "status": "unsupported",
        "ready": False,
    }
    if sys.platform != "darwin":
        return result
    try:
        # The account database, not an inherited or caller-supplied HOME, fixes
        # this path. Refuse links that would redirect the check elsewhere.
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        path = home / "Library" / "Messages" / "chat.db"
        if not path.exists():
            return {**result, "status": "not_found"}
        if (
            not home.is_absolute()
            or path.resolve(strict=True) != path
            or not path.is_file()
            or path.stat().st_uid != os.getuid()
            or path.stat().st_mode & 0o022
        ):
            return {**result, "status": "unavailable"}
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2.0)
        ) as connection:
            connection.execute("PRAGMA query_only=ON")
            # Only schema metadata is read, never a row from these tables.
            ready = all(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (name,),
                ).fetchone()
                is not None
                for name in ("message", "chat", "handle")
            )
        return {
            **result,
            "status": "ready" if ready else "invalid_schema",
            "ready": ready,
        }
    except (OSError, KeyError, sqlite3.Error):
        return {**result, "status": "unavailable"}


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("Database preflight accepts no arguments")
    print(json.dumps(probe(), separators=(",", ":")))


if __name__ == "__main__":
    main()
