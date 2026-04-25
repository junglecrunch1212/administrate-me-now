"""
Scope enforcement — defense-in-depth around every projection query.

Implemented in prompt 08a per ADMINISTRATEME_BUILD.md §L3-continued and
SYSTEM_INVARIANTS.md §6, CONSOLE_PATTERNS.md §§6-7, DIAGRAMS.md §5.

Scope predicates auto-append to every projection query::

    WHERE visibility_scope IN (allowed_scopes)
      AND (sensitivity != 'privileged' OR owner_scope = current_user)

Every projection test ships a canary that expects ``ScopeViolation`` on
out-of-scope reads [§6.4].

Public API:

- ``ScopeViolation`` — exception raised when a query is invoked with an
  out-of-scope Session.
- ``allowed_read(session, sensitivity, owner_scope) -> bool`` — predicate
  that mirrors the SQL WHERE clause for in-Python row filtering and the
  multi-dimensional matrix in DIAGRAMS §5.
- ``privacy_filter(session, row) -> dict`` — privileged calendar-event
  redaction per CONSOLE_PATTERNS §6: keep time/duration/coarse owner
  hint, drop everything content-bearing.
- ``coach_column_strip(row) -> dict`` — strip ``financial_*`` and
  ``health_*`` columns for coach-role per [§13] / DIAGRAMS §5.
- ``child_hidden_tag_filter(session, row) -> bool`` — return False (drop)
  if a row's ``tags`` overlap the child-forbidden set per
  CONSOLE_PATTERNS §7.
- ``CHILD_FORBIDDEN_TAGS`` — module-level frozenset; the same blocklist
  the 14a server-side nav middleware will reference.

Cross-cutting decisions cited inline below.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:  # avoid runtime import cycle with session.py
    from adminme.lib.session import Session


CHILD_FORBIDDEN_TAGS: frozenset[str] = frozenset(
    {"finance", "health", "legal", "adult_only"}
)
"""Tags that a child-role session must never see, regardless of the row's
nominal sensitivity [CONSOLE_PATTERNS.md §6/§7]. 14a's server-side nav
middleware references the same constant."""


_BUSY_TITLE = "[busy]"

_REDACTED_KEEP_FIELDS: frozenset[str] = frozenset(
    {
        "calendar_event_id",
        "tenant_id",
        "start_at",
        "end_at",
        "all_day",
        "owner_party",
        "owner_scope",
        "visibility_scope",
        "sensitivity",
        "calendar_source",
        "last_event_id",
    }
)
"""Allowlist of fields preserved in the redacted busy-block output for
privileged events when the viewer is not the owner [CONSOLE_PATTERNS.md
§6]. Allowlist (not blocklist) so a future schema field cannot leak by
forgetting to delete it."""


class ScopeViolation(Exception):
    """Raised when a projection query is invoked with a Session that lacks
    the necessary scope, or when ``allowed_read`` is consulted as a canary
    and refuses [§6.4]."""


def allowed_read(
    session: "Session", sensitivity: str, owner_scope: str
) -> bool:
    """Mirror of the SQL WHERE clause from BUILD.md L3-continued, applied
    in Python to a single (sensitivity, owner_scope) pair [DIAGRAMS.md §5].

    Multi-dimensional matrix:
      - ambient: never (no surface).
      - device: only ``shared:household`` at ``normal`` sensitivity.
      - child: ``shared:household`` or ``private:<self>`` at ``normal`` only.
      - principal / coach_session: visibility_scope must be in
        ``allowed_scopes``; if sensitivity == 'privileged', owner_scope
        must equal ``private:<auth_member>``.
    """
    role = session.auth_role

    # Ambient: no surface at all [§6.2, DIAGRAMS.md §5].
    if role == "ambient":
        return False

    # Device (e.g. scoreboard TV): household-shared only, normal only
    # [DIAGRAMS.md §5].
    if role == "device":
        if owner_scope != "shared:household":
            return False
        return sensitivity == "normal"

    # Child: household + own-private at normal only [DIAGRAMS.md §5].
    if role == "child":
        if sensitivity != "normal":
            return False
        if owner_scope == "shared:household":
            return True
        if owner_scope == f"private:{session.auth_member_id}":
            return True
        return False

    # Principal + coach_session: visibility-scope axis first.
    if owner_scope not in session.allowed_scopes:
        # Privileged content from another principal is sometimes allowed
        # for coarse calendar busy-block rendering — that's read by the
        # calendars query passing through privacy_filter, NOT by
        # allowed_read returning True. allowed_read is the gate; the
        # privacy filter shapes the result. So a strict NO here is right.
        return False

    if sensitivity == "privileged":
        # Owner of the privileged content may always read [§6.9].
        # Anyone else is refused at the read layer; the calendar
        # surface re-fetches with the busy-block path under a separate
        # query that explicitly opts in via privacy_filter.
        if owner_scope == f"private:{session.auth_member_id}":
            return True
        return False

    return True


def privacy_filter(session: "Session", row: dict[str, Any]) -> dict[str, Any]:
    """Apply read-time privacy redaction to a single row
    [CONSOLE_PATTERNS.md §6, §5.5].

    For a privileged-sensitivity row whose owner is not the viewer,
    returns the busy-block allowlist shape (time/duration only). For
    everything else, returns the row unchanged.

    Coach-session always invokes ``coach_column_strip`` on the way out;
    this function does not duplicate that work.
    """
    sensitivity = row.get("sensitivity") or "normal"
    if sensitivity != "privileged":
        return row

    owner_scope = row.get("owner_scope") or ""
    if owner_scope == f"private:{session.auth_member_id}":
        # Owner: see full content [CONSOLE_PATTERNS.md §6 owner branch].
        return row

    # Non-owner of privileged content — collapse to busy block.
    redacted: dict[str, Any] = {
        k: row[k] for k in _REDACTED_KEEP_FIELDS if k in row
    }
    redacted["title"] = _BUSY_TITLE
    redacted["kind"] = "busy_block"
    redacted["display_hint"] = "privileged"
    # owner_hint is configurable per CONSOLE_PATTERNS §6; default first-
    # name-only, but 08a does not have the member-name lookup. Carry the
    # owner_party id through so the surface can render "Owner is busy".
    redacted["owner_hint"] = row.get("owner_party")
    return redacted


_COACH_STRIP_PREFIXES: tuple[str, ...] = ("financial_", "health_")


def coach_column_strip(row: dict[str, Any]) -> dict[str, Any]:
    """Strip ``financial_*`` and ``health_*`` columns for coach-role
    sessions [§13, DIAGRAMS.md §5]. Coach context never carries domain
    columns the principal might consider sensitive even if they are
    technically of normal sensitivity.

    Pure function; no Session lookup needed because the caller decides
    whether to apply (only when ``session.auth_role == 'coach_session'``).
    """
    return {
        k: v
        for k, v in row.items()
        if not any(k.startswith(p) for p in _COACH_STRIP_PREFIXES)
    }


def _row_tags(row: dict[str, Any]) -> Iterable[str]:
    """Pull tags from a row in either of the two shapes projections use.

    Calendar events store ``tags`` as JSON in ``tags_json``; tasks store
    them as a comma-separated string in ``tags`` per their respective
    schemas. Either shape is normalized to an iterable of strings.
    """
    raw = row.get("tags_json") or row.get("tags")
    if raw is None:
        return ()
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return ()
        if s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except json.JSONDecodeError:
                return ()
            return ()
        return [t.strip() for t in s.split(",") if t.strip()]
    return ()


def child_hidden_tag_filter(session: "Session", row: dict[str, Any]) -> bool:
    """Return True if ``row`` is visible to ``session``, False to drop.

    For child-role sessions, drops any row tagged with a member of
    ``CHILD_FORBIDDEN_TAGS`` regardless of the row's nominal sensitivity
    [CONSOLE_PATTERNS.md §6/§7].
    """
    if session.auth_role != "child":
        return True
    tags = set(_row_tags(row))
    if tags & CHILD_FORBIDDEN_TAGS:
        return False
    return True


def filter_rows(
    session: "Session", rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Bundle ``allowed_read`` + ``privacy_filter`` + ``child_hidden_tag_filter``
    + coach-strip into a single helper called by every projection query
    [BUILD.md L3-continued].

    Rows from structural projection tables that lack ``owner_scope`` /
    ``sensitivity`` columns (memberships, identifiers, relationships,
    pure join tables) are treated as ``shared:household`` / ``normal`` —
    they carry no per-row privacy semantics and are gated only by the
    role-axis defense.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        sens = row.get("sensitivity") or "normal"
        owner = row.get("owner_scope") or "shared:household"
        if not allowed_read(session, sens, owner):
            continue
        filtered = privacy_filter(session, row)
        if not child_hidden_tag_filter(session, filtered):
            continue
        if session.auth_role == "coach_session":
            filtered = coach_column_strip(filtered)
        out.append(filtered)
    return out


def filter_one(
    session: "Session", row: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Single-row variant of ``filter_rows``."""
    if row is None:
        return None
    out = filter_rows(session, [row])
    return out[0] if out else None


__all__ = [
    "CHILD_FORBIDDEN_TAGS",
    "ScopeViolation",
    "allowed_read",
    "child_hidden_tag_filter",
    "coach_column_strip",
    "filter_one",
    "filter_rows",
    "privacy_filter",
]
