"""
demo_projections.py — end-to-end smoke for prompt 05.

Spins up a tmp instance, registers parties/interactions/artifacts
projections, appends a scripted set of events, queries each projection,
and prints counts. Runs in under 5s on lab hardware. No user interaction.

Invocation: ``poetry run python scripts/demo_projections.py``.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.projections.artifacts import ArtifactsProjection
from adminme.projections.artifacts.queries import list_recent
from adminme.projections.interactions import InteractionsProjection
from adminme.projections.interactions.queries import recent_with
from adminme.projections.parties import PartiesProjection
from adminme.projections.parties.queries import all_parties, list_household_members
from adminme.projections.runner import ProjectionRunner

TEST_KEY = b"s" * 32


def _env(
    event_type: str,
    payload: dict,
    *,
    tenant_id: str = "tenant-demo",
    owner_scope: str = "shared:household",
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="demo",
        source_account_id="demo",
        owner_scope=owner_scope,
        visibility_scope=owner_scope,
        sensitivity="normal",
        payload=payload,
    )


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        instance_dir = Path(tmpdir) / "instance"
        instance_dir.mkdir()
        config = load_instance_config(instance_dir)

        log = EventLog(config, TEST_KEY)
        bus = EventBus(log, config.bus_checkpoint_path)
        runner = ProjectionRunner(bus, log, config, encryption_key=TEST_KEY)
        runner.register(PartiesProjection())
        runner.register(InteractionsProjection())
        runner.register(ArtifactsProjection())
        await runner.start()

        # 1 household + 3 members + 2 external parties
        await log.append(
            _env("party.created", {
                "party_id": "hh",
                "kind": "household",
                "display_name": "Demo HH",
                "sort_name": "Demo HH",
            })
        )
        members = [("m1", "Member A"), ("m2", "Member B"), ("m3", "Child C")]
        for pid, name in members:
            await log.append(
                _env("party.created", {
                    "party_id": pid,
                    "kind": "person",
                    "display_name": name,
                    "sort_name": name,
                })
            )
            await log.append(
                _env("membership.added", {
                    "membership_id": f"mem-{pid}",
                    "party_id": pid,
                    "parent_party_id": "hh",
                    "role": "principal" if pid != "m3" else "child",
                })
            )
        for pid, name in [("x1", "Vendor V"), ("x2", "Contact C")]:
            await log.append(
                _env("party.created", {
                    "party_id": pid,
                    "kind": "person" if pid == "x2" else "organization",
                    "display_name": name,
                    "sort_name": name,
                })
            )

        # 5 messaging interactions (3 inbound, 2 outbound)
        for i in range(5):
            if i % 2 == 0:
                await log.append(
                    _env(
                        "messaging.received",
                        {
                            "source_channel": "gmail",
                            "from_identifier": "vendor@example.com",
                            "to_identifier": "me@example.com",
                            "received_at": f"2026-04-10T10:{i:02d}:00Z",
                            "thread_id": "demo-t1",
                        },
                    )
                )
            else:
                await log.append(
                    _env(
                        "messaging.sent",
                        {
                            "source_channel": "gmail",
                            "to_identifier": "vendor@example.com",
                            "sent_at": f"2026-04-10T10:{i:02d}:00Z",
                            "delivery_status": "sent",
                            "thread_id": "demo-t1",
                        },
                    )
                )

        # 2 artifacts
        for i in range(2):
            await log.append(
                _env(
                    "artifact.received",
                    {
                        "source": "demo",
                        "external_artifact_id": None,
                        "mime_type": "application/pdf",
                        "size_bytes": 1024,
                        "filename": f"doc{i}.pdf",
                        "sha256": f"{i:064x}",
                        "artifact_ref": f"artifact://{i:064x}",
                        "received_at": f"2026-04-10T11:{i:02d}:00Z",
                    },
                )
            )

        latest = await log.latest_event_id()
        assert latest is not None
        await bus.notify(latest)

        # Allow up to a couple of seconds for catch-up.
        for _ in range(200):
            done = True
            for sid in (
                "projection:parties",
                "projection:interactions",
                "projection:artifacts",
            ):
                s = await bus.subscriber_status(sid)
                if s["lag_count"] > 0:
                    done = False
                    break
            if done:
                break
            await asyncio.sleep(0.01)

        status = await runner.status()
        print("Projection status:")
        for name, s in status.items():
            print(
                f"  {name} v{s['version']}  rows={s['row_counts']}  "
                f"lag={s['lag_count']}"
            )

        conn_p = runner.connection("parties")
        parties = all_parties(conn_p, tenant_id="tenant-demo")
        print(f"\nParties ({len(parties)}):")
        for p in parties:
            print(f"  {p['party_id']:<8} {p['kind']:<13} {p['display_name']}")

        members_rows = list_household_members(
            conn_p, tenant_id="tenant-demo", household_party_id="hh"
        )
        print(f"\nHousehold members ({len(members_rows)}):")
        for m in members_rows:
            print(f"  {m['party_id']:<4} role={m['role']:<10} {m['display_name']}")

        conn_i = runner.connection("interactions")
        ix = recent_with(
            conn_i, tenant_id="tenant-demo", party_id="vendor@example.com", days=60
        )
        print(f"\nInteractions with vendor@example.com ({len(ix)}):")
        for row in ix:
            print(
                f"  {row['interaction_id']}  {row['direction']:<8} "
                f"{row['channel_specific']}  {row['occurred_at']}"
            )

        conn_a = runner.connection("artifacts")
        arts = list_recent(conn_a, tenant_id="tenant-demo")
        print(f"\nArtifacts ({len(arts)}):")
        for a in arts:
            print(f"  {a['artifact_id']}  {a['title']}  {a['sha256'][:12]}...")

        await runner.stop()
        await log.close()


if __name__ == "__main__":
    asyncio.run(main())
