"""
Adapter cross-cutting schemas — emitted by every L1 standalone adapter.

Per ADMINISTRATEME_BUILD.md §L1 (adapters emit typed events; never write
projections; never compose outbound — channel transport is OpenClaw's job
per SYSTEM_INVARIANTS.md §8 invariant 3), DECISIONS.md §D6 (no LLM SDK in
adminme/), §D7 (schemas register at v1), §D8 (correlation_id propagation).

Three event types register here at v1:

- ``adapter.error`` — emitted on transient or fatal adapter failure
  (auth, rate limit, network, api-changed, quota, unknown). ``retry_after_s``
  informs the supervisor's backoff. Severity = transient | fatal.

- ``adapter.authenticated`` — emitted once per startup-auth success. Carries
  a 1Password token-storage reference (``op://<vault>/<item>/<field>``),
  NEVER the secret value itself. ``scopes`` lists the OAuth scopes granted.

- ``adapter.cursor_advanced`` — emitted on milestone cursor persistence (every
  100 events OR every 5 minutes per adapter — anti-vision §"Quiet by default":
  one event per cursor tick would be noisy stream-of-everything). ``cursor_kind``
  is family-specific: ``history_id`` for Gmail, ``transactions_cursor`` for
  Plaid, etc.

The module sits alongside ``ingest.py``, ``crm.py``, ``domain.py``,
``governance.py`` — adapters is a peer cross-cutting family. Plugin-introduced
adapter event types still register via the ``hearth.event_types`` entry point
per §D9; this module covers first-party L1 adapters only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class AdapterErrorV1(BaseModel):
    model_config = {"extra": "forbid"}

    adapter_name: str = Field(min_length=1)
    adapter_family: str = Field(min_length=1)
    severity: Literal["transient", "fatal"]
    error_kind: Literal[
        "auth_failed",
        "rate_limited",
        "network",
        "api_changed",
        "quota_exceeded",
        "unknown",
    ]
    error_message: str = Field(min_length=1)
    retry_after_s: int | None = Field(default=None, ge=0)
    occurred_at: str = Field(min_length=1)


class AdapterAuthenticatedV1(BaseModel):
    model_config = {"extra": "forbid"}

    adapter_name: str = Field(min_length=1)
    adapter_family: str = Field(min_length=1)
    token_storage_ref: str = Field(
        min_length=1,
        pattern=r"^(op://[^/]+/[^/]+/[^/]+|keychain://[^/]+/[^/]+)$",
        description="Secure-provider reference — NEVER the secret itself.",
    )
    authenticated_at: str = Field(min_length=1)
    scopes: list[str] = Field(default_factory=list)


class AdapterAuthenticatedV2(AdapterAuthenticatedV1):
    """Member-bridge capability proof bound to one sealed configuration.

    Version 1 remains the generic central-adapter authentication event. Version
    2 is intentionally stricter: it cannot be copied across bridge enrollments
    or configuration changes, and it contains only content-free coordinates.
    """

    bridge_enrollment_id: str = Field(min_length=1)
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_account_id: str = Field(pattern=r"^provider-account:[0-9a-f]{32,64}$")


class ServiceOSAuthenticatedV3(BaseModel):
    """Actual local OS authority without inventing an OAuth/Keychain token."""

    model_config = {"extra": "forbid"}
    adapter_name: Literal["reminders-cos-apple-eventkit"]
    adapter_family: Literal["reminders"]
    authorization_kind: Literal["os-permission"]
    authenticated_at: str = Field(min_length=1)
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    device_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_ownership_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scopes: list[Literal["eventkit.reminders.read"]] = Field(min_length=1, max_length=1)


class AdapterCursorAdvancedV1(BaseModel):
    model_config = {"extra": "forbid"}

    adapter_name: str = Field(min_length=1)
    adapter_family: str = Field(min_length=1)
    cursor_kind: str = Field(min_length=1)
    cursor_value: str = Field(min_length=1)
    events_since_last: int = Field(ge=0)
    advanced_at: str = Field(min_length=1)


class AdapterRecordQuarantinedV1(BaseModel):
    """One provider record was durably accounted for without ingestion."""

    model_config = {"extra": "forbid"}
    adapter_name: str = Field(min_length=1, max_length=240)
    adapter_family: str = Field(min_length=1, max_length=120)
    external_id: str = Field(min_length=1, max_length=1000)
    content_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    error_kind: Literal["api_changed", "validation", "malformed"]
    error_message: str = Field(min_length=1, max_length=2000)
    quarantined_at: str = Field(min_length=1)
    provider_watermark: str | None = Field(default=None, max_length=1000)


registry.register("adapter.error", 1, AdapterErrorV1)
registry.register("adapter.authenticated", 1, AdapterAuthenticatedV1)
registry.register("adapter.authenticated", 2, AdapterAuthenticatedV2)
registry.register("adapter.authenticated", 3, ServiceOSAuthenticatedV3)
registry.register("adapter.cursor_advanced", 1, AdapterCursorAdvancedV1)
registry.register("adapter.record.quarantined", 1, AdapterRecordQuarantinedV1)
