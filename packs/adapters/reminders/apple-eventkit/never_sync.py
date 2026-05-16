"""
Never-sync predicate for the Apple Reminders bidirectional adapter.

Stand-alone module so c2 (outbound EventKit write path) and c3 (inbound
polling + dedupe) can both import + test the predicate in isolation per
prompt 11c §8 commit 1 structure. The predicate is a PURE FUNCTION — no
projection writes, no skill calls, no LLM. The caller resolves the inputs
(list name, item title + notes, member role) and passes them in.

Three rules, applied in this order. ANY blocking rule short-circuits to a
``False`` (block-sync) return. The predicate returns ``True`` only when
NONE of the rules block the item.

1. **Member role exclusion** — owning household member's role is
   ``"child"`` or ``"ambient"`` (e.g. shared family iPad, voice-assistant
   capture device). The membership check resolves via the projection
   ``members`` slice from prompt 05; for c1 + c2 + c3 the caller supplies
   the resolved role string. Per BUILD.md APPLE REMINDERS BIDIRECTIONAL
   §"Never-sync rules" + VISION §"Privacy by default".

2. **List-name substring exclusion** — list name contains any
   ``never_sync_list_substrings`` substring (case-insensitive). Default
   ``("Work", "Case", "Client")`` per the canonical rules; configurable
   via ``config/channels.yaml``'s ``never_sync_list_substrings``.

3. **Item tag substring exclusion** — title OR notes contains any
   ``never_sync_tag_substrings`` substring (case-insensitive). Default
   ``("#private",)`` per the canonical rules; configurable via
   ``config/channels.yaml``'s ``never_sync_tag_substrings``.

Applied at BOTH boundaries (inbound iCloud→event-log AND outbound
event-log→iCloud) per BUILD.md APPLE REMINDERS BIDIRECTIONAL — the
adapter does not sync child/ambient member reminders OUT to iCloud's
shared list, and does not emit ``reminder.list_item.synced`` events IN
from never-sync lists. Symmetric enforcement.
"""

from __future__ import annotations

from typing import Iterable

EXCLUDED_MEMBER_ROLES: frozenset[str] = frozenset({"child", "ambient"})
"""Member roles whose reminders are never synced by the adapter. Resolved
via the ``members`` projection slice (prompt 05) at the caller layer."""

DEFAULT_NEVER_SYNC_LIST_SUBSTRINGS: tuple[str, ...] = ("Work", "Case", "Client")
"""Default list-name substrings (case-insensitive) that block sync. Per
BUILD.md APPLE REMINDERS BIDIRECTIONAL §"Never-sync rules"."""

DEFAULT_NEVER_SYNC_TAG_SUBSTRINGS: tuple[str, ...] = ("#private",)
"""Default in-item tag substrings (case-insensitive) that block sync.
Matched against the concatenated title + notes of each reminder."""


def passes_never_sync(
    *,
    list_name: str,
    title: str,
    notes: str | None,
    member_role: str,
    never_sync_list_substrings: Iterable[str] = DEFAULT_NEVER_SYNC_LIST_SUBSTRINGS,
    never_sync_tag_substrings: Iterable[str] = DEFAULT_NEVER_SYNC_TAG_SUBSTRINGS,
) -> bool:
    """Return ``True`` iff the reminder is eligible to sync.

    The predicate short-circuits on the first blocking rule. Member-role
    exclusion runs first because it is the cheapest check (a frozenset
    membership lookup) and the strongest signal (a child/ambient member's
    reminder should never sync regardless of list or content).
    """
    if member_role in EXCLUDED_MEMBER_ROLES:
        return False

    lower_list = list_name.lower()
    for sub in never_sync_list_substrings:
        if sub.lower() in lower_list:
            return False

    haystack = ((title or "") + "\n" + (notes or "")).lower()
    for tag in never_sync_tag_substrings:
        if tag.lower() in haystack:
            return False

    return True


__all__ = [
    "EXCLUDED_MEMBER_ROLES",
    "DEFAULT_NEVER_SYNC_LIST_SUBSTRINGS",
    "DEFAULT_NEVER_SYNC_TAG_SUBSTRINGS",
    "passes_never_sync",
]
