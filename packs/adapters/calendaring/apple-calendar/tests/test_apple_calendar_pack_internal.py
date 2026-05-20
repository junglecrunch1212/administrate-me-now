"""Pack-internal tests for the Apple Calendar Cat-B fresh adapter.

Closes prompt 11b-ii-β-α §7 c4 deliverable 1 (4 tests; F-4 unique
basename ``test_apple_calendar_pack_internal.py`` against the Google
Calendar / Apple Reminders / Gmail / Plaid pack-internal basenames).
Sister to the framework-level suite at
``tests/unit/adapters/calendaring/test_apple_calendar.py`` (11b-ii-β-α
c3) and the integration sibling at
``tests/integration/test_adapter_apple_calendar_supervisor.py`` (c4
deliverable 2). The framework tests cover the Cat-B inheritance / emit
contract / observation wrap / inbound dedupe end-to-end; this file pins
the pack-side contract surface the pack itself owns:

1. ``test_capabilities_class_attribute_matches_manifest`` — the
   :class:`AppleCalendarAdapter` ``capabilities`` ClassVar agrees with
   the manifest's ``capabilities`` block per memo 05 §3.2. The
   supervisor reads the ClassVar; the manifest is the source of truth;
   drift between the two would mean the pack ships with two different
   stated capabilities (the validator only reads the manifest, not the
   class, so this pack-internal test is the only guard).

2. ``test_pack_yaml_round_trips_through_parse_adapter_manifest`` — the
   manifest's Cat-B + asymmetric ``write_capabilities=["create_event"]``
   (per [UT-27]) + ``sharing_model_discriminator="owner_member_id"``
   (per memo 05 §2.5) + ``dedup_strategy={"kind": "external_id"}``
   (EKEvent's stable ``eventIdentifier``) all round-trip cleanly
   through :func:`parse_adapter_manifest`. F-7 pinned here too: NO
   top-level ``subkind`` / ``deployment_targets`` fields on the central
   pack (dual-deployment is 11b-ii-β-β scope per
   `docs/11-split-memo-11b-ii-beta.md` §2).

3. ``test_inbound_emit_payload_validates_against_calendar_event_added_v1_schema``
   — :meth:`_emit_calendar_event_added` produces a payload that
   validates byte-for-byte against the on-main
   :class:`CalendarEventAddedV1` schema (``extra="forbid"`` — any drift
   would fail validation here, the pack-side guard against accidental
   schema-shape drift). Pins F-2 (MAP DON'T REGISTER) at the pack
   layer — the framework test pins it at the schema-source layer.

4. ``test_six_validator_checks_return_zero_errors_and_no_advisory`` —
   the 11-β validator's six checks
   (capability_runtime_coherence, event_schema_registration,
   projection_subscription_compatibility, base_class_conformance,
   write_capabilities_check, observation_mode_wrapping) return zero
   ``error`` findings against the fresh pack AND no
   ``advisory_pending_retrofit`` finding (which only attaches to
   retrofitted v1 packs, never to fresh canonical-build packs like
   this one).

F-4 (HARD/standing) — basename ``test_apple_calendar_pack_internal.py``
is unique against ``test_google_calendar_pack_internal.py``,
``test_gmail_send_pack_internal.py``,
``test_gmail_adapter_pack_internal.py``,
``test_apple_eventkit_adapter_pack_internal.py``,
``test_apple_eventkit_dual_emit_pack_internal.py``,
``test_apple_eventkit_external_state_mirror_retrofit_pack_internal.py``,
``test_apple_eventkit_dual_deployment_pack_internal.py``,
``test_plaid_adapter_pack_internal.py``,
``test_google_tasks_pack_internal.py``. Pytest collection from the
repo root does not collide.

F-6 (HARD/from 11a c4) — this directory carries NO ``__init__.py``.
Canonical 11a c4 removed an accidental empty ``__init__.py`` from
``packs/adapters/messaging/gmail/tests/`` because it tripped pytest
package-discovery collision; the same prohibition applies to every
pack-internal tests directory.

``packs/adapters/calendaring/apple-calendar`` includes a hyphen in the
final directory segment and is therefore not a valid Python package
name; the adapter is loaded via the file-path importlib pattern shared
with the framework test at
``tests/unit/adapters/calendaring/test_apple_calendar.py``.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from adminme.adapters.base import AdapterContext
from adminme.events.schemas.ingest import CalendarEventAddedV1
from adminme.lib.pack_registry.parser import parse_adapter_manifest
from adminme.lib.pack_registry.validator import validate_adapter_pack

PACK_ROOT = Path(__file__).resolve().parents[1]


def _load_adapter_module() -> Any:
    """Load ``adapter.py`` from the pack root via importlib — same
    file-path import pattern the supervisor's loader uses at runtime.
    Packs are not a top-level Python package under poetry (only
    ``adminme`` is), and the ``apple-calendar`` directory segment's
    hyphen prevents a ``from packs...apple-calendar.adapter import``
    statement entirely."""
    spec = importlib.util.spec_from_file_location(
        "apple_calendar_pack_internal_under_test", PACK_ROOT / "adapter.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_adapter_module()
AppleCalendarAdapter: Any = _adapter_mod.AppleCalendarAdapter


def test_capabilities_class_attribute_matches_manifest() -> None:
    """The :class:`AppleCalendarAdapter` ``capabilities`` ClassVar
    mirrors the manifest's ``capabilities`` block at the class layer
    per memo 05 §3.2. The supervisor's runtime introspection reads the
    ClassVar; the validator only reads the manifest. Drift between the
    two would mean the pack ships with two different stated
    capabilities — this pack-internal test is the only guard."""
    manifest = parse_adapter_manifest(PACK_ROOT)
    cls_caps = AppleCalendarAdapter.capabilities

    assert isinstance(cls_caps, list)
    assert len(cls_caps) == len(manifest.capabilities) == 1
    cls_entry = cls_caps[0]
    manifest_entry = manifest.capabilities[0]
    assert cls_entry["kind"] == manifest_entry.kind == "Cat-B"
    assert cls_entry["runtime"] == manifest_entry.runtime == "central"
    assert (
        cls_entry["event_families"]
        == manifest_entry.event_families
        == ["calendar.event_added"]
    )


def test_pack_yaml_round_trips_through_parse_adapter_manifest() -> None:
    """The fresh-pack manifest extension fields populate the
    :class:`AdapterManifest` model per memo 05 §3.1.
    ``write_capabilities`` is the asymmetric Cat-B surface per [UT-27];
    declaring ONLY ``create_event`` matches what the adapter actually
    implements (``update_event`` / ``delete_event`` deferred per parent
    draft + §6). ``dedup_strategy`` is ``{"kind": "external_id"}``
    because the ``EKEvent.eventIdentifier()`` is the stable external id
    per the pack's inbound dedupe contract.
    ``sharing_model_discriminator`` is ``owner_member_id`` per memo 05
    §2.5 — the future calendar projection's per-member-vs-shared row
    column. F-7 also pinned: NO top-level ``subkind`` /
    ``deployment_targets`` fields on the central-only β-α pack
    (dual-deployment is β-β scope per
    `docs/11-split-memo-11b-ii-beta.md` §2)."""
    manifest = parse_adapter_manifest(PACK_ROOT)

    assert manifest.capabilities[0].kind == "Cat-B"
    assert manifest.capabilities[0].runtime == "central"
    assert manifest.write_capabilities == ["create_event"]
    assert manifest.sharing_model_discriminator == "owner_member_id"
    assert manifest.dedup_strategy == {"kind": "external_id"}
    assert manifest.sensitivity_default == "normal"
    assert manifest.observation_mode_required is True

    # F-7: NO dual-deployment surface on β-α. The parser strips
    # unknown top-level keys, so the AdapterManifest model never sees
    # ``subkind`` / ``deployment_targets`` regardless of what the
    # pack.yaml contains. Pin F-7 by reading pack.yaml directly and
    # asserting the absence at the source — α-2's pack.yaml (Apple
    # Reminders) added these top-level keys, and β-β will add them to
    # Apple Calendar's pack.yaml the same way. Until β-β lands they
    # must be absent here.
    with (PACK_ROOT / "pack.yaml").open(encoding="utf-8") as f:
        raw_manifest = yaml.safe_load(f)
    assert "subkind" not in raw_manifest, (
        "F-7: NO top-level subkind on β-α pack.yaml (dual-deployment "
        "is β-β scope per docs/11-split-memo-11b-ii-beta.md §2)"
    )
    assert "deployment_targets" not in raw_manifest, (
        "F-7: NO top-level deployment_targets on β-α pack.yaml "
        "(dual-deployment is β-β scope)"
    )


class _FakeEventLog:
    """Records ``append_sync(**kwargs)`` calls for assertion. The
    adapter's ``_append`` probes for ``append_sync`` first, matching
    the Gmail / Apple Reminders patterns the framework + integration
    suites use."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def append_sync(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _make_adapter() -> Any:
    return AppleCalendarAdapter(
        config={
            "calendar_identifier": "apple-calendar-household-001",
            "member_id": "member-001",
        },
    )


def _make_ctx(event_log: _FakeEventLog) -> AdapterContext:
    frozen = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
    return AdapterContext(
        event_log=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-pack-internal-1",
        now_factory=lambda: frozen,
    )


class _PackInternalFakeEKEvent:
    """Minimal ``EKEvent`` fake for the pack-internal emit-payload test.
    Mirrors the framework suite's ``_FakeEKEvent`` shape — the seven
    accessor methods :meth:`_emit_calendar_event_added` reads."""

    def __init__(
        self,
        *,
        event_identifier: str,
        title: str | None,
        start_date: Any,
        end_date: Any,
        location: str | None,
        notes: str | None,
        attendees: list[Any] | None,
    ) -> None:
        self._event_identifier = event_identifier
        self._title = title
        self._start_date = start_date
        self._end_date = end_date
        self._location = location
        self._notes = notes
        self._attendees = attendees

    def eventIdentifier(self) -> str:
        return self._event_identifier

    def title(self) -> str | None:
        return self._title

    def startDate(self) -> Any:
        return self._start_date

    def endDate(self) -> Any:
        return self._end_date

    def location(self) -> str | None:
        return self._location

    def notes(self) -> str | None:
        return self._notes

    def attendees(self) -> list[Any] | None:
        return self._attendees


def test_inbound_emit_payload_validates_against_calendar_event_added_v1_schema() -> None:
    """The pack's :meth:`_emit_calendar_event_added` helper produces a
    payload that validates byte-for-byte against the on-main
    :class:`CalendarEventAddedV1` schema. The schema's
    ``model_config = {"extra": "forbid"}`` rejects any drift in field
    names introduced by the pack; the per-field ``min_length=1`` on
    ``source`` / ``external_event_id`` / ``calendar_id`` / ``start`` /
    ``end`` rejects empty values. This is the pack-side guard against
    accidental schema-shape drift across future adapter edits; F-2 (MAP
    DON'T REGISTER) is pinned both here (pack layer) and at the
    framework layer in
    ``tests/unit/adapters/calendaring/test_apple_calendar.py
    ::test_poll_emit_payload_validates_against_calendar_event_added_v1``.
    """
    adapter = _make_adapter()
    event_log = _FakeEventLog()
    ctx = _make_ctx(event_log)

    event = _PackInternalFakeEKEvent(
        event_identifier="apple-evt-pack-001",
        title="Pack-internal emit",
        start_date=datetime(2026, 5, 20, 15, 0, 0, tzinfo=timezone.utc),
        end_date=datetime(2026, 5, 20, 16, 0, 0, tzinfo=timezone.utc),
        location="HQ Conference Room",
        notes="Pack-internal round-trip",
        attendees=[{"name": "Alice", "url": "mailto:alice@example.test"}],
    )
    adapter._emit_calendar_event_added(
        ctx=ctx,
        calendar_identifier="apple-calendar-household-001",
        event=event,
    )

    added_calls = [
        c for c in event_log.calls if c["type"] == "calendar.event_added"
    ]
    assert len(added_calls) == 1
    payload = added_calls[0]["payload"]
    # extra="forbid" — extras would raise here.
    instance = CalendarEventAddedV1.model_validate(payload)
    assert instance.source == "apple_calendar"
    assert instance.external_event_id == "apple-evt-pack-001"
    assert instance.calendar_id == "apple-calendar-household-001"
    assert instance.summary == "Pack-internal emit"
    assert instance.start == "2026-05-20T15:00:00Z"
    assert instance.end == "2026-05-20T16:00:00Z"
    assert instance.location == "HQ Conference Room"
    assert instance.body == "Pack-internal round-trip"
    assert instance.attendees == [
        {"name": "Alice", "url": "mailto:alice@example.test"}
    ]


def test_six_validator_checks_return_zero_errors_and_no_advisory() -> None:
    """The 11-β validator runs six checks per memo 05 §3.4
    (capability_runtime_coherence, event_schema_registration,
    projection_subscription_compatibility, base_class_conformance,
    write_capabilities_check, observation_mode_wrapping). The fresh
    canonical-build pack passes all six with zero ``error`` findings
    and NO ``advisory_pending_retrofit`` finding (the latter only
    attaches to retrofitted v1 packs that predate the framework's
    ABCs; this pack is fresh canonical-build per 11b-ii-β-α, so no
    retrofit is pending). Closes the parent draft §"Stop condition":
    ``adminme adapters validate packs/adapters/calendaring/apple-calendar``
    exits 0."""
    result = validate_adapter_pack(PACK_ROOT)

    assert not result.has_errors(), (
        f"validator reports errors against fresh pack: {result.errors()}"
    )
    pending = [
        f
        for f in result.advisories()
        if f.check == "base_class_conformance"
        and "advisory_pending_retrofit" in f.message
    ]
    assert pending == [], (
        f"unexpected advisory_pending_retrofit on fresh pack: {pending}"
    )
