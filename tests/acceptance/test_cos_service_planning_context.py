"""Real provider emission -> private current facts -> source-backed CoS work."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from adminme.adapters.base import AdapterContext
from adminme.adapters.loader import load_builtin_adapters
from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import create_application
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from tests.acceptance.test_cos_apple_reminders_context import _poll
from tests.acceptance.test_cos_apple_reminders_context import apple as apple
from tests.acceptance.test_cos_identity_delegation_kernel import (
    A,
    B,
    B_HEADERS,
    _grant_private,
    _settings,
)
from tests.acceptance.test_cos_service_private_work import _read
from tests.acceptance.test_cos_service_working_context import (
    _context,
    _calendar,
    _mail_policy,
    _observed_source,
    _own_mail,
    _run_context,
    _run_note,
)
from tests.integration.test_service_owned_google_sources import _config
from tests.unit.bootstrap.test_cos_apple_reminders import _item

ROOT = Path(__file__).resolve().parents[2]


async def _planning_source(
    runtime: Any,
    kind: str,
    *,
    container: str = "service-planning-list",
    native_id: str = "service-native-item",
    account: str = "cos-service@example.invalid",
    revision: str = "1",
) -> str:
    """Use actual built-in producer methods, with no client or credential load."""
    config = _config()
    for row in config["adapters"]:
        row["config"]["provider_account_id"] = account
        row["config"]["user_email"] = account
    adapters = load_builtin_adapters(
        config,
        repository_root=ROOT,
        member_roles={"member-fixture-a": "principal", "member-fixture-b": "principal"},
        household_mapping={},
        service_principal_ids={COS_SERVICE_PRINCIPAL_ID},
        validate_bindings=True,
    )
    by_name = {adapter.name: adapter for adapter in adapters}
    calendar = by_name["calendaring-google-calendar"]
    tasks = by_name["tasks-google-tasks"]
    assert calendar.write_capabilities == tasks.write_capabilities == []
    ctx = AdapterContext(
        event_sink=runtime.events,
        config={},
        correlation_id_factory=lambda: f"planning-{revision}",
        now_factory=lambda: datetime(2032, 1, 1, tzinfo=timezone.utc),
    )
    if kind in {"calendar.event_added", "calendar.event_updated"}:
        await calendar._emit_calendar_snapshot(
            ctx=ctx,
            event_type=kind,
            calendar_id=container,
            calendar_name="Service calendar",
            event={
                "id": native_id,
                "summary": f"Prepare briefing {revision}",
                "description": "Do not expose arbitrary calendar HTML <b>content</b>",
                "status": "confirmed",
                "start": {"dateTime": "2032-01-02T12:00:00Z"},
                "end": {"dateTime": "2032-01-02T13:00:00Z"},
                "updated": "2032-01-01T12:00:00Z",
                "etag": f"revision-{revision}",
            },
        )
    elif kind == "calendar.event_deleted":
        await calendar._append(
            ctx=ctx,
            event_type=kind,
            schema_version=3,
            payload={
                **calendar._owner_payload(),
                "provider": "google_calendar",
                "provider_account_id": account,
                "calendar_external_id": container,
                "external_event_id": native_id,
                "provider_revision": f"deleted-{revision}",
                "deleted_at": "2032-01-01T12:00:00Z",
            },
        )
    elif kind in {"list.created", "list.updated"}:
        await tasks._emit_list_change(
            ctx=ctx,
            event_type=kind,
            tasklist={"id": container, "title": f"Private list {revision}"},
            fingerprint=f"list:{revision}",
        )
    elif kind == "list.deleted":
        await tasks._emit_list_deleted(ctx=ctx, tasklist_id=container)
    elif kind == "list_item.deleted":
        await tasks._emit_deleted(
            ctx=ctx,
            tasklist_id=container,
            task={"id": native_id, "updated": "2032-01-01T12:00:00Z"},
            fingerprint=f"deleted:{revision}",
        )
    else:
        await tasks._emit_item(
            ctx=ctx,
            event_type=kind,
            tasklist={"id": container, "title": "Private list"},
            task={
                "id": native_id,
                "title": f"Read briefing {revision}",
                "notes": "Private task note body",
                "position": "000000000000000001",
                "status": "completed"
                if kind == "list_item.completed"
                else "needsAction",
                "completed": "2032-01-01T12:00:00Z"
                if kind == "list_item.completed"
                else None,
                "due": "2032-01-03T00:00:00Z",
                "updated": "2032-01-01T12:00:00Z",
                "etag": f"revision-{revision}",
            },
        )
    event_id = await runtime.event_log.latest_event_id(types=[kind])
    assert event_id is not None
    await runtime.wait_for_projection(
        "calendars" if kind.startswith("calendar.") else "lists", event_id
    )
    return str(event_id)


def _emit(client: TestClient, kind: str, **values: Any) -> str:
    async def emit() -> str:
        return await _planning_source(client.app.state.runtime, kind, **values)

    return str(client.portal.call(emit))


def _selected(
    client: TestClient, source_kind: str, **values: Any
) -> list[dict[str, Any]]:
    result = _context(
        client,
        f"selected-{source_kind}-{uuid4().hex}",
        source_kind=source_kind,
        **values,
    )
    assert result.status_code == 200, result.text
    assert result.json()["source_kind"] == source_kind
    return result.json()["facts"]


def test_source_selection_discovers_planning_without_reading_mail(
    apple: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _, _ = apple
    runtime = client.app.state.runtime
    reminder = _poll(apple)["list_item.added"]
    mail = _own_mail(client, "mail-before-planning")
    calendar = _emit(client, "calendar.event_added")
    _emit(client, "list.created")
    item = _emit(client, "list_item.added")
    _own_mail(client, "mail-after-planning")
    original_read = runtime.event_log.read_since
    scans: list[tuple[set[str], list[str]]] = []

    async def observe_scan(*args: Any, **kwargs: Any):
        selected_types = set(kwargs.get("types") or [])
        events: list[str] = []
        if kwargs.get("owner_scope") is not None:
            scans.append((selected_types, events))
        async for event in original_read(*args, **kwargs):
            events.append(event["type"])
            yield event

    monkeypatch.setattr(runtime.event_log, "read_since", observe_scan)
    for source_kind, event_id in (
        ("calendar", calendar),
        ("google_tasks", item),
        ("apple_reminders", reminder),
    ):
        result = _run_context(
            client, "selected-brief", source_kind, source_kind=source_kind, limit=1
        )
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["source_kind"] == source_kind
        assert [fact["event_id"] for fact in body["facts"]] == [event_id]
        assert body["next_cursor"] is None and not body["scan_truncated"]
        assert body["provider_effect_count"] == 0
    assert scans and all(
        types and not any(kind.startswith("messaging.") for kind in types | set(read))
        for types, read in scans
    )
    brief = _run_note(
        client,
        "selected-brief",
        "selected-planning-brief",
        description="Prepare the observed briefing appointment, task and reminder; bounded coverage.",
    )
    assert brief.status_code == 201, brief.text
    work = brief.json()["work"]
    assert {row["source_event_id"] for row in work["source_authorities"]} == {
        calendar,
        item,
        reminder,
    }
    assert _read(client, "selected-brief-readback", task_id=work["task_id"])[
        "work"
    ] == [work]
    default = _context(
        client, "original-mixed-scan", after_event_id=reminder, limit=1
    ).json()
    assert "source_kind" not in default and default["facts"][0]["event_id"] == mail
    for values in (
        {"source_kind": "arbitrary-provider"},
        {"source_kind": "calendar", "source_event_ids": [mail]},
        {"source_kind": "calendar", "source_event_ids": [calendar]},
    ):
        assert _context(client, "invalid-selection", **values).status_code == 422
    assert runtime.fixture_adapter.delivered == []


def test_selected_reminders_keep_current_completion_and_parent_tombstones(
    apple: Any,
) -> None:
    client, reader, _, ctx = apple
    _poll(apple)
    reader.snapshot = replace(
        reader.snapshot,
        items=(_item(completed=True, completion_date=ctx.now_factory()),),
    )
    completed = _poll(apple)["list_item.completed"]
    facts = _selected(client, "apple_reminders", limit=1)
    assert [fact["event_id"] for fact in facts] == [completed]
    assert facts[0]["fact"]["completed"] is True
    reader.snapshot = replace(reader.snapshot, items=(_item(title="Reopened item"),))
    reopened = _poll(apple)["list_item.uncompleted"]
    facts = _selected(client, "apple_reminders", limit=1)
    assert [fact["event_id"] for fact in facts] == [reopened]
    assert facts[0]["fact"]["completed"] is False
    reader.snapshot = replace(reader.snapshot, items=())
    _poll(apple)
    assert _selected(client, "apple_reminders") == []
    reader.snapshot = replace(reader.snapshot, items=(_item(title="Restored item"),))
    restored = _poll(apple)["list_item.added"]
    assert [fact["event_id"] for fact in _selected(client, "apple_reminders")] == [
        restored
    ]
    reader.snapshot = replace(reader.snapshot, list_name=None, items=())
    _poll(apple)
    reader.snapshot = replace(reader.snapshot, list_name="Restored list")
    _poll(apple)
    client.portal.call(client.app.state.runtime.projections.rebuild, "lists")
    assert _selected(client, "apple_reminders") == []


def test_selected_calendars_page_across_authorized_scopes_and_honor_revocation(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        _own_mail(client, "mail-before-calendars")
        first = _observed_source(
            client, A, "calendar.event_added", _calendar(A), version=2
        )
        second = _observed_source(
            client, B, "calendar.event_added", _calendar(B), version=2
        )
        own = _emit(client, "calendar.event_added")
        assert [fact["event_id"] for fact in _selected(client, "calendar")] == [own]
        _grant_private(client, A, "selected-calendar-a")
        grant_b = _grant_private(client, B, "selected-calendar-b")
        found: list[str] = []
        cursor = None
        for page in range(3):
            result = _context(
                client,
                f"selected-calendar-page-{page}",
                source_kind="calendar",
                after_event_id=cursor,
                limit=1,
            )
            assert result.status_code == 200, result.text
            body = result.json()
            found.extend(fact["event_id"] for fact in body["facts"])
            cursor = body["next_cursor"]
            assert bool(cursor) is (page < 2)
        assert found == [first, second, own]
        revoked = client.request(
            "DELETE",
            f"/api/member/v1/cos/delegations/{grant_b}",
            headers=B_HEADERS,
            json={
                "request_id": "selected-calendar-revoke",
                "reason": "fixture revocation",
            },
        )
        assert revoked.status_code == 200, revoked.text
        assert [fact["event_id"] for fact in _selected(client, "calendar")] == [
            first,
            own,
        ]
        assert (
            _context(
                client,
                "revoked-selected-cursor",
                source_kind="calendar",
                after_event_id=second,
            ).status_code
            == 404
        )


@pytest.mark.parametrize("source_kind", ["google_tasks", "apple_reminders"])
def test_selected_items_do_not_widen_to_delegated_member_observations(
    apple: Any,
    source_kind: str,
) -> None:
    client, _, _, _ = apple
    runtime = client.app.state.runtime
    source = (
        _emit(client, "list_item.added")
        if source_kind == "google_tasks"
        else _poll(apple)["list_item.added"]
    )
    original = client.portal.call(runtime.event_log.get, source)
    payload = dict(original["payload"])
    payload.update(member_id=A, service_principal_id=None, workspace="personal")
    member = client.portal.call(
        runtime.events.write,
        EventEnvelope(
            event_at_ms=2,
            tenant_id=original["tenant_id"],
            type="list_item.updated",
            schema_version=4,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter=original["source_adapter"],
            source_account_id=original["source_account_id"],
            owner_scope=f"private:{A}",
            visibility_scope=f"private:{A}",
            sensitivity="normal",
            payload=payload,
        ),
    )
    client.portal.call(runtime.wait_for_projection, "lists", member)
    _grant_private(client, A, f"selected-item-grant-{source_kind}")
    assert [fact["event_id"] for fact in _selected(client, source_kind)] == [source]
    assert (
        _context(
            client, "member-item-not-current", source_event_ids=[member]
        ).status_code
        == 404
    )


def test_own_calendar_and_tasks_are_usable_current_facts_and_private_follow_up(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        calendar = _emit(client, "calendar.event_added", container="exact-calendar")
        tasklist = _emit(client, "list.created", container="exact-list")
        item = _emit(client, "list_item.added", container="exact-list")
        result = _run_context(
            client,
            "planning-run",
            "read-own-planning",
            source_event_ids=[calendar, tasklist, item],
        )
        assert result.status_code == 200, result.text
        facts = result.json()["facts"]
        assert facts[0]["fact"]["start"] == "2032-01-02T12:00:00Z"
        assert facts[1]["fact"]["list_name"] == "Private list 1"
        assert facts[2]["fact"]["title"] == "Read briefing 1"
        assert facts[2]["fact"]["due"] == {
            "year": 2032,
            "month": 1,
            "day": 3,
            "all_day": True,
            "floating": True,
        }
        assert facts[2]["fact"]["provider_position"] == "000000000000000001"
        assert all(
            f["authority"]["authority_kind"] == "service_self"
            and f["authority"]["authority_grant_id"] is None
            for f in facts
        )
        assert all(
            not f["provider_body_included"] and not f["outside_disclosure_authorized"]
            for f in facts
        )
        assert "description" not in facts[0]["fact"] and "notes" not in facts[2]["fact"]
        note = _run_note(
            client,
            "planning-run",
            "planning-follow-up",
            title="Prepare briefing before the meeting",
        )
        assert note.status_code == 201, note.text
        task_id = note.json()["task_id"]
        work = note.json()["work"]
        assert {s["source_event_id"] for s in work["source_authorities"]} == {
            calendar,
            tasklist,
            item,
        }
        assert (
            not note.json()["timer_registered"]
            and note.json()["provider_effect_count"] == 0
        )
        # Provider mirrors do not create member calendar rows, member list rows,
        # or extra canonical tasks. The note is the one explicit private task.
        assert (
            runtime.projections.connection("calendars")
            .execute("SELECT COUNT(*) FROM calendar_events")
            .fetchone()[0]
            == 0
        )
        assert (
            runtime.projections.connection("lists")
            .execute("SELECT COUNT(*) FROM list_items")
            .fetchone()[0]
            == 0
        )
        assert (
            runtime.projections.connection("tasks")
            .execute("SELECT COUNT(*) FROM tasks")
            .fetchone()[0]
            == 1
        )
        for projection in ("calendars", "lists", "tasks"):
            client.portal.call(runtime.projections.rebuild, projection)
        assert (
            _context(
                client, "planning-replay", source_event_ids=[calendar, tasklist, item]
            ).status_code
            == 200
        )
        assert _read(client, "planning-note-replay", task_id=task_id)["work"][0] == work
        assert runtime.fixture_adapter.delivered == []
    with TestClient(create_application(settings)) as client:
        assert (
            _context(
                client, "planning-restart", source_event_ids=[calendar, tasklist, item]
            ).status_code
            == 200
        )
        assert (
            _read(client, "planning-note-restart", task_id=task_id)["work"][0] == work
        )


def test_planning_currentness_updates_completion_and_tombstones_are_exact(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        first = _emit(client, "calendar.event_added", container="calendar-a")
        other_calendar = _emit(client, "calendar.event_added", container="calendar-b")
        other_account = _emit(
            client,
            "calendar.event_added",
            container="calendar-a",
            account="other-service@example.invalid",
        )
        changed = _emit(
            client, "calendar.event_updated", container="calendar-a", revision="2"
        )
        assert {fact["event_id"] for fact in _selected(client, "calendar")} == {
            changed,
            other_calendar,
            other_account,
        }
        assert (
            _context(client, "old-calendar", source_event_ids=[first]).status_code
            == 404
        )
        assert (
            _context(
                client,
                "new-calendar",
                source_event_ids=[changed, other_calendar, other_account],
            ).status_code
            == 200
        )
        _emit(client, "calendar.event_deleted", container="calendar-a")
        assert {fact["event_id"] for fact in _selected(client, "calendar")} == {
            other_calendar,
            other_account,
        }
        assert (
            _context(client, "deleted-calendar", source_event_ids=[changed]).status_code
            == 404
        )
        assert (
            _context(
                client,
                "other-calendar-copies",
                source_event_ids=[other_calendar, other_account],
            ).status_code
            == 200
        )
        assert (
            _context(client, "old-calendar-cursor", after_event_id=first).status_code
            == 200
        )
        _emit(client, "list.created")
        other_list = _emit(client, "list_item.added", container="another-exact-list")
        other_task_account = _emit(
            client, "list_item.added", account="other-tasks@example.invalid"
        )
        original = _emit(client, "list_item.added")
        completed = _emit(client, "list_item.completed", revision="2")
        selected = _selected(client, "google_tasks")
        assert {fact["event_id"] for fact in selected} == {
            other_list,
            other_task_account,
            completed,
        }
        assert (
            next(fact for fact in selected if fact["event_id"] == completed)["fact"][
                "completed"
            ]
            is True
        )
        assert (
            _context(client, "old-item", source_event_ids=[original]).status_code == 404
        )
        assert (
            _context(client, "completed-item", source_event_ids=[completed]).json()[
                "facts"
            ][0]["fact"]["completed"]
            is True
        )
        reopened = _emit(client, "list_item.uncompleted", revision="3")
        selected = _selected(client, "google_tasks")
        assert {fact["event_id"] for fact in selected} == {
            other_list,
            other_task_account,
            reopened,
        }
        assert (
            next(fact for fact in selected if fact["event_id"] == reopened)["fact"][
                "completed"
            ]
            is False
        )
        assert (
            _context(client, "reopened-item", source_event_ids=[reopened]).json()[
                "facts"
            ][0]["fact"]["completed"]
            is False
        )
        _emit(client, "list_item.deleted", revision="4")
        assert {fact["event_id"] for fact in _selected(client, "google_tasks")} == {
            other_list,
            other_task_account,
        }
        assert (
            _context(client, "deleted-item", source_event_ids=[reopened]).status_code
            == 404
        )
        restored = _emit(client, "list_item.added", revision="5")
        assert (
            _context(client, "restored-item", source_event_ids=[restored]).status_code
            == 200
        )
        _emit(client, "list.deleted")
        assert (
            _context(
                client,
                "other-task-copies",
                source_event_ids=[other_list, other_task_account],
            ).status_code
            == 200
        )
        assert (
            _context(
                client, "parent-list-deleted", source_event_ids=[restored]
            ).status_code
            == 404
        )
        _emit(client, "list.created", revision="6")
        assert {fact["event_id"] for fact in _selected(client, "google_tasks")} == {
            other_list,
            other_task_account,
        }
        assert (
            _context(
                client, "no-stale-child-resurrection", source_event_ids=[restored]
            ).status_code
            == 404
        )
        fresh = _emit(client, "list_item.added", revision="7")
        assert (
            _context(client, "fresh-child", source_event_ids=[fresh]).status_code == 200
        )
        client.portal.call(client.app.state.runtime.projections.rebuild, "lists")
        assert {fact["event_id"] for fact in _selected(client, "google_tasks")} == {
            other_list,
            other_task_account,
            fresh,
        }
        assert (
            _context(
                client, "replayed-child-tombstones", source_event_ids=[restored]
            ).status_code
            == 404
        )
        assert (
            _context(
                client, "replayed-fresh-child", source_event_ids=[fresh]
            ).status_code
            == 200
        )
        assert client.app.state.runtime.fixture_adapter.delivered == []


@pytest.mark.parametrize("kind", ["calendar.event_added", "list_item.added"])
@pytest.mark.parametrize(
    "mode", ["none", "metadata_only", "content_on_explicit_authorized_demand"]
)
def test_planning_policy_and_retained_note_restrictions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, mode: str
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        source = _emit(client, kind)
        policy = _mail_policy(client, source, monkeypatch, "typed_facts_safe_snippet")
        note = _run_note(
            client,
            "policy-planning-run",
            "retain-planning-fact",
            source_event_ids=[source],
        )
        assert note.status_code == 201, note.text
        assert (
            note.json()["work"]["source_authorities"][0]["read_mode"] == "typed_facts"
        )
        # No body was consumed; moving snippet mode to typed facts need not
        # hide a purely typed operational note.
        policy["context_mode"] = "typed_facts"
        assert _read(client, "planning-typed-note", task_id=note.json()["task_id"])[
            "work"
        ]
        policy["context_mode"] = mode
        assert _read(client, "planning-restricted-note")["work"] == []
        context = _context(client, "planning-mode-context", source_event_ids=[source])
        selected = _selected(
            client,
            "calendar" if kind.startswith("calendar.") else "google_tasks",
        )
        if mode == "metadata_only":
            assert context.status_code == 200, context.text
            fact = context.json()["facts"][0]["fact"]
            assert [row["fact"] for row in selected] == [fact]
            assert (
                "summary" not in fact
                and "title" not in fact
                and "list_name" not in fact
            )
        else:
            assert context.status_code == 404
            assert selected == []
        assert client.app.state.runtime.fixture_adapter.delivered == []


@pytest.mark.parametrize(
    "mutation", ["owner", "account", "workspace", "tenant", "member", "provider"]
)
def test_service_planning_rejects_mismatched_authority_without_displacing_valid_current_fact(
    tmp_path: Path, mutation: str
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        runtime = client.app.state.runtime
        source = _emit(client, "calendar.event_added")
        original = client.portal.call(runtime.event_log.get, source)
        payload = dict(original["payload"])
        owner = original["owner_scope"]
        account = original["source_account_id"]
        tenant = original["tenant_id"]
        if mutation == "owner":
            owner = "private:other-member"
        elif mutation == "account":
            account = "wrong-account@example.invalid"
        elif mutation == "workspace":
            # A schema-valid member source must not impersonate service work.
            payload.update(
                member_id="other-member", service_principal_id=None, workspace="work"
            )
        elif mutation == "tenant":
            tenant = "other-tenant"
        elif mutation == "member":
            payload.update(
                member_id="other-member",
                service_principal_id=None,
                workspace="personal",
            )
        else:
            payload["provider"] = "apple_calendar"
        event = EventEnvelope(
            event_at_ms=2,
            tenant_id=tenant,
            type="calendar.event_updated",
            schema_version=3,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter=original["source_adapter"],
            source_account_id=account,
            owner_scope=owner,
            visibility_scope=owner,
            sensitivity="normal",
            payload=payload,
        )
        wrong = client.portal.call(runtime.events.write, event)
        client.portal.call(runtime.wait_for_projection, "calendars", wrong)
        assert (
            _context(
                client, f"planning-invalid-{mutation}", source_event_ids=[wrong]
            ).status_code
            == 404
        )
        assert (
            _context(
                client, f"planning-valid-{mutation}", source_event_ids=[source]
            ).status_code
            == 200
        )
        assert [fact["event_id"] for fact in _selected(client, "calendar")] == [source]
