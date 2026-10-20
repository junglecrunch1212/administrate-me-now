"""CoS replies cannot disappear from terminal-effect or privacy accounting."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.cos_response_assurance import cos_provider_effect_counts
from adminme.cos_response_policy import POLICY_CONTRACT, POLICY_EVENT_TYPE
from adminme.cos_responses import RESPONSE_CONTRACT, content_fingerprint
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events import log as event_log_module
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from adminme.source_assurance import (
    _provider_effect_counts,
    _provider_effect_integrity_healthy,
    audit_source_assurance,
)

MEMBER = "member-cos-audit-fixture"  # fixture:tenant_data:ok
ROOT = Path(__file__).resolve().parents[2]


def _chain(status: str = "sent", channel: str = "email") -> list[dict[str, Any]]:
    account = "cos-audit@example.invalid"
    recipient = "member-audit@example.invalid"
    body = "Synthetic private response for audit coverage."
    fingerprint = content_fingerprint(
        account_id=account, recipient=recipient, subject="", body=body, channel=channel
    )

    def event(
        number: int, kind: str, payload: dict[str, Any], **fields: Any
    ) -> dict[str, Any]:
        return {
            "event_id": f"cos-audit-{number:04}",
            "event_at_ms": 1_792_497_600_000 + number,
            "type": kind,
            "schema_version": 1,
            "tenant_id": "tenant-cos-audit-fixture",
            "source_adapter": "fixture-cos-audit",
            "source_account_id": "fixture",
            "owner_scope": f"private:{MEMBER}",
            "visibility_scope": f"private:{MEMBER}",
            "actor_identity": MEMBER,
            "occurred_at": f"2026-10-20T12:00:0{number}Z",
            "recorded_at": f"2026-10-20T12:00:0{number}Z",
            "payload": payload,
            **fields,
        }

    policy = event(
        1,
        POLICY_EVENT_TYPE,
        {
            "contract": POLICY_CONTRACT,
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "revision": 1,
            "entries": [
                {"member_id": MEMBER, "channel": channel, "address": recipient}
            ],
            "changed_by_member_id": MEMBER,
            "changed_at": "2026-10-20T12:00:00Z",
            "provider_effects": False,
            "grants_provider_authority": False,
        },
    )
    approved = event(
        2,
        "cos.response.approved",
        {
            "contract": RESPONSE_CONTRACT,
            "member_id": MEMBER,
            "channel": channel,
            "service_channel_id": "channel:cos:audit-fixture",
            "provider_account_id": account,
            "recipient": recipient,
            "subject": "",
            "body": body,
            "content_fingerprint": fingerprint,
            "policy_event_id": policy["event_id"],
            "policy_revision": 1,
            "approved_at": "2026-10-20T12:00:00.100000Z",
            "expires_at": "2026-10-20T12:10:00.100000Z",
        },
    )
    receipt = event(
        3,
        "openclaw.approval.received",
        {
            "receipt_id": "fixture-openclaw-receipt",
            "member_id": MEMBER,
            "tool_name": "administrateme_cos_response",
            "action": "cos.response.send",
            "request_fingerprint": "a" * 64,
            "resource_fingerprint": "a" * 64,
            "issued_at": "2026-10-20T12:00:00.200000Z",
            "consumed_at": "2026-10-20T12:00:00Z",
            "expires_at": "2026-10-20T12:02:00.200000Z",
            "single_use": True,
            "approval_method": "allow_once",
            "openclaw_version": "fixture",
            "openclaw_commit": "fixture-commit",
            "provenance": {"member_id": MEMBER},
        },
        causation_id=approved["event_id"],
        raw_ref=f"request-sha256:{'a' * 64}",
    )
    attempt = event(
        4,
        "cos.response.attempted",
        {
            "contract": RESPONSE_CONTRACT,
            "member_id": MEMBER,
            "approval_event_id": approved["event_id"],
            "openclaw_approval_event_id": receipt["event_id"],
            "content_fingerprint": fingerprint,
            "policy_event_id": policy["event_id"],
            "attempted_at": "2026-10-20T12:00:00.300000Z",
            "automatic_retry_allowed": False,
        },
        causation_id=receipt["event_id"],
    )
    sent = status == "sent"
    provider = "gmail" if channel == "email" else "imessage"
    outbound = event(
        5,
        "external.sent" if sent else "observation.suppressed",
        {
            "initiating_command_id": approved["event_id"],
            "initiating_event_id": attempt["event_id"],
            "domain_approval_event_id": approved["event_id"],
            "openclaw_approval_event_id": receipt["event_id"],
            "provider": provider,
            "provider_account_id": account,
            "operation": "cos.response.send",
            "content_fingerprint": fingerprint,
            "provider_called": sent,
            "target_channel": provider,
            "target_identifier": recipient,
            **(
                {
                    "action": "cos.response.send",
                    "sent_at": "2026-10-20T12:00:05Z",
                    "payload": {},
                }
                if sent
                else {
                    "attempted_action": "cos.response.send",
                    "attempted_at": "2026-10-20T12:00:05Z",
                    "would_have_sent_payload": {},
                    "reason": "observation_mode_active",
                }
            ),
        },
        schema_version=2,
        causation_id=attempt["event_id"],
    )
    terminal = event(
        6,
        "cos.response.result",
        {
            "contract": RESPONSE_CONTRACT,
            "member_id": MEMBER,
            "approval_event_id": approved["event_id"],
            "content_fingerprint": fingerprint,
            "status": status,
            "reason": status,
            "provider_effect_count": None if status == "unknown" else int(sent),
            "automatic_retry_allowed": False,
            "completed_at": "2026-10-20T12:00:06Z",
        },
        causation_id=approved["event_id"],
    )
    if status == "denied":
        return [policy, approved, terminal]
    if status == "unknown":
        return [policy, approved, receipt, attempt, terminal]
    return [policy, approved, receipt, attempt, outbound, terminal]


def _counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts = _provider_effect_counts([], {})
    counts.update(cos_provider_effect_counts(events))
    return counts


@pytest.mark.parametrize("channel", ["email", "imessage"])
@pytest.mark.parametrize("status", ["sent", "suppressed", "denied", "unknown"])
def test_exact_response_terminal_accounting(channel: str, status: str) -> None:
    counts = _counts(_chain(status, channel))
    assert counts["write_requests"] == counts["terminal_results"] == 1
    assert counts["provider_called_true"] == int(status == "sent")
    assert counts["provider_called_false"] == int(status in {"denied", "suppressed"})
    assert counts["provider_called_unknown"] == int(status == "unknown")
    assert _provider_effect_integrity_healthy(counts) is (status != "unknown")


@pytest.mark.parametrize(
    "channel,reason,healthy",
    [
        ("imessage", "native_dispatch_not_claimed", True),
        ("email", "native_dispatch_not_claimed", False),
        ("imessage", "transport_outcome_unknown", False),
    ],
)
def test_zero_effect_after_attempt_requires_native_unclaimed_result(
    channel: str, reason: str, healthy: bool
) -> None:
    # The native result endpoint alone emits this reason after atomically
    # retiring an unclaimed admission. Other denied-after-attempt shapes are
    # still incomplete evidence, including the same reason on email.
    events = _chain("unknown", channel)
    events[-1]["payload"].update(
        status="denied", reason=reason, provider_effect_count=0
    )
    counts = _counts(events)
    assert _provider_effect_integrity_healthy(counts) is healthy
    assert counts["approval_correlation_missing"] == int(not healthy)


@pytest.mark.parametrize(
    "removed,defect",
    [
        (0, "malformed_request"),
        (1, "malformed_request"),
        (2, "approval_correlation_missing"),
        (3, "orphan_terminal"),
        (4, "approval_correlation_missing"),
        (5, "missing_terminal"),
    ],
)
def test_missing_link_never_disappears_from_accounting(
    removed: int, defect: str
) -> None:
    events = _chain()
    del events[removed]
    counts = _counts(events)
    assert counts[defect] > 0
    assert counts["provider_called_true"] == 1
    assert not _provider_effect_integrity_healthy(counts)


@pytest.mark.parametrize(
    "index,field,value",
    [
        (1, "actor_identity", "member-other-fixture"),
        (3, "tenant_id", "tenant-other-fixture"),
        (4, "owner_scope", "shared:household"),
        (5, "visibility_scope", "shared:household"),
    ],
)
def test_private_identity_mismatch_degrades(index: int, field: str, value: str) -> None:
    events = _chain()
    events[index][field] = value
    counts = _counts(events)
    assert counts["approval_correlation_missing"] > 0
    assert not _provider_effect_integrity_healthy(counts)


@pytest.mark.parametrize(
    "index,field,value",
    [
        (1, "body", "Changed after approval."),
        (2, "approval_method", "standing_order"),
        (3, "content_fingerprint", "b" * 64),
        (3, "automatic_retry_allowed", True),
        (4, "provider_account_id", "other@example.invalid"),
        (4, "target_identifier", "other@example.invalid"),
        (5, "provider_effect_count", 0),
        (5, "automatic_retry_allowed", True),
    ],
)
def test_approval_or_effect_drift_degrades(index: int, field: str, value: Any) -> None:
    events = _chain()
    events[index]["payload"][field] = value
    assert not _provider_effect_integrity_healthy(_counts(events))


@pytest.mark.parametrize(
    "index,key",
    [
        (3, "duplicate_request"),
        (4, "duplicate_terminal"),
        (5, "duplicate_terminal"),
    ],
)
def test_duplicate_request_or_terminal_is_not_healthy(index: int, key: str) -> None:
    events = _chain()
    duplicate = copy.deepcopy(events[index])
    duplicate["event_id"] += "-duplicate"
    events.append(duplicate)
    counts = _counts(events)
    assert counts[key] == 1
    assert not _provider_effect_integrity_healthy(counts)


def test_orphan_effect_and_unknown_result_remain_visible() -> None:
    events = _chain()
    assert _counts([events[4]])["provider_called_true"] == 1
    assert _counts([events[4]])["orphan_terminal"] == 1
    unknown = _chain("unknown")
    unknown.insert(-1, events[4])
    counts = _counts(unknown)
    assert counts["provider_called_true"] == counts["provider_called_unknown"] == 1
    assert not _provider_effect_integrity_healthy(counts)


def test_revocation_before_attempt_invalidates_old_policy_binding() -> None:
    events = _chain()
    revocation = copy.deepcopy(events[0])
    revocation["event_id"] = "cos-audit-0003-revocation"
    revocation["payload"].update(revision=2, entries=[])
    events.append(revocation)
    assert _counts(events)["approval_correlation_missing"] > 0


@pytest.mark.asyncio
async def test_encrypted_auditor_keeps_closed_window_and_global_effects(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "events.db", b"a" * 32)
    try:
        for event in _chain():
            await log.append(EventEnvelope(**event))
        audit = await audit_source_assurance(
            log,
            repository_root=ROOT,
            capabilities={},
            provider_effect_window_started_at="2026-10-19T00:00:00Z",
            provider_effect_window_ended_at="2026-10-20T12:00:00Z",
        )
        effects = audit["provider_effects"]
        assert audit["state"] == effects["state"] == "healthy"
        assert effects["counts"]["provider_called_true"] == 1
        assert effects["observation_window"]["counts"]["provider_called_true"] == 0
        assert effects["observation_window"]["state"] == "healthy"
        assert "Synthetic private response" not in str(audit)
        assert "member-audit@example.invalid" not in str(audit)
        assert audit["by_event_type"]["cos.response.attempted"]["accounted"] == 1
        bounded = await audit_source_assurance(
            log,
            repository_root=ROOT,
            capabilities={},
            provider_effect_window_started_at="2026-10-20T12:00:04Z",
        )
        assert (
            bounded["provider_effects"]["observation_window"]["counts"][
                "provider_called_true"
            ]
            == 1
        )
        assert (
            bounded["provider_effects"]["observation_window"]["counts"][
                "approval_correlation_missing"
            ]
            == 0
        )
    finally:
        await log.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("placement", ["before", "between", "after"])
async def test_default_recorded_precision_preserves_same_second_effect_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, placement: str
) -> None:
    # Exercise the real append default and auditor, with deterministic local
    # ingestion times. Occurrence remains coarse as in the production reply
    # route; it cannot establish which side of a subsecond boundary was recorded.
    origin = datetime(2026, 10, 20, 12, 0, 10, tzinfo=timezone.utc)
    offset = {"before": 200_000, "between": 500_000, "after": 800_000}[placement]
    stamps = [origin + timedelta(microseconds=offset + index) for index in range(6)]
    clock = iter(stamps)

    class RecordingClock:
        @staticmethod
        def now(zone):
            assert zone == timezone.utc
            return next(clock)

    monkeypatch.setattr(event_log_module, "datetime", RecordingClock)
    log = EventLog(tmp_path / "same-second-effects.db", b"s" * 32)
    try:
        for index, event in enumerate(_chain()):
            event.pop("recorded_at")
            event["occurred_at"] = origin.strftime("%Y-%m-%dT%H:%M:%SZ")
            event_id = await log.append(EventEnvelope(**event))
            retained = await log.get(event_id)
            assert retained is not None
            assert retained["recorded_at"] == stamps[index].strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
        for start, end, included in (
            (
                origin - timedelta(seconds=1),
                origin + timedelta(microseconds=300_000),
                placement == "before",
            ),
            (
                origin + timedelta(microseconds=700_000),
                origin + timedelta(seconds=1),
                placement == "after",
            ),
        ):
            audit = await audit_source_assurance(
                log,
                repository_root=ROOT,
                capabilities={},
                provider_effect_window_started_at=start.isoformat(),
                provider_effect_window_ended_at=end.isoformat(),
            )
            effects = audit["provider_effects"]
            assert audit["state"] == effects["state"] == "healthy"
            assert effects["counts"]["provider_called_true"] == 1
            window = effects["observation_window"]
            assert window["state"] == ("degraded" if included else "healthy")
            assert window["counts"]["write_requests"] == int(included)
            assert window["counts"]["provider_called_true"] == int(included)
    finally:
        await log.close()
