"""Cross-module Cat-B integration tests for the Google Tasks adapter
(prompt 11b-iv c4 deliverable 2 — second fresh canonical-build Cat-B
adapter; default-CI sibling to the framework + pack-internal suites).

Sits alongside ``test_adapter_google_calendar_supervisor.py`` (11b-iii
c4) as the second fresh Cat-B integration test file in the suite. Unlike
the Apple Reminders sibling — which is a retrofit + dual-emit on a v1
shipped pack — Google Tasks is a FRESH Cat-B adapter under the
canonical-build pattern (prompts 11-α/β/γ framework + 11a / 11b-i /
11b-iii precursors); there is no pre-retrofit baseline to preserve and
no dual-emit migration to verify. The three tests in this file pin the
contract surface the supervisor's loader path observes for a fresh
Cat-B pack:

- the supervisor's loader (file-path importlib — the same path the
  supervisor uses at runtime because ``google-tasks``'s hyphen forbids
  a ``from packs...google-tasks.adapter`` import) produces a class that
  inherits from :class:`ExternalStateMirrorAdapter` and from none of
  the other four ABCs ([PM-34] mutual-exclusion);

- the loaded class declares the asymmetric ``write_capabilities ==
  ["create_task"]`` per [UT-27]; the base-class :meth:`apply_write`
  gate raises :class:`WriteCapabilityError` for any undeclared kind
  (e.g. ``update_task`` / ``delete_task`` / ``complete_task`` — all
  out of scope per parent draft + §6) BEFORE the Google Tasks API is
  touched;

- the inbound poll's ``_poll_once`` emits ``list_item.added@v1`` on
  first observation of a Google task id with a payload that validates
  byte-for-byte against the on-main :class:`ListItemAddedV1` schema.
  Pins F-2 (MAP DON'T REGISTER) at the integration seam — the
  schema-source layer is pinned by the framework test, the pack layer
  by the pack-internal test, and this layer pins the same contract
  through the supervisor's loader path (so any future loader-side
  regression that mangles the import or the module-load registration
  is caught here). F-7 (Google Tasks has no native priority field) is
  pinned here too: the emit's ``priority == 5`` (medium).

Cat-B specifics vs the Cat-A (Gmail send) integration sibling: the
gate-rejection assertion in test (2) is the load-bearing addition —
Cat-B has TWO abstract methods + the [UT-27] gate + the [§6.20] wrap
(observation-mode contract); Cat-A has only ``send``. The fresh-pack
discriminator from the retrofit pattern is that the validator's
``base_class_conformance`` finding for this pack is NOT
``advisory_pending_retrofit`` (which only attaches to v1 packs that
predate the framework's ABCs — fresh packs like this one declare the
inheritance directly and the check passes cleanly with no advisory).

The supervisor's full lifecycle test (start / stop / poll-loop /
backoff) lives in the canonical Gmail integration file
(``test_adapter_supervisor_gmail.py``) and is exercised end-to-end
there; for Google Tasks' fresh Cat-B canonical-build, this default-CI
sibling pins the loader + ABC + gate + emit contract at the integration
seam. A future ``test_adapter_google_tasks_supervisor_live.py`` under
``requires_live_services`` will exercise the full poll-loop +
restart-with-backoff path against the real Google Tasks v1 API per the
Plaid-supervisor precedent.

``packs/adapters/tasks/google-tasks`` includes a hyphen in the final
directory segment and is therefore not a valid Python package name; the
adapter class is loaded via the file-path importlib pattern shared with
the framework test at
``tests/unit/adapters/tasks/test_google_tasks.py`` and the pack-internal
test at
``packs/adapters/tasks/google-tasks/tests/test_google_tasks_pack_internal.py``.
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.base import AdapterContext
from adminme.events.schemas.lists import ListItemAddedV1
from adminme.lib.adapters import (
    CommunicationAdapter,
    ExternalStateMirrorAdapter,
    InboundDataAdapter,
    OutboundActionAdapter,
    PersonalKnowledgeAdapter,
    WriteCapabilityError,
    WriteOperation,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOOGLE_TASKS_PACK_ROOT = (
    REPO_ROOT / "packs" / "adapters" / "tasks" / "google-tasks"
)


def _load_google_tasks_module() -> Any:
    """Load ``adapter.py`` from the pack root by file path.

    Same file-path importlib pattern the supervisor's loader uses at
    runtime — packs are not a top-level Python package under poetry's
    package config (only ``adminme`` is), and ``google-tasks``'s hyphen
    forbids a ``from packs.adapters.tasks.google-tasks.adapter``
    statement entirely. Mirrors the framework + pack-internal suite
    loaders so all three test layers exercise the same import seam."""
    spec = importlib.util.spec_from_file_location(
        "google_tasks_adapter_integration_under_test",
        GOOGLE_TASKS_PACK_ROOT / "adapter.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adapter_mod: Any = _load_google_tasks_module()
GoogleTasksAdapter: Any = _adapter_mod.GoogleTasksAdapter


_INTEGRATION_FROZEN_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


class _IntegrationEventLog:
    """Captures synchronous ``append_sync`` calls for assertion. Mirrors
    the duck-typed contract :meth:`GoogleTasksAdapter._append` reads
    via ``getattr(ctx.event_log, "append_sync", ...)``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def append_sync(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _make_integration_ctx(event_log: _IntegrationEventLog) -> AdapterContext:
    return AdapterContext(
        event_log=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-integration-1",
        now_factory=lambda: _INTEGRATION_FROZEN_NOW,
    )


class _IntegrationListRequest:
    """``tasks().list(...).execute()`` half of the fake Tasks service."""

    def __init__(
        self, pages: list[dict[str, Any]], call_log: list[dict[str, Any]],
    ) -> None:
        self._pages = pages
        self._call_log = call_log

    def execute(self) -> dict[str, Any]:
        if self._pages:
            return dict(self._pages.pop(0))
        return {"items": []}


class _IntegrationTasksResource:
    def __init__(self, service: "_IntegrationTasksService") -> None:
        self._service = service

    def list(self, **kwargs: Any) -> _IntegrationListRequest:
        self._service.list_calls.append(kwargs)
        return _IntegrationListRequest(
            self._service._list_pages, self._service.list_calls
        )

    def insert(self, **kwargs: Any) -> Any:  # pragma: no cover — no insert tests
        self._service.insert_calls.append(kwargs)
        raise AssertionError(
            "tasks().insert(...) is not exercised by these integration tests"
        )


class _IntegrationTasksService:
    """Minimal Google Tasks v1 ``service`` surface — supports
    ``service.tasks().list(...).execute()``. Insert is intentionally
    absent because the gate-rejection test (2) never reaches the impl
    and the inbound-emit test (3) does not write."""

    def __init__(self, list_pages: list[dict[str, Any]] | None = None) -> None:
        self._list_pages = list(list_pages or [])
        self.list_calls: list[dict[str, Any]] = []
        self.insert_calls: list[dict[str, Any]] = []

    def tasks(self) -> _IntegrationTasksResource:
        return _IntegrationTasksResource(self)


def _make_adapter(
    *, service: _IntegrationTasksService | None = None,
) -> Any:
    """Construct a :class:`GoogleTasksAdapter` skipping ``start()``.

    The full lifecycle (start / stop / poll loop) lives in the future
    ``test_adapter_google_tasks_supervisor_live.py`` under
    ``requires_live_services``. The three tests here only pin the
    loader + ABC + gate + emit contract at the integration seam, so the
    real Tasks v1 service is seeded directly via ``self._service``
    without going through the credentials resolver."""
    adapter = GoogleTasksAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "tasklist_id": "tasklist-primary",
            "user_email": "operator@example.test",
            "member_id": "member-integration-001",
            "list_name": "Integration tasks",
        },
    )
    if service is not None:
        adapter._service = service
    return adapter


# ===========================================================================
# 1) [PM-34] mutual-exclusion + Cat-B inheritance through the loader.
# ===========================================================================


def test_supervisor_loaded_adapter_inherits_external_state_mirror_adapter() -> None:
    """The supervisor's adapter-loading code path (file-path importlib —
    same path the supervisor uses at runtime) yields a class that
    inherits from :class:`ExternalStateMirrorAdapter` and is NOT a
    subclass of any of the other four ABCs per [PM-34]. The validator's
    ``base_class_conformance`` check observes this via AST walk against
    the same file; this integration test pins the same contract at the
    runtime-loader seam. Fresh Cat-B canonical-build adapter: unlike
    the Apple Reminders sibling (which is a retrofit), this inheritance
    was declared at authorship time per the canonical-build pattern, so
    no ``advisory_pending_retrofit`` finding attaches."""
    assert issubclass(GoogleTasksAdapter, ExternalStateMirrorAdapter)
    # [PM-34] mutual-exclusion across the 5 ABCs.
    assert not issubclass(GoogleTasksAdapter, CommunicationAdapter)
    assert not issubclass(GoogleTasksAdapter, InboundDataAdapter)
    assert not issubclass(GoogleTasksAdapter, OutboundActionAdapter)
    assert not issubclass(GoogleTasksAdapter, PersonalKnowledgeAdapter)


# ===========================================================================
# 2) [UT-27] asymmetric write_capabilities gate through the loader.
# ===========================================================================


def test_supervisor_write_capabilities_gate_through_loader() -> None:
    """The [UT-27] gate on the base-class :meth:`apply_write` is what
    makes the asymmetric ``write_capabilities`` declaration load-bearing
    rather than decorative. The shipped outbound surface is
    ``create_task`` ONLY; ``update_task`` / ``delete_task`` /
    ``complete_task`` are out of scope per parent draft + §6 (deferred
    to 11b-iv-β). The framework test pins the gate at the class-shape
    layer; this integration test pins it on the instance the supervisor
    would actually run, closing the loop between the contract
    (``write_capabilities`` ClassVar + base-class gate) and the
    supervisor's runtime invariant. The Google Tasks v1 API is NOT
    touched — the gate rejects the operation BEFORE the impl is
    dispatched."""
    adapter = _make_adapter()
    assert GoogleTasksAdapter.write_capabilities == ["create_task"]
    assert hasattr(adapter, "apply_write")
    assert callable(adapter.apply_write)

    op = WriteOperation(
        kind="update_task",
        target_external_id="gtask-1",
        payload={},
    )
    with pytest.raises(WriteCapabilityError):
        asyncio.run(adapter.apply_write(op))

    # The defense-in-depth check inside ``_apply_write_impl`` also
    # rejects ``delete_task`` directly (the gate would catch it first
    # in production; this asserts the inner guard is wired correctly).
    delete_op = WriteOperation(
        kind="delete_task",
        target_external_id="gtask-2",
        payload={},
    )
    with pytest.raises(WriteCapabilityError):
        asyncio.run(adapter.apply_write(delete_op))


# ===========================================================================
# 3) Inbound poll emits ``list_item.added@v1`` via the loader path.
# ===========================================================================


def test_supervisor_inbound_poll_emits_list_item_added() -> None:
    """The supervisor-loaded ``GoogleTasksAdapter`` (loaded via the
    file-path importlib pattern the supervisor uses at runtime) emits
    ``list_item.added@v1`` on first observation of a Google task id,
    with a payload that validates byte-for-byte against the on-main
    :class:`ListItemAddedV1` schema. F-2 (MAP DON'T REGISTER) pinned at
    the integration seam: any future loader-side regression that
    mangles the import surface or the module-load registration would
    fail here first. ``extra="forbid"`` on the schema means any drift
    introducing a new field would fail validation; the ``min_length=1``
    on ``member_id`` / ``list_external_id`` / ``item_external_id`` /
    ``list_name`` / ``title`` / ``sync_cycle_id`` would reject empties.
    F-7 also pinned here: Google Tasks has no native priority field, so
    the emit's ``priority`` is always ``5`` (medium)."""
    google_task: dict[str, Any] = {
        "id": "gtask-integration-001",
        "title": "Integration emit",
        "notes": "Integration round-trip",
        "due": "2026-05-20T00:00:00.000Z",
        "status": "needsAction",
        "updated": "2026-05-18T14:00:00.000Z",
    }
    service = _IntegrationTasksService(
        list_pages=[{"items": [google_task]}]
    )
    adapter = _make_adapter(service=service)
    event_log = _IntegrationEventLog()
    ctx = _make_integration_ctx(event_log)
    adapter._ctx = ctx

    emitted = asyncio.run(adapter._poll_once(ctx=ctx))

    assert emitted == 1
    added_calls = [
        c for c in event_log.calls if c["type"] == "list_item.added"
    ]
    assert len(added_calls) == 1
    payload = added_calls[0]["payload"]
    instance = ListItemAddedV1.model_validate(payload)
    assert instance.member_id == "member-integration-001"
    assert instance.list_external_id == "tasklist-primary"
    assert instance.item_external_id == "gtask-integration-001"
    assert instance.list_name == "Integration tasks"
    assert instance.title == "Integration emit"
    assert instance.notes == "Integration round-trip"
    # F-7: no native priority on Google Tasks → medium default.
    assert instance.priority == 5
    assert instance.completed is False
    assert instance.sync_cycle_id == "corr-integration-1"
    # Re-poll on the same id is a no-op per the inbound dedupe gate
    # (F-3 of c2): _seen_inbound populated → no second emit.
    service._list_pages = [{"items": [google_task]}]
    emitted_second = asyncio.run(adapter._poll_once(ctx=ctx))
    assert emitted_second == 0
    added_calls_after = [
        c for c in event_log.calls if c["type"] == "list_item.added"
    ]
    assert len(added_calls_after) == 1
