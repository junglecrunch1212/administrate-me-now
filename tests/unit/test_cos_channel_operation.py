"""Live authorization cannot be inferred from a config flag or an old command."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.cos_channel_acquisition import policy_from_config
from adminme.cos_channel_operation import (
    ACTIVATED_EVENT,
    DEACTIVATED_EVENT,
    CosChannelActivation,
    SenderProof,
    live_authorization,
    read_receipt,
)
from adminme.cos_response_policy import POLICY_EVENT_TYPE
from adminme.deployment_readiness import evaluate_deployment_readiness
from tests.acceptance.test_cos_channel_acquisition import _configuration
from tests.acceptance.test_forwarded_channel_intake import EMAIL_ACCOUNT, MEMBER_A
from tests.unit.test_observation_readiness_window import _inputs


class _Log:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    async def read_since(self, *_args: Any, types: list[str], **_kwargs: Any) -> Any:
        for event in self.events:
            if event["type"] in types:
                yield event


def _fixture(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any], _Log]:
    config = _configuration(tmp_path)
    policy = policy_from_config(config)
    assert policy is not None
    now = datetime.now(timezone.utc) - timedelta(seconds=1)
    activation = CosChannelActivation(
        activation_id="a" * 32,
        release_commit="b" * 40,
        enabled_at=now.isoformat(),
        acquisition_sha256=policy.digest(),
        allowlist_event_id="policy-1",
        allowlist_revision=1,
        channels=[
            SenderProof(
                channel="email",
                provider_account_sha256=hashlib.sha256(
                    EMAIL_ACCOUNT.encode()
                ).hexdigest(),
                sender_evidence_sha256="c" * 64,
                transport_evidence_sha256="d" * 64,
                observed_at=(now - timedelta(minutes=1)).isoformat(),
            )
        ],
        backup_sha256="e" * 64,
        rehearsal_sha256="f" * 64,
        owner_authorization_sha256="0" * 64,
        operator_principal_id="operator:fixture-activation",
        observation_window_started_at=(now - timedelta(days=50)).isoformat(),
    ).model_dump(mode="json")
    receipt = {"event_id": "activation-1", "activation": activation}
    log = _Log(
        [
            {
                "type": POLICY_EVENT_TYPE,
                "tenant_id": "tenant-fixture",
                "event_id": "policy-1",
                "payload": {
                    "contract": "cos-response-allowlist-v1",
                    "service_principal_id": "service-principal:chief-of-staff",
                    "revision": 1,
                    "entries": [
                        {
                            "member_id": MEMBER_A,
                            "channel": "email",
                            "address": "member-a@example.invalid",
                        }
                    ],
                    "changed_by_member_id": MEMBER_A,
                    "provider_effects": False,
                    "grants_provider_authority": False,
                },
            },
            {
                "type": ACTIVATED_EVENT,
                "tenant_id": "tenant-fixture",
                "event_id": "activation-1",
                "payload": activation,
            },
        ]
    )
    return config, receipt, log


@pytest.mark.asyncio
@pytest.mark.parametrize("standing", [False, True])
async def test_exact_live_authorization_rejects_backlog_wrong_sender_and_deactivation(
    tmp_path: Path,
    standing: bool,
) -> None:
    config, receipt, log = _fixture(tmp_path)
    if standing:
        receipt["activation"]["self_reply_bindings"] = ["f" * 64]
    arguments = {
        "channel": "email",
        "provider_account_id": EMAIL_ACCOUNT,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await live_authorization(
        log, "tenant-fixture", config, receipt, **arguments
    )
    assert result["authorized"] is True
    assert result["self_reply_bindings"] == (["f" * 64] if standing else [])
    result = await live_authorization(
        log,
        "tenant-fixture",
        config,
        receipt,
        **{**arguments, "requested_at": "2000-01-01T00:00:00+00:00"},
    )
    assert result == {"authorized": False, "reason": "pre_activation_action_refused"}
    result = await live_authorization(
        log,
        "tenant-fixture",
        config,
        receipt,
        **{**arguments, "provider_account_id": "different@example.invalid"},
    )
    assert result["authorized"] is False
    log.events.append(
        {
            "type": DEACTIVATED_EVENT,
            "tenant_id": "tenant-fixture",
            "event_id": "off",
            "payload": {},
        }
    )
    assert (await live_authorization(log, "tenant-fixture", config, receipt))[
        "authorized"
    ] is False


@pytest.mark.asyncio
async def test_policy_changes_apply_without_reactivation_but_empty_revokes(
    tmp_path: Path,
) -> None:
    config, receipt, log = _fixture(tmp_path)
    update = copy.deepcopy(log.events[0])
    update["event_id"] = "policy-2"
    update["payload"]["revision"] = 2
    update["payload"]["entries"][0]["address"] = "new-member-address@example.invalid"
    log.events.append(update)
    result = await live_authorization(log, "tenant-fixture", config, receipt)
    assert result["authorized"] is True
    assert result["allowlist_revision"] == 2
    update = copy.deepcopy(update)
    update["event_id"] = "policy-3"
    update["payload"]["revision"] = 3
    update["payload"]["entries"] = []
    log.events.append(update)
    assert (await live_authorization(log, "tenant-fixture", config, receipt))[
        "authorized"
    ] is False


def test_live_window_keeps_old_zero_effect_interval_and_accounts_new_live_effects() -> (
    None
):
    inputs = _inputs()
    enabled_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    inputs.update(
        observation_active=False,
        cos_acquisition_configured=True,
        cos_live_authorization={"authorized": True, "enabled_at": enabled_at},
    )
    inputs["deployment_evidence"]["provider_effect_window"]["ended_at"] = enabled_at
    effect = inputs["source_assurance"]["provider_effects"]
    effect["observation_window"]["ended_at"] = enabled_at
    effect["counts"]["provider_called_true"] = 4
    result = evaluate_deployment_readiness(**inputs)
    assert result["dimensions"]["observation_effects"]["state"] == "healthy"
    assert result["dimensions"]["cos_channel_operation"]["state"] == "healthy"
    effect["state"] = "degraded"
    assert (
        evaluate_deployment_readiness(**inputs)["dimensions"]["observation_effects"][
            "state"
        ]
        == "blocked"
    )
    effect["state"] = "healthy"
    inputs["cos_live_authorization"]["authorized"] = False
    assert (
        evaluate_deployment_readiness(**inputs)["dimensions"]["cos_channel_operation"][
            "state"
        ]
        == "blocked"
    )


@pytest.mark.parametrize("operation", ["cos-channel-operation", "activation"])
def test_pending_operation_holds_replies_until_commit(
    tmp_path: Path, operation: str
) -> None:
    config_dir = tmp_path / "state/config"
    config_dir.mkdir(parents=True)
    receipt = {"event_id": "fixture", "activation": {}}
    (config_dir / "cos-channel-operation.json").write_text(json.dumps(receipt))
    journal = tmp_path / "app" / operation / "active.json"
    journal.parent.mkdir(parents=True)
    journal.write_text("{}")
    assert read_receipt(config_dir) == {}
    assert read_receipt(config_dir, allow_verification=True) == receipt
    journal.unlink()
    assert read_receipt(config_dir) == receipt


@pytest.mark.parametrize("selected_count", [0, 3])
def test_readonly_google_diagnostics_preserve_independent_cos_authority(
    selected_count: int,
) -> None:
    inputs = _inputs()
    inputs.update(
        cos_acquisition_configured=True,
        member_google_source_count=selected_count,
    )
    result = evaluate_deployment_readiness(**inputs)
    evidence = result["dimensions"]["cos_channel_operation"]["evidence"]
    assert evidence["personal_channel_polling"] is (selected_count > 0)
    assert evidence["member_google_source_count"] == selected_count
    assert evidence["live_authorized"] is False
    inputs["observation_active"] = False
    assert (
        evaluate_deployment_readiness(**inputs)["dimensions"]["cos_channel_operation"][
            "state"
        ]
        == "blocked"
    )
