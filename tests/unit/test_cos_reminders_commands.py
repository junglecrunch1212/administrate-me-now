"""CoS source ownership and exact decisions remain provable after revocation."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from adminme.adapters.cos_apple_reminders import digest
from adminme.adapters.cos_reminders_write_policy import (
    RULE_ID,
    policy_from_config,
    write_selection,
)
from adminme.cos_reminders_commands import (
    CoSReminderIntent,
    SOURCE_ADAPTER,
    command_owner_matches,
)
from tests.unit.adapters.reminders.test_cos_reminders_write_policy import (
    _configuration_pair,
)


def _chain(tmp_path):
    _, config = _configuration_pair(tmp_path)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=5)
    intent = CoSReminderIntent(
        resource_owner="service-principal:chief-of-staff",
        operating_principal="service-principal:chief-of-staff",
        selection=write_selection(config),
        write_policy=policy_from_config(config),
        source_event_id="ev_001",
        title="Prepare the household brief",
        due_at=(now + timedelta(days=1)).replace(microsecond=0),
        authorization_expires_at=expires,
        selected_by_policy=True,
    )
    member = "member-fixture"
    envelope = {
        "tenant_id": "tenant-fixture",
        "owner_scope": f"private:{member}",
        "visibility_scope": f"private:{member}",
        "actor_identity": member,
    }
    text = "Add a reminder to your work list to prepare the household brief tomorrow."
    source = {
        **envelope,
        "event_id": "ev_001",
        "type": "cos.conversation.instruction",
        "schema_version": 1,
        "source_adapter": "cos-conversation",
        "payload": {"member_id": member, "text": text, "channel_id": "gmail"},
    }
    grounded = {
        **envelope,
        "event_id": "ev_002",
        "type": "cos.action.request_grounded",
        "schema_version": 1,
        "actor_identity": "service-principal:chief-of-staff",
        "payload": {
            "member_id": member,
            "source_event_id": source["event_id"],
            "action_id": "fixture-command",
            "request_fingerprint": "a" * 64,
            "action_fingerprint": digest(intent.action()),
            "source_fingerprint": digest(text),
            "action": intent.action(),
            "rule_id": RULE_ID,
            "issued_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "evidence": [{"start": 0, "end": len(text), "text": text}],
        },
    }
    common = {
        "member_id": member,
        "provider": "apple_reminders",
        "operation": "create_reminder",
        "provider_account_id": intent.selection.provider_account_id,
        "target_external_id": None,
        "expected_provider_revision": None,
        "request_fingerprint": "a" * 64,
    }
    domain = {
        **envelope,
        "event_id": "ev_003",
        "type": "member.provider_action.confirmed",
        "schema_version": 1,
        "source_adapter": SOURCE_ADAPTER,
        "source_account_id": member,
        "causation_id": source["event_id"],
        "raw_ref": "request-sha256:" + "a" * 64,
        "payload": {
            **common,
            "action_id": "fixture-command",
            "command_payload": intent.model_dump(mode="json"),
        },
    }
    approval_fingerprint = digest(
        {
            "domain_approval_event_id": domain["event_id"],
            "request_authorization_event_id": grounded["event_id"],
        }
    )
    host = {
        **envelope,
        "event_id": "ev_004",
        "type": "openclaw.approval.received",
        "schema_version": 2,
        "source_adapter": "openclaw-plugin",
        "causation_id": domain["event_id"],
        "payload": {
            "member_id": member,
            "tool_name": "administrateme_execute_pending_provider_action",
            "action": "create_reminder",
            "approval_method": "authenticated_request",
            "single_use": True,
            "request_fingerprint": approval_fingerprint,
            "resource_fingerprint": approval_fingerprint,
            "issued_at": now.isoformat(),
            "consumed_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=1)).isoformat(),
            "request_authorization_event_id": grounded["event_id"],
            "source_event_id": source["event_id"],
            "domain_approval_event_id": domain["event_id"],
            "action_fingerprint": digest(intent.action()),
            "rule_id": RULE_ID,
        },
    }
    command = {
        **envelope,
        "event_id": "ev_005",
        "type": "external_sync.write_requested",
        "schema_version": 2,
        "source_adapter": "openclaw-plugin",
        "causation_id": host["event_id"],
        "idempotency_key": "external-sync-approved:" + domain["event_id"],
        "payload": {
            **common,
            "command_id": "fixture-command",
            "payload": intent.model_dump(mode="json"),
            "domain_approval_event_id": domain["event_id"],
            "openclaw_approval_event_id": host["event_id"],
        },
    }
    rows = {row["event_id"]: row for row in (source, grounded, domain, host, command)}

    async def get(event_id):
        return rows.get(event_id)

    return config, intent, command, rows, SimpleNamespace(get=get)


@pytest.mark.asyncio
async def test_retained_exact_cos_owner_chain_survives_current_revocation(tmp_path):
    config, intent, command, _, log = _chain(tmp_path)
    intent.require_current(config)
    assert await command_owner_matches(log, command, through=command["event_id"])
    config["cos_apple_reminders_writes"]["enabled"] = False
    with pytest.raises(PermissionError):
        intent.require_current(config)
    assert await command_owner_matches(log, command, through=command["event_id"])
    native = intent.native_command(command)
    assert native.resource_owner == "service-principal:chief-of-staff"
    assert native.requesting_member_id == "member-fixture"
    assert native.provider_account_id == intent.selection.provider_account_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    ["scope", "source", "owner", "list", "host", "rule", "expiry", "command_identity"],
)
async def test_wrong_owner_or_borrowed_approval_cannot_attribute_a_write(
    tmp_path, fault
):
    _, _, command, rows, log = _chain(tmp_path)
    if fault == "scope":
        command["visibility_scope"] = "shared:household"
    elif fault == "source":
        rows["ev_001"]["payload"]["text"] += " changed"
    elif fault == "owner":
        command["payload"]["payload"]["resource_owner"] = "member-fixture"
    elif fault == "list":
        command["payload"]["payload"]["selection"]["list_id"] = "other-list"
    elif fault == "host":
        rows["ev_004"]["payload"]["domain_approval_event_id"] = "other-domain"
    elif fault == "rule":
        rows["ev_004"]["payload"]["rule_id"] = (
            "current-human-request.apple-reminder-create.v1"
        )
    elif fault == "expiry":
        rows["ev_004"]["payload"]["expires_at"] = "2100-01-01T00:00:00+00:00"
    else:
        command["payload"]["command_id"] = "other-command"
    assert not await command_owner_matches(log, command, through=command["event_id"])


def test_own_action_never_inherits_member_resource_owner(tmp_path):
    config, intent, _, _, _ = _chain(tmp_path)
    action = intent.action()
    assert (
        action["resource_owner"]
        == action["operating_principal"]
        == "service-principal:chief-of-staff"
    )
    assert action["provider_account_id"] == write_selection(config).provider_account_id
    changed = copy.deepcopy(config)
    changed["cos_apple_reminders_writes"]["native_helper"]["artifact_sha256"] = "0" * 64
    with pytest.raises(PermissionError):
        intent.require_current(changed)
