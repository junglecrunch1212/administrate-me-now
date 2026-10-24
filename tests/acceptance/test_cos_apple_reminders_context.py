"""Selected EventKit acquisition -> current private context -> retained CoS work."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.adapters.base import AdapterContext
from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import create_application
from tests.acceptance.test_cos_identity_delegation_kernel import _settings
from tests.acceptance.test_cos_service_private_work import _read
from tests.acceptance.test_cos_service_working_context import (
    _context,
    _mail_policy,
    _run_context,
    _run_note,
)
from tests.unit.bootstrap.test_cos_apple_reminders import (
    Reader,
    _adapter,
    _item,
    _start,
)


@pytest.fixture
def apple(tmp_path: Path):
    instance = tmp_path / "instance"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        assert client.portal is not None
        reader = Reader()
        adapter = _adapter(tmp_path, reader)
        ctx = AdapterContext(
            event_sink=runtime.events,
            config={},
            correlation_id_factory=lambda: "apple-planning",
            now_factory=lambda: datetime(2032, 1, 1, tzinfo=timezone.utc),
            cursor_store=runtime.adapter_cursor_store,
            source_events=runtime.event_log,
        )

        async def start() -> None:
            await _start(adapter, ctx, runtime.event_log)

        try:
            client.portal.call(start)
            yield client, reader, adapter, ctx
        finally:
            client.portal.call(adapter.stop)


def _poll(apple: Any) -> dict[str, str]:
    """Poll the actual loaded adapter, then wait for its persisted list facts."""
    client, _, adapter, ctx = apple
    runtime = client.app.state.runtime

    async def poll() -> dict[str, str]:
        await adapter._poll_once(ctx=ctx)
        facts = [
            row
            async for row in runtime.event_log.read_since()
            if row["type"].startswith(("list.", "list_item."))
        ]
        assert facts
        await runtime.wait_for_projection("lists", facts[-1]["event_id"])
        return {row["type"]: row["event_id"] for row in facts}

    return dict(client.portal.call(poll))


def test_selected_reminders_become_private_facts_and_source_backed_follow_up(
    apple: Any,
) -> None:
    client, _, adapter, _ = apple
    runtime = client.app.state.runtime
    emitted = _poll(apple)
    sources = [emitted["list.created"], emitted["list_item.added"]]
    result = _run_context(
        client, "apple-follow-up", "read-apple-reminders", source_event_ids=sources
    )
    assert result.status_code == 200, result.text
    facts = result.json()["facts"]
    assert facts[0]["fact"]["list_name"] == "Fixture list"
    assert facts[1]["fact"]["title"] == "Fixture reminder"
    assert "notes" not in facts[1]["fact"]
    for fact in facts:
        assert fact["authority"]["authority_kind"] == "service_self"
        assert fact["authority"]["authority_grant_id"] is None
        assert fact["authority"]["source_adapter"] == "reminders-cos-apple-eventkit"
        assert fact["authority"]["source_audience"] == []
        assert not fact["provider_body_included"]
        assert not fact["outside_disclosure_authorized"]
    note = _run_note(
        client,
        "apple-follow-up",
        "retain-apple-reminders",
        title="Prepare the selected reminder follow-up",
    )
    assert note.status_code == 201, note.text
    work = note.json()["work"]
    assert {row["source_event_id"] for row in work["source_authorities"]} == set(
        sources
    )
    assert not note.json()["timer_registered"]
    assert note.json()["provider_effect_count"] == 0
    assert adapter.write_capabilities == []
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
    for projection in ("lists", "tasks"):
        client.portal.call(runtime.projections.rebuild, projection)
    assert (
        _context(client, "apple-replayed-context", source_event_ids=sources).status_code
        == 200
    )
    assert (
        _read(client, "apple-replayed-note", task_id=note.json()["task_id"])["work"][0]
        == work
    )
    assert runtime.fixture_adapter.delivered == []


def test_polled_reminder_updates_and_tombstones_control_current_context(
    apple: Any,
) -> None:
    client, reader, _, ctx = apple
    original = _poll(apple)["list_item.added"]
    reader.snapshot = replace(
        reader.snapshot,
        items=(_item(completed=True, completion_date=ctx.now_factory()),),
    )
    completed = _poll(apple)["list_item.completed"]
    assert (
        _context(client, "apple-old-item", source_event_ids=[original]).status_code
        == 404
    )
    assert _context(
        client, "apple-completed-item", source_event_ids=[completed]
    ).json()["facts"][0]["fact"]["completed"]
    reader.snapshot = replace(reader.snapshot, items=(_item(title="Reopened item"),))
    reopened = _poll(apple)["list_item.uncompleted"]
    assert not _context(
        client, "apple-reopened-item", source_event_ids=[reopened]
    ).json()["facts"][0]["fact"]["completed"]
    reader.snapshot = replace(reader.snapshot, items=())
    assert "list_item.deleted" in _poll(apple)
    assert (
        _context(client, "apple-deleted-item", source_event_ids=[reopened]).status_code
        == 404
    )
    reader.snapshot = replace(reader.snapshot, items=(_item(title="Restored item"),))
    restored = _poll(apple)["list_item.added"]
    assert (
        _context(client, "apple-restored-item", source_event_ids=[restored]).status_code
        == 200
    )
    reader.snapshot = replace(reader.snapshot, list_name=None, items=())
    assert "list.deleted" in _poll(apple)
    assert (
        _context(client, "apple-deleted-list", source_event_ids=[restored]).status_code
        == 404
    )
    reader.snapshot = replace(reader.snapshot, list_name="Restored list")
    _poll(apple)
    runtime = client.app.state.runtime
    client.portal.call(runtime.projections.rebuild, "lists")
    assert (
        _context(
            client, "apple-no-resurrection", source_event_ids=[restored]
        ).status_code
        == 404
    )
    reader.snapshot = replace(reader.snapshot, items=(_item(title="Fresh item"),))
    fresh = _poll(apple)["list_item.added"]
    assert (
        _context(client, "apple-fresh-item", source_event_ids=[fresh]).status_code
        == 200
    )
    assert runtime.fixture_adapter.delivered == []


@pytest.mark.parametrize(
    "mode", ["none", "metadata_only", "content_on_explicit_authorized_demand"]
)
def test_reminder_policy_restricts_context_and_retained_private_work(
    apple: Any, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    client, _, _, _ = apple
    source = _poll(apple)["list_item.added"]
    policy = _mail_policy(client, source, monkeypatch, "typed_facts_safe_snippet")
    note = _run_note(
        client, "apple-policy-run", "apple-policy-note", source_event_ids=[source]
    )
    assert note.status_code == 201, note.text
    assert note.json()["work"]["source_authorities"][0]["read_mode"] == "typed_facts"
    policy["context_mode"] = "typed_facts"
    assert _read(client, "apple-typed-note", task_id=note.json()["task_id"])["work"]
    policy["context_mode"] = mode
    assert _read(client, "apple-restricted-note")["work"] == []
    context = _context(client, "apple-policy-context", source_event_ids=[source])
    if mode == "metadata_only":
        assert context.status_code == 200, context.text
        assert "title" not in context.json()["facts"][0]["fact"]
    else:
        assert context.status_code == 404
    assert client.app.state.runtime.fixture_adapter.delivered == []


@pytest.mark.parametrize(
    "mutation", ["adapter", "owner", "account", "tenant", "member"]
)
def test_foreign_reminder_facts_cannot_replace_current_authorized_source(
    apple: Any, mutation: str
) -> None:
    client, _, _, _ = apple
    runtime = client.app.state.runtime
    source = _poll(apple)["list_item.added"]
    original = client.portal.call(runtime.event_log.get, source)
    payload = dict(original["payload"])
    owner = original["owner_scope"]
    account = original["source_account_id"]
    tenant = original["tenant_id"]
    adapter = original["source_adapter"]
    if mutation == "adapter":
        adapter = "reminders-apple-eventkit"
    elif mutation == "owner":
        owner = "service-private:other-service"
    elif mutation == "account":
        account = "other-device-source"
    elif mutation == "tenant":
        tenant = "other-tenant"
    else:
        payload.update(
            member_id="other-member", service_principal_id=None, workspace="personal"
        )
    event = EventEnvelope(
        event_at_ms=2,
        tenant_id=tenant,
        type="list_item.updated",
        schema_version=4,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter=adapter,
        source_account_id=account,
        owner_scope=owner,
        visibility_scope=owner,
        sensitivity="normal",
        payload=payload,
    )
    wrong = client.portal.call(runtime.events.write, event)
    client.portal.call(runtime.wait_for_projection, "lists", wrong)
    assert (
        _context(
            client, f"apple-invalid-{mutation}", source_event_ids=[wrong]
        ).status_code
        == 404
    )
    assert (
        _context(
            client, f"apple-valid-{mutation}", source_event_ids=[source]
        ).status_code
        == 200
    )
