"""CoS-only ingestion through production loading, routing, extraction and replay."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.adapters.cos_channel_acquisition import (
    CONFIG_KEY,
    CosAcquisitionPolicy,
    policy_from_config,
)
from adminme.adapters.disposable_test_acquisition import (
    CONFIG_KEY as TEST_KEY,
    DisposableTestAcquisition,
    restrict_adapter_config,
)
from adminme.adapters.loader import load_builtin_adapters
from adminme.runtime.application import create_application
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from bootstrap.cos_channel_activation import CoSActivationError, prepare_configuration
from tests.acceptance.test_forwarded_channel_intake import (
    EMAIL_ACCOUNT,
    MEMBER_A,
    MEMBER_HEADERS,
    ROOT,
    TEXT,
    _events,
    _settings,
    _settle,
)


def _configuration(path: Path) -> dict[str, Any]:
    settings = _settings(path)
    value = copy.deepcopy(settings.adapter_config)
    policy = DisposableTestAcquisition.parse(
        {
            "contract": "disposable-test-acquisition-v1",
            "adapter_id": "fixture-personal-gmail",
            "member_id": MEMBER_A,
            "provider_account_id": "personal-fixture@example.invalid",
            "message_ids": ["old-selected-id"],
        }
    )
    value[TEST_KEY] = policy.private_config()
    value[CONFIG_KEY] = CosAcquisitionPolicy(
        retained_test_policy_sha256=policy.receipt_digest(),
        gmail_adapter_id="fixture-cos-gmail",
        gmail_channel_id="channel:chief-of-staff:gmail",
        gmail_provider_account_id=EMAIL_ACCOUNT,
    ).model_dump(mode="json")
    value["service_identities"]["channels"][1]["intake_mode"] = "member_submission"
    value["adapters"] = [
        {
            "id": "fixture-personal-gmail",
            # A forbidden personal pack is intentionally unavailable: loading it
            # would fail this test before any synthetic provider were contacted.
            "kind": "fixture-unavailable-personal-mail",
            "enabled": True,
            "config": {
                "member_id": MEMBER_A,
                "provider_account_id": "personal-fixture@example.invalid",
            },
        },
        {
            "id": "fixture-cos-gmail",
            "kind": "messaging-gmail-api",
            "enabled": True,
            "config": {
                "service_account_key_ref": "op://fixture/cos/credential",
                "user_email": EMAIL_ACCOUNT,
                "provider_account_id": EMAIL_ACCOUNT,
                "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
                "workspace": "service",
                "permission_scope": ["https://www.googleapis.com/auth/gmail.readonly"],
                "binding_provenance": "service-account-delegation",
                "ownership_attested": True,
                "read_only": True,
                "enable_writes": False,
                "channel_id": "channel:chief-of-staff:gmail",
                "trusted_sender_routes": [
                    {
                        "exact_from_identifier": "member-a@example.invalid",
                        "route_key": "route:" + "1" * 64,
                        "authentication_policy": "receiver_aligned_sender",
                    }
                ],
                "initial_backfill_days": 365,
                "initial_backfill_max_messages": 100,
            },
        },
    ]
    return value


def test_overlay_preserves_selection_disables_personal_and_blocks_expansion(
    tmp_path: Path,
) -> None:
    config = _configuration(tmp_path)
    original = copy.deepcopy(config)
    effective = restrict_adapter_config(config)
    assert config == original
    assert effective[TEST_KEY] == original[TEST_KEY]
    assert effective["adapters"][0]["enabled"] is False
    selected = effective["adapters"][1]["config"]
    assert TEST_KEY not in selected
    assert selected["initial_backfill_days"] == 0
    assert selected["initial_backfill_max_messages"] == 0
    assert selected["historical_seed_queries"] == []
    assert selected["historical_related_messages_per_participant"] == 0
    assert [
        adapter.instance_name
        for adapter in load_builtin_adapters(config, repository_root=ROOT)
    ] == ["fixture-cos-gmail"]
    modified = copy.deepcopy(config)
    modified[TEST_KEY]["message_ids"].append("new-personal-id")
    with pytest.raises(ValueError, match="unchanged bounded test policy"):
        restrict_adapter_config(modified)
    modified = copy.deepcopy(config)
    modified["adapters"][1]["config"]["member_id"] = MEMBER_A
    with pytest.raises(ValueError, match="service owner"):
        restrict_adapter_config(modified)
    modified = copy.deepcopy(config)
    modified["service_identities"]["channels"][1]["enrolled"] = False
    with pytest.raises(ValueError, match="enrollment"):
        restrict_adapter_config(modified)


@pytest.mark.parametrize("live", [False, True])
def test_guarded_preparation_retains_member_config_and_requires_send_grant(
    tmp_path: Path, live: bool
) -> None:
    requested = _configuration(tmp_path)
    original = copy.deepcopy(requested)
    original.pop(CONFIG_KEY)
    if live:
        with pytest.raises(CoSActivationError, match="send grant"):
            prepare_configuration(original, requested, enable_live=True)
        requested["adapters"][1]["config"]["permission_scope"].append(
            "https://www.googleapis.com/auth/gmail.send"
        )
    prepared = prepare_configuration(original, requested, enable_live=live)
    assert prepared[TEST_KEY] == original[TEST_KEY]
    assert prepared["adapters"][0]["config"] == original["adapters"][0]["config"]
    assert prepared["adapters"][0]["enabled"] is False
    assert prepared["adapters"][1]["config"]["enable_writes"] is live
    changed = copy.deepcopy(requested)
    changed["adapters"][0]["config"]["provider_account_id"] = "wrong@example.invalid"
    with pytest.raises(CoSActivationError, match="member adapters"):
        prepare_configuration(original, changed, enable_live=live)


@pytest.mark.parametrize("live", [False, True])
def test_prepared_dedicated_oauth_grants_pass_production_adapter_binding_gate(
    tmp_path: Path, live: bool
) -> None:
    requested = _configuration(tmp_path)
    selected = requested["adapters"][1]["config"]
    selected.pop("service_account_key_ref")
    selected.update(
        oauth_credentials_ref="keychain://fixture/cos-gmail",
        binding_provenance="oauth-subject",
        permission_scope=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
        ],
    )
    previous = copy.deepcopy(requested)
    previous.pop(CONFIG_KEY)
    prepared = prepare_configuration(previous, requested, enable_live=live)
    # Exercise the production loader's ownership gate before credentials are
    # resolved or any provider can be contacted. A broader OAuth grant must not
    # accidentally become a general service-owned write exception.
    adapters = load_builtin_adapters(
        prepared,
        repository_root=ROOT,
        member_roles={MEMBER_A: "principal"},
        household_mapping={},
        service_principal_ids={COS_SERVICE_PRINCIPAL_ID},
        validate_bindings=True,
    )
    assert [adapter.instance_name for adapter in adapters] == ["fixture-cos-gmail"]
    prepared["adapters"][1]["config"]["permission_scope"].append(
        "https://www.googleapis.com/auth/gmail.modify"
    )
    with pytest.raises(ValueError, match="exact read and send grants"):
        load_builtin_adapters(
            prepared,
            repository_root=ROOT,
            member_roles={MEMBER_A: "principal"},
            household_mapping={},
            service_principal_ids={COS_SERVICE_PRINCIPAL_ID},
            validate_bindings=True,
        )


class _ReadOnlyGmail:
    def __init__(self) -> None:
        self.mode = ""

    def users(self) -> _ReadOnlyGmail:
        return self

    def getProfile(self, **_kwargs: Any) -> _ReadOnlyGmail:
        self.mode = "profile"
        return self

    def history(self) -> _ReadOnlyGmail:
        return self

    def list(self, **_kwargs: Any) -> _ReadOnlyGmail:
        self.mode = "history"
        return self

    def execute(self) -> dict[str, Any]:
        return {"historyId": "100", "emailAddress": EMAIL_ACCOUNT}


@pytest.mark.parametrize("live_channel_state", [False, True])
def test_production_root_only_loads_cos_and_live_intake_still_reaches_triage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_channel_state: bool
) -> None:
    config = _configuration(tmp_path)
    channel = config["service_identities"]["channels"][1]
    if live_channel_state:
        channel.update(state="healthy", observation_active=False, sending_enabled=True)
    policy = policy_from_config(config)
    assert policy is not None
    adapter = load_builtin_adapters(config, repository_root=ROOT)[0]
    module = sys.modules[type(adapter).__module__]
    subjects: list[str] = []
    monkeypatch.setattr(module, "_default_credentials_resolver", lambda _ref: {})

    def service(_credentials: Any, scopes: list[str], subject: str) -> _ReadOnlyGmail:
        subjects.append(subject)
        assert scopes == ["https://www.googleapis.com/auth/gmail.readonly"]
        return _ReadOnlyGmail()

    monkeypatch.setattr(module, "_default_service_factory", service)
    monkeypatch.setattr(type(adapter), "POLL_INTERVAL_S", 3600)
    settings = replace(_settings(tmp_path), adapter_config=config)
    for run in range(2):
        app = create_application(settings)
        with TestClient(app) as client:
            runtime = app.state.runtime
            assert set(runtime.adapters.inventory()) == {"fixture-cos-gmail"}
            assert runtime.fixture_adapter is None
            assert runtime.xlsx_reverse._observer is None
            selected = runtime.adapters._managed["fixture-cos-gmail"].adapter

            async def wait_adapter() -> None:
                for _attempt in range(1000):
                    if selected._ctx is not None:
                        return
                    await asyncio.sleep(0.01)
                raise AssertionError("service adapter did not start")

            client.portal.call(wait_adapter)
            if run == 0:
                message = {
                    "id": "new-cos-forward",
                    "internalDate": "1925035200000",
                    "payload": {
                        "mimeType": "text/plain",
                        "headers": [
                            {
                                "name": "Received",
                                "value": "from sender.invalid by mx.google.com with ESMTPS id fixture",
                            },
                            {
                                "name": "Authentication-Results",
                                "value": "mx.google.com; dkim=pass header.i=@example.invalid; dmarc=pass header.from=example.invalid",
                            },
                            {"name": "From", "value": "member-a@example.invalid"},
                            {"name": "To", "value": EMAIL_ACCOUNT},
                        ],
                        "body": {
                            "data": base64.urlsafe_b64encode(TEXT.encode()).decode()
                        },
                    },
                }

                async def ingest() -> str:
                    return await selected._emit_message_observation(
                        ctx=selected._ctx,
                        message=message,
                        fingerprint=hashlib.sha256(TEXT.encode()).hexdigest(),
                    )

                source_id = client.portal.call(ingest)
                client.portal.call(
                    runtime.wait_for_subscriber, "direct-intake:routing", source_id
                )
                intents = client.portal.call(
                    _events, runtime, "triage.intent.submitted"
                )
                assert len(intents) == 1
                assert _settle(client, intents[0]["event_id"])
            triage = client.get(
                "/api/member/v1/surfaces/triage", headers=MEMBER_HEADERS
            )
            assert triage.status_code == 200
            assert any(row["title"] == TEXT for row in triage.json()["items"])
            assert (
                client.portal.call(_events, runtime, "external_sync.write_requested")
                == []
            )
            assert runtime.settings.adapter_config[TEST_KEY]["message_ids"] == [
                "old-selected-id"
            ]
    assert subjects == [EMAIL_ACCOUNT, EMAIL_ACCOUNT]
