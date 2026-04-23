"""
places_assets_accounts projection handlers — three linked entity families.

Per ADMINISTRATEME_BUILD.md §3.8 and SYSTEM_INVARIANTS.md §2, §12.
Handlers are idempotent: re-applying the same event produces the same row
state (§2 invariant 4). Keyed by ``(tenant_id, place_id|asset_id|account_id)``.

Subscribed event types:
- ``place.added``    → INSERT/UPSERT a place row
- ``place.updated``  → UPDATE allowlisted fields
- ``asset.added``    → INSERT/UPSERT an asset row
- ``asset.updated``  → UPDATE allowlisted fields
- ``account.added``  → INSERT/UPSERT an account row
- ``account.updated``→ UPDATE allowlisted fields

Per [§2.3], handlers do not validate cross-projection references — an
``asset.added`` whose ``linked_place`` does not exist in ``places`` still
lands.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlcipher3

_log = logging.getLogger(__name__)

_UPDATABLE_PLACE_COLUMNS = frozenset({
    "display_name",
    "kind",
    "address_json",
    "geo_lat",
    "geo_lon",
    "attributes_json",
})

_UPDATABLE_ASSET_COLUMNS = frozenset({
    "display_name",
    "kind",
    "linked_place",
    "attributes_json",
})

_UPDATABLE_ACCOUNT_COLUMNS = frozenset({
    "display_name",
    "organization",
    "kind",
    "status",
    "billing_rrule",
    "next_renewal",
    "login_vault_ref",
    "linked_asset",
    "linked_place",
    "attributes_json",
})


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    event_type = envelope["type"]
    dispatch = {
        "place.added": apply_place_added,
        "place.updated": apply_place_updated,
        "asset.added": apply_asset_added,
        "asset.updated": apply_asset_updated,
        "account.added": apply_account_added,
        "account.updated": apply_account_updated,
    }
    handler = dispatch.get(event_type)
    if handler is None:
        return
    handler(envelope, conn)


def _json_dump(value: Any) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def apply_place_added(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO places (
            place_id, tenant_id, display_name, kind, address_json,
            geo_lat, geo_lon, attributes_json, owner_scope, visibility_scope,
            sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, place_id) DO UPDATE SET
            display_name     = excluded.display_name,
            kind             = excluded.kind,
            address_json     = excluded.address_json,
            geo_lat          = excluded.geo_lat,
            geo_lon          = excluded.geo_lon,
            attributes_json  = excluded.attributes_json,
            owner_scope      = excluded.owner_scope,
            visibility_scope = excluded.visibility_scope,
            sensitivity      = excluded.sensitivity,
            last_event_id    = excluded.last_event_id
        """,
        (
            p["place_id"],
            envelope["tenant_id"],
            p["display_name"],
            p["kind"],
            _json_dump(p.get("address_json")),
            p.get("geo_lat"),
            p.get("geo_lon"),
            _json_dump(p.get("attributes") or {}),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_place_updated(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    updates: dict[str, Any] = dict(p.get("field_updates") or {})
    if "attributes" in updates and "attributes_json" not in updates:
        updates["attributes_json"] = _json_dump(updates.pop("attributes"))
    if "address_json" in updates:
        updates["address_json"] = _json_dump(updates["address_json"])
    columns = [k for k in updates if k in _UPDATABLE_PLACE_COLUMNS]
    if not columns:
        return
    assignments = ", ".join(f"{c} = ?" for c in columns) + ", last_event_id = ?"
    params: list[Any] = [updates[c] for c in columns]
    params.append(envelope["event_id"])
    params.extend([envelope["tenant_id"], p["place_id"]])
    conn.execute(
        f"UPDATE places SET {assignments} WHERE tenant_id = ? AND place_id = ?",
        params,
    )


def apply_asset_added(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO assets (
            asset_id, tenant_id, display_name, kind, linked_place,
            attributes_json, owner_scope, visibility_scope, sensitivity,
            last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, asset_id) DO UPDATE SET
            display_name     = excluded.display_name,
            kind             = excluded.kind,
            linked_place     = excluded.linked_place,
            attributes_json  = excluded.attributes_json,
            owner_scope      = excluded.owner_scope,
            visibility_scope = excluded.visibility_scope,
            sensitivity      = excluded.sensitivity,
            last_event_id    = excluded.last_event_id
        """,
        (
            p["asset_id"],
            envelope["tenant_id"],
            p["display_name"],
            p["kind"],
            p.get("linked_place"),
            _json_dump(p.get("attributes") or {}),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_asset_updated(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    updates: dict[str, Any] = dict(p.get("field_updates") or {})
    if "attributes" in updates and "attributes_json" not in updates:
        updates["attributes_json"] = _json_dump(updates.pop("attributes"))
    columns = [k for k in updates if k in _UPDATABLE_ASSET_COLUMNS]
    if not columns:
        return
    assignments = ", ".join(f"{c} = ?" for c in columns) + ", last_event_id = ?"
    params: list[Any] = [updates[c] for c in columns]
    params.append(envelope["event_id"])
    params.extend([envelope["tenant_id"], p["asset_id"]])
    conn.execute(
        f"UPDATE assets SET {assignments} WHERE tenant_id = ? AND asset_id = ?",
        params,
    )


def apply_account_added(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    conn.execute(
        """
        INSERT INTO accounts (
            account_id, tenant_id, display_name, organization, kind, status,
            billing_rrule, next_renewal, login_vault_ref, linked_asset,
            linked_place, attributes_json, owner_scope, visibility_scope,
            sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, account_id) DO UPDATE SET
            display_name     = excluded.display_name,
            organization     = excluded.organization,
            kind             = excluded.kind,
            status           = excluded.status,
            billing_rrule    = excluded.billing_rrule,
            next_renewal     = excluded.next_renewal,
            login_vault_ref  = excluded.login_vault_ref,
            linked_asset     = excluded.linked_asset,
            linked_place     = excluded.linked_place,
            attributes_json  = excluded.attributes_json,
            owner_scope      = excluded.owner_scope,
            visibility_scope = excluded.visibility_scope,
            sensitivity      = excluded.sensitivity,
            last_event_id    = excluded.last_event_id
        """,
        (
            p["account_id"],
            envelope["tenant_id"],
            p["display_name"],
            p["organization_party_id"],
            p["kind"],
            p.get("status", "active"),
            p.get("billing_rrule"),
            p.get("next_renewal"),
            p.get("login_vault_ref"),
            p.get("linked_asset"),
            p.get("linked_place"),
            _json_dump(p.get("attributes") or {}),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def apply_account_updated(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    p = envelope["payload"]
    updates: dict[str, Any] = dict(p.get("field_updates") or {})
    if "organization_party_id" in updates and "organization" not in updates:
        updates["organization"] = updates.pop("organization_party_id")
    if "attributes" in updates and "attributes_json" not in updates:
        updates["attributes_json"] = _json_dump(updates.pop("attributes"))
    columns = [k for k in updates if k in _UPDATABLE_ACCOUNT_COLUMNS]
    if not columns:
        return
    assignments = ", ".join(f"{c} = ?" for c in columns) + ", last_event_id = ?"
    params: list[Any] = [updates[c] for c in columns]
    params.append(envelope["event_id"])
    params.extend([envelope["tenant_id"], p["account_id"]])
    conn.execute(
        f"UPDATE accounts SET {assignments} WHERE tenant_id = ? AND account_id = ?",
        params,
    )
