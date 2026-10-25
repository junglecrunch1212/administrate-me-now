"""Exact sender and requester selection for the requested-email preparation seam."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from pydantic import ValidationError

from adminme.lib.session import build_internal_session
from adminme.products.openclaw import requested_email as email
from tests.acceptance.test_native_apple_reminder import _provenance


ACCOUNT = "staff@example.invalid"
RECIPIENT = "requester@example.invalid"
MEMBER = "member-requester"
COS = "service-principal:chief-of-staff"


def request(**changes):
    return email.RequestedEmailRequest(
        provenance=_provenance(),
        source_message_event_id="ev_current_instruction",
        request_id="requested-email-001",
        subject="Requested note",
        body="Call the workshop tomorrow.",
        **changes,
    )


def configured(monkeypatch, *, recipients=None):
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            adapter_config={
                "adapters": [
                    {
                        "id": "cos-gmail",
                        "kind": "messaging-gmail-api",
                        "enabled": True,
                        "config": {
                            "service_principal_id": COS,
                            "member_id": None,
                            "provider_account_id": ACCOUNT,
                            "user_email": ACCOUNT,
                            "channel_id": "cos-gmail",
                            "enable_writes": True,
                            "read_only": False,
                        },
                    }
                ]
            },
            service_channel_accounts=[
                SimpleNamespace(
                    channel_id="cos-gmail",
                    provider="gmail",
                    service_principal_id=COS,
                    provider_account_reference="provider-account:"
                    + hashlib.sha256(ACCOUNT.encode()).hexdigest(),
                    enrolled=True,
                    state="healthy",
                    sending_enabled=True,
                    observation_active=False,
                    display_name="CoS email",
                )
            ],
        ),
        event_log=object(),
    )

    async def policy(_log, _tenant):
        return {
            "entries": recipients
            if recipients is not None
            else [
                {"member_id": MEMBER, "channel": "email", "address": RECIPIENT},
                {
                    "member_id": "another-member",
                    "channel": "email",
                    "address": "other@example.invalid",
                },
            ]
        }

    monkeypatch.setattr(email, "policy_from_event_log", policy)
    session = replace(
        build_internal_session(
            actor="member-principal", role="principal", tenant_id="tenant-a"
        ),
        auth_member_id=MEMBER,
        view_member_id=MEMBER,
    )
    return runtime, session


async def test_one_current_sender_and_requester_address_are_selected_without_new_permission(
    monkeypatch,
):
    runtime, session = configured(monkeypatch)
    values = await email.selected_email(runtime, session, request())
    assert values["provider_account_id"] == ACCOUNT
    assert values["recipient"] == RECIPIENT
    action = email.email_action(MEMBER, values, request())
    assert action["resource_owner"] == action["operating_principal"] == COS
    assert action["recipient_member_id"] == MEMBER
    assert action["recipient_selected_by_policy"] is True
    assert action["sender_selected_by_policy"] is True
    assert action["body"] == values["body"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("enable_writes", False),
        ("read_only", True),
        ("member_id", MEMBER),
        ("user_email", "different@example.invalid"),
        ("service_principal_id", "other-service"),
    ],
)
async def test_sender_configuration_cannot_borrow_another_account_or_read_authority(
    monkeypatch, field, value
):
    runtime, session = configured(monkeypatch)
    runtime.settings.adapter_config["adapters"][0]["config"][field] = value
    with pytest.raises(HTTPException) as error:
        await email.selected_email(runtime, session, request())
    assert error.value.status_code == 409
    assert error.value.detail["accounts"] == []


async def test_multiple_own_addresses_require_only_narrow_destination_choice(
    monkeypatch,
):
    recipients = [
        {"member_id": MEMBER, "channel": "email", "address": RECIPIENT},
        {"member_id": MEMBER, "channel": "email", "address": "second@example.invalid"},
        {
            "member_id": "another-member",
            "channel": "email",
            "address": "other@example.invalid",
        },
    ]
    runtime, session = configured(monkeypatch, recipients=recipients)
    with pytest.raises(HTTPException) as error:
        await email.selected_email(runtime, session, request())
    assert error.value.detail["code"] == "email_recipient_choice_required"
    assert error.value.detail["recipients"] == [RECIPIENT, "second@example.invalid"]
    values = await email.selected_email(runtime, session, request(recipient=RECIPIENT))
    assert values["recipient"] == RECIPIENT


async def test_other_member_recipient_is_rejected_even_when_on_household_allowlist(
    monkeypatch,
):
    runtime, session = configured(monkeypatch)
    with pytest.raises(HTTPException) as error:
        await email.selected_email(
            runtime, session, request(recipient="other@example.invalid")
        )
    assert error.value.status_code == 403


@pytest.mark.parametrize(
    "field,value",
    [
        ("subject", "Injected\r\nCc: other@example.invalid"),
        ("body", "\0"),
        ("body", " "),
    ],
)
def test_requested_email_plain_text_validation(field, value):
    values = request().model_dump()
    values[field] = value
    with pytest.raises(ValidationError):
        email.RequestedEmailRequest.model_validate(values)
