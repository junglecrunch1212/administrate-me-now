"""Run an exact archived EventLog against disposable encrypted index history."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

from pydantic import BaseModel

import adminme.events.log as log_module
from adminme.events.envelope import EventEnvelope
from adminme.events.registry import registry


KEY = b"m" * 32
SOURCE = "fixture.index.source"
TERMINAL = "fixture.index.terminal"


class FixtureIndexPayload(BaseModel):
    model_config = {"extra": "forbid"}
    index: int
    body: str


async def main() -> None:
    archive, database, output = map(Path, sys.argv[1:4])
    mode = sys.argv[4]
    assert Path(log_module.__file__).resolve() == archive / "adminme/events/log.py"
    for event_type in (SOURCE, TERMINAL):
        registry.register(event_type, 1, FixtureIndexPayload)
    log = log_module.EventLog(database, KEY, read_only=mode == "read-only")
    try:
        if mode == "seed":
            for index in range(6):
                source_id = await log.append(
                    EventEnvelope(
                        event_at_ms=1_700_000_000_000 + index * 2,
                        tenant_id="fixture-index-tenant",
                        type=SOURCE,
                        schema_version=1,
                        occurred_at="2023-11-14T22:13:20Z",
                        source_adapter="fixture-index-adapter",
                        source_account_id="fixture-index-account",
                        owner_scope="private:fixture-index-member",
                        visibility_scope="private:fixture-index-member",
                        payload={
                            "index": index,
                            "body": "fixture retained body " * 6000,
                        },
                    )
                )
                await log.append(
                    EventEnvelope(
                        event_at_ms=1_700_000_000_001 + index * 2,
                        tenant_id="fixture-index-tenant",
                        type=TERMINAL,
                        schema_version=1,
                        occurred_at="2023-11-14T22:13:20Z",
                        source_adapter="fixture-index-consumer",
                        source_account_id="fixture-index-account",
                        owner_scope="private:fixture-index-member",
                        visibility_scope="private:fixture-index-member",
                        causation_id=source_id,
                        payload={"index": index, "body": "fixture terminal outcome"},
                    )
                )
        full = [event async for event in log.read_since()]
        metadata = [
            event
            async for event in log.read_metadata_since(
                types=[SOURCE, TERMINAL], include_payload_for=[TERMINAL], limit=500
            )
        ]
        assert len(full) == len(metadata) == 12
        assert all(
            event["payload"] is None for event in metadata if event["type"] == SOURCE
        )
        assert all(
            event["causation_id"] is not None
            for event in metadata
            if event["type"] == TERMINAL
        )

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

        result = {
            "imports_from_archive": True,
            "event_count": len(full),
            "event_digest": hashlib.sha256(
                json.dumps(full, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "metadata": metadata,
            **await log._run_locked(inventory),  # noqa: SLF001
        }
    finally:
        await log.close()
    output.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
