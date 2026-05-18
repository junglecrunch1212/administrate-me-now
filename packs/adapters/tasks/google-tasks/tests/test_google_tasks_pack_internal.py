"""Pack-internal tests for the Google Tasks Cat-B fresh adapter.

Closes prompt 11b-iv c4 deliverable 1 (4 tests; F-4 unique basename per
``test_google_tasks_pack_internal.py`` against the Gmail / Plaid / Apple
Reminders / Google Calendar pack-internal basenames). Sister to the
framework-level suite at ``tests/unit/adapters/tasks/test_google_tasks.py``
(11b-iv c3) — the framework tests cover the Cat-B inheritance / emit
contract / observation wrap / inbound dedupe end-to-end; this file pins
the pack-side contract surface the pack itself owns:

1. ``test_capabilities_class_attribute_matches_manifest`` — the
   :class:`GoogleTasksAdapter` ``capabilities`` ClassVar agrees with the
   manifest's ``capabilities`` block per memo 05 §3.2. The supervisor
   reads the ClassVar; the manifest is the source of truth; drift
   between the two would mean the pack ships with two different stated
   capabilities (the validator only reads the manifest, not the class,
   so this pack-internal test is the only guard).

2. ``test_pack_yaml_round_trips_through_parse_adapter_manifest`` — the
   manifest's ``family=tasks`` (authorized by the PR #170 sidecar Cat-B
   Literal extension) + Cat-B + asymmetric
   ``write_capabilities=["create_task"]`` (per [UT-27]) +
   ``sharing_model_discriminator="owner_member_id"`` (per memo 05 §2.5)
   + ``dedup_strategy={"kind": "external_id"}`` (Google's stable
   ``task.id``) all round-trip cleanly through
   :func:`parse_adapter_manifest`.

3. ``test_inbound_emit_payload_validates_against_list_item_added_v1_schema``
   — :meth:`_emit_list_item_added` produces a payload that validates
   byte-for-byte against the on-main :class:`ListItemAddedV1` schema
   (``extra="forbid"`` — any drift would fail validation here, the
   pack-side guard against accidental schema-shape drift). Pins F-2
   (MAP DON'T REGISTER) at the pack layer — the framework test pins it
   at the schema-source layer.

4. ``test_six_validator_checks_return_zero_errors_and_no_advisory`` —
   the 11-β validator's six checks (capability_runtime_coherence,
   event_schema_registration, projection_subscription_compatibility,
   base_class_conformance, write_capabilities_check,
   observation_mode_wrapping) return zero ``error`` findings against
   the fresh pack AND no ``advisory_pending_retrofit`` finding (which
   only attaches to retrofitted v1 packs, never to fresh canonical-build
   packs like this one).

F-4 (HARD/standing) — basename ``test_google_tasks_pack_internal.py``
is unique against ``test_gmail_send_pack_internal.py``,
``test_gmail_adapter_pack_internal.py``,
``test_apple_eventkit_adapter_pack_internal.py``,
``test_apple_eventkit_dual_emit_pack_internal.py``,
``test_apple_eventkit_external_state_mirror_retrofit_pack_internal.py``,
``test_plaid_adapter_pack_internal.py``,
``test_google_calendar_pack_internal.py``. Pytest collection from the
repo root does not collide.

F-6 (HARD/new) — this directory carries NO ``__init__.py``. The
prohibition applies to every pack-internal tests directory per the
11a / 11b-iii precedent (an accidental empty ``__init__.py`` would trip
pytest package-discovery collision).

``packs/adapters/tasks/google-tasks`` includes a hyphen in the final
directory segment and is therefore not a valid Python package name; the
adapter is loaded via the file-path importlib pattern shared with the
framework test at ``tests/unit/adapters/tasks/test_google_tasks.py``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from adminme.events.schemas.lists import ListItemAddedV1
from adminme.lib.pack_registry.parser import parse_adapter_manifest
from adminme.lib.pack_registry.validator import validate_adapter_pack
from adminme.adapters.base import AdapterContext

PACK_ROOT = Path(__file__).resolve().parents[1]


def _load_adapter_module() -> Any:
    """Load ``adapter.py`` from the pack root via importlib — same
    file-path import pattern the supervisor's loader uses at runtime.
    Packs are not a top-level Python package under poetry (only
    ``adminme`` is), and the ``google-tasks`` directory segment's hyphen
    prevents a ``from packs...google-tasks.adapter import`` statement
    entirely."""
    spec = importlib.util.spec_from_file_location(
        "google_tasks_pack_internal_under_test", PACK_ROOT / "adapter.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_adapter_module()
GoogleTasksAdapter: Any = _adapter_mod.GoogleTasksAdapter


def test_capabilities_class_attribute_matches_manifest() -> None:
    """The :class:`GoogleTasksAdapter` ``capabilities`` ClassVar mirrors
    the manifest's ``capabilities`` block at the class layer per memo 05
    §3.2. The supervisor's runtime introspection reads the ClassVar; the
    validator only reads the manifest. Drift between the two would mean
    the pack ships with two different stated capabilities — this
    pack-internal test is the only guard."""
    manifest = parse_adapter_manifest(PACK_ROOT)
    cls_caps = GoogleTasksAdapter.capabilities

    assert isinstance(cls_caps, list)
    assert len(cls_caps) == len(manifest.capabilities) == 1
    cls_entry = cls_caps[0]
    manifest_entry = manifest.capabilities[0]
    assert cls_entry["kind"] == manifest_entry.kind == "Cat-B"
    assert cls_entry["runtime"] == manifest_entry.runtime == "central"
    assert (
        cls_entry["event_families"]
        == manifest_entry.event_families
        == ["list_item.added"]
    )


def test_pack_yaml_round_trips_through_parse_adapter_manifest() -> None:
    """The fresh-pack manifest extension fields populate the
    :class:`AdapterManifest` model per memo 05 §3.1. ``family=tasks`` is
    the first pack to declare the new Literal value (authorized by the
    PR #170 sidecar Cat-B Literal extension). ``write_capabilities`` is
    the asymmetric Cat-B surface per [UT-27]; declaring ONLY
    ``create_task`` matches what the adapter actually implements
    (``update_task`` / ``delete_task`` / ``complete_task`` deferred per
    parent draft + §6). ``dedup_strategy`` is ``{"kind": "external_id"}``
    because Google's ``task.id`` is the stable external id per the
    pack's inbound dedupe contract. ``sharing_model_discriminator`` is
    ``owner_member_id`` per memo 05 §2.5 — the future tasks projection's
    per-member-vs-shared row column. All assertions together flip the
    validator's six checks to zero errors + no advisories (the fourth
    test pins that)."""
    manifest = parse_adapter_manifest(PACK_ROOT)

    assert manifest.family == "tasks"
    assert manifest.capabilities[0].kind == "Cat-B"
    assert manifest.capabilities[0].runtime == "central"
    assert manifest.write_capabilities == ["create_task"]
    assert manifest.sharing_model_discriminator == "owner_member_id"
    assert manifest.dedup_strategy == {"kind": "external_id"}
    assert manifest.sensitivity_default == "normal"
    assert manifest.observation_mode_required is True


class _FakeEventLog:
    """Records ``append_sync(**kwargs)`` calls for assertion. The
    adapter's ``_append`` probes for ``append_sync`` first, matching the
    Gmail / Apple Reminders / Google Calendar patterns the framework +
    integration suites use."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def append_sync(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _make_adapter() -> Any:
    return GoogleTasksAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "tasklist_id": "tasklist-primary",
            "user_email": "operator@example.test",
            "member_id": "member-pack-internal-001",
            "list_name": "Personal tasks",
        },
    )


def _make_ctx(event_log: _FakeEventLog) -> AdapterContext:
    from datetime import datetime, timezone

    frozen = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
    return AdapterContext(
        event_log=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-pack-internal-1",
        now_factory=lambda: frozen,
    )


def test_inbound_emit_payload_validates_against_list_item_added_v1_schema() -> None:
    """The pack's :meth:`_emit_list_item_added` helper produces a payload
    that validates byte-for-byte against the on-main
    :class:`ListItemAddedV1` schema. The schema's
    ``model_config = {"extra": "forbid"}`` rejects any drift in field
    names introduced by the pack; the per-field ``min_length=1`` on
    ``member_id`` / ``list_external_id`` / ``item_external_id`` /
    ``list_name`` / ``title`` / ``sync_cycle_id`` rejects empty values;
    the ``priority: Literal[0, 1, 5, 9]`` gate rejects any value other
    than the four ordinal scale slots. This is the pack-side guard
    against accidental schema-shape drift across future adapter edits;
    F-2 (MAP DON'T REGISTER) is pinned both here (pack layer) and at
    the framework layer in
    ``tests/unit/adapters/tasks/test_google_tasks.py
    ::test_poll_emit_payload_validates_against_list_item_added_v1``.
    F-7 also pinned here: Google Tasks has no native priority field,
    so the emit defaults to ``priority=5`` (medium)."""
    adapter = _make_adapter()
    event_log = _FakeEventLog()
    ctx = _make_ctx(event_log)

    google_task: dict[str, Any] = {
        "id": "gtask-pack-001",
        "title": "Pack-internal emit",
        "notes": "Pack-internal round-trip",
        "due": "2026-05-20T00:00:00.000Z",
        "status": "needsAction",
        "updated": "2026-05-18T14:00:00.000Z",
    }
    adapter._emit_list_item_added(
        ctx=ctx, tasklist_id="tasklist-primary", task=google_task,
    )

    added_calls = [
        c for c in event_log.calls if c["type"] == "list_item.added"
    ]
    assert len(added_calls) == 1
    payload = added_calls[0]["payload"]
    # extra="forbid" — extras would raise here.
    instance = ListItemAddedV1.model_validate(payload)
    assert instance.member_id == "member-pack-internal-001"
    assert instance.list_external_id == "tasklist-primary"
    assert instance.item_external_id == "gtask-pack-001"
    assert instance.list_name == "Personal tasks"
    assert instance.title == "Pack-internal emit"
    assert instance.notes == "Pack-internal round-trip"
    # F-7: no native priority on Google Tasks → medium default.
    assert instance.priority == 5
    assert instance.completed is False
    assert instance.sync_cycle_id == "corr-pack-internal-1"


def test_six_validator_checks_return_zero_errors_and_no_advisory() -> None:
    """The 11-β validator runs six checks per memo 05 §3.4
    (capability_runtime_coherence, event_schema_registration,
    projection_subscription_compatibility, base_class_conformance,
    write_capabilities_check, observation_mode_wrapping). The fresh
    canonical-build pack passes all six with zero ``error`` findings and
    NO ``advisory_pending_retrofit`` finding (the latter only attaches
    to retrofitted v1 packs that predate the framework's ABCs; this
    pack is fresh canonical-build per 11b-iv, so no retrofit is
    pending). Closes the parent draft §"Stop condition":
    ``adminme adapters validate packs/adapters/tasks/google-tasks``
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
