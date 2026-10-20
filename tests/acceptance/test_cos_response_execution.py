"""Exact CoS reply approval, real adapter guard, replay and privacy composition."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import time
from typing import Any

from fastapi.testclient import TestClient
import httpx
import pytest

from adminme.lib.adapters import OutboundMessage
from adminme.daemons.adapter_supervisor import _ManagedAdapter
from adminme.openclaw_approvals import sign_approval_receipt
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from adminme.source_assurance import audit_source_assurance

ROOT = Path(__file__).resolve().parents[2]
A = "member-response-a"  # fixture:tenant_data:ok
B = "member-response-b"  # fixture:tenant_data:ok
ACCOUNT = "cos-response@example.invalid"
RECIPIENT = "member-response@example.invalid"
SECRET = "cos-response-fixture-secret-at-least-32"
PHONE = "+12025550101"  # fixture:tenant_data:ok
IMESSAGE_ACCOUNT = "cos-imessage-fixture"
HEADERS = {"Tailscale-User-Login": "response-a.example.test"}
AUTH = {"Authorization": f"Bearer {SECRET}"}
BASE = "/api/member/v1/cos/responses"
EXECUTE = "/api/openclaw/v1/tools/cos-response"
POLICY = "/api/member/v1/settings/cos-replies"


def _settings(instance: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance,
        encryption_key=b"c" * 32,
        repository_root=ROOT,
        member_identities={
            "response-a.example.test": A,
            "response-b.example.test": B,
        },
        member_roles={A: "principal", B: "principal"},
        fixture_member_id=A,
        deterministic_openclaw_fixture=True,
        openclaw_enabled=True,
        openclaw_shared_secret=SECRET,
        openclaw_actor_identities={
            "discord|fixture-account|fixture-actor": A,
            f"imessage|{IMESSAGE_ACCOUNT}|{PHONE}": A,
        },
        adapter_config={
            "adapter_supervisor_port": 0,
            "adapters": [],
            "service_identities": {
                "principals": [
                    {
                        "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
                        "kind": "chief_of_staff",
                        "party_id": "service-party:chief-of-staff",
                        "display_name": "Fixture CoS",
                    }
                ],
                "channels": [
                    {
                        "channel_id": "channel:cos:response",
                        "display_name": "Fixture email",
                        "provider": "gmail",
                        "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
                        "state": "healthy",
                        "enrolled": True,
                        "observation_active": False,
                        "sending_enabled": True,
                        "provider_account_reference": "provider-account:"
                        + hashlib.sha256(ACCOUNT.encode()).hexdigest(),
                        "routes": [],
                    },
                    {
                        "channel_id": "channel:cos:imessage",
                        "display_name": "Fixture iMessage",
                        "provider": "apple_imessage",
                        "openclaw_channel_id": "imessage",
                        "intake_mode": "member_submission",
                        "response_transport_contract": "cos-imessage-guard-v1",
                        "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
                        "state": "healthy",
                        "enrolled": True,
                        "observation_active": False,
                        "sending_enabled": True,
                        "provider_account_reference": "provider-account:"
                        + hashlib.sha256(IMESSAGE_ACCOUNT.encode()).hexdigest(),
                        "routes": [],
                    },
                ],
            },
        },
    )


def _prepare(client: TestClient) -> None:
    result = client.put(
        POLICY,
        headers=HEADERS,
        json={
            "request_id": "response-policy-initial",
            "expected_revision": 0,
            "entries": [
                {"member_id": A, "channel": "email", "address": RECIPIENT},
                {"member_id": A, "channel": "imessage", "address": PHONE},
            ],
        },
    )
    assert result.status_code == 202, result.text
    grant = client.post(
        "/api/member/v1/cos/delegations",
        headers=HEADERS,
        json={
            "request_id": "response-cos-delegation",
            "context_scope": "member_private",
            "permissions": ["read_context", "prepare_external_actions"],
        },
    )
    assert grant.status_code in {200, 201}, grant.text


def _approve(client: TestClient, key: str = "response-exact-approval") -> str:
    response = client.post(
        BASE + "/approvals",
        headers=HEADERS,
        json={
            "request_id": key,
            "channel": "email",
            "service_channel_id": "channel:cos:response",
            "provider_account_id": ACCOUNT,
            "recipient": RECIPIENT,
            "subject": "Fixture response",
            "body": "Your selected obligation is ready for review.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["approval_event_id"]


def _execution(
    client: TestClient, approval: str, *, allow: bool = True
) -> dict[str, Any]:
    params = {"request_id": "response-execution-once", "approval_event_id": approval}
    receipt = f"response-receipt-{approval}"
    if allow:
        issued = int(time.time() * 1000)
        fingerprint = hashlib.sha256(
            json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        request = {
            "receipt_id": receipt,
            "tool_name": "administrateme_cos_response",
            "request_fingerprint": fingerprint,
            "issued_at_ms": issued,
            "expires_at_ms": issued + 120_000,
            "openclaw_version": OPENCLAW_VERSION,
            "openclaw_commit": OPENCLAW_COMMIT,
        }
        signed = client.post(
            "/api/openclaw/v1/approvals/resolve",
            headers=AUTH,
            json={
                **request,
                "decision": "allow-once",
                "signature": sign_approval_receipt(SECRET, **request),
            },
        )
        assert signed.status_code == 200, signed.text
    return {
        **params,
        **({"openclaw_approval_receipt_id": receipt} if allow else {}),
        "provenance": {
            "actor_id": "fixture-actor",
            "channel_id": "discord",
            "account_id": "fixture-account",
            "correlation_id": "response-execution",
            "call_kind": "tool",
            "openclaw_version": OPENCLAW_VERSION,
            "openclaw_commit": OPENCLAW_COMMIT,
        },
    }


class FakeGmailService:
    def __init__(self, *, ambiguous: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.ambiguous = ambiguous

    def users(self) -> "FakeGmailService":
        return self

    def messages(self) -> "FakeGmailService":
        return self

    def send(self, **values: Any) -> "FakeGmailService":
        self.calls.append(values)
        return self

    def execute(self) -> dict[str, str]:
        if self.ambiguous:
            raise TimeoutError("credential-free provider accepted before timeout")
        return {"id": "fixture-delivery", "threadId": "fixture-thread"}


def _install_gmail(runtime: Any, service: FakeGmailService) -> Any:
    spec = importlib.util.spec_from_file_location(
        "cos_response_gmail_fixture", ROOT / "packs/adapters/messaging/gmail/adapter.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    adapter = module.GmailAdapter(
        config={
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "provider_account_id": ACCOUNT,
            "user_email": ACCOUNT,
            "enable_writes": True,
            "channel_id": "channel:cos:response",
        },
        observation_session=runtime.adapters._ctx_factory().observation_session,
        observation_manager=runtime.observation,
        observation_event_log=runtime.events,
    )
    adapter._service = service
    runtime.adapters._managed["fixture-cos-gmail"] = _ManagedAdapter(adapter=adapter)
    return adapter


async def _events(runtime: Any, types: list[str]) -> list[dict[str, Any]]:
    return [event async for event in runtime.event_log.read_since(types=types)]


async def _provider_assurance(runtime: Any) -> dict[str, Any]:
    report = await audit_source_assurance(
        runtime.event_log, repository_root=ROOT, capabilities={}
    )
    return report["provider_effects"]


def test_response_observation_independent_approval_and_adapter_bypass(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        _prepare(client)
        runtime = client.app.state.runtime
        service = FakeGmailService()
        adapter = _install_gmail(runtime, service)
        approval = _approve(client)
        denied = client.post(
            EXECUTE, headers=AUTH, json=_execution(client, approval, allow=False)
        )
        assert denied.status_code == 403, denied.text
        approval = _approve(client, "response-after-denied-approval")
        execution = _execution(client, approval)
        sent = client.post(EXECUTE, headers=AUTH, json=execution)
        assert sent.status_code == 200, sent.text
        assert sent.json()["status"] == "suppressed"
        assert client.post(EXECUTE, headers=AUTH, json=execution).json() == sent.json()
        with pytest.raises(PermissionError, match="governed response permit"):
            client.portal.call(
                adapter.send,
                OutboundMessage(
                    channel="gmail",
                    recipient=RECIPIENT,
                    body="bypass",
                    metadata={"subject": ""},
                ),
            )
        assert service.calls == []
        effects = client.portal.call(_events, runtime, ["external.sent"])
        assert effects == []
        terminals = client.portal.call(_events, runtime, ["cos.response.result"])
        assert [event["payload"]["status"] for event in terminals] == [
            "denied",
            "suppressed",
        ]
        assurance = client.portal.call(_provider_assurance, runtime)
        assert assurance["state"] == "healthy", assurance
        assert assurance["zero_effect_state"] == "healthy", assurance
        assert assurance["counts"]["provider_called_true"] == 0
        assert assurance["counts"]["provider_called_false"] == 2
        assert assurance["counts"]["suppressed_true"] == 1
        suppressed = client.portal.call(_events, runtime, ["observation.suppressed"])
        assert len(suppressed) == 1
        assert suppressed[0]["actor_identity"] == COS_SERVICE_PRINCIPAL_ID
        assert suppressed[0]["owner_scope"] == f"private:{A}"
        assert suppressed[0]["visibility_scope"] == f"private:{A}"


def test_response_rejects_other_member_group_attachment_and_revocation(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        _prepare(client)
        approval = _approve(client)
        wrong_member = client.post(
            BASE + "/approvals",
            headers={
                "Tailscale-User-Login": "response-b.example.test",
            },
            json={
                "request_id": "wrong-member-approval",
                "channel": "email",
                "service_channel_id": "channel:cos:response",
                "provider_account_id": ACCOUNT,
                "recipient": RECIPIENT,
                "subject": "",
                "body": "private",
            },
        )
        assert wrong_member.status_code == 403
        for extra in (
            {"recipient": RECIPIENT + ",other@example.invalid"},
            {"attachments": ["attachment-fixture"]},
            {"cc": ["other@example.invalid"]},
            {"subject": "Header\nBcc: other@example.invalid"},
        ):
            invalid = client.post(
                BASE + "/approvals",
                headers=HEADERS,
                json={
                    "request_id": "invalid-scope-approval",
                    "channel": "email",
                    "service_channel_id": "channel:cos:response",
                    "provider_account_id": ACCOUNT,
                    "recipient": RECIPIENT,
                    "subject": "",
                    "body": "private",
                    **extra,
                },
            )
            assert invalid.status_code == 422, invalid.text
        revoked = client.put(
            POLICY,
            headers=HEADERS,
            json={
                "request_id": "response-policy-revoked",
                "expected_revision": 1,
                "entries": [],
            },
        )
        assert revoked.status_code == 202
        denied = client.post(EXECUTE, headers=AUTH, json=_execution(client, approval))
        assert denied.status_code == 403, denied.text
        assert (
            client.portal.call(
                _events,
                client.app.state.runtime,
                ["cos.response.attempted", "external.sent"],
            )
            == []
        )


@pytest.mark.parametrize("ambiguous", [False, True])
def test_response_exact_delivery_or_ambiguity_never_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ambiguous: bool,
) -> None:
    # Operational receipt validation has its own contract tests. This boundary
    # fixture admits activation so the production API and actual Gmail adapter
    # can prove exactly-once contact, final permit, and ambiguity handling.
    async def activation(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"authorized": True}

    monkeypatch.setattr(
        "adminme.products.openclaw.cos_responses.require_cos_response_activation",
        activation,
    )
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    with TestClient(
        create_application(settings), raise_server_exceptions=False
    ) as client:
        _prepare(client)
        runtime = client.app.state.runtime
        service = FakeGmailService(ambiguous=ambiguous)
        _install_gmail(runtime, service)
        client.portal.call(
            runtime.observation.disable, A, "isolated fixture activation"
        )
        approval = _approve(client)
        execution = _execution(client, approval)
        first = client.post(EXECUTE, headers=AUTH, json=execution)
        assert first.status_code == (500 if ambiguous else 200), first.text
        second = client.post(EXECUTE, headers=AUTH, json=execution)
        assert second.status_code == 200, second.text
        assert second.json()["status"] == ("unknown" if ambiguous else "sent")
        assert second.json()["automatic_retry_allowed"] is False
        assert len(service.calls) == 1
        assurance = client.portal.call(_provider_assurance, runtime)
        assert assurance["state"] == ("degraded" if ambiguous else "healthy"), assurance
        assert assurance["counts"]["provider_called_true"] == (0 if ambiguous else 1)
        assert assurance["counts"]["provider_called_unknown"] == (1 if ambiguous else 0)
        outbound = client.portal.call(_events, runtime, ["external.sent"])
        assert len(outbound) == (0 if ambiguous else 1)
        if outbound:
            assert outbound[0]["actor_identity"] == COS_SERVICE_PRINCIPAL_ID
            assert outbound[0]["owner_scope"] == f"private:{A}"
            assert outbound[0]["visibility_scope"] == f"private:{A}"
    with TestClient(create_application(_settings(instance))) as client:
        repeated = client.post(EXECUTE, headers=AUTH, json=execution)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["status"] == ("unknown" if ambiguous else "sent")
        assert len(service.calls) == 1


@pytest.mark.parametrize("revoke_before_claim", [False, True])
def test_native_response_claim_is_single_use_and_rechecks_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    revoke_before_claim: bool,
) -> None:
    async def activation(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"authorized": True}

    monkeypatch.setattr(
        "adminme.products.openclaw.cos_responses.require_cos_response_activation",
        activation,
    )
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        _prepare(client)
        runtime = client.app.state.runtime
        client.portal.call(runtime.observation.disable, A, "isolated native fixture")
        approved = client.post(
            BASE + "/approvals",
            headers=HEADERS,
            json={
                "request_id": "native-exact-approval",
                "channel": "imessage",
                "service_channel_id": "channel:cos:imessage",
                "provider_account_id": IMESSAGE_ACCOUNT,
                "recipient": PHONE,
                "subject": "",
                "body": "Exact private fixture response.",
            },
        )
        assert approved.status_code == 201, approved.text
        approval = approved.json()["approval_event_id"]
        execution = client.post(
            EXECUTE, headers=AUTH, json=_execution(client, approval)
        )
        assert execution.status_code == 200, execution.text
        dispatch = execution.json()["transport_dispatch"]
        assert dispatch["account_id"] == IMESSAGE_ACCOUNT
        claim = {
            "account_id": IMESSAGE_ACCOUNT,
            "recipient": PHONE,
            "body": dispatch["body"],
        }
        endpoint = "/api/openclaw/v1/cos-responses"
        assert client.post(endpoint + "/claim", json=claim).status_code == 401
        for update in (
            {"account_id": "other-account"},
            {"recipient": "+12025550102"},  # fixture:tenant_data:ok
            {"body": "Changed private fixture response."},
        ):
            assert (
                client.post(
                    endpoint + "/claim", headers=AUTH, json={**claim, **update}
                ).status_code
                == 403
            )
        if revoke_before_claim:
            revoke = client.put(
                POLICY,
                headers=HEADERS,
                json={
                    "request_id": "revoke-before-native-contact",
                    "expected_revision": 1,
                    "entries": [],
                },
            )
            assert revoke.status_code == 202, revoke.text
            assert (
                client.post(endpoint + "/claim", headers=AUTH, json=claim).status_code
                == 403
            )
        else:
            claimed = client.post(endpoint + "/claim", headers=AUTH, json=claim)
            assert claimed.status_code == 200, claimed.text
            assert (
                client.post(endpoint + "/claim", headers=AUTH, json=claim).status_code
                == 403
            )
            terminal = client.post(
                endpoint + "/terminal",
                headers=AUTH,
                json={
                    "claim_id": claimed.json()["claim_id"],
                    "status": "sent",
                    "receipt_digest": "a" * 64,
                },
            )
            assert terminal.status_code == 200, terminal.text
            assert terminal.json()["status"] == "sent"
        result = client.post(
            endpoint + "/result",
            headers=AUTH,
            json={
                "provenance": _execution(client, approval, allow=False)["provenance"],
                "approval_event_id": approval,
            },
        )
        assert result.status_code == 200, result.text
        assert result.json()["status"] == ("denied" if revoke_before_claim else "sent")
        assert "transport_dispatch" not in result.json()
        effects = client.portal.call(_events, runtime, ["external.sent"])
        assert len(effects) == (0 if revoke_before_claim else 1)
        assurance = client.portal.call(_provider_assurance, runtime)
        assert assurance["state"] == "healthy", assurance
        assert assurance["counts"]["provider_called_true"] == (
            0 if revoke_before_claim else 1
        )
        assert (
            client.post(endpoint + "/claim", headers=AUTH, json=claim).status_code
            == 403
        )


def test_native_response_observation_has_complete_zero_effect_audit(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        _prepare(client)
        approved = client.post(
            BASE + "/approvals",
            headers=HEADERS,
            json={
                "request_id": "native-suppressed-approval",
                "channel": "imessage",
                "service_channel_id": "channel:cos:imessage",
                "provider_account_id": IMESSAGE_ACCOUNT,
                "recipient": PHONE,
                "subject": "",
                "body": "Exact observation fixture response.",
            },
        )
        assert approved.status_code == 201, approved.text
        approval = approved.json()["approval_event_id"]
        execution = client.post(
            EXECUTE, headers=AUTH, json=_execution(client, approval)
        )
        assert execution.status_code == 200, execution.text
        assert execution.json()["status"] == "suppressed"
        assert "transport_dispatch" not in execution.json()
        runtime = client.app.state.runtime
        assert client.portal.call(_events, runtime, ["external.sent"]) == []
        assurance = client.portal.call(_provider_assurance, runtime)
        assert assurance["state"] == "healthy", assurance
        assert assurance["zero_effect_state"] == "healthy", assurance
        assert assurance["counts"]["provider_called_true"] == 0
        assert assurance["counts"]["provider_called_false"] == 1
        assert assurance["counts"]["suppressed_true"] == 1


def test_native_expiry_cannot_retire_a_suspended_claim_then_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Coordinate the real claim route at its awaited final-check boundary."""
    from adminme.products.openclaw import cos_responses as responses

    async def activation(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"authorized": True}

    monkeypatch.setattr(responses, "require_cos_response_activation", activation)
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        _prepare(client)
        runtime = client.app.state.runtime
        client.portal.call(runtime.observation.disable, A, "isolated expiry fixture")
        approved = client.post(
            BASE + "/approvals",
            headers=HEADERS,
            json={
                "request_id": "native-expiry-race-approval",
                "channel": "imessage",
                "service_channel_id": "channel:cos:imessage",
                "provider_account_id": IMESSAGE_ACCOUNT,
                "recipient": PHONE,
                "body": "Exact expiring fixture response.",
            },
        )
        assert approved.status_code == 201, approved.text
        approval = approved.json()["approval_event_id"]
        execution = client.post(
            EXECUTE, headers=AUTH, json=_execution(client, approval)
        )
        assert execution.status_code == 200, execution.text
        dispatch = execution.json()["transport_dispatch"]

        async def coordinate() -> None:
            dispatch_id, admission = next(iter(responses._admissions(runtime).items()))
            admission["expiry_task"].cancel()
            await asyncio.gather(admission["expiry_task"], return_exceptions=True)
            entered, resume, expiring = (
                asyncio.Event(),
                asyncio.Event(),
                asyncio.Event(),
            )
            original_check = admission["final_check"]

            async def suspended_check(provider_effect: bool = False) -> None:
                await original_check(provider_effect)
                entered.set()
                await resume.wait()

            class ExpiryClock:
                def __getattr__(self, name: str) -> Any:
                    return getattr(asyncio, name)

                async def sleep(self, seconds: float) -> None:
                    assert seconds == 30
                    expiring.set()

            admission["final_check"] = suspended_check
            # Replace only this module's expiry wait, preserving the actual
            # claim route, policy checks, locks, audit writer and event loop.
            monkeypatch.setattr(responses, "asyncio", ExpiryClock())
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=client.app),
                base_url="http://fixture.test",
            ) as api:
                claim = asyncio.create_task(
                    api.post(
                        "/api/openclaw/v1/cos-responses/claim",
                        headers=AUTH,
                        json={
                            "account_id": IMESSAGE_ACCOUNT,
                            "recipient": PHONE,
                            "body": dispatch["body"],
                        },
                    )
                )
                await asyncio.wait_for(entered.wait(), 2)
                admission["expires_monotonic"] = 0
                expiry = asyncio.create_task(
                    responses._expire_native_admission(runtime, dispatch_id)
                )
                try:
                    await asyncio.wait_for(expiring.wait(), 2)
                    resume.set()
                    result = await asyncio.wait_for(claim, 5)
                    await asyncio.wait_for(expiry, 5)
                    assert result.status_code == 403, result.text
                    assert admission["provider_future"] is None
                    assert admission["outbound_task"] is None
                    assert responses._admissions(runtime) == {}
                finally:
                    resume.set()
                    for task in (claim, expiry, admission["outbound_task"]):
                        if task is not None and not task.done():
                            task.cancel()
                    await asyncio.gather(
                        *(
                            task
                            for task in (claim, expiry, admission["outbound_task"])
                            if task is not None
                        ),
                        return_exceptions=True,
                    )

        client.portal.call(coordinate)
        terminals = client.portal.call(_events, runtime, ["cos.response.result"])
        assert len(terminals) == 1
        assert terminals[0]["payload"]["status"] == "denied"
        assert terminals[0]["payload"]["provider_effect_count"] == 0
        assert client.portal.call(_events, runtime, ["external.sent"]) == []
        assert client.portal.call(_provider_assurance, runtime)["state"] == "healthy"
