"""Pack-internal tests for the Home Assistant Cat-C+E multi-capability adapter.

Closes prompt 11g §7 c4 deliverable 1 (≥3 tests; F-4 unique basename per
``test_home_assistant_pack_internal.py`` against the Gmail / Plaid / Apple
EventKit / Google Calendar / Google Tasks pack-internal basenames). Sister to
the framework-level suite at ``tests/unit/adapters/automation/test_home_assistant.py``
(11g c3) — the framework tests cover the Cat-E inheritance / multi-capability
declaration / execute_action contract / observation wrap / Cat-C inbound
dedupe end-to-end; this file pins the pack-side contract surface the pack
itself owns:

1. ``test_capabilities_class_attribute_matches_manifest`` — the
   :class:`HomeAssistantAdapter` ``capabilities`` ClassVar (two entries:
   Cat-C reads + Cat-E writes per [PM-35] / [D24]) agrees with the
   manifest's ``capabilities`` block per memo 05 §3.2. The supervisor
   reads the ClassVar; the validator reads the manifest; drift between
   the two would mean the pack ships with two different stated
   capabilities (only this pack-internal test guards the drift since
   the validator's checks read the manifest alone).

2. ``test_pack_yaml_round_trips_through_parse_adapter_manifest`` — the
   manifest's multi-capability declaration (Cat-C + Cat-E) + the
   Cat-C per-capability ``observation_mode_required=False`` override
   (reads are NOT side effects per memo §8) coexisting with the
   pack-level Cat-E ``observation_mode_required=True`` (§6.20) +
   ``sensitivity_default="normal"`` + ``dedup_strategy={"kind":
   "external_id"}`` round-trip cleanly through
   :func:`parse_adapter_manifest`.

3. ``test_inbound_emit_payload_validates_against_ha_state_changed_v1_schema``
   — :meth:`_emit_state_changed` produces a payload that
   :class:`HaStateChangedV1` (c1 schema) accepts byte-for-byte
   (``extra="forbid"`` rejects drift). Pack-side guard against
   accidental schema-shape drift on the Cat-C emit path; pins F-2
   (MAP DON'T REGISTER) at the pack layer alongside the framework-layer
   pin in c3.

4. ``test_six_validator_checks_return_zero_errors_and_no_advisory`` —
   the 11-β validator's six checks (capability_runtime_coherence /
   event_schema_registration / projection_subscription_compatibility /
   base_class_conformance / write_capabilities_check /
   observation_mode_wrapping) return zero ``error`` findings against
   the fresh pack AND no ``advisory_pending_retrofit`` finding (the
   latter only attaches to retrofitted v1 packs; this is fresh
   canonical-build per 11g, so no retrofit is pending).

F-4 (HARD/standing) — basename ``test_home_assistant_pack_internal.py`` is
unique against ``test_gmail_send_pack_internal.py``,
``test_gmail_adapter_pack_internal.py``,
``test_apple_eventkit_adapter_pack_internal.py``,
``test_apple_eventkit_dual_emit_pack_internal.py``,
``test_apple_eventkit_external_state_mirror_retrofit_pack_internal.py``,
``test_plaid_adapter_pack_internal.py``,
``test_google_calendar_pack_internal.py``,
``test_google_tasks_pack_internal.py``. Pytest collection from the repo
root does not collide.

F-6 (HARD/standing) — this directory carries NO ``__init__.py``. Canonical
11a c4 removed an accidental empty ``__init__.py`` from
``packs/adapters/messaging/gmail/tests/`` because it tripped pytest
package-discovery collision; the same prohibition applies to every
pack-internal tests directory.

``packs/adapters/automation/home-assistant`` includes a hyphen in the
final directory segment and is therefore not a valid Python package
name; the adapter is loaded via the file-path importlib pattern shared
with the framework test at
``tests/unit/adapters/automation/test_home_assistant.py``.
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adminme.adapters.base import AdapterContext
from adminme.events.schemas.automation import HaStateChangedV1
from adminme.lib.pack_registry.parser import parse_adapter_manifest
from adminme.lib.pack_registry.validator import validate_adapter_pack

PACK_ROOT = Path(__file__).resolve().parents[1]


def _load_adapter_module() -> Any:
    """Load ``adapter.py`` from the pack root via importlib — same
    file-path import pattern the supervisor's loader uses at runtime."""
    spec = importlib.util.spec_from_file_location(
        "home_assistant_pack_internal_under_test", PACK_ROOT / "adapter.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_adapter_module()
HomeAssistantAdapter: Any = _adapter_mod.HomeAssistantAdapter


def test_capabilities_class_attribute_matches_manifest() -> None:
    """The :class:`HomeAssistantAdapter` ``capabilities`` ClassVar mirrors
    the manifest's ``capabilities`` block at the class layer per memo 05
    §3.2 / [PM-35]. Two entries (Cat-C + Cat-E); the supervisor's runtime
    introspection reads the ClassVar; the validator only reads the
    manifest. Drift between the two would mean the pack ships with two
    different stated capabilities — this pack-internal test is the only
    guard. The Cat-C per-capability ``observation_mode_required=False``
    override (reads are NOT side effects per memo §8) coexists with the
    Cat-E entry's ``True`` value (§6.20) — the test pins both."""
    manifest = parse_adapter_manifest(PACK_ROOT)
    cls_caps = HomeAssistantAdapter.capabilities

    assert isinstance(cls_caps, list)
    assert len(cls_caps) == len(manifest.capabilities) == 2

    cls_by_kind = {entry["kind"]: entry for entry in cls_caps}
    manifest_by_kind = {entry.kind: entry for entry in manifest.capabilities}
    assert set(cls_by_kind) == set(manifest_by_kind) == {"Cat-C", "Cat-E"}

    cls_c = cls_by_kind["Cat-C"]
    manifest_c = manifest_by_kind["Cat-C"]
    assert cls_c["runtime"] == manifest_c.runtime == "central"
    assert cls_c["event_families"] == manifest_c.event_families == ["ha.state_changed"]
    assert cls_c["observation_mode_required"] is False
    assert manifest_c.observation_mode_required is False

    cls_e = cls_by_kind["Cat-E"]
    manifest_e = manifest_by_kind["Cat-E"]
    assert cls_e["runtime"] == manifest_e.runtime == "central"
    assert (
        cls_e["event_families"]
        == manifest_e.event_families
        == ["action.executed", "action.failed"]
    )
    assert cls_e["observation_mode_required"] is True
    assert manifest_e.observation_mode_required is True


def test_pack_yaml_round_trips_through_parse_adapter_manifest() -> None:
    """The multi-capability manifest fields populate the
    :class:`AdapterManifest` model per memo 05 §3.1 / [PM-35] / [D24].
    ``observation_mode_required: true`` at the pack level reflects the
    Cat-E gate that cannot be opted out per §6.20; the per-capability
    Cat-C ``false`` override is exercised here to pin memo §8 (reads are
    not side effects). ``dedup_strategy={"kind": "external_id"}`` reflects
    HA's stable ``entity_id`` keying for the inbound cache (the c2
    adapter dedupes by ``(entity_id, last_changed)``). All assertions
    together exercise the manifest extension fields that flip the
    validator's six checks to zero errors (the fourth test pins that)."""
    manifest = parse_adapter_manifest(PACK_ROOT)

    kinds = {cap.kind for cap in manifest.capabilities}
    assert kinds == {"Cat-C", "Cat-E"}
    # Cat-E ABC contract: cannot opt out at pack level per §6.20.
    assert manifest.observation_mode_required is True
    # Cat-E sensitivity_default per memo 05 §2.5 + §6 invariant 10.
    assert manifest.sensitivity_default == "normal"
    # Cat-E has no write_capabilities surface (that's Cat-B); Cat-E
    # discriminates the outbound surface via ActionRequest.kind in-pack.
    assert manifest.write_capabilities == []
    assert manifest.dedup_strategy == {"kind": "external_id"}


class _FakeEventLog:
    """Records typed asynchronous event-sink writes for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def tenant_id(self) -> str:
        return "tenant-test"

    async def write(self, envelope: Any, **kwargs: Any) -> str:
        self.calls.append({**envelope.model_dump(), **kwargs})
        return f"event-{len(self.calls)}"


def _make_adapter() -> Any:
    return HomeAssistantAdapter(
        config={
            "access_token_ref": "op://test/v/ha/token",
            "base_url": "http://homeassistant.local:8123",
        },
    )


def _make_ctx(event_log: _FakeEventLog) -> AdapterContext:
    frozen = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
    return AdapterContext(
        event_sink=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-pack-internal-1",
        now_factory=lambda: frozen,
    )


def test_inbound_emit_payload_validates_against_ha_state_changed_v1_schema() -> None:
    """The pack's :meth:`_emit_state_changed` helper produces a payload
    that validates byte-for-byte against the on-main
    :class:`HaStateChangedV1` schema. ``extra="forbid"`` rejects field
    drift; ``min_length=1`` on ``entity_id`` / ``state`` rejects empties;
    ``source: Literal["home_assistant"]`` pins the source discriminant.
    Pack-side guard against accidental schema-shape drift; F-2 (MAP
    DON'T REGISTER) pinned at the pack layer alongside the framework
    layer pin in
    ``tests/unit/adapters/automation/test_home_assistant.py
    ::test_poll_states_emit_payload_validates_against_ha_state_changed_v1``.
    """
    adapter = _make_adapter()
    event_log = _FakeEventLog()
    ctx = _make_ctx(event_log)

    ha_state: dict[str, Any] = {
        "entity_id": "light.living_room",
        "state": "on",
        "attributes": {
            "brightness": 200,
            "color_mode": "color_temp",
            "color_temp_kelvin": 3000,
        },
        "last_changed": "2026-05-18T11:00:00+00:00",
        "last_updated": "2026-05-18T11:00:05+00:00",
    }
    asyncio.run(adapter._emit_state_changed(ctx=ctx, state=ha_state))

    state_calls = [c for c in event_log.calls if c["type"] == "ha.state_changed"]
    assert len(state_calls) == 1
    payload = state_calls[0]["payload"]
    # extra="forbid" — extras would raise here.
    instance = HaStateChangedV1.model_validate(payload)
    assert instance.entity_id == "light.living_room"
    assert instance.state == "on"
    assert instance.source == "home_assistant"
    assert instance.attributes["brightness"] == 200
    assert instance.attributes["color_mode"] == "color_temp"
    assert instance.last_changed == datetime(2026, 5, 18, 11, 0, 0, tzinfo=timezone.utc)
    assert instance.last_updated == datetime(2026, 5, 18, 11, 0, 5, tzinfo=timezone.utc)


def test_six_validator_checks_return_zero_errors_and_no_advisory() -> None:
    """The 11-β validator runs six checks per memo 05 §3.4
    (capability_runtime_coherence, event_schema_registration,
    projection_subscription_compatibility, base_class_conformance,
    write_capabilities_check, observation_mode_wrapping). The fresh
    canonical-build pack passes all six with zero ``error`` findings and
    NO ``advisory_pending_retrofit`` finding (the latter only attaches
    to retrofitted v1 packs that predate the framework's ABCs; this
    pack is fresh canonical-build per 11g, so no retrofit is pending).
    Closes the parent draft §"Stop condition":
    ``adminme adapters validate packs/adapters/automation/home-assistant``
    exits 0."""
    result = validate_adapter_pack(PACK_ROOT)

    assert (
        not result.has_errors()
    ), f"validator reports errors against fresh pack: {result.errors()}"
    pending = [
        f
        for f in result.advisories()
        if f.check == "base_class_conformance"
        and "advisory_pending_retrofit" in f.message
    ]
    assert (
        pending == []
    ), f"unexpected advisory_pending_retrofit on fresh pack: {pending}"
