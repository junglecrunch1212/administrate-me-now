"""Exact, separately enrolled CoS Calendar and Tasks read acquisitions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID

CONFIG_KEY = "cos_google_workspace"
IDENTITY_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
Purpose = Literal["calendar-read", "tasks-read"]
PURPOSES = {
    "calendar-read": (
        "calendaring-google-calendar",
        "calendar_id",
        "https://www.googleapis.com/auth/calendar.readonly",
    ),
    "tasks-read": (
        "tasks-google-tasks",
        "tasklist_id",
        "https://www.googleapis.com/auth/tasks.readonly",
    ),
}


def grant_scopes(purpose: str) -> tuple[str, str]:
    if purpose not in PURPOSES:
        raise ValueError("unsupported CoS Google grant purpose")
    return PURPOSES[purpose][2], IDENTITY_SCOPE


class CoSGoogleSelection(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    adapter_id: str = Field(min_length=1, max_length=240)
    purpose: Purpose
    provider_account_id: str = Field(min_length=1, max_length=500)
    resource_id: str = Field(min_length=1, max_length=1024)
    credential_reference: str = Field(pattern=r"^(keychain|op)://.+")
    owner_authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("resource_id")
    @classmethod
    def exact_resource(cls, value: str) -> str:
        if value != value.strip() or value in {"primary", "@default", "*"}:
            raise ValueError("CoS Google requires an exact provider resource id")
        return value

    def adapter_config(self) -> dict[str, Any]:
        kind, resource_key, scope = PURPOSES[self.purpose]
        return {
            "oauth_credentials_ref": self.credential_reference,
            "provider_account_id": self.provider_account_id,
            "user_email": self.provider_account_id,
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "workspace": "service",
            "ownership_attested": True,
            "binding_provenance": "oauth-subject",
            "read_only": True,
            "permission_scope": [scope],
            resource_key: self.resource_id,
            **(
                {"discover_calendars": False}
                if kind == "calendaring-google-calendar"
                else {"include_all_tasklists": False}
            ),
        }


class CoSGooglePolicy(BaseModel):
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    contract: Literal["cos-google-workspace-v1"] = "cos-google-workspace-v1"
    selections: list[CoSGoogleSelection] = Field(max_length=2)

    @model_validator(mode="after")
    def separate_grants(self) -> CoSGooglePolicy:
        for key in ("adapter_id", "purpose", "credential_reference"):
            values = [getattr(row, key) for row in self.selections]
            if len(values) != len(set(values)):
                raise ValueError("CoS Google selections require separate named grants")
        return self


def policy_from_config(value: dict[str, Any]) -> CoSGooglePolicy | None:
    if CONFIG_KEY not in value:
        return None
    if "cos_channel_acquisition" not in value:
        raise ValueError("CoS Google workspace requires the CoS acquisition policy")
    return CoSGooglePolicy.model_validate(value[CONFIG_KEY])


def selected_entries(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    policy = policy_from_config(value)
    if policy is None:
        return {}
    from adminme.adapters.cos_channel_acquisition import (
        policy_from_config as cos_policy,
    )

    owner = cos_policy(value)
    assert owner is not None
    entries = value.get("adapters", [])
    gmail = next(
        (row for row in entries if row.get("id") == owner.gmail_adapter_id), {}
    )
    gmail_reference = (gmail.get("config") or {}).get("oauth_credentials_ref")
    result = {}
    for selection in policy.selections:
        matches = [row for row in entries if row.get("id") == selection.adapter_id]
        if (
            len(matches) != 1
            or selection.adapter_id == owner.gmail_adapter_id
            or selection.provider_account_id != owner.gmail_provider_account_id
            or selection.credential_reference == gmail_reference
            or matches[0].get("kind") != PURPOSES[selection.purpose][0]
            or matches[0].get("config") != selection.adapter_config()
        ):
            raise ValueError("CoS Google owner/account/resource/grant binding mismatch")
        result[selection.adapter_id] = matches[0]
    return result


def validate_authorized_change(
    previous: dict[str, Any],
    requested: dict[str, Any],
    *,
    owner_authorization_sha256: str,
) -> None:
    """The guarded operation separately verifies the authorizing household head."""
    prior = policy_from_config(previous)
    current = policy_from_config(requested)
    old = {row.adapter_id: row for row in prior.selections} if prior else {}
    new = {row.adapter_id: row for row in current.selections} if current else {}
    selected_entries(previous)
    selected_entries(requested)
    before = {row["id"]: row for row in previous.get("adapters", [])}
    after = {row["id"]: row for row in requested.get("adapters", [])}
    for adapter_id, selection in new.items():
        enabled_changed = (before.get(adapter_id, {}).get("enabled") is not False) != (
            after.get(adapter_id, {}).get("enabled") is not False
        )
        if (old.get(adapter_id) != selection or enabled_changed) and (
            selection.owner_authorization_sha256 != owner_authorization_sha256
        ):
            raise ValueError(
                "CoS Google change requires this owner's exact authorization"
            )


def validate_oauth_adapter(config: dict[str, Any], *, purpose: str) -> None:
    """Fail before credential use, including direct adapter construction."""
    kind, resource_key, scope = PURPOSES[purpose]
    resource = config.get(resource_key)
    if (
        config.get("service_principal_id") != COS_SERVICE_PRINCIPAL_ID
        or config.get("member_id") is not None
        or config.get("workspace") != "service"
        or config.get("binding_provenance") != "oauth-subject"
        or config.get("ownership_attested") is not True
        or not isinstance(config.get("oauth_credentials_ref"), str)
        or not config["oauth_credentials_ref"].startswith(("keychain://", "op://"))
        or config.get("service_account_key_ref") is not None
        or config.get("read_only") is not True
        or config.get("enable_writes") not in (None, False)
        or config.get("permission_scope") != [scope]
        or config.get("b03_google_tasks_create") is not None
        or not isinstance(resource, str)
        or not resource.strip()
        or resource != resource.strip()
        or resource in {"primary", "@default", "*"}
        or (
            config.get("discover_calendars") is not False
            if kind == "calendaring-google-calendar"
            else config.get("include_all_tasklists") is not False
        )
    ):
        raise ValueError("CoS Google OAuth requires one exact read-only source")


def verify_source_resource(service: Any, *, purpose: str, resource_id: str) -> None:
    """Check provider ownership and canonical ID before polling source contents."""
    if purpose == "calendar-read":
        resource = service.calendarList().get(calendarId=resource_id).execute()
        if not isinstance(resource, dict) or resource.get("accessRole") != "owner":
            raise ValueError(
                "CoS Calendar source must be owned by its authorized account"
            )
    elif purpose == "tasks-read":
        resource = service.tasklists().get(tasklist=resource_id).execute()
    else:
        raise ValueError("unsupported CoS Google grant purpose")
    if not isinstance(resource, dict) or resource.get("id") != resource_id:
        raise ValueError("Google returned a different source resource")
