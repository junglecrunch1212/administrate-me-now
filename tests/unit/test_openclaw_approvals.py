"""Independent OpenClaw allow-once receipts are exact, expiring, and single-use."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from adminme.openclaw_approvals import (
    ApprovalReceiptError,
    OpenClawApprovalStore,
    sign_approval_receipt,
)


SECRET = "fixture-secret-at-least-24-characters"
VERSION = "2026.10.10"
COMMIT = "7dc3350cb9f08fcf9a0af5c4845a20d5b2f1a8be"
FINGERPRINT = "a" * 64


@pytest.mark.parametrize("request_kind", ["provenance", "approval"])
@pytest.mark.parametrize(
    ("version", "commit", "accepted"),
    [
        (VERSION, COMMIT, True),
        ("2026.7.2", COMMIT, False),
        (VERSION, "ce67ffb70e83e5ebb56ea02e814e577153f84f61", False),
        ("2026.7.2", "ce67ffb70e83e5ebb56ea02e814e577153f84f61", False),
    ],
)
def test_current_request_models_enforce_exact_native_tuple(
    request_kind: str, version: str, commit: str, accepted: bool
) -> None:
    from pydantic import ValidationError

    from adminme.products.openclaw.main import ApprovalResolutionRequest, Provenance

    model: type[Provenance] | type[ApprovalResolutionRequest]
    payload: dict[str, Any]
    if request_kind == "provenance":
        model = Provenance
        payload = {
            "actor_id": "fixture-actor",
            "channel_id": "fixture",
            "correlation_id": "fixture-correlation",
            "call_kind": "tool",
        }
    else:
        model = ApprovalResolutionRequest
        payload = {
            "receipt_id": "fixture-receipt",
            "tool_name": "administrateme_review",
            "request_fingerprint": FINGERPRINT,
            "issued_at_ms": 1_000_000,
            "expires_at_ms": 1_120_000,
            "decision": "allow-once",
            "signature": "a" * 64,
        }
    payload.update(openclaw_version=version, openclaw_commit=commit)
    if accepted:
        parsed = model.model_validate(payload)
        assert (parsed.openclaw_version, parsed.openclaw_commit) == (VERSION, COMMIT)
    else:
        with pytest.raises(ValidationError) as failure:
            model.model_validate(payload)
        assert {tuple(row["loc"]) for row in failure.value.errors()} == {
            (key,)
            for key, observed, expected in (
                ("openclaw_version", version, VERSION),
                ("openclaw_commit", commit, COMMIT),
            )
            if observed != expected
        }


def _resolve(
    store: OpenClawApprovalStore,
    *,
    receipt_id: str = "receipt-a",
    tool_name: str = "administrateme_execute_pending_provider_action",
    fingerprint: str = FINGERPRINT,
    issued_at_ms: int = 1_000_000,
    expires_at_ms: int = 1_120_000,
    now_ms: int = 1_000_001,
) -> None:
    signature = sign_approval_receipt(
        SECRET,
        receipt_id=receipt_id,
        tool_name=tool_name,
        request_fingerprint=fingerprint,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
        openclaw_version=VERSION,
        openclaw_commit=COMMIT,
    )
    store.resolve(
        secret=SECRET,
        receipt_id=receipt_id,
        tool_name=tool_name,
        request_fingerprint=fingerprint,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
        openclaw_version=VERSION,
        openclaw_commit=COMMIT,
        signature=signature,
        now_ms=now_ms,
    )


def test_receipt_is_content_bound_and_consumed_exactly_once(tmp_path: Path) -> None:
    store = OpenClawApprovalStore(tmp_path / "approvals.sqlite3")
    try:
        _resolve(store)
        with pytest.raises(ApprovalReceiptError, match="another tool"):
            store.consume(
                receipt_id="receipt-a",
                tool_name="administrateme_review",
                request_fingerprint=FINGERPRINT,
                member_id="member-a",
                now_ms=1_000_002,
            )
        with pytest.raises(ApprovalReceiptError, match="content changed"):
            store.consume(
                receipt_id="receipt-a",
                tool_name="administrateme_execute_pending_provider_action",
                request_fingerprint="b" * 64,
                member_id="member-a",
                now_ms=1_000_002,
            )
        consumed = store.consume(
            receipt_id="receipt-a",
            tool_name="administrateme_execute_pending_provider_action",
            request_fingerprint=FINGERPRINT,
            member_id="member-a",
            now_ms=1_000_002,
        )
        assert consumed.consumed_by_member_id == "member-a"
        with pytest.raises(ApprovalReceiptError, match="already consumed"):
            store.consume(
                receipt_id="receipt-a",
                tool_name="administrateme_execute_pending_provider_action",
                request_fingerprint=FINGERPRINT,
                member_id="member-a",
                now_ms=1_000_003,
            )
        recovered = store.consume(
            receipt_id="receipt-a",
            tool_name="administrateme_execute_pending_provider_action",
            request_fingerprint=FINGERPRINT,
            member_id="member-a",
            now_ms=1_000_003,
            allow_same_binding_recovery=True,
        )
        assert recovered.consumed_at_ms == consumed.consumed_at_ms
        with pytest.raises(ApprovalReceiptError, match="already consumed"):
            store.consume(
                receipt_id="receipt-a",
                tool_name="administrateme_execute_pending_provider_action",
                request_fingerprint=FINGERPRINT,
                member_id="member-b",
                now_ms=1_000_003,
                allow_same_binding_recovery=True,
            )
    finally:
        store.close()


def test_forged_stale_and_rebound_receipts_are_rejected(tmp_path: Path) -> None:
    store = OpenClawApprovalStore(tmp_path / "approvals.sqlite3")
    try:
        with pytest.raises(ApprovalReceiptError, match="signature"):
            store.resolve(
                secret=SECRET,
                receipt_id="forged",
                tool_name="administrateme_review",
                request_fingerprint=FINGERPRINT,
                issued_at_ms=1_000_000,
                expires_at_ms=1_120_000,
                openclaw_version=VERSION,
                openclaw_commit=COMMIT,
                signature="0" * 64,
                now_ms=1_000_001,
            )
        with pytest.raises(ApprovalReceiptError, match="valid window"):
            _resolve(
                store,
                receipt_id="expired",
                issued_at_ms=1_000_000,
                expires_at_ms=1_010_000,
                now_ms=1_010_000,
            )
        _resolve(store, receipt_id="same-id")
        with pytest.raises(ApprovalReceiptError, match="different content"):
            _resolve(store, receipt_id="same-id", fingerprint="c" * 64)
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("recovered", [False, True])
async def test_audit_retains_exact_first_consumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recovered: bool
) -> None:
    from adminme.lib.session import Session
    from adminme.products.openclaw import main

    store = OpenClawApprovalStore(tmp_path / "approvals.sqlite3")
    captured: list[dict[str, Any]] = []
    provenance = main.Provenance(
        actor_id="fixture-actor",
        channel_id="fixture",
        correlation_id="fixture-correlation",
        call_kind="tool",
        openclaw_version=VERSION,
        openclaw_commit=COMMIT,
    )
    body = main.ExecutePendingProviderActionRequest(
        provenance=provenance,
        domain_approval_event_id="domain-a",
        openclaw_approval_receipt_id="receipt-a",
    )
    fingerprint = main._fingerprint({"domain_approval_event_id": "domain-a"})
    session = Session(
        tenant_id="tenant-a",
        auth_member_id="member-a",
        auth_role="principal",
        view_member_id="member-a",
        view_role="principal",
        dm_scope="per_channel_peer",
        source="member_portal",
    )

    async def append(*args: Any, **kwargs: Any) -> tuple[str, bool]:
        captured.append(kwargs["payload"])
        return "native-approval-a", False

    def consume(**kwargs: Any) -> Any:
        return store.consume(now_ms=1_000_900, **kwargs)

    monkeypatch.setattr(main, "_append", append)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                runtime=SimpleNamespace(
                    openclaw_approval_store=SimpleNamespace(
                        consume=consume, get=store.get
                    )
                )
            )
        )
    )
    try:
        _resolve(
            store,
            fingerprint=fingerprint,
            issued_at_ms=1_000_123,
            now_ms=1_000_124,
        )
        if recovered:
            store.consume(
                receipt_id="receipt-a",
                tool_name="administrateme_execute_pending_provider_action",
                request_fingerprint=fingerprint,
                member_id="member-a",
                now_ms=1_000_500,
            )
        result = await main._consume_openclaw_approval(
            request,
            session,
            provenance,
            body,
            tool_name="administrateme_execute_pending_provider_action",
            action="create_note",
            causation_id="domain-a",
        )
        assert result == "native-approval-a"
        assert len(captured) == 1
        assert captured[0]["issued_at"] == "1970-01-01T00:16:40.123000+00:00"
        assert captured[0]["consumed_at"] == (
            "1970-01-01T00:16:40.500000+00:00"
            if recovered
            else "1970-01-01T00:16:40.900000+00:00"
        )
    finally:
        store.close()


def test_machine_policy_receipts_cannot_claim_human_approval_and_survive_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "approvals.sqlite3"
    store = OpenClawApprovalStore(path)
    authorization = {
        "source_event_id": "source-a",
        "request_authorization_event_id": "grounded-a",
        "domain_approval_event_id": "domain-a",
        "action_fingerprint": "b" * 64,
        "rule_id": "current-human-request.apple-reminder-create.v1",
    }
    values = dict(
        receipt_id="ocreq_policy-a",
        tool_name="administrateme_execute_pending_provider_action",
        request_fingerprint=FINGERPRINT,
        issued_at_ms=1_000_000,
        expires_at_ms=1_120_000,
        openclaw_version=VERSION,
        openclaw_commit=COMMIT,
    )
    signature = sign_approval_receipt(SECRET, **values, authorization=authorization)
    try:
        with pytest.raises(ApprovalReceiptError, match="kind"):
            store.resolve(
                secret=SECRET, **values, signature=signature, now_ms=1_000_001
            )
        with pytest.raises(ApprovalReceiptError, match="signature"):
            store.resolve(
                secret=SECRET,
                **values,
                authorization={**authorization, "source_event_id": "source-b"},
                signature=signature,
                now_ms=1_000_001,
            )
        store.resolve(
            secret=SECRET,
            **values,
            authorization=authorization,
            signature=signature,
            now_ms=1_000_001,
        )
        _resolve(
            store
        )  # legacy rows retain their original shape and independent meaning
    finally:
        store.close()
    restored = OpenClawApprovalStore(path)
    try:
        assert restored.get("receipt-a").authorization is None
        assert restored.get("ocreq_policy-a").authorization == authorization
        result = restored.consume(
            receipt_id="ocreq_policy-a",
            tool_name=values["tool_name"],
            request_fingerprint=FINGERPRINT,
            member_id="member-a",
            now_ms=1_000_002,
        )
        assert result.authorization == authorization
        with pytest.raises(ApprovalReceiptError, match="consumed"):
            restored.consume(
                receipt_id="ocreq_policy-a",
                tool_name=values["tool_name"],
                request_fingerprint=FINGERPRINT,
                member_id="member-b",
                now_ms=1_000_003,
                allow_same_binding_recovery=True,
            )
    finally:
        restored.close()


@pytest.mark.parametrize(
    "tool,rule,allowed",
    [
        (
            "administrateme_cos_response",
            "current-human-request.cos-email-to-requester.v1",
            True,
        ),
        (
            "administrateme_execute_pending_provider_action",
            "current-human-request.cos-email-to-requester.v1",
            False,
        ),
        (
            "administrateme_cos_response",
            "current-human-request.apple-reminder-create.v1",
            False,
        ),
        (
            "administrateme_cos_response",
            "current-human-request.cos-reminder-create.v1",
            False,
        ),
    ],
)
def test_request_approval_rules_are_bound_to_their_exact_execution_tool(
    tmp_path: Path, tool: str, rule: str, allowed: bool
) -> None:
    authorization = {
        "source_event_id": "source-member-request",
        "request_authorization_event_id": "grounded-member-request",
        "domain_approval_event_id": "domain-member-request",
        "action_fingerprint": FINGERPRINT,
        "rule_id": rule,
    }
    signed = dict(
        receipt_id="ocreq_closed-rule-fixture",
        tool_name=tool,
        request_fingerprint=FINGERPRINT,
        issued_at_ms=1_000_000,
        expires_at_ms=1_120_000,
        openclaw_version=VERSION,
        openclaw_commit=COMMIT,
        authorization=authorization,
    )
    with_store = OpenClawApprovalStore(tmp_path / "approvals.sqlite3")
    try:
        if not allowed:
            with pytest.raises(ApprovalReceiptError, match="binding is invalid"):
                with_store.resolve(
                    secret=SECRET,
                    signature=sign_approval_receipt(SECRET, **signed),
                    now_ms=1_000_001,
                    **signed,
                )
        else:
            receipt = with_store.resolve(
                secret=SECRET,
                signature=sign_approval_receipt(SECRET, **signed),
                now_ms=1_000_001,
                **signed,
            )
            assert receipt.authorization == authorization
            consumed = with_store.consume(
                receipt_id=signed["receipt_id"],
                tool_name=tool,
                request_fingerprint=FINGERPRINT,
                member_id="member-fixture",
                now_ms=1_000_002,
            )
            assert consumed.consumed_by_member_id == "member-fixture"
    finally:
        with_store.close()
