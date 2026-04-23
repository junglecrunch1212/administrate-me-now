"""Demo: exercise the prompt-04 typed event log.

Appends 10 party.created + 10 commitment.proposed envelopes, attempts one
invalid commitment.proposed (confidence=1.5 violates the le=1.0 bound) and
catches the resulting AppendValidationError, reads all events back via
read_since(), and prints a by-type summary. Uses a tmpdir; does not touch
any instance directory.

Exit status: 0 iff 20 valid events landed and 1 invalid event was caught.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

# Allow `poetry run python scripts/demo_event_log.py` without an installed adminme.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adminme.events.envelope import EventEnvelope  # noqa: E402
from adminme.events.log import AppendValidationError, EventLog  # noqa: E402

KEY = b"demo-demo-demo-demo-demo-demo-aa"  # 32 bytes for sandbox demo only


def _envelope(event_type: str, schema_version: int, payload: dict) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id="demo-tenant",
        type=event_type,
        schema_version=schema_version,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="demo:inproc",
        source_account_id="demo-account",
        owner_scope="shared:household",
        visibility_scope="shared:household",
        payload=payload,
    )


def _party_created(i: int) -> EventEnvelope:
    return _envelope(
        "party.created",
        1,
        {
            "party_id": f"p-demo-{i:02d}",
            "kind": "person",
            "display_name": f"Demo Person {i}",
            "sort_name": f"Person, Demo {i}",
            "nickname": None,
            "pronouns": None,
            "notes": None,
            "attributes": {},
        },
    )


def _commitment_proposed(i: int, *, confidence: float = 0.8) -> EventEnvelope:
    return _envelope(
        "commitment.proposed",
        1,
        {
            "commitment_id": f"c-demo-{i:02d}",
            "kind": "reply",
            "owed_by_member_id": "m-demo",
            "owed_to_party_id": f"p-demo-{i:02d}",
            "text_summary": f"Reply to demo person {i}",
            "suggested_due": None,
            "urgency": "this_week",
            "confidence": confidence,
            "strength": "confident",
            "source_interaction_id": None,
            "source_message_preview": None,
            "classify_reasons": ["demo"],
        },
    )


async def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        log_path = td_path / "events.db"

        log = EventLog(log_path, KEY)

        print("Appending 10 party.created events...", end=" ", flush=True)
        t0 = time.perf_counter()
        party_ids = await log.append_batch([_party_created(i) for i in range(10)])
        print(f" elapsed: {(time.perf_counter() - t0) * 1000:.0f}ms")

        print("Appending 10 commitment.proposed events...", end=" ", flush=True)
        t0 = time.perf_counter()
        commitment_ids = await log.append_batch(
            [_commitment_proposed(i) for i in range(10)]
        )
        print(f" elapsed: {(time.perf_counter() - t0) * 1000:.0f}ms")

        print("Attempting 1 INVALID commitment.proposed (confidence=1.5)...")
        bad = _commitment_proposed(99, confidence=1.5)
        caught = False
        try:
            await log.append(bad)
        except AppendValidationError as exc:
            caught = True
            print(f"  caught validation error as expected: {exc}")

        valid_count = len(party_ids) + len(commitment_ids)
        print("\nReading everything back via read_since()...")
        by_type: Counter[str] = Counter()
        all_events: list[dict] = []
        async for ev in log.read_since():
            all_events.append(ev)
            by_type[ev["type"]] += 1

        print(f"Total events in log: {len(all_events)}")
        for t, n in sorted(by_type.items()):
            print(f"  {t}: {n}")

        await log.close()

        success = valid_count == 20 and caught and len(all_events) == 20
        print(
            "\nResult: "
            + ("OK" if success else "FAIL")
            + f" (20 valid landed={valid_count == 20}, "
            + f"invalid caught={caught}, "
            + f"read-back total={len(all_events)})"
        )
        return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
