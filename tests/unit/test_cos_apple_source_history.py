"""Exact historical attribution from real producer events in encrypted logs."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from adminme.adapters.base import AdapterContext
from adminme.adapters.cos_apple_reminders import ADAPTER_KIND, READ_SCOPE
from adminme.adapters.cursor_store import AdapterCursorStore
from adminme.cos_apple_source_history import (
    EVENT_TYPE,
    SOURCE_ADAPTER,
    event_fingerprint,
    receipt_idempotency_key,
    source_owner_matches,
    validate_evidence,
)
from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import AppendValidationError, EventLog
from adminme.events.writer import EventWriter
from adminme.historical_attribution import audit_historical_attribution
from adminme.lib.instance_config import load_instance_config
from adminme.provider_bindings import ProviderBinding
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID, service_private_scope
from tests.unit.bootstrap import test_cos_apple_reminders as acquisition

ROOT = Path(__file__).resolve().parents[2]
Event = dict[str, Any]
Pair = tuple[Event, Event]
INVALID_EVIDENCE = "^CoS Apple historical ownership evidence is invalid$"


@pytest.fixture(scope="module")
async def produced_pair(tmp_path_factory: pytest.TempPathFactory) -> Pair:
    """Use the actual loaded adapter, event writer and cursor once, faking native I/O."""
    root = tmp_path_factory.mktemp("retained-cos-producer")
    instance_dir = root / "state"
    instance_dir.mkdir()
    instance = load_instance_config(instance_dir)
    log = EventLog(instance, b"r" * 32)
    bus = EventBus(log, instance.bus_checkpoint_path)
    cursor = AdapterCursorStore(root / "cursor.sqlite")
    ctx = AdapterContext(
        event_sink=EventWriter(log, bus),
        config={},
        correlation_id_factory=lambda: "fixture-retained-cos",
        # Persisted ingestion and the subsequent authentication must share a
        # real clock; the older frozen fixture clock predates recorded_at.
        now_factory=lambda: datetime.now(timezone.utc),
        cursor_store=cursor,
        source_events=log,
    )
    reader = acquisition.Reader()
    reader.snapshot = replace(reader.snapshot, items=())
    adapter = acquisition._adapter(root, reader)
    task: asyncio.Task[None] | None = None
    try:
        task = await acquisition._start(adapter, ctx, log)
        rows = [row async for row in log.read_since()]
        assert [(row["type"], row["schema_version"]) for row in rows] == [
            ("list.created", 2),
            ("adapter.authenticated", 3),
        ]
        assert reader.calls == 1
        return copy.deepcopy(rows[0]), copy.deepcopy(rows[1])
    finally:
        await adapter.stop()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        cursor.close()
        await bus.stop()
        await log.close()


@pytest.fixture
async def log(tmp_path: Path) -> AsyncIterator[EventLog]:
    store = EventLog(tmp_path / "retained-events.db", b"t" * 32)
    try:
        yield store
    finally:
        await store.close()


async def _append(log: EventLog, event: Event) -> Event:
    envelope = EventEnvelope.model_validate(
        {
            key: value
            for key, value in event.items()
            if key in EventEnvelope.model_fields
        }
    )
    event_id = await log.append(
        envelope,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
    )
    stored = await log.get(event_id)
    assert stored is not None
    return stored


async def _retain(log: EventLog, pair: Pair) -> Pair:
    return await _append(log, pair[0]), await _append(log, pair[1])


def _receipt(source: Event, authentication: Event) -> Event:
    now = datetime.now(timezone.utc)
    activation_id = "e" * 32
    scope = service_private_scope(COS_SERVICE_PRINCIPAL_ID)
    return EventEnvelope(
        event_at_ms=int(now.timestamp() * 1000),
        tenant_id=source["tenant_id"],
        type=EVENT_TYPE,
        schema_version=1,
        occurred_at=now.isoformat(),
        source_adapter=SOURCE_ADAPTER,
        source_account_id=acquisition._selection().provider_account_id,
        owner_scope=scope,
        visibility_scope=scope,
        sensitivity="privileged",
        actor_identity="operator:fixture-owner",
        correlation_id=activation_id,
        causation_id=source["event_id"],
        idempotency_key=receipt_idempotency_key(source["event_id"]),
        payload={
            "source_event_id": source["event_id"],
            "source_fingerprint": event_fingerprint(source),
            "authentication_event_id": authentication["event_id"],
            "authentication_fingerprint": event_fingerprint(authentication),
            "selection": acquisition._selection().model_dump(mode="json"),
            "activation_id": activation_id,
            "activation_journal_sha256": "1" * 64,
            "activation_request_sha256": "2" * 64,
            "prepared_adapter_configuration_sha256": "3" * 64,
            "owner_member_id": acquisition.MEMBER_A,
            "operator_principal_id": "operator:fixture-owner",
            "recovery_authorization_sha256": "4" * 64,
            "reconciled_at": now.isoformat(),
            "authority": "historical_attribution_only",
        },
    ).model_dump(mode="json")


def _binding(source: Event) -> ProviderBinding:
    owner = source["owner_scope"].removeprefix("service-private:")
    return ProviderBinding(
        adapter_id=acquisition._selection().adapter_id,
        kind=ADAPTER_KIND,
        provider_family="apple",
        owner_kind="service_principal",
        owner_id=owner,
        member_id=None,
        service_principal_id=owner,
        provider_account_id=source["source_account_id"],
        workspace="service",
        permission_scope=(READ_SCOPE,),
        binding_provenance="service-device-authorization",
    )


async def _audit(
    log: EventLog,
    source: Event,
    *,
    with_current_binding: bool = False,
    through: str | None = None,
) -> Event:
    return await audit_historical_attribution(
        log,
        repository_root=ROOT,
        provider_bindings=(_binding(source),) if with_current_binding else (),
        hash_key=b"fixture-audit-key" * 2,
        through_event_id=through,
    )


def _assert_unattributed(report: Event) -> None:
    assert report["state"] == "degraded"
    assert report["source_event_count"] == report["unattributed_event_count"] == 1
    assert report["attributed_event_count"] == 0
    assert report["content_exposed"] is False
    assert report["raw_identifiers_exposed"] is False


def _set(event: Event, path: str, value: Any) -> None:
    parts = path.split(".")
    for key in parts[:-1]:
        event = event[key]
    event[parts[-1]] = value


@pytest.mark.asyncio
async def test_actual_pair_is_attributed_only_after_exact_retained_receipt(
    log: EventLog, produced_pair: Pair
) -> None:
    source, authentication = await _retain(log, produced_pair)
    originals = copy.deepcopy((source, authentication))
    assert (
        await validate_evidence(
            log,
            acquisition._selection(),
            source["event_id"],
            authentication["event_id"],
            authentication["event_id"],
        )
        == originals
    )
    _assert_unattributed(await _audit(log, source))
    # Existing active-binding behavior remains available without a receipt.
    assert (await _audit(log, source, with_current_binding=True))["state"] == "healthy"

    receipt = await _append(log, _receipt(source, authentication))
    assert await source_owner_matches(log, source, receipt["event_id"])
    report = await _audit(log, source)
    assert report["state"] == "healthy"
    assert report["source_event_count"] == report["attributed_event_count"] == 1
    assert report["unattributed_event_count"] == 0
    assert (
        report["groups"][0]["binding_state"] == "retained_cos_apple_source_reconciled"
    )
    assert report["content_exposed"] is report["raw_identifiers_exposed"] is False
    assert await log.get(source["event_id"]) == originals[0]
    assert await log.get(authentication["event_id"]) == originals[1]
    assert [row async for row in log.read_since()] == [*originals, receipt]

    # Future receipts cannot make a historical watermark pass, even if today's
    # matching binding would otherwise attribute the source.
    assert not await source_owner_matches(log, source, authentication["event_id"])
    _assert_unattributed(
        await _audit(
            log, source, with_current_binding=True, through=authentication["event_id"]
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("side", "path", "value"),
    [
        (0, "source_adapter", "reminders-apple-eventkit"),
        (0, "source_account_id", "provider-account:other"),
        (0, "owner_scope", "service-private:service-principal:other"),
        (0, "visibility_scope", "shared:household"),
        (0, "actor_identity", "operator:other"),
        (0, "causation_id", "unrelated-cause"),
        (0, "raw_ref", "fixture-raw-reference"),
        (0, "sensitivity", "sensitive"),
        (0, "correlation_id", None),
        (0, "payload.service_principal_id", "service-principal:other"),
        (0, "payload.provider", "google_tasks"),
        (0, "payload.provider_account_id", "provider-account:other"),
        (0, "payload.list_external_id", "other-list"),
        (0, "payload.provider_revision", "f" * 64),
        (0, "payload.is_default", True),
        (0, "payload.color", "#123456"),
        (0, "idempotency_key", "cos-reminders:" + "f" * 64 + ":" + "a" * 64),
        (0, "payload.changed_at", "2099-01-01T00:00:00Z"),
        (1, "source_adapter", "reminders-apple-eventkit"),
        (1, "source_account_id", "provider-account:other"),
        (1, "tenant_id", "tenant-other"),
        (1, "idempotency_key", "invented-authentication-key"),
        (1, "payload.configuration_sha256", "f" * 64),
        (1, "payload.device_identity_sha256", "f" * 64),
        (1, "payload.source_ownership_evidence_sha256", "f" * 64),
        (1, "payload.authenticated_at", "2000-01-01T00:00:00Z"),
        (1, "recorded_at", "2000-01-01T00:00:00Z"),
    ],
    ids=lambda value: str(value),
)
async def test_malformed_source_or_auth_cannot_borrow_matching_current_binding(
    log: EventLog, produced_pair: Pair, side: int, path: str, value: Any
) -> None:
    pair = copy.deepcopy(produced_pair)
    _set(pair[side], path, value)
    source, authentication = await _retain(log, pair)
    with pytest.raises(ValueError, match=INVALID_EVIDENCE):
        await validate_evidence(
            log,
            acquisition._selection(),
            source["event_id"],
            authentication["event_id"],
            authentication["event_id"],
        )
    # Fingerprints describe these actual stored events, so the failure must be
    # their semantic provenance rather than an incidental stale fingerprint.
    receipt = await _append(log, _receipt(source, authentication))
    assert not await source_owner_matches(log, source, receipt["event_id"])
    _assert_unattributed(await _audit(log, source, with_current_binding=True))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("source_adapter", "other-recovery-controller"),
        ("source_account_id", "provider-account:other"),
        ("owner_scope", "service-private:service-principal:other"),
        ("visibility_scope", "shared:household"),
        ("sensitivity", "normal"),
        ("actor_identity", "operator:other"),
        ("causation_id", "unrelated-source"),
        ("correlation_id", "f" * 32),
        ("raw_ref", "fixture-raw-reference"),
        ("payload.source_event_id", "unrelated-source"),
        ("payload.authentication_event_id", "unrelated-authentication"),
        ("payload.source_fingerprint", "f" * 64),
        ("payload.authentication_fingerprint", "f" * 64),
        ("payload.selection.source_id", "other-source"),
        ("payload.selection.list_id", "other-list"),
        ("payload.selection.source_kind", "icloud"),
        ("payload.selection.device_identity_sha256", "f" * 64),
        ("payload.selection.os_uid", 502),
        ("payload.selection.source_ownership_evidence_sha256", "f" * 64),
        ("payload.selection.owner_authorization_sha256", "f" * 64),
        ("payload.selection.native_helper.artifact_sha256", "f" * 64),
        ("payload.reconciled_at", "2000-01-01T00:00:00Z"),
        ("occurred_at", "2000-01-01T00:00:00Z"),
        ("recorded_at", "2000-01-01T00:00:00Z"),
    ],
    ids=lambda value: str(value),
)
async def test_malformed_receipt_cannot_borrow_matching_current_binding(
    log: EventLog, produced_pair: Pair, path: str, value: Any
) -> None:
    source, authentication = await _retain(log, produced_pair)
    receipt = _receipt(source, authentication)
    _set(receipt, path, value)
    stored = await _append(log, receipt)
    assert not await source_owner_matches(log, source, stored["event_id"])
    _assert_unattributed(await _audit(log, source, with_current_binding=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["source", "authentication"])
async def test_receipt_requires_both_referenced_events(
    log: EventLog, produced_pair: Pair, missing: str
) -> None:
    source, authentication = copy.deepcopy(produced_pair)
    await _append(log, authentication if missing == "source" else source)
    receipt = await _append(log, _receipt(source, authentication))
    assert not await source_owner_matches(log, source, receipt["event_id"])
    if missing == "authentication":
        _assert_unattributed(await _audit(log, source, with_current_binding=True))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch", ["idempotency", "tenant", "receipt_order", "auth_order"]
)
async def test_receipt_is_exactly_indexed_and_follows_authentication(
    log: EventLog, produced_pair: Pair, mismatch: str
) -> None:
    pair = copy.deepcopy(produced_pair)
    if mismatch == "auth_order":
        pair[0]["event_id"], pair[1]["event_id"] = (
            pair[1]["event_id"],
            pair[0]["event_id"],
        )
    source, authentication = await _retain(log, pair)
    receipt = _receipt(source, authentication)
    if mismatch == "idempotency":
        receipt["idempotency_key"] = receipt_idempotency_key("other-source")
    elif mismatch == "tenant":
        receipt["tenant_id"] = "tenant-other"
    elif mismatch == "receipt_order":
        receipt["event_id"] = "00000000000000000000000000"
    stored = await _append(log, receipt)
    watermark = await log.latest_event_id()
    assert watermark is not None
    assert not await source_owner_matches(log, source, watermark)
    _assert_unattributed(await _audit(log, source))
    assert stored["payload"]["source_fingerprint"] == event_fingerprint(source)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("payload.authority", "acquire_future_sources"),
        ("payload.owner_member_id", " owner-with-whitespace"),
        ("payload.operator_principal_id", "member:other"),
        ("payload.activation_id", "not-an-activation-id"),
        ("payload.activation_journal_sha256", "not-a-digest"),
        ("payload.selection.os_uid", "501"),
        ("payload.reconciled_at", "2026-01-01T00:00:00"),
        ("payload.extra_authority", "provider-write"),
    ],
    ids=lambda value: str(value),
)
async def test_typed_receipt_rejects_invalid_authority_before_append(
    log: EventLog, produced_pair: Pair, path: str, value: Any
) -> None:
    source, authentication = await _retain(log, produced_pair)
    receipt = _receipt(source, authentication)
    _set(receipt, path, value)
    with pytest.raises(AppendValidationError):
        await _append(log, receipt)
    assert await log.latest_event_id() == authentication["event_id"]
    assert (
        await log.get_by_idempotency(
            source["tenant_id"], receipt_idempotency_key(source["event_id"])
        )
        is None
    )
    _assert_unattributed(await _audit(log, source))


@pytest.mark.asyncio
async def test_receipt_does_not_attribute_later_facts_or_altered_source_view(
    log: EventLog, produced_pair: Pair
) -> None:
    source, authentication = await _retain(log, produced_pair)
    receipt = await _append(log, _receipt(source, authentication))
    altered_view = copy.deepcopy(source)
    altered_view["payload"]["list_name"] = "Altered retained view"
    assert not await source_owner_matches(log, altered_view, receipt["event_id"])

    # The receipt is a fact-specific historical repair, never a standing grant
    # for another acquisition of even the same source/account/list coordinate.
    later = copy.deepcopy(source)
    later["event_id"] = ""
    now = datetime.now(timezone.utc) + timedelta(milliseconds=1)
    later["event_at_ms"] = int(now.timestamp() * 1000)
    later["occurred_at"] = later["payload"]["changed_at"] = now.isoformat()
    later["recorded_at"] = now.isoformat()
    later["idempotency_key"] += ":later"
    later = await _append(log, later)
    assert not await source_owner_matches(log, later, later["event_id"])
    report = await _audit(log, later)
    assert report["state"] == "degraded"
    assert report["source_event_count"] == 2
    assert report["attributed_event_count"] == report["unattributed_event_count"] == 1
