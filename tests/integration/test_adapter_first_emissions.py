"""Every shipped central adapter's first event crosses the real writer."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from adminme.adapters.base import AdapterContext
from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.lib.instance_config import load_instance_config

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_KEY = b"a" * 32


class _WriterBridgeSink:
    """Bridge-shaped sink backed by the real central event writer."""

    def __init__(self, writer: EventWriter) -> None:
        self._writer = writer

    async def emit(self, envelope: Any) -> Any:
        event_id = await self._writer.write(envelope)
        return SimpleNamespace(ok=True, event_id=event_id, error=None)


ADAPTERS = (
    (
        "automation/home-assistant",
        "HomeAssistantAdapter",
        True,
        {"op_ref": "op://test/home-assistant/token"},
    ),
    ("calendaring/apple-calendar", "AppleCalendarAdapter", True, {}),
    (
        "calendaring/google-calendar",
        "GoogleCalendarAdapter",
        True,
        {"op_ref": "op://test/google-calendar/key"},
    ),
    (
        "financial/plaid",
        "PlaidAdapter",
        False,
        {"token_storage_ref": "op://test/plaid/token"},
    ),
    (
        "messaging/gmail",
        "GmailAdapter",
        True,
        {"op_ref": "op://test/gmail/key"},
    ),
    ("reminders/apple-eventkit", "AppleEventKitAdapter", True, {}),
    (
        "tasks/google-tasks",
        "GoogleTasksAdapter",
        True,
        {"op_ref": "op://test/google-tasks/key"},
    ),
)


def _load_adapter(path_fragment: str, class_name: str, config: dict[str, Any]) -> Any:
    path = REPO_ROOT / "packs" / "adapters" / path_fragment / "adapter.py"
    module_name = "gate2_first_emit_" + path_fragment.replace("/", "_").replace(
        "-", "_"
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    adapter_class = getattr(module, class_name)
    if class_name == "PlaidAdapter":
        return adapter_class()
    return adapter_class(config=config)


@pytest.mark.parametrize(
    "path_fragment,class_name,constructor_uses_config,emit_kwargs", ADAPTERS
)
async def test_adapter_first_emission_uses_real_event_writer(
    tmp_path: Path,
    path_fragment: str,
    class_name: str,
    constructor_uses_config: bool,
    emit_kwargs: dict[str, Any],
) -> None:
    del constructor_uses_config
    instance_dir = tmp_path / class_name
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    await bus.start()
    writer = EventWriter(log, bus)
    bridge_sink = _WriterBridgeSink(writer)
    member_apple = class_name in {"AppleCalendarAdapter", "AppleEventKitAdapter"}
    source_account_id = (
        f"provider-account:{'a' * 32}" if member_apple else f"account-{class_name}"
    )
    adapter_config = {
        "member_id": "member-adapter-test",
        "source_account_id": source_account_id,
        "provider_account_id": source_account_id,
        "_bridge_enrollment_id": "enrollment-adapter-first-emission",
        "_bridge_configuration_sha256": "b" * 64,
    }
    adapter = _load_adapter(path_fragment, class_name, adapter_config)
    ctx = AdapterContext(
        event_sink=writer,
        config={
            **adapter_config,
            "deployment_target": "bridge",
            "_bridge_member_id": "member-adapter-test",
            "_bridge_sink": bridge_sink,
        },
        correlation_id_factory=lambda: f"corr-{class_name}",
        now_factory=lambda: datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc),
    )
    try:
        await adapter._emit_authenticated(ctx=ctx, **emit_kwargs)
        events = [event async for event in log.read_since()]
        assert len(events) == 1
        event = events[0]
        assert event["type"] == "adapter.authenticated"
        assert event["source_adapter"] == adapter.name
        assert event["source_account_id"] == source_account_id
        assert event["owner_scope"] == "private:member-adapter-test"
        assert event["correlation_id"] == f"corr-{class_name}"
    finally:
        await bus.stop()
        await log.close()
