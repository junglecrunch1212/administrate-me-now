"""Round-trip integration tests for the supervisor + Apple Reminders adapter.

Closes prompt 11c's C4 deliverable: the supervisor
(``adminme/daemons/adapter_supervisor.py``) and the Reminders adapter
pack (``packs/adapters/reminders/apple-eventkit/``) are exercised
together end-to-end through the two bidirectional sync paths:

  (a) inbound — supervisor spawns the adapter; the bounded inbound poll
      runs once against a recorded EventKit fixture; one
      ``reminder.list_item.synced`` v1 event per genuinely-new reminder
      lands in the event log with ``direction="inbound"``.
  (b) outbound — an upstream ``reminder.dispatched`` event is queued
      before start; the bounded outbound drain calls
      ``EKEventStore.save(_:commit:)`` and emits one
      ``reminder.list_item.synced`` v1 event with
      ``direction="outbound"`` and ``causation_id`` set to the upstream
      event id for round-trip traceability per BUILD.md APPLE REMINDERS
      BIDIRECTIONAL §"Outbound write cadence".

Per the 11a integration template at
``tests/integration/test_adapter_supervisor_gmail.py``: shared recording
event log + supervisor ``ctx_factory`` + a fake EKEventStore that does
not depend on pyobjc. Marked ``skip_in_ci``-equivalent via
``pytest.mark.requires_live_services`` so the test suite gates these on
the live-services marker (the in-process fakes here do not require a
live grant, but the marker keeps integration tests aligned with the
11a/11b pattern + signals "supervisor + adapter end-to-end" semantics).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.base import Adapter, AdapterContext
from adminme.daemons.adapter_supervisor import AdapterSupervisor

pytestmark = pytest.mark.requires_live_services

REPO_ROOT = Path(__file__).resolve().parents[2]
REMINDERS_PACK_ROOT = (
    REPO_ROOT / "packs" / "adapters" / "reminders" / "apple-eventkit"
)
FIXTURES = REMINDERS_PACK_ROOT / "tests" / "fixtures"

_FROZEN_NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)


def _load_reminders_module() -> Any:
    """Load ``adapter.py`` from the Reminders pack root by file path.

    ``packs/`` is not on the import path under poetry's package config
    (only ``adminme`` is), so the pack-internal tests use the same
    file-path import pattern. Mirrors
    ``test_adapter_supervisor_gmail._load_gmail_module``."""
    spec = importlib.util.spec_from_file_location(
        "reminders_adapter_integration", REMINDERS_PACK_ROOT / "adapter.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_rem_mod: Any = _load_reminders_module()
AppleEventKitAdapter: Any = _rem_mod.AppleEventKitAdapter


def _load_inbound_fixture() -> dict[str, Any]:
    with (FIXTURES / "inbound_basic.json").open() as f:
        return json.load(f)


class _RecordingEventLog:
    """Recording event log shared by the supervisor + adapter contexts.

    Mirrors the 11a integration test's recording log surface: the
    Reminders adapter and the supervisor both probe for ``append_sync``
    first per their respective ``_append`` helpers."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def append_sync(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)

    def by_type(self, event_type: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c.get("type") == event_type]


class _FakeEKReminder:
    """Stand-in for ``EKReminder``. Mirrors the pack-internal test's
    shape — kept local so this file reads in isolation."""

    def __init__(
        self,
        *,
        list_identifier: str,
        title: str,
        notes: str | None,
        due_at: Any,
        priority: int,
        calendar_item_identifier: str,
        last_modified_at: datetime | None = None,
        completed: bool = False,
    ) -> None:
        self.list_identifier = list_identifier
        self.title = title
        self.notes = notes
        self.due_at = due_at
        self.priority = priority
        self.calendarItemIdentifier = calendar_item_identifier
        self.last_modified_at = last_modified_at or _FROZEN_NOW
        self.completed = completed


class _FakeEKEventStore:
    """Strict fake EKEventStore — records every ``build_reminder`` /
    ``save`` / ``fetch_reminders`` invocation for assertion. Mirrors the
    pack-internal test surface but kept local so the integration file
    is self-contained."""

    def __init__(
        self,
        *,
        list_names: dict[str, str],
        mint_calendar_item_identifiers: list[str] | None = None,
        inbound_reminders: list[Any] | None = None,
    ) -> None:
        self._list_names = dict(list_names)
        self._mint_queue = list(mint_calendar_item_identifiers or [])
        self._inbound_reminders = list(inbound_reminders or [])
        self.builds: list[dict[str, Any]] = []
        self.saves: list[tuple[Any, bool]] = []
        self.fetch_calls: int = 0

    def build_reminder(
        self,
        *,
        list_identifier: str,
        title: str,
        notes: str | None,
        due_at: Any,
        priority: int,
    ) -> _FakeEKReminder:
        self.builds.append(
            {
                "list_identifier": list_identifier,
                "title": title,
                "notes": notes,
                "due_at": due_at,
                "priority": priority,
            }
        )
        cid = (
            self._mint_queue.pop(0)
            if self._mint_queue
            else f"EKID-{len(self.builds)}"
        )
        return _FakeEKReminder(
            list_identifier=list_identifier,
            title=title,
            notes=notes,
            due_at=due_at,
            priority=priority,
            calendar_item_identifier=cid,
        )

    def save(self, reminder: Any, *, commit: bool) -> None:
        self.saves.append((reminder, commit))

    def remove(self, reminder: Any, *, commit: bool) -> None:  # pragma: no cover
        return None

    def fetch_reminders(
        self, *, list_identifier: str | None = None
    ) -> list[Any]:
        self.fetch_calls += 1
        if list_identifier is None:
            return list(self._inbound_reminders)
        return [
            r
            for r in self._inbound_reminders
            if getattr(r, "list_identifier", None) == list_identifier
        ]

    def list_name_for_identifier(self, list_identifier: str) -> str:
        return self._list_names.get(list_identifier, list_identifier)


def _reminders_from_fixture(
    fixture: dict[str, Any],
) -> list[_FakeEKReminder]:
    """Materialize fixture JSON into ``_FakeEKReminder`` instances with
    parsed datetimes. ``last_modified_at`` is required for the dedupe
    predicate; ``due_at`` is optional."""
    out: list[_FakeEKReminder] = []
    for entry in fixture["reminders"]:
        lmod = datetime.fromisoformat(
            entry["last_modified_at"].replace("Z", "+00:00")
        )
        due_raw = entry.get("due_at")
        due_at = (
            datetime.fromisoformat(due_raw.replace("Z", "+00:00"))
            if isinstance(due_raw, str)
            else None
        )
        out.append(
            _FakeEKReminder(
                list_identifier=entry["list_identifier"],
                title=entry["title"],
                notes=entry.get("notes"),
                due_at=due_at,
                priority=int(entry.get("priority", 0)),
                calendar_item_identifier=entry["calendarItemIdentifier"],
                last_modified_at=lmod,
                completed=bool(entry.get("completed", False)),
            )
        )
    return out


def _build_reminders_adapter(
    *,
    ek_store: _FakeEKEventStore,
    max_inbound_cycles: int = 1,
    max_outbound_cycles: int = 1,
    target_list_identifier: str = "list-household-001",
    member_id: str = "member-001",
) -> Any:
    """Construct a real ``AppleEventKitAdapter`` with the injected fake
    store. Bounded poll cycles so ``start()`` returns rather than
    blocking forever, with the per-instance interval pushed to 0 so the
    test settles in milliseconds."""

    def factory() -> Any:
        return ek_store

    adapter = AppleEventKitAdapter(
        config={
            "target_list_identifier": target_list_identifier,
            "member_id": member_id,
        },
        ek_event_store_factory=factory,
        max_outbound_cycles=max_outbound_cycles,
        max_inbound_cycles=max_inbound_cycles,
    )
    adapter.OUTBOUND_INTERVAL_S = 0  # type: ignore[misc]
    adapter.INBOUND_INTERVAL_S = 0  # type: ignore[misc]
    return adapter


def _write_channels_yaml(path: Path, *, port: int = 0) -> None:
    """Minimal config/channels.yaml with a single Reminders adapter
    entry. The supervisor only needs the file shape — adapters are
    injected via the loader callable, not the kind-string."""
    path.write_text(
        f"""
adapter_supervisor_host: 127.0.0.1
adapter_supervisor_port: {port}
adapters:
  - kind: reminders-apple-eventkit
    family: reminders
    sensitivity_floor: normal
""".lstrip(),
        encoding="utf-8",
    )


def _supervisor(
    *,
    config_path: Path,
    event_log: _RecordingEventLog,
    adapters: list[Adapter],
) -> AdapterSupervisor:
    """Build a supervisor whose ``ctx_factory`` hands each adapter a
    context tied to the shared recording log + frozen clock +
    deterministic correlation-id sequence."""

    counter = {"n": 0}

    def _next_corr() -> str:
        counter["n"] += 1
        return f"corr-rem-{counter['n']}"

    def _ctx_factory() -> AdapterContext:
        return AdapterContext(
            event_log=event_log,  # type: ignore[arg-type]
            config={},
            correlation_id_factory=_next_corr,
            now_factory=lambda: _FROZEN_NOW,
        )

    return AdapterSupervisor(
        config_path=config_path,
        ctx_factory=_ctx_factory,
        adapter_loader=lambda _cfg: list(adapters),
    )


async def _wait_for(predicate: Any, *, timeout: float = 2.0) -> None:
    """Spin-wait helper. Async tasks settle within milliseconds in this
    suite; the timeout guards against hangs if the supervisor's task
    machinery regresses. Mirrors the 11a integration helper."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {predicate!r}")


# ---------------------------------------------------------------------------
# 1) inbound round-trip — supervisor spawns adapter, inbound poll emits
#    one reminder.list_item.synced per non-never-sync reminder.
# ---------------------------------------------------------------------------


async def test_supervisor_inbound_round_trip_emits_synced_inbound(
    tmp_path: Path,
) -> None:
    """End-to-end inbound: the supervisor spawns the real
    ``AppleEventKitAdapter`` against a fake EKEventStore preloaded with
    the recorded fixture; ``start()`` spawns BOTH poll tasks; the
    bounded inbound cycle emits 3 ``reminder.list_item.synced`` v1
    events (the fixture has 4 reminders, 1 is never-sync via
    ``"#private"`` tag); the supervisor's status snapshot reflects the
    adapter; and ``stop()`` transitions to ``"stopped"`` cleanly."""

    channels = tmp_path / "channels.yaml"
    _write_channels_yaml(channels)

    fixture = _load_inbound_fixture()
    reminders = _reminders_from_fixture(fixture)
    ek_store = _FakeEKEventStore(
        list_names=fixture["lists"],
        inbound_reminders=reminders,
    )
    adapter = _build_reminders_adapter(ek_store=ek_store)

    event_log = _RecordingEventLog()
    sup = _supervisor(
        config_path=channels, event_log=event_log, adapters=[adapter]
    )

    await sup.start()
    try:
        await _wait_for(
            lambda: len(event_log.by_type("reminder.list_item.synced")) >= 3
        )

        # adapter.authenticated emitted exactly once at start.
        auth = event_log.by_type("adapter.authenticated")
        assert len(auth) == 1
        assert auth[0]["payload"]["adapter_name"] == "reminders-apple-eventkit"
        assert auth[0]["payload"]["adapter_family"] == "reminders"
        # OS-level permission ref, not a 1Password reference — EventKit
        # access is granted at the OS level per
        # docs/reference/apple-eventkit/access.md.
        assert (
            auth[0]["payload"]["token_storage_ref"]
            == "os://reminders-permission"
        )

        # 3 of 4 fixture reminders pass the never-sync filter; one event
        # per genuinely-new reminder lands in the log with
        # direction="inbound" and no causation_id (iCloud is the source).
        synced = event_log.by_type("reminder.list_item.synced")
        assert len(synced) == 3
        for call in synced:
            assert call["payload"]["direction"] == "inbound"
            assert "causation_id" not in call

        # The "#private" reminder is filtered.
        emitted_ids = {c["payload"]["remind_id"] for c in synced}
        assert (
            "x-coredata://AdministrateMe/EKReminder/rem-private"
            not in emitted_ids
        )

        # No adapter.error on the happy path.
        assert event_log.by_type("adapter.error") == []

        # Supervisor's snapshot reflects the live adapter entry.
        snap = sup.snapshot()
        assert "reminders-apple-eventkit" in snap["adapters"]
    finally:
        await sup.stop()
        snap_after = sup.snapshot()
        assert (
            snap_after["adapters"]["reminders-apple-eventkit"]["state"]
            == "stopped"
        )


# ---------------------------------------------------------------------------
# 2) outbound round-trip — upstream reminder.dispatched → EKEventStore.save
#    → reminder.list_item.synced with direction="outbound".
# ---------------------------------------------------------------------------


async def test_supervisor_outbound_round_trip_emits_synced_outbound(
    tmp_path: Path,
) -> None:
    """End-to-end outbound: an upstream ``reminder.dispatched`` event is
    queued on the adapter before ``start()`` spawns the supervised
    tasks; the bounded outbound drain calls
    ``EKEventStore.save(_:commit:)`` and emits one
    ``reminder.list_item.synced`` v1 event with ``direction="outbound"``
    and ``causation_id`` set to the upstream event id."""

    channels = tmp_path / "channels.yaml"
    _write_channels_yaml(channels)

    ek_store = _FakeEKEventStore(
        list_names={"list-household-001": "AdministrateMe Household"},
        mint_calendar_item_identifiers=["EKID-int-7"],
    )
    # No inbound reminders so the inbound cycle is a clean no-op; only
    # the outbound path produces a synced event.
    adapter = _build_reminders_adapter(ek_store=ek_store)
    adapter.enqueue_outbound(
        {
            "id": "evt-integration-1",
            "type": "reminder.dispatched",
            "payload": {
                "commitment_id": "cmt-int-1",
                "owner_member_id": "member-001",
                "recipient_party_id": "party-int-1",
                "composed_text": "Mail rent check by Friday",
                "channel": "imessage",
                "due_at": _FROZEN_NOW.isoformat(),
                "delivered": False,
                "validation_failed": False,
            },
        }
    )

    event_log = _RecordingEventLog()
    sup = _supervisor(
        config_path=channels, event_log=event_log, adapters=[adapter]
    )

    await sup.start()
    try:
        await _wait_for(
            lambda: any(
                c["payload"].get("direction") == "outbound"
                for c in event_log.by_type("reminder.list_item.synced")
            )
        )

        # save(_:commit:) was called once with commit=True.
        assert len(ek_store.saves) == 1
        saved_reminder, commit_flag = ek_store.saves[0]
        assert commit_flag is True
        assert saved_reminder.calendarItemIdentifier == "EKID-int-7"

        outbound = [
            c
            for c in event_log.by_type("reminder.list_item.synced")
            if c["payload"]["direction"] == "outbound"
        ]
        assert len(outbound) == 1
        call = outbound[0]
        assert call["schema_version"] == 1
        # [Causation] outbound carries causation_id pointing at the
        # upstream event that triggered the write — the round-trip
        # traceability guarantee from §9.
        assert call["causation_id"] == "evt-integration-1"
        payload = call["payload"]
        assert payload["remind_id"] == "EKID-int-7"
        assert payload["list_identifier"] == "list-household-001"
        assert payload["list_name"] == "AdministrateMe Household"
        assert payload["title"] == "Mail rent check by Friday"
        assert payload["priority"] == 1
        # correlation_id came from the supervisor's ctx_factory.
        assert call["correlation_id"].startswith("corr-rem-")
    finally:
        await sup.stop()


# ---------------------------------------------------------------------------
# 3) shape check — config/channels.yaml on disk now includes the
#    Reminders entry alongside Gmail + Plaid.
# ---------------------------------------------------------------------------


async def test_supervisor_parses_real_repo_channels_yaml_has_reminders_entry(
    tmp_path: Path,
) -> None:
    """The on-disk ``config/channels.yaml`` extended in c4 round-trips
    through ``AdapterSupervisor.load_config()`` and exposes the
    Reminders adapter entry alongside Gmail (11a) + Plaid (11b). Any
    drift in the YAML shape breaks this test."""

    repo_channels = REPO_ROOT / "config" / "channels.yaml"
    copied = tmp_path / "channels.yaml"
    copied.write_text(
        repo_channels.read_text(encoding="utf-8"), encoding="utf-8"
    )

    sup = _supervisor(
        config_path=copied, event_log=_RecordingEventLog(), adapters=[]
    )
    config = sup.load_config()

    adapters_entries = config["adapters"]
    assert isinstance(adapters_entries, list)
    rem = next(
        (
            a
            for a in adapters_entries
            if a.get("kind") == "reminders-apple-eventkit"
        ),
        None,
    )
    assert rem is not None, (
        "config/channels.yaml is missing the reminders-apple-eventkit entry"
    )
    assert rem["family"] == "reminders"
    assert rem["sensitivity_floor"] == "normal"
    rem_config = rem["config"]
    assert rem_config["member_id"]
    assert rem_config["target_list_identifier"]
    assert "Work" in rem_config["never_sync_list_substrings"]
    assert "#private" in rem_config["never_sync_tag_substrings"]
    assert rem_config["inbound_poll_interval_s"] == 30
    assert rem_config["outbound_poll_interval_s"] == 30
