"""
Scope enforcement — defense-in-depth around every projection query.

Implemented in prompt 08a per ADMINISTRATEME_BUILD.md §L3-continued and
SYSTEM_INVARIANTS.md §6, CONSOLE_PATTERNS.md §§6-7, DIAGRAMS.md §5.

Scope predicates auto-append to every projection query::

    WHERE visibility_scope IN (allowed_scopes)
      AND (sensitivity != 'privileged' OR owner_scope = current_user)

Every projection test ships a canary that expects ``ScopeViolation`` on
out-of-scope reads [§6.4].

Commit 2 fleshes out ``allowed_read``, ``privacy_filter``, ``coach_column_strip``,
and ``child_hidden_tag_filter``. This module currently exports
``ScopeViolation`` so ``adminme.lib.session`` can re-export it without an
import cycle.
"""

from __future__ import annotations


class ScopeViolation(Exception):
    """Raised when a projection query is invoked with a Session that lacks
    the necessary scope, or when ``allowed_read`` is consulted as a
    canary and refuses [§6.4]."""


__all__ = ["ScopeViolation"]
