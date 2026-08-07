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
import re
from datetime import datetime, timezone
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
    rows = conn.execute(
        """
        SELECT p.*, i.owner_scope AS identifier_owner_scope,
               i.visibility_scope AS identifier_visibility_scope,
               i.sensitivity AS identifier_sensitivity
          FROM identifiers i
          JOIN parties p
            ON p.tenant_id = i.tenant_id AND p.party_id = i.party_id
         WHERE i.tenant_id = ? AND i.kind = ? AND i.value_normalized = ?
           AND i.revoked_at IS NULL
           AND p.qualification = 'qualified'
         ORDER BY i.verified DESC, i.primary_for_kind DESC, p.party_id
        """,
        (session.tenant_id, kind, value_normalized),
    ).fetchall()
    visible: list[dict[str, Any]] = []
    for raw in rows:
        row = _row_to_dict(raw)
        if row is None:
            continue
        identifier_scope_row = {
            "owner_scope": row.pop("identifier_owner_scope"),
            "visibility_scope": row.pop("identifier_visibility_scope"),
            "sensitivity": row.pop("identifier_sensitivity"),
        }
        if not filter_rows(session, [identifier_scope_row]):
            continue
        party = filter_one(session, row)
        if party is not None:
            visible.append(party)
    party_ids = {str(row["party_id"]) for row in visible}
    if len(party_ids) != 1:
        return None
    return visible[0]


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
           AND m.ended_at IS NULL
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
           AND ended_at IS NULL
         ORDER BY relationship_id ASC
        """,
        (session.tenant_id, party_id, party_id),
    ).fetchall()
    return filter_rows(session, [dict(r) for r in rows])


def identifiers_of(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    party_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM identifiers WHERE tenant_id = ? AND party_id = ? "
        "AND revoked_at IS NULL "
        "ORDER BY primary_for_kind DESC, kind ASC, identifier_id ASC",
        (session.tenant_id, party_id),
    ).fetchall()
    return filter_rows(session, [dict(row) for row in rows])


def identifiers_for_person(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    party_id: str,
) -> list[dict[str, Any]]:
    """Return identifiers only when core scope or an identifiers grant permits."""

    person = get_visible_person(conn, session, party_id=party_id)
    if person is None:
        return []
    if person.get("access_mode") != "shared_grant":
        return identifiers_of(conn, session, party_id=party_id)
    if "identifiers" not in set(person.get("grant_field_groups") or []):
        return []
    grant_owner = str(person.get("grant_owner_member_id") or "")
    if not grant_owner:
        return []
    rows = conn.execute(
        "SELECT * FROM identifiers WHERE tenant_id=? AND party_id=? "
        "AND revoked_at IS NULL AND sensitivity!='privileged' "
        "AND owner_scope=? "
        "ORDER BY primary_for_kind DESC, kind, identifier_id",
        (session.tenant_id, party_id, f"private:{grant_owner}"),
    ).fetchall()
    return [dict(row) for row in rows]


def memberships_of(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    party_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM memberships WHERE tenant_id = ? AND party_id = ? "
        "AND ended_at IS NULL "
        "ORDER BY started_at DESC, membership_id ASC",
        (session.tenant_id, party_id),
    ).fetchall()
    return filter_rows(session, [dict(row) for row in rows])


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


def _adult_self(session: Session) -> bool:
    return (
        session.auth_role not in {"child", "device", "ambient"}
        and session.auth_member_id == session.view_member_id
    )


def _json_field(row: dict[str, Any], source: str, target: str, *, default: Any) -> None:
    raw = row.pop(source, None)
    if raw is None:
        row[target] = default
        return
    try:
        row[target] = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        row[target] = default


def _active_grant(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    party_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM party_access_grants
         WHERE tenant_id=? AND party_id=? AND grantee_member_id=?
           AND revoked_at IS NULL
           AND (expires_at IS NULL OR expires_at > ?)
         ORDER BY granted_at DESC, grant_id DESC LIMIT 1
        """,
        (
            session.tenant_id,
            party_id,
            session.auth_member_id,
            datetime.now(timezone.utc).isoformat(),
        ),
    ).fetchone()
    return dict(row) if row is not None else None


def get_visible_person(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    party_id: str,
) -> dict[str, Any] | None:
    if session.auth_role in {"child", "device", "ambient"}:
        return None
    row = conn.execute(
        "SELECT * FROM parties WHERE tenant_id=? AND party_id=? "
        "AND qualification='qualified'",
        (session.tenant_id, party_id),
    ).fetchone()
    if row is None:
        return None
    value = _row_to_dict(row)
    if value is None:
        return None
    if value.get("owner_scope") == "shared:household":
        visible = filter_one(session, value)
        if visible is not None:
            visible["access_mode"] = "household_shared"
        return visible
    if (
        _adult_self(session)
        and value.get("owner_scope") == f"private:{session.auth_member_id}"
    ):
        visible = filter_one(session, value)
        if visible is not None:
            visible["access_mode"] = "owner"
        return visible
    grant = _active_grant(conn, session, party_id=party_id)
    if grant is None or value.get("sensitivity") == "privileged":
        return None
    value["access_mode"] = "shared_grant"
    value["grant_owner_member_id"] = str(grant["owner_member_id"])
    value["grant_field_groups"] = json.loads(grant["field_groups_json"] or "[]")
    # The canonical display identity is required to recognize the Party. The
    # owner's unstructured Party attributes are not a granted field group.
    value["attributes"] = {}
    value["attributes_json"] = "{}"
    return value


def list_visible_people(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    lens: str = "all",
    limit: int = 500,
) -> list[dict[str, Any]]:
    if session.auth_role in {"child", "device", "ambient"}:
        return []
    rows = conn.execute(
        "SELECT * FROM parties WHERE tenant_id=? AND qualification='qualified' "
        "AND kind IN ('person', 'organization') "
        "ORDER BY sort_name, party_id LIMIT ?",
        (session.tenant_id, limit),
    ).fetchall()
    values: list[dict[str, Any]] = []
    for raw in rows:
        party_id = str(raw["party_id"])
        party = get_visible_person(conn, session, party_id=party_id)
        if party is None:
            continue
        profile = conn.execute(
            "SELECT * FROM member_relationship_profiles WHERE tenant_id=? "
            "AND member_id=? AND party_id=?",
            (session.tenant_id, session.auth_member_id, party_id),
        ).fetchone()
        party["profile"] = (
            dict(profile) if profile is not None and _adult_self(session) else None
        )
        if party["profile"] is not None:
            _json_field(party["profile"], "tags_json", "tags", default=[])
        party["contact_record_count"] = int(
            conn.execute(
                "SELECT COUNT(*) FROM contact_records WHERE tenant_id=? "
                "AND party_id=? AND deleted_at IS NULL AND member_id=?",
                (session.tenant_id, party_id, session.auth_member_id),
            ).fetchone()[0]
        )
        active_relationships = relationships_of(conn, session, party_id=party_id)
        party["relationship_labels"] = sorted(
            {str(item["label"]) for item in active_relationships}
        )
        profile_value = party.get("profile") or {}
        if lens == "household" and not (
            party["relationship_labels"]
            or conn.execute(
                "SELECT 1 FROM memberships WHERE tenant_id=? AND party_id=? "
                "AND ended_at IS NULL LIMIT 1",
                (session.tenant_id, party_id),
            ).fetchone()
        ):
            continue
        if lens == "organizations" and party.get("kind") != "organization":
            continue
        if lens == "favorites" and not profile_value.get("favorite"):
            continue
        if lens == "suppressed" and not profile_value.get("suppressed"):
            continue
        values.append(party)
    return values


_SEARCH_TOKEN = re.compile(r"[\w@+.'-]+", re.UNICODE)


def search_visible_people(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    query: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    tokens = [token.replace('"', '""') for token in _SEARCH_TOKEN.findall(query)]
    if not tokens or session.auth_role in {"child", "device", "ambient"}:
        return []
    expression = " AND ".join(f'"{token}"' for token in tokens[:16])
    rows = conn.execute(
        "SELECT party_id, bm25(people_fts) AS search_rank FROM people_fts "
        "WHERE tenant_id=? AND audience_member_id IN ('', ?) "
        "AND people_fts MATCH ? ORDER BY search_rank LIMIT ?",
        (session.tenant_id, session.auth_member_id, expression, limit * 4),
    ).fetchall()
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        party_id = str(row["party_id"])
        if party_id in seen:
            continue
        person = get_visible_person(conn, session, party_id=party_id)
        if person is None:
            continue
        person["search_rank"] = row["search_rank"]
        results.append(person)
        seen.add(party_id)
        if len(results) >= limit:
            break

    # Explicitly granted facts and identifiers are checked against live grant
    # rows at query time. They are intentionally absent from the grantee's FTS
    # row so an expired grant cannot leave a private search side channel.
    lowered_tokens = [token.casefold() for token in tokens]

    def matches(values: list[Any]) -> bool:
        haystack = " ".join(str(value or "") for value in values).casefold()
        return all(token in haystack for token in lowered_tokens)

    fact_rows = conn.execute(
        "SELECT party_id, member_id, kind, label, value FROM relationship_facts "
        "WHERE tenant_id=? AND revoked_at IS NULL AND sensitivity!='privileged'",
        (session.tenant_id,),
    ).fetchall()
    for row in fact_rows:
        party_id = str(row["party_id"])
        if party_id in seen or not matches([row["kind"], row["label"], row["value"]]):
            continue
        allowed = row["member_id"] == session.auth_member_id
        if not allowed:
            grant = _active_grant(conn, session, party_id=party_id)
            allowed = bool(
                grant is not None
                and grant["owner_member_id"] == row["member_id"]
                and "facts" in json.loads(grant["field_groups_json"] or "[]")
            )
        if not allowed:
            continue
        person = get_visible_person(conn, session, party_id=party_id)
        if person is not None:
            person["search_rank"] = None
            results.append(person)
            seen.add(party_id)
        if len(results) >= limit:
            return results

    identifier_rows = conn.execute(
        "SELECT party_id, kind, value, label, owner_scope, visibility_scope, "
        "sensitivity FROM identifiers WHERE tenant_id=? AND revoked_at IS NULL "
        "AND sensitivity!='privileged'",
        (session.tenant_id,),
    ).fetchall()
    for raw in identifier_rows:
        row = dict(raw)
        party_id = str(row["party_id"])
        if party_id in seen or not matches([row["kind"], row["label"], row["value"]]):
            continue
        allowed = bool(filter_rows(session, [row]))
        if not allowed and str(row["owner_scope"]).startswith("private:"):
            grant = _active_grant(conn, session, party_id=party_id)
            allowed = bool(
                grant is not None
                and f"private:{grant['owner_member_id']}" == row["owner_scope"]
                and "identifiers" in json.loads(grant["field_groups_json"] or "[]")
            )
        if not allowed:
            continue
        person = get_visible_person(conn, session, party_id=party_id)
        if person is not None:
            person["search_rank"] = None
            results.append(person)
            seen.add(party_id)
        if len(results) >= limit:
            break
    return results


def contact_records_for_person(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    party_id: str,
) -> list[dict[str, Any]]:
    person = get_visible_person(conn, session, party_id=party_id)
    if person is None or not _adult_self(session):
        return []
    rows = conn.execute(
        "SELECT * FROM contact_records WHERE tenant_id=? AND party_id=? "
        "AND member_id=? AND deleted_at IS NULL ORDER BY provider, display_name",
        (session.tenant_id, party_id, session.auth_member_id),
    ).fetchall()
    values = filter_rows(session, [dict(row) for row in rows])
    for value in values:
        _json_field(value, "fields_json", "fields", default={})
        _json_field(value, "groups_json", "groups", default=[])
        _json_field(
            value,
            "capability_warnings_json",
            "capability_warnings",
            default=[],
        )
    return values


def relationship_facts_for_person(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    party_id: str,
) -> list[dict[str, Any]]:
    person = get_visible_person(conn, session, party_id=party_id)
    if person is None:
        return []
    granted = person.get("access_mode") == "shared_grant"
    if granted and "facts" not in set(person.get("grant_field_groups") or []):
        return []
    fact_member_id = (
        str(person.get("grant_owner_member_id") or "")
        if granted
        else session.auth_member_id
    )
    if not fact_member_id:
        return []
    rows = conn.execute(
        "SELECT * FROM relationship_facts WHERE tenant_id=? AND party_id=? "
        "AND member_id=? AND revoked_at IS NULL ORDER BY kind, effective_date, fact_id",
        (session.tenant_id, party_id, fact_member_id),
    ).fetchall()
    if granted:
        return [dict(row) for row in rows if row["sensitivity"] != "privileged"]
    return filter_rows(session, [dict(row) for row in rows])


def people_source_summary(
    conn: sqlcipher3.Connection,
    session: Session,
) -> list[dict[str, Any]]:
    if not _adult_self(session):
        return []
    rows = conn.execute(
        """
        SELECT provider, provider_account_id, container_external_id,
               COUNT(*) AS record_count, MAX(observed_at) AS latest_at,
               SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS deleted_count
          FROM contact_records
         WHERE tenant_id=? AND member_id=?
         GROUP BY provider, provider_account_id, container_external_id
         ORDER BY provider, provider_account_id, container_external_id
        """,
        (session.tenant_id, session.auth_member_id),
    ).fetchall()
    return [dict(row) for row in rows]


def member_relationship_profile(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    party_id: str,
) -> dict[str, Any] | None:
    if not _adult_self(session):
        return None
    row = conn.execute(
        "SELECT * FROM member_relationship_profiles WHERE tenant_id=? "
        "AND member_id=? AND party_id=?",
        (session.tenant_id, session.auth_member_id, party_id),
    ).fetchone()
    if row is None:
        return None
    value = dict(row)
    _json_field(value, "tags_json", "tags", default=[])
    return value


__all__ = [
    "all_parties",
    "contact_records_for_person",
    "find_party_by_identifier",
    "get_party",
    "get_visible_person",
    "identifiers_of",
    "identifiers_for_person",
    "list_household_members",
    "list_visible_people",
    "memberships_of",
    "member_relationship_profile",
    "people_source_summary",
    "relationship_facts_for_person",
    "relationships_of",
    "search_visible_people",
]
