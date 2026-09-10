"""Gmail receipt compatibility using the real encrypted log and cursor store.

Provider responses are synthetic. Process-level classifier continuity is also
exercised in test_disposable_test_acquisition through create_application.
"""

from __future__ import annotations

import base64
import copy
import importlib.util
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.base import AdapterContext
from adminme.adapters.cursor_store import AdapterCursorStore
from adminme.events.log import EventLog, IdempotencyConflict


ROOT = Path(__file__).resolve().parents[2]
ACCOUNT = "receipt-fixture@example.invalid"
MEMBER = "fixture-receipt-member"  # fixture:tenant_data:ok
CURSOR = "fixture-gmail-source"


class _Sink:
    tenant_id = "fixture-tenant"

    def __init__(self, log: EventLog) -> None:
        self.log = log

    async def write(self, envelope: Any, **kwargs: Any) -> str:
        return await self.log.append(envelope, **kwargs)

    async def write_batch(self, envelopes: Any, **kwargs: Any) -> list[str]:
        return await self.log.append_batch(envelopes, **kwargs)


def _message(*, sent: bool = False) -> dict[str, Any]:
    return {
        "id": "12ab",
        "threadId": "fixture-thread",
        "internalDate": "1788264000000",
        "historyId": "1",
        "labelIds": ["SENT"] if sent else ["INBOX", "UNREAD"],
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {
                    "name": "From",
                    "value": ACCOUNT if sent else "sender@example.invalid",
                },
                {
                    "name": "To",
                    "value": "recipient@example.invalid" if sent else ACCOUNT,
                },
                {"name": "Subject", "value": "Synthetic observation"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {
                        "data": base64.urlsafe_b64encode(b"Fixture content.").decode()
                    },
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "fixture.pdf",
                    "body": {"size": 23, "attachmentId": "handle-a"},
                },
            ],
        },
    }


@pytest.fixture
async def receipt_case(tmp_path: Path):
    spec = importlib.util.spec_from_file_location(
        "gmail_receipt_fixture", ROOT / "packs/adapters/messaging/gmail/adapter.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    adapter = module.GmailAdapter(
        config={
            "service_account_key_ref": "op://fixture/gmail/credentials",
            "user_email": ACCOUNT,
            "provider_account_id": ACCOUNT,
            "member_id": MEMBER,
        }
    )
    log = EventLog(tmp_path / "events.db", b"f" * 32)
    store = AdapterCursorStore(tmp_path / "cursors.sqlite3")
    ctx = AdapterContext(
        event_sink=_Sink(log),
        config={},
        correlation_id_factory=lambda: "fixture-correlation",
        now_factory=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
        cursor_store=store,
        source_events=log,
    )
    try:
        yield adapter, ctx, store, log
    finally:
        store.close()
        await log.close()


async def _ingest(case: Any, message: dict[str, Any]) -> tuple[int, int]:
    adapter, ctx, store, _log = case
    return await adapter._ingest_message_ids(
        ctx=ctx,
        store=store,
        cursor_id=CURSOR,
        message_ids=["12ab"],
        provider_watermark="2",
        prefetched_messages={"12ab": message},
    )


@pytest.mark.parametrize("sent", [False, True])
@pytest.mark.parametrize("cursor_committed", [False, True])
async def test_legacy_transport_receipt_is_reused_with_or_without_cursor_commit(
    receipt_case: Any,
    sent: bool,
    cursor_committed: bool,
) -> None:
    adapter, ctx, store, log = receipt_case
    message = _message(sent=sent)
    legacy_fingerprint = adapter._message_fingerprint(message)
    legacy_id = await adapter._emit_message_observation(
        ctx=ctx, message=message, fingerprint=legacy_fingerprint
    )
    if cursor_committed:
        assert store.record_success(
            CURSOR,
            external_id="12ab",
            fingerprint=legacy_fingerprint,
            event_id=legacy_id,
        )
    immutable = await log.get(legacy_id)
    message["payload"]["parts"][1]["body"]["attachmentId"] = "handle-b"
    message["historyId"] = "2"
    message["sizeEstimate"] = 999
    assert await _ingest(receipt_case, message) == (0, 0)
    assert await log.count() == 1
    assert await log.get(legacy_id) == immutable
    typed = adapter._observation_fingerprint(ctx, message)
    assert store.dedupe_event(CURSOR, "12ab", typed) == legacy_id
    assert await log.get_by_idempotency(ctx.event_sink.tenant_id, typed) is None
    if cursor_committed:
        assert store.dedupe_event(CURSOR, "12ab", legacy_fingerprint) == legacy_id
    assert store.get(CURSOR).provider_watermark is None
    # The durable alias survives a restart with another rotating handle.
    reopened = AdapterCursorStore(store.path)
    try:
        message["payload"]["parts"][1]["body"]["attachmentId"] = "handle-c"
        restarted_ctx = replace(ctx, cursor_store=reopened)
        assert await _ingest((adapter, restarted_ctx, reopened, log), message) == (0, 0)
        assert reopened.dedupe_event(CURSOR, "12ab", typed) == legacy_id
    finally:
        reopened.close()


@pytest.mark.parametrize("sent", [False, True])
async def test_stable_receipt_recovers_commit_gap_with_changed_transport(
    receipt_case: Any,
    sent: bool,
) -> None:
    adapter, ctx, store, log = receipt_case
    message = _message(sent=sent)
    fingerprint = adapter._observation_fingerprint(ctx, message)
    original = await adapter._emit_message_observation(
        ctx=ctx, message=message, fingerprint=fingerprint
    )
    assert store.latest_event(CURSOR, "12ab") is None
    message["payload"]["parts"][1]["body"]["attachmentId"] = "retry-handle"
    message["labelIds"].reverse()
    assert await _ingest(receipt_case, message) == (0, 0)
    assert await log.count() == 1
    assert store.dedupe_event(CURSOR, "12ab", fingerprint) == original


@pytest.mark.parametrize(
    "change",
    ["body", "subject", "date", "thread", "labels", "filename", "mime", "size"],
)
async def test_typed_fingerprint_preserves_meaningful_source_updates(
    receipt_case: Any, change: str
) -> None:
    message = _message()
    assert await _ingest(receipt_case, message) == (1, 0)
    if change == "body":
        message["payload"]["parts"][0]["body"]["data"] = base64.urlsafe_b64encode(
            b"Changed content."
        ).decode()
    elif change == "subject":
        message["payload"]["headers"][2]["value"] = "Changed subject"
    elif change == "date":
        message["internalDate"] = "1788264060000"
    elif change == "thread":
        message["threadId"] = "other-thread"
    elif change == "labels":
        message["labelIds"] = ["INBOX"]
    elif change == "filename":
        message["payload"]["parts"][1]["filename"] = "changed.pdf"
    elif change == "mime":
        message["payload"]["parts"][1]["mimeType"] = "application/octet-stream"
    else:
        message["payload"]["parts"][1]["body"]["size"] = 24
    assert await _ingest(receipt_case, message) == (1, 0)
    assert await receipt_case[3].count() == 2
    assert await _ingest(receipt_case, message) == (0, 0)


@pytest.mark.parametrize(
    "field",
    [
        "tenant_id",
        "owner_scope",
        "visibility_scope",
        "source_adapter",
        "source_account_id",
        "type",
        "sensitivity",
        "native_message_id",
        "provider_account_id",
        "member_id",
    ],
)
async def test_legacy_equivalence_refuses_different_source_identity(
    receipt_case: Any, field: str
) -> None:
    adapter, ctx, _store, _log = receipt_case
    expected = adapter._observation_envelope(ctx, _message())
    event = expected.model_dump()
    if field in {"native_message_id", "provider_account_id", "member_id"}:
        event["payload"][field] = "different-fixture"
    else:
        event[field] = "different-fixture"
    assert not adapter._equivalent_observation(event, expected)


@pytest.mark.parametrize("sent", [False, True])
async def test_older_account_bound_schema_defaults_are_conservative(
    receipt_case: Any, sent: bool
) -> None:
    adapter, ctx, _store, log = receipt_case
    message = _message(sent=sent)
    expected = adapter._observation_envelope(ctx, message)
    old = expected.model_dump()
    old["schema_version"] = 3
    old["payload"].pop("body_html" if sent else "in_reply_to")
    assert adapter._equivalent_observation(old, expected)
    old_key = adapter._observation_key(
        expected.type, "12ab", adapter._message_fingerprint(message)
    )
    old_id = await log.append(
        expected.model_copy(
            update={
                "schema_version": 3,
                "payload": old["payload"],
                "idempotency_key": old_key,
            }
        )
    )
    assert await _ingest(receipt_case, message) == (0, 0)
    assert (
        receipt_case[2].dedupe_event(
            CURSOR, "12ab", adapter._observation_fingerprint(ctx, message)
        )
        == old_id
    )
    old["schema_version"] = 1
    assert not adapter._equivalent_observation(old, expected)
    old["schema_version"] = 3
    expected.payload["body_html" if sent else "in_reply_to"] = "new evidence"
    assert not adapter._equivalent_observation(old, expected)
    assert await log.count() == 1


@pytest.mark.parametrize("alias", [False, True])
async def test_wrong_account_receipt_or_alias_refuses_ingest_without_mutation(
    receipt_case: Any,
    alias: bool,
) -> None:
    adapter, ctx, store, log = receipt_case
    message = _message()
    fingerprint = adapter._observation_fingerprint(ctx, message)
    envelope = adapter._observation_envelope(ctx, message)
    envelope = envelope.model_copy(
        update={
            "source_account_id": "other-receipt@example.invalid",
            "payload": {
                **envelope.payload,
                "provider_account_id": "other-receipt@example.invalid",
            },
            "idempotency_key": "fixture-other-account-receipt"
            if alias
            else fingerprint,
        }
    )
    wrong_id = await log.append(envelope)
    if alias:
        assert store.record_success(
            CURSOR, external_id="12ab", fingerprint=fingerprint, event_id=wrong_id
        )
    with pytest.raises(IdempotencyConflict, match="identity mismatch"):
        await _ingest(receipt_case, message)
    assert await log.count() == 1
    assert store.get(CURSOR).provider_watermark is None
    assert store.dedupe_event(CURSOR, "12ab", fingerprint) == (
        wrong_id if alias else None
    )


@pytest.mark.parametrize("equivalent", [False, True])
async def test_concurrent_receipt_conflict_requires_exact_verified_equivalence(
    receipt_case: Any,
    monkeypatch: pytest.MonkeyPatch,
    equivalent: bool,
) -> None:
    adapter, ctx, store, log = receipt_case
    message = _message()
    real_emit = adapter._emit_message_observation
    winner_id = None

    async def race(**kwargs: Any) -> str:
        nonlocal winner_id
        winner_message = copy.deepcopy(message)
        winner_message["payload"]["parts"][1]["body"]["attachmentId"] = (
            "concurrent-handle"
        )
        if not equivalent:
            winner_message["payload"]["headers"][2]["value"] = "Different observation"
        winner_id = await real_emit(**{**kwargs, "message": winner_message})
        return await real_emit(**kwargs)

    monkeypatch.setattr(adapter, "_emit_message_observation", race)
    if equivalent:
        assert await _ingest(receipt_case, message) == (0, 0)
        assert (
            store.dedupe_event(
                CURSOR, "12ab", adapter._observation_fingerprint(ctx, message)
            )
            == winner_id
        )
    else:
        with pytest.raises(IdempotencyConflict):
            await _ingest(receipt_case, message)
        assert store.latest_event(CURSOR, "12ab") is None
    assert await log.count() == 1


async def test_alias_commit_remains_fenced_after_receipt_read(
    receipt_case: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, ctx, store, log = receipt_case
    message = _message()
    old_id = await adapter._emit_message_observation(
        ctx=ctx, message=message, fingerprint=adapter._message_fingerprint(message)
    )
    reader = ctx.source_events
    assert reader is not None
    real_lookup = reader.get_by_idempotency_prefix

    async def lose_lease(tenant: str, prefix: str) -> list[dict[str, Any]]:
        receipts = await real_lookup(tenant, prefix)
        assert store.release(CURSOR, adapter._lease_owner)
        assert store.claim(CURSOR, "other-worker", ttl_ms=60_000)
        return receipts

    monkeypatch.setattr(reader, "get_by_idempotency_prefix", lose_lease)
    assert await _ingest(receipt_case, message) == (0, 0)
    assert store.latest_event(CURSOR, "12ab") is None
    assert store.get(CURSOR).provider_watermark is None
    assert adapter.status().extra["cursor_lease"] == "lost_before_commit"
    assert await log.get(old_id) is not None and await log.count() == 1


async def test_wrong_provider_native_id_never_publishes_or_advances(
    receipt_case: Any,
) -> None:
    message = _message()
    message["id"] = "12ab-other"
    with pytest.raises(RuntimeError, match="native ID"):
        await _ingest(receipt_case, message)
    assert await receipt_case[3].count() == 0
    assert receipt_case[2].latest_event(CURSOR, "12ab") is None
