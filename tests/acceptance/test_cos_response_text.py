"""Production approval and native admission behavior for exact CoS reply text."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.runtime.application import create_application
from tests.acceptance.test_cos_response_execution import (
    A,
    ACCOUNT,
    AUTH,
    BASE,
    EXECUTE,
    HEADERS,
    IMESSAGE_ACCOUNT,
    PHONE,
    RECIPIENT,
    _events,
    _execution,
    _prepare,
    _provider_assurance,
    _settings,
)


def test_transformed_imessage_text_is_refused_before_approval_email_unchanged(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        _prepare(client)
        text = "Bring **passport**\nand tickets."
        request = {
            "request_id": "formatted-response",
            "channel": "imessage",
            "service_channel_id": "channel:cos:imessage",
            "provider_account_id": IMESSAGE_ACCOUNT,
            "recipient": PHONE,
            "subject": "",
            "body": text,
        }
        refused = client.post(BASE + "/approvals", headers=HEADERS, json=request)
        assert refused.status_code == 422, refused.text
        assert "one plain-text line" in refused.json()["detail"]
        runtime = client.app.state.runtime
        assert client.portal.call(_events, runtime, ["cos.response.approved"]) == []
        email = client.post(
            BASE + "/approvals",
            headers=HEADERS,
            json={
                **request,
                "channel": "email",
                "service_channel_id": "channel:cos:response",
                "provider_account_id": ACCOUNT,
                "recipient": RECIPIENT,
            },
        )
        assert email.status_code == 201, email.text
        approvals = client.portal.call(_events, runtime, ["cos.response.approved"])
        assert len(approvals) == 1
        assert approvals[0]["payload"]["body"] == text
        assert client.portal.call(_events, runtime, ["external.sent"]) == []


@pytest.mark.parametrize("claimed", [False, True])
def test_native_result_proves_zero_effect_only_before_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, claimed: bool
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
        approval_response = client.post(
            BASE + "/approvals",
            headers=HEADERS,
            json={
                "request_id": "native-literal-response",
                "channel": "imessage",
                "service_channel_id": "channel:cos:imessage",
                "provider_account_id": IMESSAGE_ACCOUNT,
                "recipient": PHONE,
                "subject": "",
                "body": "Café at noon.  Bring tickets. 😀",
            },
        )
        assert approval_response.status_code == 201, approval_response.text
        approval = approval_response.json()["approval_event_id"]
        execution_request = _execution(client, approval)
        execution = client.post(EXECUTE, headers=AUTH, json=execution_request)
        assert execution.status_code == 200, execution.text
        dispatch = execution.json()["transport_dispatch"]
        claim = {
            "account_id": IMESSAGE_ACCOUNT,
            "recipient": PHONE,
            "body": dispatch["body"],
        }
        endpoint = "/api/openclaw/v1/cos-responses"
        if claimed:
            admitted = client.post(endpoint + "/claim", headers=AUTH, json=claim)
            assert admitted.status_code == 200, admitted.text
        result_request = {
            "provenance": execution_request["provenance"],
            "approval_event_id": approval,
        }
        result = client.post(endpoint + "/result", headers=AUTH, json=result_request)
        assert result.status_code == 200, result.text
        expected = "unknown" if claimed else "denied"
        assert result.json()["status"] == expected
        assert result.json()["provider_effect_count"] == (None if claimed else 0)
        assert result.json()["automatic_retry_allowed"] is False
        if not claimed:
            assert result.json()["reason"] == "native_dispatch_not_claimed"
            assert runtime._cos_response_admissions == {}
        assert (
            client.post(endpoint + "/claim", headers=AUTH, json=claim).status_code
            == 403
        )
        repeated = client.post(EXECUTE, headers=AUTH, json=execution_request)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["status"] == expected
        assert "transport_dispatch" not in repeated.json()
        assert client.portal.call(_events, runtime, ["external.sent"]) == []
        assurance = client.portal.call(_provider_assurance, runtime)
        assert assurance["state"] == ("degraded" if claimed else "healthy"), assurance
        assert assurance["counts"]["provider_called_true"] == 0
        assert assurance["counts"]["provider_called_unknown"] == int(claimed)
