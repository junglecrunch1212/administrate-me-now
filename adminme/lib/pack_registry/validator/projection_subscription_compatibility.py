"""Projection-subscription compatibility check (3 of 6).

Cat-B and Cat-D are the two epistemic-role categories whose ingest
events feed L3 projections (Cat-B = external state mirror; Cat-D =
personal knowledge vault per memo 05 §2.1). For every capability of
either category, at least one entry in the capability's
``event_families`` MUST appear in some projection's ``subscribes_to``
declaration — otherwise the adapter is emitting events that no L3
projection consumes, which is install-time evidence that either the
manifest is mistyped or a needed projection has not yet shipped.

The forward-compat carve-out per memo 05 §3.4 [PM-34]: Cohort D ships
two new projections (``member_knowledge`` from prompt 11d; ``lists``
from prompt 11f) that adapter packs landing AHEAD of those projection
prompts will reference. A Cat-B or Cat-D capability whose declared
``event_families`` all carry a known-pending prefix (``member_knowledge.``
or ``lists.``) surfaces as an ``advisory_pending_projection`` finding,
not an error — the validator is forward-compat: when 11d/11f ship, the
advisory naturally becomes a pass.

Discovery shape per the validator's broader stance (see
:mod:`~adminme.lib.pack_registry.validator`): the check AST-walks each
``<projections_dir>/<projection_name>/__init__.py``, locates a class
inheriting from ``Projection``, and extracts the class-body
``subscribes_to = [...]`` (or ``subscribes_to = "..."``) literal. No
``importlib.import_module`` call: projection modules are first-party
but the same AST-walk discipline as the adapter checks keeps test
fixtures trivial (write a few stub ``__init__.py`` files under a
``tmp_path`` and pass the path in).

Cat-A / Cat-C / Cat-E capabilities skip this check entirely: those
categories do not feed projections directly (Cat-A is outbound
messaging, Cat-C is bidirectional external state, Cat-E is outbound
action). The capability-runtime-coherence check governs them.
"""

from __future__ import annotations

import ast
from pathlib import Path

from adminme.lib.pack_registry.models import AdapterManifest
from adminme.lib.pack_registry.validator.result import ValidationFinding

_CHECK_NAME = "projection_subscription_compatibility"

# Projections that ship in later Cohort D prompts. A Cat-B / Cat-D
# capability whose families all carry one of these prefixes surfaces as
# ``advisory_pending_projection`` instead of ``error``. See memo 05 §3.4.
# ``reminder`` covers the legacy ``reminder.list_item.synced`` family the
# 11r-c retrofit preserves byte-for-byte per memo 08 §5; the ``[D18]``
# migration to the ``list_item.*`` taxonomy (subscribed by the future
# ``lists`` projection in 11f) is Cohort D scope.
_PENDING_PROJECTIONS: frozenset[str] = frozenset(
    {"member_knowledge", "lists", "reminder"}
)

# Categories whose ingest events feed L3 projections per memo 05 §2.1.
_PROJECTION_FEEDING_KINDS: frozenset[str] = frozenset({"Cat-B", "Cat-D"})


def _default_projections_dir() -> Path:
    """Resolve the on-main projections directory without importing projections."""
    from adminme.projections import base as _projections_base

    return Path(_projections_base.__file__).parent


def _inherits_projection(class_def: ast.ClassDef) -> bool:
    for base in class_def.bases:
        if isinstance(base, ast.Name) and base.id == "Projection":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "Projection":
            return True
    return False


def _extract_subscribes_to(class_def: ast.ClassDef) -> set[str] | None:
    """Return the set of families from a class-body ``subscribes_to`` literal.

    Accepts either ``subscribes_to = [...]`` (the common shape across
    the 11 on-main projections) or ``subscribes_to = "..."`` (the
    star-wildcard shape that the :class:`Projection` ABC's type
    annotation allows). Returns ``None`` when no recognizable
    ``subscribes_to`` assignment is present, signaling that the
    class is not a usable projection declaration for compatibility
    purposes.
    """
    for stmt in class_def.body:
        targets: list[ast.expr]
        value: ast.expr | None
        if isinstance(stmt, ast.Assign):
            targets = list(stmt.targets)
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            targets = [stmt.target]
            value = stmt.value
        else:
            continue
        if value is None:
            continue
        if not any(
            isinstance(t, ast.Name) and t.id == "subscribes_to" for t in targets
        ):
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return {value.value}
        if isinstance(value, ast.List):
            families: set[str] = set()
            for elt in value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    families.add(elt.value)
            return families
    return None


def _discover_subscriptions(projections_dir: Path) -> dict[str, set[str]]:
    """Walk ``projections_dir`` for Projection subclasses; map dir-name → families.

    Iteration is sorted by directory name for stable test output.
    Malformed or missing ``__init__.py`` files are skipped silently
    (this check's job is to surface MANIFEST drift, not projection-tree
    misconfiguration — the projection runner has its own discovery
    and would fail loudly long before install).
    """
    result: dict[str, set[str]] = {}
    if not projections_dir.exists() or not projections_dir.is_dir():
        return result
    for child in sorted(projections_dir.iterdir()):
        if not child.is_dir():
            continue
        init_path = child / "__init__.py"
        if not init_path.exists():
            continue
        try:
            tree = ast.parse(init_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not _inherits_projection(node):
                continue
            families = _extract_subscribes_to(node)
            if families is None:
                continue
            result[child.name] = families
            break
    return result


def _family_prefix(family: str) -> str:
    """Return the segment before the first ``.`` (``"member_knowledge.added"`` → ``"member_knowledge"``)."""
    dot = family.find(".")
    return family if dot < 0 else family[:dot]


def _all_families_are_pending(families: list[str]) -> bool:
    """True iff every family's prefix names a known-pending projection.

    A capability mixing a pending family with a definitively-unknown
    family still surfaces as an error: the pending carve-out is for
    forward-compat with prompts not-yet-shipped, not a blanket excuse.
    """
    if not families:
        return False
    return all(_family_prefix(f) in _PENDING_PROJECTIONS for f in families)


def check_projection_subscription_compatibility(
    manifest: AdapterManifest,
    *,
    projections_dir: Path | None = None,
) -> list[ValidationFinding]:
    """Run the projection-subscription compatibility rule against ``manifest``.

    Default: discovers subscriptions under the on-main
    ``adminme/projections/`` directory. Tests pass a ``tmp_path``
    containing stub projection ``__init__.py`` files to avoid coupling
    to the full on-main projection tree.

    Cat-B / Cat-D capabilities with non-empty ``event_families`` that
    have no matching subscription yield one finding per capability:

    - ``advisory`` (``advisory_pending_projection``) when ALL declared
      families carry a known-pending projection prefix
      (``member_knowledge.`` / ``lists.``).
    - ``error`` otherwise.

    Cat-A / Cat-C / Cat-E capabilities and capabilities with empty
    ``event_families`` are skipped.
    """
    resolved_dir = (
        projections_dir if projections_dir is not None else _default_projections_dir()
    )
    subscriptions = _discover_subscriptions(Path(resolved_dir))
    all_families = {family for fams in subscriptions.values() for family in fams}

    findings: list[ValidationFinding] = []
    for cap_index, cap in enumerate(manifest.capabilities):
        if cap.kind not in _PROJECTION_FEEDING_KINDS:
            continue
        if not cap.event_families:
            continue
        if any(family in all_families for family in cap.event_families):
            continue
        field_path = f"capabilities[{cap_index}].event_families"
        declared = list(cap.event_families)
        if _all_families_are_pending(declared):
            findings.append(
                ValidationFinding(
                    severity="advisory",
                    check=_CHECK_NAME,
                    message=(
                        f"{cap.kind} capability at index {cap_index} declares "
                        f"event_families={declared} which are not yet "
                        f"subscribed by any on-main projection; the matching "
                        f"projection ships in a later prompt "
                        f"(advisory_pending_projection per memo 05 §3.4)."
                    ),
                    field_path=field_path,
                )
            )
        else:
            findings.append(
                ValidationFinding(
                    severity="error",
                    check=_CHECK_NAME,
                    message=(
                        f"{cap.kind} capability at index {cap_index} declares "
                        f"event_families={declared} but no on-main projection "
                        f"subscribes to any of them; declare the projection's "
                        f"subscribes_to or correct the family name."
                    ),
                    field_path=field_path,
                )
            )

    return findings


__all__ = [
    "check_projection_subscription_compatibility",
]
