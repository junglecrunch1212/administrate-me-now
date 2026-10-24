"""Private native-browser routes reference the canonical workstation inventory.

This configuration limits a supported OpenClaw browser. It does not import a
session, infer account identity from a profile label, or enable provider writes.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from adminme.workstation_inventory import IdentityInventory, StrictRecord, Text

BROWSER_POLICY_FILE = "workstation-browser.json"
BROWSER_PROFILE: Literal["chrome"] = "chrome"
Selector = Annotated[str, Field(min_length=1, max_length=300)]


class IdentityProbe(StrictRecord):
    selector: Selector
    attribute: Literal["textContent", "aria-label", "title"] = "textContent"
    extraction: Literal["exact", "email"] = "exact"


class BrowserResource(StrictRecord):
    resource_id: Text
    inventory_binding_id: Text
    inventory_binding_revision: int = Field(ge=1)
    # Operator records this from the native shared-tab route after verifying the
    # installed extension belongs to the intended actual Chrome profile.
    native_target_id: Text
    target_evidence_reference: Text
    # Exact URLs keep this first read slice bounded; no query/token wildcards.
    url: Annotated[str, Field(min_length=1, max_length=2000)]
    identity_probes: dict[Literal["account", "workspace", "role"], IdentityProbe]
    audience_member_ids: list[Text] = Field(min_length=1, max_length=20)
    restrictions: list[Text] = Field(default_factory=list, max_length=20)
    sensitivity: Literal["normal", "sensitive", "privileged"] = "sensitive"

    @model_validator(mode="after")
    def bounded_read(self) -> BrowserResource:
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or parsed.query
        ):
            raise ValueError(
                "browser resource needs an exact HTTPS URL without credentials or query"
            )
        if "account" not in self.identity_probes:
            raise ValueError("the destination account needs its own probe")
        if len(set(self.audience_member_ids)) != len(self.audience_member_ids):
            raise ValueError("browser resource audiences must be distinct")
        return self


class BrowserPolicy(StrictRecord):
    schema_version: Literal[1] = 1
    revision: int = Field(ge=1)
    enabled: bool = False
    profile: Literal["chrome"] = BROWSER_PROFILE
    resources: list[BrowserResource] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_resources(self) -> BrowserPolicy:
        if len({row.resource_id for row in self.resources}) != len(self.resources):
            raise ValueError("browser resource IDs must be distinct")
        if len({row.url for row in self.resources}) != len(self.resources):
            raise ValueError("each browser URL needs one unambiguous policy")
        return self


def browser_resource(
    policy: BrowserPolicy,
    inventory: IdentityInventory,
    resource_id: str,
    *,
    now: datetime | None = None,
    require_fresh_verification: bool = True,
) -> dict[str, Any]:
    """Resolve expectations only; a native destination probe proves identity."""
    matches = [row for row in policy.resources if row.resource_id == resource_id]
    if not policy.enabled or len(matches) != 1:
        raise ValueError("the requested browser resource is unavailable")
    resource = matches[0]
    bindings = [
        row
        for row in inventory.bindings
        if row.binding_id == resource.inventory_binding_id
    ]
    if len(bindings) != 1:
        raise ValueError("the canonical browser binding is unavailable")
    binding = bindings[0]
    if (
        binding.route.kind != "browser"
        or binding.operating_principal_id != COS_SERVICE_PRINCIPAL_ID
        or binding.resource_owner_principal_id != COS_SERVICE_PRINCIPAL_ID
    ):
        raise ValueError(
            "this browser slice requires CoS operating and source ownership"
        )
    now = now or datetime.now(timezone.utc)
    verification = binding.verification
    if resource.inventory_binding_revision != binding.revision or (
        require_fresh_verification
        and (
            binding.observed_identity != binding.expected_identity
            or verification is None
            or verification.binding_revision != binding.revision
            or not verification.observed_at <= now < verification.valid_until
        )
    ):
        raise ValueError("the native target needs a current verified canonical binding")
    expected = binding.expected_identity.model_dump(exclude_none=True)
    if set(resource.identity_probes) != set(expected):
        raise ValueError(
            "each expected account, workspace and role needs a current probe"
        )
    return {
        "resource_id": resource.resource_id,
        "inventory_binding_id": binding.binding_id,
        "binding_revision": binding.revision,
        "policy_revision": policy.revision,
        "native_target_id": resource.native_target_id,
        "target_evidence_reference": resource.target_evidence_reference,
        "profile": policy.profile,
        "device": binding.route.device,
        "os_user": binding.route.os_user,
        "browser_profile": binding.route.browser_profile,
        "url": resource.url,
        "expected_identity": expected,
        "identity_probes": {
            key: value.model_dump() for key, value in resource.identity_probes.items()
        },
        "audience_member_ids": list(resource.audience_member_ids),
        "restrictions": list(resource.restrictions),
        "sensitivity": resource.sensitivity,
    }


def assert_single_extension_profile(gateway: dict[str, Any]) -> None:
    """The pinned pairing CLI selects its first extension, not --browser-profile."""
    browser = gateway.get("browser")
    if not isinstance(browser, dict):
        raise ValueError("browser configuration is unavailable")
    profiles = browser.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError("one explicit Chrome extension profile is required")
    extensions = [
        name
        for name, row in profiles.items()
        if isinstance(row, dict) and row.get("driver") == "extension"
    ]
    # Native config implicitly creates `chrome`; using that explicit name avoids
    # a second implicit extension profile and an ambiguous first-match pair.
    if extensions != [BROWSER_PROFILE]:
        raise ValueError(
            "pairing requires exactly the explicit chrome extension profile"
        )
    if (
        browser.get("defaultProfile") != BROWSER_PROFILE
        or browser.get("allowSystemProfileImport") is not False
        or browser.get("enabled") is not True
    ):
        raise ValueError(
            "the explicit browser profile must have system-profile import disabled"
        )
    row = profiles[BROWSER_PROFILE]
    if set(row) - {"driver", "color", "cdpPort"}:
        raise ValueError(
            "extension profile cannot carry another browser/session attachment"
        )


def prepare_browser_gateway(gateway: dict[str, Any]) -> dict[str, Any]:
    """Prepare the native config without granting an agent any new tool access."""
    prepared = copy.deepcopy(gateway)
    browser = prepared.setdefault("browser", {})
    if not isinstance(browser, dict):
        raise ValueError("existing browser configuration is malformed")
    profiles = browser.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("existing browser profiles are malformed")
    if any(
        name != BROWSER_PROFILE
        and isinstance(row, dict)
        and row.get("driver") == "extension"
        for name, row in profiles.items()
    ):
        raise ValueError(
            "another extension profile exists; resolve its intended identity first"
        )
    if BROWSER_PROFILE in profiles and (
        not isinstance(profiles[BROWSER_PROFILE], dict)
        or profiles[BROWSER_PROFILE].get("driver") != "extension"
    ):
        raise ValueError("the chrome profile already uses another driver")
    profiles.setdefault(BROWSER_PROFILE, {"driver": "extension", "color": "#4477AA"})
    browser.update(
        {
            "enabled": True,
            "defaultProfile": BROWSER_PROFILE,
            "allowSystemProfileImport": False,
            # The host uses bounded read-only identity expressions. The native tool
            # guard still denies model-authored evaluate and every browser mutation.
            "evaluateEnabled": True,
        }
    )
    assert_single_extension_profile(prepared)
    return prepared
