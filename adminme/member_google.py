"""Member-owned Google consent state and exact service permission proposals.

OAuth handoffs are encrypted with the instance key. Provider credentials remain
in Keychain. This module never sends, shares, or infers obligations from mail.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator

from adminme.adapters.member_google_connections import (
    CONFIG_KEY,
    SCOPES,
    GoogleConnectionPolicy,
    binding_digest,
    connection_config,
    policy_from_config,
)

SERVICES = {
    "gmail": ("messaging-gmail-api", "Gmail", "Read new mail", "Send and manage mail"),
    "calendar": (
        "calendaring-google-calendar",
        "Google Calendar",
        "Read calendars and events",
        "Create, update and delete events",
    ),
    "tasks": (
        "tasks-google-tasks",
        "Google Tasks",
        "Read lists and tasks",
        "Create, update and complete tasks",
    ),
}
EVENT = "settings.google_connection_authorized"
COMPLETE_EVENT = "settings.google_connection_completed"
AAD = b"member-google-handoffs-v1"
IDENTITY_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]
TTL = 600


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class ServicePermission(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    read: StrictBool
    write: StrictBool

    @model_validator(mode="after")
    def write_requires_read(self) -> ServicePermission:
        if self.write and not self.read:
            raise ValueError("Write requires Read for this service")
        return self


class ServiceSelection(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    gmail: ServicePermission
    calendar: ServicePermission
    tasks: ServicePermission


class PermissionRequest(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    request_id: str
    services: ServiceSelection
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("request_id")
    @classmethod
    def valid_request(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("Request identifier must be a canonical UUID")
        return value


class AuthorizationRequest(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    request_id: str
    expected_account_id: str = Field(min_length=3, max_length=254)
    workspace: Literal["personal", "work"]
    services: ServiceSelection

    @field_validator("request_id")
    @classmethod
    def valid_request(cls, value: str) -> str:
        return PermissionRequest.valid_request(value)

    @field_validator("expected_account_id")
    @classmethod
    def email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("Enter the Google account email you intend to connect")
        return normalized

    @model_validator(mode="after")
    def selected(self) -> AuthorizationRequest:
        if not any(row["read"] for row in self.services.model_dump().values()):
            raise ValueError("Select at least one service to connect")
        return self


def oauth_scopes(services: dict[str, Any]) -> list[str]:
    scopes = set(IDENTITY_SCOPES)
    for name, (kind, *_rest) in SERVICES.items():
        selection = services[name]
        if not selection["read"]:
            continue
        scopes.add(SCOPES[kind])
        if selection["write"]:
            if name == "gmail":
                scopes.update(
                    [
                        "https://www.googleapis.com/auth/gmail.modify",
                        "https://www.googleapis.com/auth/gmail.send",
                    ]
                )
            else:
                scopes.add(SCOPES[kind].removesuffix(".readonly"))
    return sorted(scopes)


def account_id(member: str, email: str) -> str:
    return "google-" + digest([member, email.casefold()])[:24]


def owned_accounts(config: dict[str, Any], member: str) -> dict[str, dict[str, Any]]:
    policy = policy_from_config(config)
    selections = {row.adapter_id: row for row in policy.selections} if policy else {}
    accounts: dict[str, dict[str, Any]] = {}
    by_kind = {value[0]: key for key, value in SERVICES.items()}
    for row in config.get("adapters", []):
        values = row.get("config", {})
        kind = str(row.get("kind", "")).removeprefix("adapter:")
        if (
            values.get("member_id") != member
            or values.get("service_principal_id")
            or kind not in by_kind
        ):
            continue
        email = str(values.get("provider_account_id", ""))
        key = account_id(member, email)
        account = accounts.setdefault(
            key,
            {
                "id": key,
                "email": email,
                "workspace": values.get("workspace"),
                "rows": {},
                "services": {},
            },
        )
        name = by_kind[kind]
        if name in account["rows"] or account["workspace"] != values.get("workspace"):
            raise ValueError("Google account has conflicting service bindings")
        account["rows"][name] = row
        selected = selections.get(row["id"])
        read = row.get("enabled") is not False and selected is not None
        account["services"][name] = {
            "read": read,
            "write": read and selected.access == "read_write" if selected else False,
        }
    for account in accounts.values():
        for name in SERVICES:
            account["services"].setdefault(name, {"read": False, "write": False})
        account["revision"] = digest(
            {
                "rows": account["rows"],
                "selections": [
                    row.model_dump()
                    for row in (policy.selections if policy else [])
                    if row.adapter_id
                    in {entry["id"] for entry in account["rows"].values()}
                ],
            }
        )
    return accounts


def propose_config(
    config: dict[str, Any],
    *,
    member: str,
    email: str,
    workspace: str,
    services: dict[str, Any],
    credential_ref: str | None,
) -> tuple[dict[str, Any], str]:
    """Retain all accounts/history; replace only this owner's exact account rows."""
    from bootstrap.member_google_transition import google_change_digest

    result = copy.deepcopy(config)
    current = owned_accounts(config, member).get(account_id(member, email))
    if current and current["workspace"] != workspace:
        raise ValueError("An existing account's workspace cannot change during consent")
    # Do not accept a second household owner for any retained Google account.
    for row in config.get("adapters", []):
        values = row.get("config", {})
        if (
            str(row.get("kind", "")).removeprefix("adapter:") in SCOPES
            and str(values.get("provider_account_id", "")).casefold()
            == email.casefold()
            and values.get("member_id") != member
        ):
            raise ValueError("This Google account already belongs to another principal")
    previous = policy_from_config(config) or GoogleConnectionPolicy(selections=[])
    selected = {row.adapter_id: row.model_dump() for row in previous.selections}
    changed_selection_ids: set[str] = set()
    for name, (kind, *_rest) in SERVICES.items():
        row = copy.deepcopy(current["rows"].get(name)) if current else None
        if row is None:
            if credential_ref is None:
                raise ValueError(
                    "Connect this account with Google before enabling services"
                )
            row = {
                "id": f"{account_id(member, email)}-{name}",
                "kind": kind,
                "config": {
                    "member_id": member,
                    "provider_account_id": email,
                    "user_email": email,
                    "workspace": workspace,
                    "binding_provenance": "oauth-subject",
                    "ownership_attested": True,
                    "oauth_credentials_ref": credential_ref,
                },
            }
        if credential_ref:
            row["config"]["oauth_credentials_ref"] = credential_ref
        access: Literal["read_only", "read_write"] = (
            "read_write" if services[name]["write"] else "read_only"
        )
        row["config"] = connection_config(row, access=access)
        row["enabled"] = services[name]["read"]
        if name == "calendar":
            row["config"].setdefault("calendar_id", "primary")
            row["config"].setdefault("discover_calendars", True)
        if name == "tasks":
            row["config"].setdefault("tasklist_id", "@default")
        result["adapters"] = [
            entry for entry in result.get("adapters", []) if entry["id"] != row["id"]
        ] + [row]
        # Retain disabled selections too: manual intake and prior ownership
        # remain enforceable when Read is off, including for retained facts.
        changed_selection_ids.add(row["id"])
        selected[row["id"]] = {
            "adapter_id": row["id"],
            "member_id": member,
            "provider_account_id": email,
            "access": access,
            "binding_sha256": binding_digest(row, access=access),
            "owner_authorization_sha256": "0" * 64,
        }
    result[CONFIG_KEY] = {
        **previous.model_dump(),
        "selections": list(selected.values()),
    }
    proof = google_change_digest(result, member)
    for row in result[CONFIG_KEY]["selections"]:
        if row["adapter_id"] in changed_selection_ids:
            row["owner_authorization_sha256"] = proof
    return result, proof


class GoogleHandoffs:
    """Bounded encrypted state, including immutable unknown-request recovery."""

    def __init__(self, path: Path, key: bytes) -> None:
        self.path = path
        self.cipher = AESGCM(key)

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        if self.path.is_symlink() or self.path.stat().st_mode & 0o077:
            raise ValueError("Google handoff storage permissions are invalid")
        raw = self.path.read_bytes()
        if len(raw) > 2_000_000:
            raise ValueError("Google handoff storage exceeded its bound")
        value = json.loads(self.cipher.decrypt(raw[:12], raw[12:], AAD))
        if not isinstance(value, dict):
            raise ValueError("Google handoff storage is invalid")
        return value

    def save(self, values: dict[str, Any]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        nonce = secrets.token_bytes(12)
        payload = nonce + self.cipher.encrypt(nonce, canonical(values), AAD)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".google-handoffs-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def put(self, record: dict[str, Any]) -> None:
        values = self.read()
        # Keep terminal request receipts for idempotency, with a hard bound:
        # never evict a record and silently allow its UUID to change meaning.
        if record["request_id"] not in values and len(values) >= 500:
            raise ValueError("Google connection request history needs maintenance")
        values[record["request_id"]] = record
        self.save(values)

    def get(self, request_id: str, member: str) -> dict[str, Any] | None:
        record = self.read().get(request_id)
        return record if record and record["member_id"] == member else None

    @staticmethod
    def public(record: dict[str, Any]) -> dict[str, Any]:
        state = record["state"]
        if state == "pending" and record["expires_epoch"] <= time.time():
            state = "expired"
        result = {
            "request_id": record["request_id"],
            "state": state,
            "message": record.get("message", "Review and continue with Google."),
            "account_id": record["account_id"],
        }
        if state == "pending":
            result.update(
                authorization_url=record["authorization_url"],
                expires_at=record["expires_at"],
            )
        return result
