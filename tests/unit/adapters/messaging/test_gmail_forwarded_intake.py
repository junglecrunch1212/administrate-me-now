"""Credential-free Gmail receiver trust and loss-aware MIME contract tests."""

from __future__ import annotations

import base64
import copy
import importlib.util
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.gmail_submission import verified_sender_route
from adminme.events.registry import ensure_autoloaded, registry
from adminme.forwarded_evidence import submission_evidence

ROOT = Path(__file__).resolve().parents[4]
ROUTE = "route:" + "1" * 64
ACCOUNT = "cos-service@example.invalid"
SENDER = "member-a@example.invalid"


def _config() -> dict[str, Any]:
    return {
        "user_email": ACCOUNT,
        "provider_account_id": ACCOUNT,
        "service_principal_id": "service-principal:chief-of-staff",
        "workspace": "service",
        "channel_id": "channel:chief-of-staff:gmail",
        "trusted_sender_routes": [
            {
                "exact_from_identifier": SENDER,
                "route_key": ROUTE,
                "authentication_policy": "receiver_aligned_sender",
            }
        ],
    }


def _headers() -> list[dict[str, str]]:
    return [
        {
            "name": "Received",
            "value": "from sender.invalid by mx.google.com with ESMTPS id fixture",
        },
        {
            "name": "Authentication-Results",
            "value": "mx.google.com; dkim=pass header.i=@example.invalid header.d=example.invalid; dmarc=pass header.from=example.invalid",
        },
        {"name": "From", "value": SENDER},
        {"name": "To", "value": ACCOUNT},
        {"name": "Subject", "value": "Forwarded selected obligation"},
    ]


def _part(mime: str, text: str, *, filename: str = "") -> dict[str, Any]:
    data = text.encode()
    return {
        "mimeType": mime,
        "filename": filename,
        "body": {
            "data": base64.urlsafe_b64encode(data).decode(),
            "size": len(data),
        },
    }


def _adapter() -> Any:
    spec = importlib.util.spec_from_file_location(
        "gmail_forward_intake_test", ROOT / "packs/adapters/messaging/gmail/adapter.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.GmailAdapter(config=_config())


def test_reviewed_receiver_alignment_is_usable_but_not_claimed_as_person_proof() -> (
    None
):
    assert verified_sender_route(_headers(), _config()) == (
        ROUTE,
        "receiver_aligned_sender",
    )
    config = _config()
    route = config["trusted_sender_routes"][0]
    route["authentication_policy"] = "exact_dkim_identity"
    route["exact_authenticated_identifier"] = SENDER
    assert verified_sender_route(_headers(), config) == (None, "unverified")
    headers = _headers()
    headers[1]["value"] = headers[1]["value"].replace(
        "header.i=@", "header.i=member-a@"
    )
    assert verified_sender_route(headers, config) == (ROUTE, "exact_dkim_identity")


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_auth",
        "duplicate_auth",
        "fake_receiver",
        "missing_receive_hop",
        "spoofed_domain",
        "failed_dmarc",
        "multiple_from",
        "multiple_to",
        "cc",
        "unreviewed_policy",
        "auto_forward",
        "comment_injection",
        "duplicate_route",
        "wrong_account",
    ],
)
def test_spoofed_or_ambiguous_outer_metadata_never_resolves(mutation: str) -> None:
    headers, config = _headers(), _config()
    if mutation == "missing_auth":
        headers.pop(1)
    elif mutation == "duplicate_auth":
        headers.append(dict(headers[1]))
    elif mutation == "fake_receiver":
        headers[1]["value"] = headers[1]["value"].replace(
            "mx.google.com", "sender.invalid"
        )
    elif mutation == "missing_receive_hop":
        headers.pop(0)
    elif mutation == "spoofed_domain":
        headers[1]["value"] = headers[1]["value"].replace(
            "dkim=pass header.i=@example.invalid header.d=example.invalid",
            "dkim=pass header.i=@attacker.invalid header.d=attacker.invalid",
        )
    elif mutation == "failed_dmarc":
        headers[1]["value"] = headers[1]["value"].replace("dmarc=pass", "dmarc=fail")
    elif mutation == "multiple_from":
        headers[2]["value"] += ", attacker@example.invalid"
    elif mutation == "multiple_to":
        headers[3]["value"] += ", outsider@example.invalid"
    elif mutation == "cc":
        headers.append({"name": "Cc", "value": "outsider@example.invalid"})
    elif mutation == "unreviewed_policy":
        del config["trusted_sender_routes"][0]["authentication_policy"]
    elif mutation == "auto_forward":
        headers.append({"name": "X-Forwarded-To", "value": ACCOUNT})
    elif mutation == "comment_injection":
        headers[1]["value"] = (
            "mx.google.com; dkim=fail (dkim=pass header.i=@example.invalid); dmarc=pass header.from=example.invalid"
        )
    elif mutation == "duplicate_route":
        config["trusted_sender_routes"].append(
            copy.deepcopy(config["trusted_sender_routes"][0])
        )
    elif mutation == "wrong_account":
        config["provider_account_id"] = "other@example.invalid"
    assert verified_sender_route(headers, config) == (None, "unverified")


def test_forwarded_eml_headers_are_evidence_only_and_body_is_not_stripped() -> None:
    original = (
        "From: claimed-other@example.invalid\r\nTo: somebody@example.invalid\r\n"
        "Subject: Original assignment\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        "Please submit the form by Friday.\r\n"
    )
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            _part("text/plain", "Please review this.\n" + "Original details. " * 180),
            _part("message/rfc822", original, filename="forward.eml"),
            _part(
                "text/calendar",
                "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:fixture-event\r\nSEQUENCE:2\r\nDTSTART;TZID=America/New_York:20310102T090000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
                filename="event.ics",
            ),
        ],
    }
    evidence = submission_evidence(payload)
    assert evidence["complete"] is True
    assert len(evidence["text"]) > 2000
    assert "Original assignment" in evidence["text"]
    assert "Please submit the form by Friday." in evidence["text"]
    assert "UID:fixture-event" in evidence["text"]
    assert "SEQUENCE:2" in evidence["text"]
    assert {part["role"] for part in evidence["parts"]} == {
        "outer",
        "forwarded",
        "calendar",
    }
    assert all(len(part["sha256"]) == 64 for part in evidence["parts"])


def test_html_and_unsupported_or_truncated_evidence_are_truthful() -> None:
    html = submission_evidence(
        _part(
            "text/html",
            "<p>Forwarded original</p><blockquote>Due Friday</blockquote><script>hidden()</script>",
        )
    )
    assert html["complete"] is True and "Due Friday" in html["text"]
    assert "hidden" not in html["text"]
    unsupported = submission_evidence(
        {
            "mimeType": "multipart/mixed",
            "parts": [
                _part("text/plain", "Review the attached form."),
                {
                    "mimeType": "application/pdf",
                    "filename": "form.pdf",
                    "body": {"attachmentId": "pdf", "size": 10},
                },
            ],
        }
    )
    assert unsupported["complete"] is False
    assert "unsupported_attachment:application/pdf" in unsupported["issues"]
    long = submission_evidence(_part("text/plain", "x" * 32_001))
    assert long["complete"] is False and "interpretation_size_limit" in long["issues"]
    assert len(long["text"]) == 32_000
    assert len(long["parts"][0]["text"]) == 32_001


def test_service_adapter_fetches_supported_attachments_and_emits_registered_v8() -> (
    None
):
    adapter = _adapter()
    payload = {
        "mimeType": "multipart/mixed",
        "headers": _headers(),
        "parts": [
            _part("text/plain", "Please review the attached event."),
            {
                "mimeType": "text/calendar",
                "filename": "invite.ics",
                "body": {"attachmentId": "calendar-1", "size": 50},
            },
        ],
    }
    calendar = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"
    message = {"id": "native-1", "internalDate": "1925035200000", "payload": payload}
    calls: list[dict[str, Any]] = []

    class Service:
        def users(self) -> Service:
            return self

        def messages(self) -> Service:
            return self

        def attachments(self) -> Service:
            return self

        def get(self, **kwargs: Any) -> Service:
            calls.append(kwargs)
            return self

        def execute(self) -> dict[str, Any]:
            return {"data": base64.urlsafe_b64encode(calendar.encode()).decode()}

    adapter._service = Service()
    adapter._fetch_submission_attachments(message)
    event_payload, version, _ = adapter._received_observation(message)
    assert calls == [{"userId": "me", "messageId": "native-1", "id": "calendar-1"}]
    assert version == 8
    assert event_payload["authenticated_member_route"] is True
    assert calendar in event_payload["submission_evidence"]["text"]
    ensure_autoloaded()
    registry.validate("messaging.received", 8, event_payload)
