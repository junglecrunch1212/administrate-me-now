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
from adminme.projections.calendars import CalendarsProjection
from adminme.projections.calendars.queries import today as calendars_today
from adminme.projections.commitments import CommitmentsProjection
from adminme.projections.commitments.queries import pending_approval
from adminme.projections.interactions import InteractionsProjection
from adminme.projections.interactions.queries import recent_with
from adminme.projections.money import MoneyProjection
from adminme.projections.money.queries import category_totals
from adminme.projections.parties import PartiesProjection
from adminme.projections.parties.queries import all_parties, list_household_members
from adminme.projections.places_assets_accounts import PlacesAssetsAccountsProjection
from adminme.projections.places_assets_accounts.queries import (
    accounts_renewing_before,
    list_places,
)
from adminme.projections.recurrences import RecurrencesProjection
from adminme.projections.recurrences.queries import all_active as all_recurrences
from adminme.projections.runner import ProjectionRunner
from adminme.projections.tasks import TasksProjection
from adminme.projections.tasks.queries import open_for_member

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
        runner.register(CommitmentsProjection())
        runner.register(TasksProjection())
        runner.register(RecurrencesProjection())
        runner.register(CalendarsProjection())
        runner.register(PlacesAssetsAccountsProjection())
        runner.register(MoneyProjection())
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

        # 3 commitments (one pending, one confirmed, one completed)
        for i, cid in enumerate(["c1", "c2", "c3"]):
            await log.append(
                _env(
                    "commitment.proposed",
                    {
                        "commitment_id": cid,
                        "kind": "reply",
                        "owed_by_member_id": "m1",
                        "owed_to_party_id": "x1",
                        "text_summary": f"Demo commitment {i + 1}",
                        "confidence": 0.85,
                        "strength": "confident",
                        "source_interaction_id": "demo-ix",
                    },
                )
            )
        await log.append(
            _env(
                "commitment.confirmed",
                {
                    "commitment_id": "c2",
                    "confirmed_by_member_id": "m1",
                    "confirmed_at": "2026-04-11T09:00:00Z",
                },
            )
        )
        await log.append(
            _env(
                "commitment.completed",
                {
                    "commitment_id": "c3",
                    "completed_at": "2026-04-12T15:00:00Z",
                    "completed_by_party_id": "m1",
                },
            )
        )

        # 5 tasks (mix of inbox / next / in_progress)
        task_specs = [
            ("d-t1", "m1", "inbox", None),
            ("d-t2", "m1", "next", "2026-04-25"),
            ("d-t3", "m2", "in_progress", "2026-04-24"),
            ("d-t4", None, "inbox", None),
            ("d-t5", "m2", "inbox", "2026-04-26"),
        ]
        for tid, owner, status, due in task_specs:
            await log.append(
                _env(
                    "task.created",
                    {
                        "task_id": tid,
                        "title": f"Demo task {tid}",
                        "owner_member_id": owner,
                        "due": due,
                    },
                )
            )
            if status != "inbox":
                await log.append(
                    _env(
                        "task.updated",
                        {
                            "task_id": tid,
                            "updated_at": "2026-04-12T00:00:00Z",
                            "previous_status": "inbox",
                            "new_status": status,
                            "field_updates": {},
                        },
                    )
                )

        # 2 recurrences (one birthday, one oil_change)
        await log.append(
            _env(
                "recurrence.added",
                {
                    "recurrence_id": "r-birthday-m1",
                    "linked_kind": "party",
                    "linked_id": "m1",
                    "kind": "birthday",
                    "rrule": "FREQ=YEARLY",
                    "next_occurrence": "2026-06-01T00:00:00Z",
                    "lead_time_days": 7,
                    "trackable": True,
                },
            )
        )
        await log.append(
            _env(
                "recurrence.added",
                {
                    "recurrence_id": "r-oil-change",
                    "linked_kind": "household",
                    "linked_id": "hh",
                    "kind": "oil_change",
                    "rrule": "FREQ=MONTHLY;INTERVAL=3",
                    "next_occurrence": "2026-07-01T00:00:00Z",
                    "lead_time_days": 14,
                },
            )
        )

        # 4 calendar events (two today, two this week)
        cal_specs = [
            ("ce-today-1", "2026-04-23T09:00:00Z", "2026-04-23T10:00:00Z", "m1"),
            ("ce-today-2", "2026-04-23T14:00:00Z", "2026-04-23T15:00:00Z", "m2"),
            ("ce-week-1", "2026-04-25T11:00:00Z", "2026-04-25T12:00:00Z", "m1"),
            ("ce-week-2", "2026-04-27T16:00:00Z", "2026-04-27T17:00:00Z", "m2"),
        ]
        for uid, start, end, attendee in cal_specs:
            await log.append(
                _env(
                    "calendar.event_added",
                    {
                        "source": "google",
                        "external_event_id": uid,
                        "calendar_id": "cal-primary",
                        "summary": f"Demo calendar event {uid}",
                        "start": start,
                        "end": end,
                        "attendees": [{"party_id": attendee}],
                    },
                )
            )

        # 2 places + 2 assets + 2 accounts
        await log.append(
            _env(
                "place.added",
                {
                    "place_id": "pl-home",
                    "display_name": "Demo home",
                    "kind": "home",
                    "address_json": {
                        "street": "1 Example Ln",
                        "city": "Springfield",
                        "state": "IL",
                        "postal": "62704",
                        "country": "US",
                    },
                    "geo_lat": 39.78,
                    "geo_lon": -89.65,
                },
            )
        )
        await log.append(
            _env(
                "place.added",
                {
                    "place_id": "pl-office",
                    "display_name": "Demo office",
                    "kind": "office",
                    "address_json": {
                        "street": "100 Market",
                        "city": "Springfield",
                        "state": "IL",
                        "postal": "62704",
                        "country": "US",
                    },
                },
            )
        )
        await log.append(
            _env(
                "asset.added",
                {
                    "asset_id": "as-car",
                    "display_name": "Honda Civic",
                    "kind": "vehicle",
                    "linked_place": "pl-home",
                },
            )
        )
        await log.append(
            _env(
                "asset.added",
                {
                    "asset_id": "as-fridge",
                    "display_name": "Refrigerator",
                    "kind": "appliance",
                    "linked_place": "pl-home",
                },
            )
        )
        await log.append(
            _env(
                "account.added",
                {
                    "account_id": "ac-power",
                    "display_name": "Power bill",
                    "organization_party_id": "x1",
                    "kind": "utility",
                    "status": "active",
                    "billing_rrule": "FREQ=MONTHLY",
                    "next_renewal": "2026-05-10",
                    "linked_place": "pl-home",
                },
            )
        )
        await log.append(
            _env(
                "account.added",
                {
                    "account_id": "ac-bank",
                    "display_name": "Checking",
                    "organization_party_id": "x1",
                    "kind": "bank",
                    "status": "active",
                    "login_vault_ref": "op://Vault/bank",
                },
            )
        )

        # 4 money flows
        await log.append(
            _env(
                "money_flow.recorded",
                {
                    "flow_id": "mf1",
                    "from_party_id": "m1",
                    "to_party_id": "x1",
                    "amount_minor": 7500,
                    "currency": "USD",
                    "occurred_at": "2026-04-10T12:00:00Z",
                    "kind": "paid",
                    "category": "utilities",
                    "linked_account": "ac-power",
                    "source_adapter": "plaid",
                },
            )
        )
        await log.append(
            _env(
                "money_flow.recorded",
                {
                    "flow_id": "mf2",
                    "from_party_id": "m1",
                    "to_party_id": "x1",
                    "amount_minor": 2500,
                    "currency": "USD",
                    "occurred_at": "2026-04-15T12:00:00Z",
                    "kind": "paid",
                    "category": "groceries",
                    "linked_account": "ac-bank",
                    "source_adapter": "plaid",
                },
            )
        )
        await log.append(
            _env(
                "money_flow.recorded",
                {
                    "flow_id": "mf3",
                    "from_party_id": "x2",
                    "to_party_id": "m1",
                    "amount_minor": 1200,
                    "currency": "USD",
                    "occurred_at": "2026-04-16T12:00:00Z",
                    "kind": "reimbursable",
                    "category": "groceries",
                    "linked_account": "ac-bank",
                    "source_adapter": "plaid",
                },
            )
        )
        await log.append(
            _env(
                "money_flow.manually_added",
                {
                    "flow_id": "mf-manual",
                    "from_party_id": "m1",
                    "to_party_id": "x1",
                    "amount_minor": 500,
                    "currency": "USD",
                    "occurred_at": "2026-04-18T12:00:00Z",
                    "kind": "paid",
                    "category": "misc",
                    "notes": "farmer's market",
                    "added_by_party_id": "m1",
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
                "projection:commitments",
                "projection:tasks",
                "projection:recurrences",
                "projection:calendars",
                "projection:places_assets_accounts",
                "projection:money",
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

        conn_c = runner.connection("commitments")
        pending = pending_approval(conn_c, tenant_id="tenant-demo")
        print(f"\nPending commitments ({len(pending)}):")
        for c in pending:
            print(
                f"  {c['commitment_id']}  {c['kind']:<10} "
                f"{c['owed_by_party']}→{c['owed_to_party']}  {c['description']}"
            )

        conn_t = runner.connection("tasks")
        open_t = open_for_member(conn_t, tenant_id="tenant-demo", member_party_id="m1")
        print(f"\nOpen tasks for m1 ({len(open_t)}):")
        for t in open_t:
            print(f"  {t['task_id']:<6} {t['status']:<12} {t['title']}")

        conn_r = runner.connection("recurrences")
        recs = all_recurrences(conn_r, tenant_id="tenant-demo")
        print(f"\nRecurrences ({len(recs)}):")
        for r in recs:
            print(
                f"  {r['recurrence_id']:<16} {r['kind']:<12} "
                f"next={r['next_occurrence']}"
            )

        conn_cal = runner.connection("calendars")
        today_events = calendars_today(
            conn_cal,
            tenant_id="tenant-demo",
            member_party_id="m1",
            today_iso="2026-04-23T00:00:00Z",
            tz_name="UTC",
        )
        print(f"\nCalendar events today (m1) ({len(today_events)}):")
        for e in today_events:
            print(
                f"  {e['external_uid']:<14} {e['start_at']}-{e['end_at']}  "
                f"{e['summary']}"
            )

        conn_paa = runner.connection("places_assets_accounts")
        places = list_places(conn_paa, tenant_id="tenant-demo")
        print(f"\nPlaces ({len(places)}):")
        for pl in places:
            print(f"  {pl['place_id']:<10} {pl['kind']:<10} {pl['display_name']}")
        renewing = accounts_renewing_before(
            conn_paa, tenant_id="tenant-demo", cutoff_iso="2026-05-31"
        )
        print(f"\nAccounts renewing by 2026-05-31 ({len(renewing)}):")
        for ac in renewing:
            print(
                f"  {ac['account_id']:<10} {ac['kind']:<10} "
                f"next={ac['next_renewal']}  {ac['display_name']}"
            )

        conn_m = runner.connection("money")
        totals = category_totals(
            conn_m, tenant_id="tenant-demo", since_iso="2026-01-01T00:00:00Z"
        )
        total_sum = sum(totals.values())
        print(f"\nMoney flow totals since 2026-01-01 (sum={total_sum}):")
        for cat, total in sorted(totals.items()):
            print(f"  {cat:<12} {total}")

        await runner.stop()
        await log.close()


if __name__ == "__main__":
    asyncio.run(main())
