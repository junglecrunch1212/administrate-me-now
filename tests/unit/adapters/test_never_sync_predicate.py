"""Never-sync predicate canaries for the Apple Reminders adapter.

Per prompt 11c §8 commit 1 + §9 verification, the never-sync filter must
have at least one test per rule (list-name substring, item-tag substring,
child/ambient ownership) — if any rule's test is missing,
``verify_invariants.sh`` fails. This file covers each rule's positive
case (blocks sync) AND the default-allow case (passes sync), plus
boundary conditions (case insensitivity, custom substring lists, role
short-circuit).

The pack lives under ``packs/adapters/reminders/apple-eventkit/`` — the
directory name contains a hyphen and ``packs/`` is not on the Python
import path under poetry's package config, so the test loads
``never_sync.py`` via ``importlib.util.spec_from_file_location`` (the
same pattern Gmail's pack-internal tests use; see
``packs/adapters/messaging/gmail/tests/test_gmail_adapter_pack_internal.py``).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

PACK_ROOT = (
    Path(__file__).resolve().parents[3]
    / "packs"
    / "adapters"
    / "reminders"
    / "apple-eventkit"
)


def _load_never_sync_module() -> Any:
    """Load ``never_sync.py`` from the pack root via importlib — packs
    are not a top-level Python package under poetry (only ``adminme``
    is) and the pack dir name has a hyphen, so ``from packs...`` won't
    work."""
    spec = importlib.util.spec_from_file_location(
        "reminders_never_sync_under_test", PACK_ROOT / "never_sync.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ns_mod: Any = _load_never_sync_module()
passes_never_sync = _ns_mod.passes_never_sync
EXCLUDED_MEMBER_ROLES = _ns_mod.EXCLUDED_MEMBER_ROLES
DEFAULT_NEVER_SYNC_LIST_SUBSTRINGS = _ns_mod.DEFAULT_NEVER_SYNC_LIST_SUBSTRINGS
DEFAULT_NEVER_SYNC_TAG_SUBSTRINGS = _ns_mod.DEFAULT_NEVER_SYNC_TAG_SUBSTRINGS


# ---------------------------------------------------------------------------
# Default-allow baseline — a normal reminder from an adult member passes.
# ---------------------------------------------------------------------------


def test_normal_reminder_passes() -> None:
    assert passes_never_sync(
        list_name="AdministrateMe Household",
        title="Pick up milk",
        notes=None,
        member_role="adult",
    ) is True


def test_normal_reminder_with_notes_passes() -> None:
    assert passes_never_sync(
        list_name="Family",
        title="Pick up milk",
        notes="2% only — Susan is dairy-sensitive",
        member_role="adult",
    ) is True


# ---------------------------------------------------------------------------
# Rule 1: list-name substring exclusion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "list_name",
    [
        "Work",
        "Work case files",  # canonical anti-vision fixture
        "Active Case Notes",
        "Client #4 — Discovery",
        "WORK",   # case-insensitive
        "client",  # case-insensitive
    ],
)
def test_list_name_substring_blocks_sync(list_name: str) -> None:
    assert passes_never_sync(
        list_name=list_name,
        title="Pick up milk",
        notes=None,
        member_role="adult",
    ) is False


def test_custom_list_name_substrings_replace_defaults() -> None:
    """When the caller passes their own ``never_sync_list_substrings``,
    the defaults are NOT additively applied — the caller's list is the
    full enforcement set."""
    assert passes_never_sync(
        list_name="Work",
        title="Pick up milk",
        notes=None,
        member_role="adult",
        never_sync_list_substrings=("Personal",),
    ) is True
    assert passes_never_sync(
        list_name="Personal",
        title="Pick up milk",
        notes=None,
        member_role="adult",
        never_sync_list_substrings=("Personal",),
    ) is False


# ---------------------------------------------------------------------------
# Rule 2: item-tag substring exclusion
# ---------------------------------------------------------------------------


def test_private_tag_in_title_blocks_sync() -> None:
    assert passes_never_sync(
        list_name="Family",
        title="#private — therapy appt",
        notes=None,
        member_role="adult",
    ) is False


def test_private_tag_in_notes_blocks_sync() -> None:
    assert passes_never_sync(
        list_name="Family",
        title="Doctor",
        notes="#private — not for shared dashboard",
        member_role="adult",
    ) is False


def test_private_tag_case_insensitive() -> None:
    assert passes_never_sync(
        list_name="Family",
        title="Doctor",
        notes="#PRIVATE",
        member_role="adult",
    ) is False


def test_custom_tag_substrings_replace_defaults() -> None:
    """Same contract as list substrings — caller's list fully replaces
    the defaults rather than additively extending them."""
    assert passes_never_sync(
        list_name="Family",
        title="#private therapy",
        notes=None,
        member_role="adult",
        never_sync_tag_substrings=("#secret",),
    ) is True
    assert passes_never_sync(
        list_name="Family",
        title="#secret — codeword",
        notes=None,
        member_role="adult",
        never_sync_tag_substrings=("#secret",),
    ) is False


# ---------------------------------------------------------------------------
# Rule 3: owning member role exclusion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["child", "ambient"])
def test_excluded_member_roles_block_sync(role: str) -> None:
    assert passes_never_sync(
        list_name="AdministrateMe Household",
        title="Pick up milk",
        notes=None,
        member_role=role,
    ) is False


@pytest.mark.parametrize(
    "role", ["adult", "head_of_household", "partner", "guardian", ""]
)
def test_non_excluded_member_roles_pass(role: str) -> None:
    """Any role NOT in the excluded set passes the role check (the other
    rules may still block; here we keep the list + tag clean to isolate
    the role behavior)."""
    assert passes_never_sync(
        list_name="AdministrateMe Household",
        title="Pick up milk",
        notes=None,
        member_role=role,
    ) is True


# ---------------------------------------------------------------------------
# Module-level constants — sanity checks
# ---------------------------------------------------------------------------


def test_module_constants_match_spec() -> None:
    """The canonical never-sync rule defaults in BUILD.md APPLE REMINDERS
    BIDIRECTIONAL must match the module-level constants exactly. If a
    refactor changes one without changing the other, this test catches
    the drift."""
    assert EXCLUDED_MEMBER_ROLES == frozenset({"child", "ambient"})
    assert DEFAULT_NEVER_SYNC_LIST_SUBSTRINGS == ("Work", "Case", "Client")
    assert DEFAULT_NEVER_SYNC_TAG_SUBSTRINGS == ("#private",)


# ---------------------------------------------------------------------------
# Short-circuit ordering — role check runs first, even when later rules
# would also block. This is observable indirectly: if role excludes,
# nothing else is consulted; we assert that the predicate stays ``False``
# regardless of other inputs and that empty substring lists do not
# accidentally allow sync for an excluded role.
# ---------------------------------------------------------------------------


def test_role_exclusion_short_circuits_empty_substring_lists() -> None:
    assert passes_never_sync(
        list_name="AdministrateMe Household",
        title="Pick up milk",
        notes=None,
        member_role="child",
        never_sync_list_substrings=(),
        never_sync_tag_substrings=(),
    ) is False
