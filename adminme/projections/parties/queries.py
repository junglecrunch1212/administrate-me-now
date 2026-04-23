"""
Parties projection read queries — the CRM read surface.

Per ADMINISTRATEME_BUILD.md §3.1 and SYSTEM_INVARIANTS.md §3. Plain query
functions; prompt 08 wraps them with Session / scope enforcement. Every
function takes ``tenant_id`` as an explicit required keyword — §12 invariant
1, no global tenant context.

Per DECISIONS.md §D4: the CRM spine is a shared L3 concern. Any Python
product may read these queries via its local connection.
"""

from __future__ import annotations

import json
from typing import Any

import sqlcipher3


def _row_to_dict(row: sqlcipher3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = {k: row[k] for k in row.keys()}
    if "attributes_json" in out:
        out["attributes"] = json.loads(out["attributes_json"])
    return out


# TODO(prompt-08): wrap with Session scope check
def get_party(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    party_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM parties WHERE tenant_id = ? AND party_id = ?",
        (tenant_id, party_id),
    ).fetchone()
    return _row_to_dict(row)


# TODO(prompt-08): wrap with Session scope check
def find_party_by_identifier(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    kind: str,
    value_normalized: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT p.*
          FROM identifiers i
          JOIN parties p
            ON p.tenant_id = i.tenant_id AND p.party_id = i.party_id
         WHERE i.tenant_id = ? AND i.kind = ? AND i.value_normalized = ?
         LIMIT 1
        """,
        (tenant_id, kind, value_normalized),
    ).fetchone()
    return _row_to_dict(row)


# TODO(prompt-08): wrap with Session scope check
def list_household_members(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    household_party_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.*, m.membership_id, m.role, m.started_at
          FROM memberships m
          JOIN parties p
            ON p.tenant_id = m.tenant_id AND p.party_id = m.party_id
         WHERE m.tenant_id = ? AND m.parent_party_id = ?
         ORDER BY p.sort_name ASC
        """,
        (tenant_id, household_party_id),
    ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
def relationships_of(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    party_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM relationships
         WHERE tenant_id = ?
           AND (party_a = ? OR party_b = ?)
         ORDER BY relationship_id ASC
        """,
        (tenant_id, party_id, party_id),
    ).fetchall()
    return [dict(r) for r in rows]


# TODO(prompt-08): wrap with Session scope check
def all_parties(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    if kind is None:
        rows = conn.execute(
            "SELECT * FROM parties WHERE tenant_id = ? ORDER BY sort_name ASC",
            (tenant_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM parties WHERE tenant_id = ? AND kind = ? "
            "ORDER BY sort_name ASC",
            (tenant_id, kind),
        ).fetchall()
    return [dict(r) for r in rows]
