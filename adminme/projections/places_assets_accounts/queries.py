"""
places_assets_accounts projection read queries.

Per ADMINISTRATEME_BUILD.md §3.8 and SYSTEM_INVARIANTS.md §2, §12. Plain
query functions; prompt 08 wraps them with Session / scope enforcement.
Every function takes ``tenant_id`` as an explicit required keyword — §12
invariant 1, no global tenant context.

Per DECISIONS.md §D4: the CRM/ops spine is a shared L3 concern.
"""

from __future__ import annotations

from typing import Any

import sqlcipher3


# TODO(prompt-08): wrap with Session scope check
def get_place(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    place_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM places WHERE tenant_id = ? AND place_id = ?",
        (tenant_id, place_id),
    ).fetchone()
    return dict(row) if row is not None else None


# TODO(prompt-08): wrap with Session scope check
def list_places(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    if kind is None:
        rows = conn.execute(
            "SELECT * FROM places WHERE tenant_id = ? ORDER BY display_name ASC",
            (tenant_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM places WHERE tenant_id = ? AND kind = ? "
            "ORDER BY display_name ASC",
            (tenant_id, kind),
        ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
def get_asset(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    asset_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM assets WHERE tenant_id = ? AND asset_id = ?",
        (tenant_id, asset_id),
    ).fetchone()
    return dict(row) if row is not None else None


# TODO(prompt-08): wrap with Session scope check
def list_assets_for_place(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    place_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM assets WHERE tenant_id = ? AND linked_place = ? "
        "ORDER BY display_name ASC",
        (tenant_id, place_id),
    ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
def get_account(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    account_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM accounts WHERE tenant_id = ? AND account_id = ?",
        (tenant_id, account_id),
    ).fetchone()
    return dict(row) if row is not None else None


# TODO(prompt-08): wrap with Session scope check
def list_accounts_by_kind(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    kind: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM accounts WHERE tenant_id = ? AND kind = ? "
        "ORDER BY display_name ASC",
        (tenant_id, kind),
    ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
def accounts_renewing_before(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
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
        (tenant_id, cutoff_iso),
    ).fetchall()
    return [dict(r) for r in rows]
