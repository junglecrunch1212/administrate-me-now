"""Demo: append 100 synthetic events, replay via an EventBus subscriber, verify
checkpoint survives restart, append 10 more, verify they too are delivered.

Uses a tmpdir; does not touch any instance directory.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

# Allow `poetry run python scripts/demo_event_log.py` without an installed adminme.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adminme.events.bus import EventBus  # noqa: E402
from adminme.events.log import EventLog  # noqa: E402

KEY = b"demo-demo-demo-demo-demo-demo-aa"  # 32 bytes for sandbox demo only


def _make_event(i: int) -> dict:
    return {
        "type": "demo.event",
        "tenant_id": "demo-tenant",
        "owner_scope": "shared:household",
        "version": 1,
        "payload": {"i": i, "message": f"event number {i}"},
    }


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise RuntimeError("timeout")


async def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        log_path = td_path / "events.db"
        ckpt_path = td_path / "bus.db"

        log = EventLog(log_path, KEY)

        print("Appending 100 events...", end=" ", flush=True)
        t0 = time.perf_counter()
        await log.append_batch([_make_event(i) for i in range(100)])
        print(f"           elapsed: {(time.perf_counter() - t0) * 1000:.0f}ms")

        received: list[str] = []

        async def callback(event: dict) -> None:
            received.append(event["event_id"])

        print("Registering subscriber...")
        bus = EventBus(log, ckpt_path)
        bus.subscribe("demo", "*", callback)

        print("Starting bus...")
        t0 = time.perf_counter()
        await bus.start()
        await _wait_until(lambda: len(received) == 100)
        print(
            f"Subscriber received {len(received)} events."
            f"    elapsed: {(time.perf_counter() - t0) * 1000:.0f}ms"
        )

        print("Stopping bus; persisting checkpoints...")
        await bus.stop()
        await log.close()

        print("Re-opening log + bus...")
        log2 = EventLog(log_path, KEY)
        bus2 = EventBus(log2, ckpt_path)

        received_after: list[str] = []

        async def callback2(event: dict) -> None:
            received_after.append(event["event_id"])

        bus2.subscribe("demo", "*", callback2)
        status = await bus2.subscriber_status("demo")
        print(f"Subscriber's checkpoint is at {status['checkpoint_event_id']}")

        await bus2.start()
        # brief wait to confirm nothing arrives before new appends
        await asyncio.sleep(0.1)
        assert received_after == [], "old events should not replay past checkpoint"

        print("Appending 10 more events...")
        t0 = time.perf_counter()
        new_ids = await log2.append_batch([_make_event(100 + i) for i in range(10)])
        for eid in new_ids:
            await bus2.notify(eid)
        await _wait_until(lambda: len(received_after) == 10)
        print(
            f"Subscriber received 10 more.     "
            f"elapsed: {(time.perf_counter() - t0) * 1000:.0f}ms"
        )

        total = await log2.count()
        final_status = await bus2.subscriber_status("demo")
        print(
            f"Total events: {total}. Checkpoint: {final_status['checkpoint_event_id']}."
        )

        await bus2.stop()
        await log2.close()


if __name__ == "__main__":
    asyncio.run(main())
