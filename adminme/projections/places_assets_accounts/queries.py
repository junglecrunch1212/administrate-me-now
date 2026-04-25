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
            "SELECT * FROM places WHERE tenant_id = ? ORDER BY display_name ASC",
            (session.tenant_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM places WHERE tenant_id = ? AND kind = ? "
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
           AND next_renewal IS NOT NULL
           AND next_renewal <= ?
         ORDER BY next_renewal ASC
        """,
        (session.tenant_id, cutoff_iso),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])
