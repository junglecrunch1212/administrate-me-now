"""
places_assets_accounts projection read queries.

Per ADMINISTRATEME_BUILD.md §3.8 and SYSTEM_INVARIANTS.md §2, §6, §12.
Every public query takes a ``session: Session`` per [§6.1] and runs through
``scope.filter_rows`` before return.

Per DECISIONS.md §D4: the CRM/ops spine is a shared L3 concern.
"""

from __future__ import annotations

from typing import Any

import sqlcipher3

from adminme.lib.scope import filter_one, filter_rows
from adminme.lib.session import Session


def get_place(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    place_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM places WHERE tenant_id = ? AND place_id = ?",
        (session.tenant_id, place_id),
    ).fetchone()
    return filter_one(session, dict(row) if row is not None else None)


def list_places(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    if kind is None:
        rows = conn.execute(
            "SELECT * FROM places WHERE tenant_id = ? AND lifecycle_status='active' "
            "ORDER BY display_name ASC",
            (session.tenant_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM places WHERE tenant_id = ? AND kind = ? "
            "AND lifecycle_status='active' "
            "ORDER BY display_name ASC",
            (session.tenant_id, kind),
        ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def get_asset(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    asset_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM assets WHERE tenant_id = ? AND asset_id = ?",
        (session.tenant_id, asset_id),
    ).fetchone()
    return filter_one(session, dict(row) if row is not None else None)


def list_assets_for_place(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    place_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM assets WHERE tenant_id = ? AND linked_place = ? "
        "AND lifecycle_status='active' "
        "ORDER BY display_name ASC",
        (session.tenant_id, place_id),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def get_account(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    account_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM accounts WHERE tenant_id = ? AND account_id = ?",
        (session.tenant_id, account_id),
    ).fetchone()
    return filter_one(session, dict(row) if row is not None else None)


def list_accounts_by_kind(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    kind: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM accounts WHERE tenant_id = ? AND kind = ? "
        "AND lifecycle_status='active' "
        "ORDER BY display_name ASC",
        (session.tenant_id, kind),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def accounts_renewing_before(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    cutoff_iso: str,
) -> list[dict[str, Any]]:
    """Active accounts with ``next_renewal <= cutoff_iso``, earliest first."""
    rows = conn.execute(
        """
        SELECT * FROM accounts
         WHERE tenant_id = ?
           AND status = 'active'
           AND lifecycle_status = 'active'
           AND next_renewal IS NOT NULL
           AND next_renewal <= ?
         ORDER BY next_renewal ASC
        """,
        (session.tenant_id, cutoff_iso),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def linked_to_party(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    party_id: str,
) -> dict[str, list[dict[str, Any]]]:
    places = conn.execute(
        """
        SELECT p.*, pa.role AS association_role
          FROM place_associations pa
          JOIN places p ON p.tenant_id = pa.tenant_id AND p.place_id = pa.place_id
         WHERE pa.tenant_id = ? AND pa.party_id = ?
           AND p.lifecycle_status = 'active'
         ORDER BY p.display_name ASC
        """,
        (session.tenant_id, party_id),
    ).fetchall()
    assets = conn.execute(
        """
        SELECT a.*, ao.share_pct, ao.since AS ownership_since
          FROM asset_owners ao
          JOIN assets a ON a.tenant_id = ao.tenant_id AND a.asset_id = ao.asset_id
         WHERE ao.tenant_id = ? AND ao.party_id = ?
           AND a.lifecycle_status = 'active'
         ORDER BY a.display_name ASC
        """,
        (session.tenant_id, party_id),
    ).fetchall()
    accounts = conn.execute(
        "SELECT * FROM accounts WHERE tenant_id = ? AND organization = ? "
        "AND lifecycle_status='active' "
        "ORDER BY display_name ASC",
        (session.tenant_id, party_id),
    ).fetchall()
    return {
        "places": filter_rows(session, [dict(row) for row in places]),
        "assets": filter_rows(session, [dict(row) for row in assets]),
        "accounts": filter_rows(session, [dict(row) for row in accounts]),
    }


def registry_items(
    conn: sqlcipher3.Connection,
    session: Session,
) -> dict[str, list[dict[str, Any]]]:
    """All visible registry objects, including explicitly archived history."""
    result: dict[str, list[dict[str, Any]]] = {}
    for kind, table, id_column in (
        ("places", "places", "place_id"),
        ("assets", "assets", "asset_id"),
        ("accounts", "accounts", "account_id"),
    ):
        rows = conn.execute(
            f"""SELECT * FROM {table}
                 WHERE tenant_id=?
                 ORDER BY lifecycle_status ASC, display_name ASC, {id_column} ASC""",
            (session.tenant_id,),
        ).fetchall()
        result[kind] = filter_rows(session, [dict(row) for row in rows])
    return result
