"""Exact, separately authorized personal Google reads beside CoS intake.

This ceiling grants neither provider writes nor candidate extraction. It is
published only by the guarded configuration operation, never by connection
resume or by retaining the old disposable sample.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

CONFIG_KEY = "member_google_observation"
CONTRACT = "member-google-readonly-observation-v1"
SCOPES = {
    "messaging-gmail-api": "https://www.googleapis.com/auth/gmail.readonly",
    "calendaring-google-calendar": "https://www.googleapis.com/auth/calendar.readonly",
    "tasks-google-tasks": "https://www.googleapis.com/auth/tasks.readonly",
}
SOURCES = {
    "messaging-gmail-api": "adapter:gmail",
    "calendaring-google-calendar": "adapter:google_calendar",
    "tasks-google-tasks": "adapter:google_tasks",
}
READ_CONTROLS: dict[str, Any] = {"read_only": True, "enable_writes": False}
MAIL_CONTROLS: dict[str, Any] = {
    "initial_backfill_days": 0,
    "initial_backfill_max_messages": 0,
    "historical_seed_queries": [],
    "historical_seed_max_messages_per_query": 0,
    "historical_related_max_participants": 0,
    "historical_related_messages_per_participant": 0,
}


class GoogleObservationSelection(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    adapter_id: str = Field(min_length=1, max_length=240)
    member_id: str = Field(min_length=1, max_length=240)
    provider_account_id: str = Field(min_length=1, max_length=500)
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GoogleObservationPolicy(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    contract: Literal["member-google-readonly-observation-v1"] = (
        "member-google-readonly-observation-v1"
    )
    selections: list[GoogleObservationSelection] = Field(max_length=30)
    provider_writes: Literal[False] = False
    automated_candidate_extraction: Literal[False] = False
    historical_expansion: Literal[False] = False

    @model_validator(mode="after")
    def unique_adapters(self) -> GoogleObservationPolicy:
        if len({row.adapter_id for row in self.selections}) != len(self.selections):
            raise ValueError("Google observation selections must be unique")
        return self


def policy_from_config(value: dict[str, Any]) -> GoogleObservationPolicy | None:
    if CONFIG_KEY not in value:
        return None
    if "cos_channel_acquisition" not in value:
        raise ValueError(
            "member Google observation requires the CoS acquisition ceiling"
        )
    return GoogleObservationPolicy.model_validate(value[CONFIG_KEY])


def readonly_config(entry: dict[str, Any]) -> dict[str, Any]:
    kind = str(entry.get("kind") or "").removeprefix("adapter:")
    if kind not in SCOPES:
        raise ValueError(
            "member Google observation only supports reviewed Google reads"
        )
    config = copy.deepcopy(entry.get("config") or {})
    config.update(READ_CONTROLS, permission_scope=[SCOPES[kind]])
    if kind == "messaging-gmail-api":
        config.update(copy.deepcopy(MAIL_CONTROLS))
    return config


def binding_digest(entry: dict[str, Any]) -> str:
    """Bind every provider/config coordinate after the enforced narrowing.

    Pause/resume is deliberately outside this digest. Narrowing is idempotent,
    so both the requested and installed configurations have the same binding.
    """
    document = {
        "id": entry.get("id"),
        "kind": str(entry.get("kind") or "").removeprefix("adapter:"),
        "config": readonly_config(entry),
    }
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def selected_entries(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    policy = policy_from_config(value)
    if policy is None:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for selection in policy.selections:
        matches = [
            row
            for row in value.get("adapters", [])
            if isinstance(row, dict) and row.get("id") == selection.adapter_id
        ]
        if len(matches) != 1:
            raise ValueError("Google observation requires one exact existing adapter")
        entry = matches[0]
        config = entry.get("config") or {}
        if (
            config.get("member_id") != selection.member_id
            or config.get("provider_account_id") != selection.provider_account_id
            or config.get("user_email", selection.provider_account_id)
            != selection.provider_account_id
            or config.get("service_principal_id")
            or config.get("service_account_key_ref")
            or config.get("ownership_attested") is not True
            or config.get("binding_provenance") != "oauth-subject"
            or not isinstance(config.get("oauth_credentials_ref"), str)
            or not config["oauth_credentials_ref"].startswith(("keychain://", "op://"))
            or "disposable_test_acquisition" in config
            or config.get("b03_google_tasks_create") is not None
            or binding_digest(entry) != selection.binding_sha256
        ):
            raise ValueError(
                "Google observation owner/account/configuration binding mismatch"
            )
        result[selection.adapter_id] = entry
    return result


def manual_sources(value: dict[str, Any]) -> frozenset[tuple[str, str]]:
    """Keep the legacy source-family candidate seam closed for selected owners."""
    return frozenset(
        (entry["config"]["member_id"], SOURCES[entry["kind"].removeprefix("adapter:")])
        for entry in selected_entries(value).values()
    )


def validate_authorized_change(
    previous: dict[str, Any],
    requested: dict[str, Any],
    *,
    owner_member_id: str,
    owner_authorization_sha256: str,
) -> None:
    """A principal may add/revoke only their own exact selections."""
    before = policy_from_config(previous)
    after = policy_from_config(requested)
    old = {row.adapter_id: row for row in before.selections} if before else {}
    new = {row.adapter_id: row for row in after.selections} if after else {}
    selected_entries(requested)
    for adapter_id in old.keys() | new.keys():
        prior, current = old.get(adapter_id), new.get(adapter_id)
        if prior == current:
            continue
        if prior is not None and prior.member_id != owner_member_id:
            raise ValueError(
                "cannot change another member's Google observation selection"
            )
        if current is not None and (
            current.member_id != owner_member_id
            or current.owner_authorization_sha256 != owner_authorization_sha256
        ):
            raise ValueError(
                "Google observation requires this member's exact authorization"
            )
