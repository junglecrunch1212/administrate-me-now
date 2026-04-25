"""
Unit tests for the places_assets_accounts projection (prompt 07a).

Covers idempotency, rebuild correctness, multi-tenant isolation, CHECK
constraint enforcement on login_vault_ref, and each of the six event-type
state transitions. Per §12 invariant 1, queries take tenant_id as an
explicit keyword.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
import sqlcipher3

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.lib.session import Session, build_internal_session
from adminme.projections.places_assets_accounts import PlacesAssetsAccountsProjection
from adminme.projections.places_assets_accounts.queries import (
    accounts_renewing_before,
    get_account,
    get_asset,
    get_place,
    list_accounts_by_kind,
    list_assets_for_place,
    list_places,
)
from adminme.projections.runner import ProjectionRunner

TEST_KEY = b"p" * 32


def _S(tenant_id: str = "tenant-a") -> Session:
    """Internal-actor Session for projection-read tests; carries tenant_id
    only. 08a + scope filtering use principal role so allowed_read accepts
    shared:household + private:<self> rows."""
    return build_internal_session("test_actor", "principal", tenant_id)



@pytest.fixture
async def rig(tmp_path: Path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = ProjectionRunner(bus, log, config, encryption_key=TEST_KEY)
    runner.register(PlacesAssetsAccountsProjection())
    await runner.start()
    try:
        yield {
            "config": config,
            "log": log,
            "bus": bus,
            "runner": runner,
        }
    finally:
        await runner.stop()
        await log.close()


def _envelope(
    event_type: str,
    payload: dict[str, Any],
    *,
    tenant_id: str = "tenant-a",
    owner_scope: str = "shared:household",
    sensitivity: str = "normal",
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="test",
        source_account_id="test-acct",
        owner_scope=owner_scope,
        visibility_scope=owner_scope,
        sensitivity=sensitivity,
        payload=payload,
    )


async def _wait_for_checkpoint(bus: EventBus, subscriber_id: str, target: str) -> None:
    import asyncio

    for _ in range(200):
        status = await bus.subscriber_status(subscriber_id)
        if status["checkpoint_event_id"] == target and status["lag_count"] == 0:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"subscriber {subscriber_id} did not reach {target}: "
        f"{await bus.subscriber_status(subscriber_id)}"
    )


def _place_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "place_id": "pl1",
        "display_name": "Primary residence",
        "kind": "home",
        "address_json": {
            "street": "123 Example St",
            "city": "Springfield",
            "state": "IL",
            "postal": "62704",
            "country": "US",
        },
        "geo_lat": 39.78,
        "geo_lon": -89.65,
    }
    base.update(overrides)
    return base


def _asset_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "asset_id": "a1",
        "display_name": "Honda Civic",
        "kind": "vehicle",
        "linked_place": "pl1",
    }
    base.update(overrides)
    return base


def _account_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "account_id": "ac1",
        "display_name": "Power bill",
        "organization_party_id": "p-utility",
        "kind": "utility",
        "status": "active",
        "billing_rrule": "FREQ=MONTHLY",
        "next_renewal": "2026-05-15",
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------
# places
# ------------------------------------------------------------------
async def test_place_added_inserts_row(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(_envelope("place.added", _place_payload()))
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "projection:places_assets_accounts", eid)

    conn = runner.connection("places_assets_accounts")
    row = get_place(conn, _S("tenant-a"), place_id="pl1")
    assert row is not None
    assert row["kind"] == "home"
    assert row["display_name"] == "Primary residence"
    assert row["geo_lat"] == pytest.approx(39.78)


async def test_place_updated_changes_only_listed_fields(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("place.added", _place_payload()))
    last = await log.append(
        _envelope(
            "place.updated",
            {
                "place_id": "pl1",
                "updated_at": "2026-04-23T12:00:00Z",
                "field_updates": {"display_name": "The house"},
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:places_assets_accounts", last)

    conn = runner.connection("places_assets_accounts")
    row = get_place(conn, _S("tenant-a"), place_id="pl1")
    assert row is not None
    assert row["display_name"] == "The house"
    # unchanged
    assert row["kind"] == "home"


# ------------------------------------------------------------------
# assets
# ------------------------------------------------------------------
async def test_asset_added_with_linked_place(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("place.added", _place_payload()))
    last = await log.append(_envelope("asset.added", _asset_payload()))
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:places_assets_accounts", last)

    conn = runner.connection("places_assets_accounts")
    row = get_asset(conn, _S("tenant-a"), asset_id="a1")
    assert row is not None
    assert row["kind"] == "vehicle"
    assert row["linked_place"] == "pl1"


async def test_asset_updated_changes_fields(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("asset.added", _asset_payload()))
    last = await log.append(
        _envelope(
            "asset.updated",
            {
                "asset_id": "a1",
                "updated_at": "2026-04-23T12:00:00Z",
                "field_updates": {"display_name": "Honda Civic 2020"},
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:places_assets_accounts", last)

    conn = runner.connection("places_assets_accounts")
    row = get_asset(conn, _S("tenant-a"), asset_id="a1")
    assert row is not None
    assert row["display_name"] == "Honda Civic 2020"


# ------------------------------------------------------------------
# accounts
# ------------------------------------------------------------------
async def test_account_added_with_vault_ref(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(
        _envelope(
            "account.added",
            _account_payload(login_vault_ref="op://Vault/power-bill"),
        )
    )
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "projection:places_assets_accounts", eid)

    conn = runner.connection("places_assets_accounts")
    row = get_account(conn, _S("tenant-a"), account_id="ac1")
    assert row is not None
    assert row["login_vault_ref"] == "op://Vault/power-bill"
    assert row["organization"] == "p-utility"


async def test_account_added_with_raw_credential_fails_check(
    rig: dict[str, Any],
) -> None:
    """Belt-and-braces: the CHECK constraint catches a broken adapter that
    tries to write a raw credential into login_vault_ref. The real defense
    is the adapter emitting account.added."""
    log = rig["log"]
    runner = rig["runner"]

    # Append a well-formed event first so the subscriber has something to
    # checkpoint to before we append the poisoned one.
    await log.append(_envelope("account.added", _account_payload()))

    # The poisoned event appends to the log (Pydantic allows the string;
    # schema does not care about shape). When the subscriber tries to apply
    # it, SQLite raises IntegrityError from the CHECK. The bus surfaces the
    # failure via degraded status — direct-handler invocation is the
    # cleanest way to assert on IntegrityError.
    from adminme.projections.places_assets_accounts import handlers

    bad_envelope = {
        "event_id": "ev-bad",
        "tenant_id": "tenant-a",
        "type": "account.added",
        "schema_version": 1,
        "occurred_at": "2026-04-23T00:00:00Z",
        "owner_scope": "shared:household",
        "visibility_scope": "shared:household",
        "sensitivity": "normal",
        "payload": _account_payload(
            account_id="ac-bad",
            login_vault_ref="password123",
        ),
    }
    conn = runner.connection("places_assets_accounts")
    with pytest.raises(sqlcipher3.IntegrityError):
        handlers.apply_event(bad_envelope, conn)


async def test_account_updated_changes_fields(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("account.added", _account_payload()))
    last = await log.append(
        _envelope(
            "account.updated",
            {
                "account_id": "ac1",
                "updated_at": "2026-04-23T12:00:00Z",
                "field_updates": {"status": "dormant"},
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:places_assets_accounts", last)

    conn = runner.connection("places_assets_accounts")
    row = get_account(conn, _S("tenant-a"), account_id="ac1")
    assert row is not None
    assert row["status"] == "dormant"


# ------------------------------------------------------------------
# idempotency & rebuild
# ------------------------------------------------------------------
async def test_place_added_idempotent(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    env = _envelope("place.added", _place_payload())
    await log.append(env)
    last = await log.append(env)
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:places_assets_accounts", last)

    conn = runner.connection("places_assets_accounts")
    count = conn.execute(
        "SELECT count(*) FROM places WHERE tenant_id = ? AND place_id = ?",
        ("tenant-a", "pl1"),
    ).fetchone()[0]
    assert count == 1


async def test_rebuild_produces_equivalent_state(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    # 30-event fixture across all three entity families.
    for i in range(5):
        await log.append(
            _envelope(
                "place.added",
                _place_payload(
                    place_id=f"pl{i}",
                    kind="home" if i == 0 else "office",
                    display_name=f"Place {i}",
                ),
            )
        )
    for i in range(2):
        await log.append(
            _envelope(
                "place.updated",
                {
                    "place_id": f"pl{i}",
                    "updated_at": "2026-04-23T12:00:00Z",
                    "field_updates": {"display_name": f"Place {i} renamed"},
                },
            )
        )
    for i in range(10):
        await log.append(
            _envelope(
                "asset.added",
                _asset_payload(
                    asset_id=f"a{i}",
                    linked_place=f"pl{i % 5}",
                    display_name=f"Asset {i}",
                    kind="vehicle" if i % 2 == 0 else "appliance",
                ),
            )
        )
    for i in range(3):
        await log.append(
            _envelope(
                "asset.updated",
                {
                    "asset_id": f"a{i}",
                    "updated_at": "2026-04-23T12:00:00Z",
                    "field_updates": {"display_name": f"Asset {i} updated"},
                },
            )
        )
    for i in range(8):
        await log.append(
            _envelope(
                "account.added",
                _account_payload(
                    account_id=f"ac{i}",
                    display_name=f"Account {i}",
                    kind="utility" if i % 2 == 0 else "subscription",
                    next_renewal=f"2026-05-{i + 1:02d}",
                ),
            )
        )
    last = await log.append(
        _envelope(
            "account.updated",
            {
                "account_id": "ac0",
                "updated_at": "2026-04-23T12:00:00Z",
                "field_updates": {"status": "cancelled"},
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:places_assets_accounts", last)

    def _snapshot(conn: sqlcipher3.Connection) -> tuple[list[Any], list[Any], list[Any]]:
        p = [tuple(r) for r in conn.execute("SELECT * FROM places ORDER BY place_id")]
        a = [tuple(r) for r in conn.execute("SELECT * FROM assets ORDER BY asset_id")]
        ac = [tuple(r) for r in conn.execute("SELECT * FROM accounts ORDER BY account_id")]
        return p, a, ac

    conn = runner.connection("places_assets_accounts")
    pre = _snapshot(conn)

    await runner.rebuild("places_assets_accounts")

    conn = runner.connection("places_assets_accounts")
    post = _snapshot(conn)
    assert pre == post


# ------------------------------------------------------------------
# queries
# ------------------------------------------------------------------
async def test_list_places_filter_by_kind(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope("place.added", _place_payload(place_id="pl1", kind="home"))
    )
    await log.append(
        _envelope("place.added", _place_payload(place_id="pl2", kind="office"))
    )
    last = await log.append(
        _envelope("place.added", _place_payload(place_id="pl3", kind="home"))
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:places_assets_accounts", last)

    conn = runner.connection("places_assets_accounts")
    homes = list_places(conn, _S("tenant-a"), kind="home")
    assert {r["place_id"] for r in homes} == {"pl1", "pl3"}


async def test_accounts_renewing_before_orders_ascending(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "account.added",
            _account_payload(account_id="ac1", next_renewal="2026-06-10"),
        )
    )
    await log.append(
        _envelope(
            "account.added",
            _account_payload(account_id="ac2", next_renewal="2026-05-01"),
        )
    )
    await log.append(
        _envelope(
            "account.added",
            _account_payload(
                account_id="ac3",
                next_renewal="2026-07-01",
                status="cancelled",
            ),
        )
    )
    last = await log.append(
        _envelope(
            "account.added",
            _account_payload(account_id="ac4", next_renewal="2026-05-20"),
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:places_assets_accounts", last)

    conn = runner.connection("places_assets_accounts")
    rows = accounts_renewing_before(conn, _S("tenant-a"), cutoff_iso="2026-06-15"
    )
    # ac3 is cancelled — excluded. ac2 < ac4 < ac1 by next_renewal ascending.
    assert [r["account_id"] for r in rows] == ["ac2", "ac4", "ac1"]


async def test_list_assets_for_place(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(_envelope("place.added", _place_payload()))
    await log.append(
        _envelope("asset.added", _asset_payload(asset_id="a1", linked_place="pl1"))
    )
    await log.append(
        _envelope("asset.added", _asset_payload(asset_id="a2", linked_place="pl1"))
    )
    last = await log.append(
        _envelope(
            "asset.added", _asset_payload(asset_id="a3", linked_place="pl-other")
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:places_assets_accounts", last)

    conn = runner.connection("places_assets_accounts")
    rows = list_assets_for_place(conn, _S("tenant-a"), place_id="pl1")
    assert {r["asset_id"] for r in rows} == {"a1", "a2"}


async def test_list_accounts_by_kind(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope("account.added", _account_payload(account_id="ac1", kind="utility"))
    )
    await log.append(
        _envelope(
            "account.added",
            _account_payload(account_id="ac2", kind="subscription"),
        )
    )
    last = await log.append(
        _envelope("account.added", _account_payload(account_id="ac3", kind="utility"))
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:places_assets_accounts", last)

    conn = runner.connection("places_assets_accounts")
    rows = list_accounts_by_kind(conn, _S("tenant-a"), kind="utility")
    assert {r["account_id"] for r in rows} == {"ac1", "ac3"}


# ------------------------------------------------------------------
# multi-tenant & scope
# ------------------------------------------------------------------
async def test_tenant_isolation(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "place.added",
            _place_payload(display_name="Tenant A home"),
            tenant_id="tenant-a",
        )
    )
    last = await log.append(
        _envelope(
            "place.added",
            _place_payload(display_name="Tenant B home"),
            tenant_id="tenant-b",
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:places_assets_accounts", last)

    conn = runner.connection("places_assets_accounts")
    a = get_place(conn, _S("tenant-a"), place_id="pl1")
    b = get_place(conn, _S("tenant-b"), place_id="pl1")
    assert a is not None and a["display_name"] == "Tenant A home"
    assert b is not None and b["display_name"] == "Tenant B home"


async def test_scope_canary_privileged_drops_for_non_owner(
    rig: dict[str, Any],
) -> None:
    """Prompt 08a wired: a privileged-sensitivity row with shared:household
    owner_scope drops from a non-owner's read; the row is still in the
    projection table per [§6.4]."""
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(
        _envelope(
            "account.added",
            _account_payload(account_id="ac-priv"),
            sensitivity="privileged",
        )
    )
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "projection:places_assets_accounts", eid)

    conn = runner.connection("places_assets_accounts")
    row = get_account(conn, _S("tenant-a"), account_id="ac-priv")
    assert row is None

    raw = conn.execute(
        "SELECT count(*) FROM accounts WHERE tenant_id = ? AND account_id = ?",
        ("tenant-a", "ac-priv"),
    ).fetchone()
    assert raw[0] == 1
