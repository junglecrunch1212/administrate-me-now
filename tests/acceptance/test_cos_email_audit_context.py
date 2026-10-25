"""Retained email evidence reaches the actual source audit/readiness boundary.

Only existing credential-free model/provider fixtures act; no external account,
message or installed runtime is used. These cases do not grant deployment authority.
"""

from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import time
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.runtime.application import create_application
from adminme.source_assurance import audit_source_assurance
from tests.acceptance.test_authenticated_conversation_reply import (
    REPLY,
    configured,
    reply_body,
)
from tests.acceptance.test_cos_response_execution import FakeGmailService, ROOT
from tests.acceptance.test_requested_email_action import (
    AUTH,
    BODY,
    EXECUTE,
    RECIPIENT,
    TEXT,
    confirm,
    model_boundary,
    policy_receipt,
    prepare,
    source_for,
)


@pytest.fixture(autouse=True)
def isolated_activation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")

    async def activation(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"authorized": True}

    # Same unchanged operational grant boundary as the send acceptance fixture.
    monkeypatch.setattr(
        "adminme.products.openclaw.cos_responses.require_cos_response_activation",
        activation,
    )


def published_assurance(client: TestClient) -> dict[str, Any]:
    """Exercise real async hydration/cache and the HTTP readiness publication."""
    runtime = client.app.state.runtime
    original = runtime.settings
    runtime.settings = replace(original, deployment_evidence_required=True)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            report = client.get("/api/system/ready").json()
            assurance = report["capabilities"]["policy:source-assurance"]["details"]
            freshness = assurance["freshness"]
            if freshness["revision_current"] and not freshness["refresh_in_progress"]:
                dimension = report["deployment_readiness"]["dimensions"][
                    "observation_effects"
                ]
                # Unrelated deployment prerequisites are absent in this lab;
                # inspect the real effect-integrity dimension, not a fake 200.
                assert dimension["evidence"]["integrity_state"] == "healthy", dimension
                assert dimension["evidence"]["malformed_request"] == 0, dimension
                assert dimension["evidence"]["approval_correlation_missing"] == 0
                return assurance
            time.sleep(0.01)
        raise AssertionError(f"Source audit did not settle: {assurance['freshness']}")
    finally:
        runtime.settings = original


def assert_retained_effect(assurance: dict[str, Any]) -> None:
    effects = assurance["provider_effects"]
    assert effects["state"] == "healthy", effects
    assert effects["counts"]["provider_called_true"] == 1
    assert effects["counts"]["malformed_request"] == 0
    assert effects["counts"]["approval_correlation_missing"] == 0
    assert effects["counts"]["missing_terminal"] == 0
    assert effects["zero_effect_state"] == "nonzero"
    serialized = json.dumps(assurance)
    assert all(private not in serialized for private in (TEXT, BODY, RECIPIENT))


def send_once(client: TestClient, service: Any, channel: str) -> dict[str, Any]:
    source = source_for(
        client, "gmail" if channel == "reply" else channel, "audit-email"
    )
    if channel == "reply":
        retained = client.portal.call(
            client.app.state.runtime.event_log.get, source["source_message_event_id"]
        )
        response = client.post(REPLY, headers=AUTH, json=reply_body(retained))
        approved = response.json()
    else:
        approved = confirm(client, source)
        response = client.post(
            EXECUTE, headers=AUTH, json=policy_receipt(client, source, approved)
        )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "sent"
    assert len(service.calls) == 1
    return approved


@pytest.mark.parametrize("channel", ["imessage", "portal", "gmail", "reply"])
def test_real_source_audit_and_readiness_retain_email_chain_after_restart(
    tmp_path: Path, channel: str
) -> None:
    config = configured(tmp_path)
    service = FakeGmailService()
    with TestClient(
        create_application(config), base_url="https://central-fixture.ts.net"
    ) as client:
        prepare(client, service)
        model, _ = model_boundary(client)
        try:
            send_once(client, service, channel)
            assert_retained_effect(published_assurance(client))
        finally:
            client.portal.call(model.aclose)
    # Reopen the encrypted retained history without initiating another action.
    # No provider adapter or model is needed to explain the old successful send.
    config.adapter_config["adapters"] = []
    with TestClient(
        create_application(config), base_url="https://central-fixture.ts.net"
    ) as client:
        assert_retained_effect(published_assurance(client))
        assert len(service.calls) == 1


def test_exact_references_hydrate_without_weakening_retained_email_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        service = FakeGmailService()
        prepare(client, service, other=True)
        model, _ = model_boundary(client)
        try:
            approved = send_once(client, service, "portal")
            unrelated = source_for(
                client, "portal", "unrelated-private-instruction", other=True
            )
            source_id = approved["request_authorization"]["source_event_id"]
            grounding_id = approved["request_authorization"]["grounding_event_id"]
            watermark = client.portal.call(runtime.event_log.latest_event_id)
            original_get = runtime.event_log.get
            fetched: list[str] = []

            async def tracked_get(identifier: str) -> dict[str, Any] | None:
                fetched.append(identifier)
                return await original_get(identifier)

            async def audit() -> dict[str, Any]:
                return await audit_source_assurance(
                    runtime.event_log,
                    repository_root=ROOT,
                    capabilities={},
                    through_event_id=watermark,
                )

            with monkeypatch.context() as patch:
                patch.setattr(runtime.event_log, "get", tracked_get)
                assert_retained_effect(client.portal.call(audit))
            assert source_id in fetched and grounding_id in fetched
            assert unrelated["source_message_event_id"] not in fetched

            # A stored reference makes context eligible to inspect, not trusted.
            # Exercise the real audit's full-envelope boundary for missing and
            # malformed evidence, without modifying append-only retained history.
            for target, field, value in (
                (source_id, "owner_scope", "private:another-member"),
                (source_id, "actor_identity", "another-member"),
                (grounding_id, "tenant_id", "another-tenant"),
                (grounding_id, "payload.source_fingerprint", "0" * 64),
                (grounding_id, "payload.action_fingerprint", "0" * 64),
                (grounding_id, "payload.evidence", []),
                (grounding_id, "payload.expires_at", "2000-01-01T00:00:00Z"),
            ):

                async def altered_get(identifier: str) -> dict[str, Any] | None:
                    event = await original_get(identifier)
                    if identifier == target:
                        assert event is not None
                        event = copy.deepcopy(event)
                        if field.startswith("payload."):
                            event["payload"][field.removeprefix("payload.")] = value
                        else:
                            event[field] = value
                    return event

                with monkeypatch.context() as patch:
                    patch.setattr(runtime.event_log, "get", altered_get)
                    effects = client.portal.call(audit)["provider_effects"]
                    assert effects["state"] == "degraded", (target, field, effects)
                    assert effects["counts"]["malformed_request"] > 0
                    assert effects["counts"]["approval_correlation_missing"] > 0
            for missing in (source_id, grounding_id):

                async def missing_get(identifier: str) -> dict[str, Any] | None:
                    return (
                        None
                        if identifier == missing
                        else await original_get(identifier)
                    )

                with monkeypatch.context() as patch:
                    patch.setattr(runtime.event_log, "get", missing_get)
                    with pytest.raises(
                        RuntimeError, match="audit envelope is unavailable"
                    ):
                        client.portal.call(audit)
            assert len(service.calls) == 1
        finally:
            client.portal.call(model.aclose)
