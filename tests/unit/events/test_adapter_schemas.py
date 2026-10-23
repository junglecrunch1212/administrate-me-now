"""``adapter.error`` / ``adapter.authenticated`` / ``adapter.cursor_advanced``
schema-registration canaries.

Asserts the three event types added in prompt 11a commit 1 register at
v1 per [D7], the Pydantic models enforce the required-field contracts
from the prompt §7 Deliverables table, and ``extra="forbid"`` rejects
unknown payload keys — matching the shape established by
``test_10c_iii_schemas.py``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adminme.events.registry import ensure_autoloaded, registry
from adminme.events.schemas.adapters import (
    AdapterAuthenticatedV1,
    AdapterCursorAdvancedV1,
    AdapterErrorV1,
    ServiceOSAuthenticatedV3,
)


# ---------------------------------------------------------------------------
# adapter.error v1
# ---------------------------------------------------------------------------


def test_adapter_error_registered_at_v1() -> None:
    ensure_autoloaded()
    assert registry.get("adapter.error", 1) is AdapterErrorV1
    assert registry.latest_version("adapter.error") == 1


def test_adapter_error_minimum_payload_validates() -> None:
    instance = AdapterErrorV1(
        adapter_name="messaging-gmail-api",
        adapter_family="messaging",
        severity="transient",
        error_kind="rate_limited",
        error_message="429 too many requests",
        retry_after_s=60,
        occurred_at="2026-05-16T12:00:00Z",
    )
    assert instance.severity == "transient"
    assert instance.retry_after_s == 60


def test_adapter_error_retry_after_optional() -> None:
    instance = AdapterErrorV1(
        adapter_name="messaging-gmail-api",
        adapter_family="messaging",
        severity="fatal",
        error_kind="auth_failed",
        error_message="invalid credentials",
        occurred_at="2026-05-16T12:00:00Z",
    )
    assert instance.retry_after_s is None


def test_adapter_error_rejects_invalid_severity() -> None:
    with pytest.raises(ValidationError):
        AdapterErrorV1(
            adapter_name="messaging-gmail-api",
            adapter_family="messaging",
            severity="warning",  # type: ignore[arg-type]
            error_kind="network",
            error_message="boom",
            occurred_at="2026-05-16T12:00:00Z",
        )


def test_adapter_error_rejects_invalid_error_kind() -> None:
    with pytest.raises(ValidationError):
        AdapterErrorV1(
            adapter_name="messaging-gmail-api",
            adapter_family="messaging",
            severity="transient",
            error_kind="vibes",  # type: ignore[arg-type]
            error_message="boom",
            occurred_at="2026-05-16T12:00:00Z",
        )


def test_adapter_error_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        AdapterErrorV1(
            adapter_name="messaging-gmail-api",
            adapter_family="messaging",
            severity="transient",
            error_kind="network",
            error_message="boom",
            occurred_at="2026-05-16T12:00:00Z",
            mystery="nope",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# adapter.authenticated v1
# ---------------------------------------------------------------------------


def test_adapter_authenticated_registered_at_v1() -> None:
    ensure_autoloaded()
    assert registry.get("adapter.authenticated", 1) is AdapterAuthenticatedV1
    assert registry.get("adapter.authenticated", 3) is ServiceOSAuthenticatedV3
    assert registry.latest_version("adapter.authenticated") == 3


def test_adapter_authenticated_minimum_payload_validates() -> None:
    instance = AdapterAuthenticatedV1(
        adapter_name="messaging-gmail-api",
        adapter_family="messaging",
        token_storage_ref="op://AdministrateMe/gmail-sandbox/service-account-json",
        authenticated_at="2026-05-16T12:00:00Z",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )
    assert instance.token_storage_ref.startswith("op://")
    assert "gmail.readonly" in instance.scopes[0]


def test_adapter_authenticated_rejects_raw_secret_token_ref() -> None:
    """token_storage_ref must be a 1Password reference; raw secrets are
    a constitutional violation per ADMINISTRATEME_BUILD.md §L1."""
    with pytest.raises(ValidationError):
        AdapterAuthenticatedV1(
            adapter_name="messaging-gmail-api",
            adapter_family="messaging",
            token_storage_ref="ya29.A0AfH6SMB...",  # raw token, not a ref
            authenticated_at="2026-05-16T12:00:00Z",
            scopes=[],
        )


def test_adapter_authenticated_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        AdapterAuthenticatedV1(
            adapter_name="messaging-gmail-api",
            adapter_family="messaging",
            token_storage_ref="op://v/i/f",
            authenticated_at="2026-05-16T12:00:00Z",
            scopes=[],
            secret_token="leaked",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# adapter.cursor_advanced v1
# ---------------------------------------------------------------------------


def test_adapter_cursor_advanced_registered_at_v1() -> None:
    ensure_autoloaded()
    assert registry.get("adapter.cursor_advanced", 1) is AdapterCursorAdvancedV1
    assert registry.latest_version("adapter.cursor_advanced") == 1


def test_adapter_cursor_advanced_minimum_payload_validates() -> None:
    instance = AdapterCursorAdvancedV1(
        adapter_name="messaging-gmail-api",
        adapter_family="messaging",
        cursor_kind="history_id",
        cursor_value="987654321",
        events_since_last=100,
        advanced_at="2026-05-16T12:00:00Z",
    )
    assert instance.events_since_last == 100


def test_adapter_cursor_advanced_rejects_negative_events_since_last() -> None:
    with pytest.raises(ValidationError):
        AdapterCursorAdvancedV1(
            adapter_name="messaging-gmail-api",
            adapter_family="messaging",
            cursor_kind="history_id",
            cursor_value="x",
            events_since_last=-1,
            advanced_at="2026-05-16T12:00:00Z",
        )


def test_adapter_cursor_advanced_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        AdapterCursorAdvancedV1(
            adapter_name="messaging-gmail-api",
            adapter_family="messaging",
            cursor_kind="history_id",
            cursor_value="x",
            events_since_last=0,
            advanced_at="2026-05-16T12:00:00Z",
            stray="nope",  # type: ignore[call-arg]
        )
