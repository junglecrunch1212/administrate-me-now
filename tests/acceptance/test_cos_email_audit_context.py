"""Retained email evidence reaches the actual source audit/readiness boundary.

Only existing credential-free model/provider fixtures act; no external account,
message or installed runtime is used. These cases do not grant deployment authority.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from dataclasses import replace
import json
from pathlib import Path
import time
from typing import Any
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from adminme.adapters.cos_channel_acquisition import CONFIG_KEY as COS_ACQUISITION
from adminme.adapters.disposable_test_acquisition import CONFIG_KEY as TEST_ACQUISITION
from adminme.historical_attribution import (
    _cos_response_owner_matches,
    audit_historical_attribution,
)
from adminme.provider_bindings import validate_historical_provider_bindings
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from adminme.runtime.application import create_application
from adminme.source_assurance import audit_source_assurance
from bootstrap import htr10_export
from bootstrap.candidate_readiness import ACTIVE_CONTRACT, record_htr10_review
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.secrets import secret_store
from tests.acceptance.test_authenticated_conversation_reply import (
    REPLY,
    configured,
    reply_body,
)
from tests.acceptance.test_cos_channel_acquisition import _configuration
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


def complete_sender_binding(client: TestClient, path: Path) -> None:
    """Complete the reviewed synthetic OAuth binding before strict readiness."""
    config = client.app.state.runtime.settings.adapter_config
    reviewed = _configuration(path)
    sender = config["adapters"][0]
    # The shared Gmail observation fixture identifies this exact source adapter.
    # Keep its installed sender, retained binding and direct-intake source aligned.
    sender["id"] = "fixture-cos"
    managed = client.app.state.runtime.adapters._managed
    managed["fixture-cos"] = managed.pop("fixture-conversation-gmail")
    sender["config"].update(
        binding_provenance="oauth-subject",
        ownership_attested=True,
        oauth_credentials_ref="keychain://fixture/cos-gmail",
        trusted_sender_routes=copy.deepcopy(
            reviewed["adapters"][1]["config"]["trusted_sender_routes"]
        ),
    )
    config[TEST_ACQUISITION] = reviewed[TEST_ACQUISITION]
    config[COS_ACQUISITION] = {
        **reviewed[COS_ACQUISITION],
        "gmail_adapter_id": sender["id"],
    }
    for channel in config["service_identities"]["channels"]:
        if channel["provider"] == "gmail":
            channel["intake_mode"] = "member_submission"


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


def historical_audit(client: TestClient) -> dict[str, Any]:
    runtime = client.app.state.runtime
    settings = runtime.settings
    bindings = validate_historical_provider_bindings(
        settings.adapter_config,
        member_roles=settings.member_roles,
        household_mapping=settings.household_mapping,
        service_principal_ids={COS_SERVICE_PRINCIPAL_ID},
    )

    async def audit() -> dict[str, Any]:
        return await audit_historical_attribution(
            runtime.event_log,
            repository_root=ROOT,
            provider_bindings=bindings,
            hash_key=b"synthetic-historical-audit-key",
        )

    return client.portal.call(audit)


def assert_historical_email(client: TestClient) -> None:
    report = historical_audit(client)
    assert report["state"] == "healthy", report
    assert report["unattributed_event_count"] == 0, report
    assert report["content_exposed"] is False
    assert report["raw_identifiers_exposed"] is False
    assert all(private not in json.dumps(report) for private in (TEXT, BODY, RECIPIENT))


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
        complete_sender_binding(client, tmp_path)
        model, _ = model_boundary(client)
        try:
            send_once(client, service, channel)
            assert_retained_effect(published_assurance(client))
            assert_historical_email(client)

            async def exact_attempt() -> str:
                rows = [
                    event
                    async for event in client.app.state.runtime.event_log.read_since(
                        types=["cos.response.attempted"]
                    )
                ]
                assert len(rows) == 1
                return rows[0]["event_id"]

            attempt_id = client.portal.call(exact_attempt)
        finally:
            client.portal.call(model.aclose)
    # Reopen the encrypted retained history without initiating another action.
    # No provider adapter or model is needed to explain the old successful send.
    for adapter in config.adapter_config["adapters"]:
        adapter["enabled"] = False
    with TestClient(
        create_application(config), base_url="https://central-fixture.ts.net"
    ) as client:
        assert_retained_effect(published_assurance(client))
        # Disabling a service enrollment intentionally removes its current
        # provider binding; the retained internal email ownership still validates.
        historical = historical_audit(client)
        assert client.portal.call(
            _cos_response_owner_matches, client.app.state.runtime.event_log, attempt_id
        )
        assert (
            sum(
                row["event_count"]
                for row in historical["groups"]
                if row["binding_state"] == "internal_cos_member_source_reconciled"
            )
            == 1
        )
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
        complete_sender_binding(client, tmp_path)
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
                    assert historical_audit(client)["unattributed_event_count"] > 0
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
                    assert historical_audit(client)["unattributed_event_count"] > 0
            assert len(service.calls) == 1
        finally:
            client.portal.call(model.aclose)


@pytest.mark.parametrize("channel", ["portal", "reply"])
def test_historical_email_rejects_attempt_and_approval_owner_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, channel: str
) -> None:
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        service = FakeGmailService()
        prepare(client, service)
        complete_sender_binding(client, tmp_path)
        model, _ = model_boundary(client)
        try:
            send_once(client, service, channel)
            assert_historical_email(client)
            original_get = runtime.event_log.get
            for event_type in ("cos.response.approved", "cos.response.attempted"):
                for field, value in (
                    ("actor_identity", "another-member"),
                    ("owner_scope", "private:another-member"),
                    ("tenant_id", "another-tenant"),
                    ("payload.member_id", "another-member"),
                    ("schema_version", 1 if event_type.endswith("approved") else 2),
                ):

                    async def altered_get(identifier: str) -> dict[str, Any] | None:
                        event = await original_get(identifier)
                        if event is not None and event["type"] == event_type:
                            event = copy.deepcopy(event)
                            if field.startswith("payload."):
                                event["payload"][field.removeprefix("payload.")] = value
                            else:
                                event[field] = value
                        return event

                    with monkeypatch.context() as patch:
                        patch.setattr(runtime.event_log, "get", altered_get)
                        assert (
                            historical_audit(client)["unattributed_event_count"] > 0
                        ), (event_type, field)
            if channel == "reply":
                for missing, field, value in (
                    (True, "", ""),
                    (False, "actor_identity", "another-member"),
                    (False, "payload.account_id", "another-account"),
                    (False, "payload.thread_id", "another-thread"),
                ):

                    async def changed_source(identifier: str) -> dict[str, Any] | None:
                        event = await original_get(identifier)
                        if (
                            event is not None
                            and event["type"] == "cos.conversation.instruction"
                        ):
                            if missing:
                                return None
                            event = copy.deepcopy(event)
                            if field.startswith("payload."):
                                event["payload"][field.removeprefix("payload.")] = value
                            else:
                                event[field] = value
                        return event

                    with monkeypatch.context() as patch:
                        patch.setattr(runtime.event_log, "get", changed_source)
                        assert historical_audit(client)["unattributed_event_count"] > 0
            assert len(service.calls) == 1
        finally:
            client.portal.call(model.aclose)


@pytest.mark.parametrize("channel", ["portal", "reply"])
def test_genuine_email_history_crosses_real_htr_export_and_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, channel: str
) -> None:
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        service = FakeGmailService()
        prepare(client, service)
        complete_sender_binding(client, tmp_path)
        model, _ = model_boundary(client)
        try:
            send_once(client, service, channel)
            assurance = published_assurance(client)
            assert_retained_effect(assurance)
            # Disposable deployment/OS boundaries only. Both public auditors,
            # the encrypted history, exact source/grounding and HTR review are real.
            paths = replace(
                DeploymentPaths.from_root(tmp_path / "export-host"),
                instance_dir=runtime.settings.instance.instance_dir,
            )
            config_dir = paths.instance_dir / "config"
            config = runtime.settings.adapter_config
            atomic_write(config_dir / "adapters.json", json.dumps(config).encode())
            mapping = copy.deepcopy(runtime.settings.household_mapping)
            mapping["members"] = [
                {"member_id": member, "role": role}
                for member, role in runtime.settings.member_roles.items()
            ]
            atomic_write(
                config_dir / "member-mapping.json", json.dumps(mapping).encode()
            )
            atomic_write(
                config_dir / "deployment-readiness.json",
                json.dumps(
                    {
                        "contract": ACTIVE_CONTRACT,
                        "provider_effect_window": {
                            "started_at": datetime.now(timezone.utc).isoformat()
                        },
                    }
                ).encode(),
            )
            reference = secret_store(paths, test_mode=True).put(
                "event-log", runtime.settings.encryption_key
            )
            atomic_write(
                config_dir / "secret-references.json",
                json.dumps({"references": {"event_log": reference}}).encode(),
            )
            diagnostics = {
                "ready": True,
                "observation_active": True,
                "capabilities": client.get("/api/system/ready").json()["capabilities"],
            }
            monkeypatch.setattr(
                htr10_export,
                "ServiceManager",
                lambda *_a, **_kw: SimpleNamespace(
                    runtime_diagnostics=lambda: diagnostics
                ),
            )
            paths.backup_dir.mkdir(mode=0o700, parents=True)
            historical = paths.backup_dir / "historical.json"
            source = paths.backup_dir / "source.json"
            exported = asyncio.run(
                htr10_export.export_htr10_audits(
                    paths,
                    repository_root=ROOT,
                    historical_output=historical,
                    source_output=source,
                    ownership_reviewed=True,
                    test_mode=True,
                )
            )
            assert exported["historical_state"] == exported["source_state"] == "healthy"
            result = record_htr10_review(
                paths,
                historical_attribution=historical,
                source_assurance=source,
                observed_at=datetime.now(timezone.utc).isoformat(),
            )
            assert result["receipt_id"]
            assert all(
                private not in historical.read_text() + source.read_text()
                for private in (TEXT, BODY, RECIPIENT)
            )
            assert len(service.calls) == 1
        finally:
            client.portal.call(model.aclose)
