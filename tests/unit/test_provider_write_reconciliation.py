"""Exact provider identity and independently observed state, never title matching."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from adminme.provider_write_reconciliation import (
    ProviderWriteReconciler,
    compare_provider_state,
)


def _facts() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    identity = {
        "tenant_id": "fixture-tenant",
        "owner_scope": "private:fixture-member",
        "visibility_scope": "private:fixture-member",
    }
    coordinates = {
        "command_id": "fixture-command",
        "member_id": "fixture-member",
        "provider": "google_calendar",
        "provider_account_id": "fixture-account",
        "operation": "create_event",
        "domain_approval_event_id": "fixture-domain",
        "openclaw_approval_event_id": "fixture-openclaw",
        "request_fingerprint": "a" * 64,
        "origin_proposal_id": "fixture-proposal",
    }
    request = {
        **identity,
        "event_id": "001",
        "type": "external_sync.write_requested",
        "schema_version": 2,
        "payload": {
            **coordinates,
            "payload": {
                "calendar_id": "fixture-calendar",
                "summary": "Fixture appointment",
                "start": "2026-10-08T09:00:00-04:00",
                "end": "2026-10-08T10:00:00-04:00",
            },
        },
    }
    terminal = {
        **identity,
        "event_id": "002",
        "causation_id": "001",
        "type": "external_sync.write_completed",
        "schema_version": 2,
        "payload": {
            **coordinates,
            "command_event_id": "001",
            "external_id": "fixture-native-object",
            "provider_called": True,
            "suppressed": False,
            "result": {"applied": True},
        },
    }
    observation = {
        **identity,
        "event_id": "003",
        "type": "calendar.event_added",
        "source_adapter": "fixture-calendar-adapter",
        "payload": {
            "member_id": "fixture-member",
            "provider": "google_calendar",
            "provider_account_id": "fixture-account",
            "calendar_external_id": "fixture-calendar",
            "external_event_id": "fixture-native-object",
            "summary": "Fixture appointment",
            "start": "2026-10-08T13:00:00Z",
            "end": "2026-10-08T14:00:00Z",
        },
    }
    return request, terminal, observation


def test_exact_identity_and_equivalent_instants_match() -> None:
    request, terminal, observation = _facts()
    assert ProviderWriteReconciler._valid_request(request, terminal)
    assert ProviderWriteReconciler._matches(request, terminal, observation)
    assert compare_provider_state(request, observation)[0] == "matched"


@pytest.mark.parametrize(
    ("location", "field", "value"),
    [
        ("root", "tenant_id", "another-tenant"),
        ("root", "owner_scope", "private:another-member"),
        ("root", "visibility_scope", "household"),
        ("root", "event_id", "000"),
        ("root", "causation_id", "001"),
        ("root", "source_adapter", "external-sync-write-echo"),
        ("payload", "provider_account_id", "another-account"),
        ("payload", "member_id", "another-member"),
        ("payload", "calendar_external_id", "another-calendar"),
        ("payload", "external_event_id", "another-object"),
        ("payload", "provider", "apple_calendar"),
        ("payload", "service_principal_id", "fixture-service"),
    ],
)
def test_similar_content_cannot_replace_identity(
    location: str, field: str, value: str
) -> None:
    request, terminal, observation = _facts()
    (observation if location == "root" else observation["payload"])[field] = value
    assert not ProviderWriteReconciler._matches(request, terminal, observation)


def test_drift_missing_and_unobservable_fields_are_distinct() -> None:
    request, _, observation = _facts()
    observation["payload"]["summary"] = "Changed externally"
    outcome, _, _, differences = compare_provider_state(request, observation)
    assert outcome == "drifted"
    assert differences == ["summary"]
    observation["type"] = "calendar.event_deleted"
    assert compare_provider_state(request, observation)[0] == "missing"
    observation["type"] = "calendar.event_added"
    request["payload"]["payload"]["unsupported_provider_detail"] = "requested"
    assert compare_provider_state(request, observation)[0] == "incomplete"


def test_date_only_tasks_and_zoned_apple_dates_keep_their_semantics() -> None:
    request, _, observation = _facts()
    request["payload"].update(provider="google_tasks", operation="create_task")
    request["payload"]["payload"] = {"title": "Fixture task", "due": "2026-10-09"}
    observation["payload"].update(title="Fixture task", due="2026-10-09T00:00:00Z")
    assert compare_provider_state(request, observation)[0] == "matched"
    request, _, observation = _facts()
    request["payload"]["provider"] = "apple_calendar"
    observation["payload"]["start"] = {
        "year": 2026,
        "month": 10,
        "day": 8,
        "hour": 9,
        "minute": 0,
        "time_zone": "America/New_York",
        "floating": False,
    }
    assert compare_provider_state(request, observation)[0] == "matched"
    floating = deepcopy(observation)
    floating["payload"]["start"]["floating"] = True
    assert compare_provider_state(request, floating)[0] == "drifted"
