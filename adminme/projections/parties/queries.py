"""
Parties projection read queries — the CRM read surface.

Per ADMINISTRATEME_BUILD.md §3.1 and SYSTEM_INVARIANTS.md §3. Every public
query takes a ``session: Session`` per [§6.1] and runs scope-filtered SQL
per BUILD.md L3-continued. Rows pass through ``scope.filter_rows`` (which
wraps ``allowed_read`` + ``privacy_filter`` + ``child_hidden_tag_filter`` +
coach-strip) before return.

Per DECISIONS.md §D4: the CRM spine is a shared L3 concern. Any Python
product may read these queries via its local connection.
"""

from __future__ import annotations

import json
from typing import Any

import sqlcipher3

from adminme.lib.scope import filter_one, filter_rows
from adminme.lib.session import Session


def _row_to_dict(row: sqlcipher3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = {k: row[k] for k in row.keys()}
    if "attributes_json" in out:
        out["attributes"] = json.loads(out["attributes_json"])
    return out


def get_party(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    party_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM parties WHERE tenant_id = ? AND party_id = ?",
        (session.tenant_id, party_id),
    ).fetchone()
    return filter_one(session, _row_to_dict(row))


def find_party_by_identifier(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
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
        (session.tenant_id, kind, value_normalized),
    ).fetchone()
    return filter_one(session, _row_to_dict(row))


def list_household_members(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
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
        (session.tenant_id, household_party_id),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def relationships_of(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    party_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM relationships
         WHERE tenant_id = ?
           AND (party_a = ? OR party_b = ?)
         ORDER BY relationship_id ASC
        """,
        (session.tenant_id, party_id, party_id),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def all_parties(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    if kind is None:
        rows = conn.execute(
            "SELECT * FROM parties WHERE tenant_id = ? ORDER BY sort_name ASC",
            (session.tenant_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM parties WHERE tenant_id = ? AND kind = ? "
            "ORDER BY sort_name ASC",
            (session.tenant_id, kind),
        ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])
