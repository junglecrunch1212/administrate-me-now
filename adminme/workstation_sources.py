"""Current browser-source authority and retained lineage for CoS internal work."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from adminme.service_identities import COS_PARTY_ID, COS_SERVICE_PRINCIPAL_ID
from adminme.source_policy import SourceCoordinate
from adminme.workstation_browser import (
    BROWSER_POLICY_FILE,
    BrowserPolicy,
    browser_resource,
)
from adminme.workstation_inventory import (
    INVENTORY_FILE,
    IdentityInventory,
    read_private_document,
)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def current_resource(
    runtime: Any, resource_id: str, *, for_read: bool
) -> dict[str, Any]:
    directory = runtime.settings.instance.config_dir
    policy = BrowserPolicy.model_validate(
        read_private_document(directory / BROWSER_POLICY_FILE)
    )
    inventory = IdentityInventory.model_validate(
        read_private_document(directory / INVENTORY_FILE)
    )
    return browser_resource(
        policy, inventory, resource_id, require_fresh_verification=for_read
    )


def coordinate(runtime: Any, resource: dict[str, Any]) -> SourceCoordinate:
    identity = resource["expected_identity"]
    return SourceCoordinate(
        tenant_id=runtime.settings.instance.tenant_id,
        owner_principal_id=COS_SERVICE_PRINCIPAL_ID,
        owner_kind="service_principal",
        adapter_category="external_state_mirror",
        adapter_kind="openclaw-browser",
        adapter_instance_id=resource["inventory_binding_id"],
        provider="web",
        provider_account_id=identity["account"],
        workspace=identity.get("workspace") or "service",
        source_kind="native-browser-page",
        source_class="context_observation",
        trusted_route="service_private:" + COS_SERVICE_PRINCIPAL_ID,
        protected_source_id=resource["resource_id"],
        native_container_kind="source",
        native_container_id=digest(resource["url"]),
    )


def authorized_resource(
    runtime: Any, resource_id: str, *, for_read: bool
) -> dict[str, Any]:
    """Private route policy supplements existing source policy; it cannot override it."""
    resource = current_resource(runtime, resource_id, for_read=for_read)
    source = coordinate(runtime, resource)
    effective = runtime.settings_control.effective_source_policy_v2(
        COS_SERVICE_PRINCIPAL_ID, source
    )["policy"]
    # Every admitted run retains a genuine explicit human instruction. Its
    # original source authority is seeded beside this browser observation.
    if effective["acquisition_state"] != "active" or effective["context_mode"] not in {
        "typed_facts_safe_snippet",
        "content_on_explicit_authorized_demand",
    }:
        raise ValueError(
            "current source policy does not permit this requested page excerpt"
        )
    stable = {
        key: resource[key]
        for key in (
            "resource_id",
            "inventory_binding_id",
            "binding_revision",
            "device",
            "os_user",
            "browser_profile",
            "url",
            "expected_identity",
            "audience_member_ids",
            "restrictions",
            "sensitivity",
        )
    }
    stable["source_policy_key"] = source.key
    stable["context_mode"] = effective["context_mode"]
    resource["authority_sha256"] = digest(stable)
    resource["source_policy_key"] = source.key
    resource["route_sha256"] = digest(resource)
    return resource


def identity_matches(resource: dict[str, Any], actual: dict[str, Any]) -> bool:
    actual = {key: value for key, value in actual.items() if value is not None}
    expected = resource["expected_identity"]
    if actual.keys() != expected.keys():
        return False
    for field, wanted in expected.items():
        observed = actual[field]
        if not isinstance(observed, str):
            return False
        if resource["identity_probes"][field]["extraction"] == "email":
            if observed.lower() != wanted.lower():
                return False
        elif " ".join(observed.split()) != " ".join(wanted.split()):
            return False
    return True


def observation_authority(
    runtime: Any, session: Any, event: dict[str, Any]
) -> dict[str, Any] | None:
    """Historical evidence survives tab refresh; changed source authority stops reuse."""
    if (
        event["type"] != "cos.workstation.source_observed"
        or event["schema_version"] != 1
        or event["tenant_id"] != session.tenant_id
        or event["owner_scope"] != session.private_scope
        or event["visibility_scope"] != session.private_scope
        or event["source_adapter"] != "openclaw-browser"
    ):
        return None
    payload = event["payload"]
    try:
        current = authorized_resource(runtime, payload["resource_id"], for_read=False)
    except (OSError, ValueError, KeyError):
        return None
    if (
        current["authority_sha256"] != payload["authority_sha256"]
        or event["source_account_id"] != current["expected_identity"]["account"]
        or not identity_matches(current, payload["actual_identity"])
        or payload["operating_principal_id"] != session.principal.principal_id
        or payload["resource_owner_principal_id"] != session.principal.principal_id
    ):
        return None
    return {
        "source_event_id": event["event_id"],
        "authority_kind": "service_self",
        "authority_grant_id": None,
        "context_scope": "service_private",
        "context_member_id": None,
        "owner_party_id": COS_PARTY_ID,
        "source_owner_scope": event["owner_scope"],
        "source_visibility_scope": event["visibility_scope"],
        "source_sensitivity": event["sensitivity"],
        "source_adapter": event["source_adapter"],
        "source_account_id": event["source_account_id"],
        "source_policy_key": current["source_policy_key"],
        "source_policy_owner_id": COS_SERVICE_PRINCIPAL_ID,
        "source_audience": [],
        "read_mode": "typed_facts_safe_snippet",
        "restriction": "internal_processing_only",
    }
