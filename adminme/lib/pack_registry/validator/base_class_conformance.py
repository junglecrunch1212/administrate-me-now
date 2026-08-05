"""Base-class conformance check (4 of 6).

Asserts the top-level adapter class in ``<pack_dir>/adapter.py`` inherits
from exactly one of the five ABCs at :mod:`adminme.lib.adapters` and that
the inherited ABC matches the manifest's primary capability category per
memo 05 §3.2, [D19], [PM-34]: Cat-A→``CommunicationAdapter``,
Cat-B→``ExternalStateMirrorAdapter``, Cat-C→``InboundDataAdapter``,
Cat-D→``PersonalKnowledgeAdapter``, Cat-E→``OutboundActionAdapter``.

AST-walk only — no ``importlib.import_module`` on the adapter module.

Severity ladder per memo 05 §3.4:

- No capabilities AND no ABC inheritance → ``advisory_pending_retrofit``
  (the three on-main v1 packs predate the framework; Cohort C closes it).
- Has capabilities, no ABC inheritance → ``error``.
- Has ABC inheritance but the wrong ABC for the declared category → ``error``.
- Missing/malformed ``adapter.py`` → ``error`` (named) rather than crashing.
"""

from __future__ import annotations

import ast
from pathlib import Path

from adminme.lib.pack_registry.models import AdapterManifest
from adminme.lib.pack_registry.validator.result import ValidationFinding

_CHECK_NAME = "base_class_conformance"

_CATEGORY_TO_ABC: dict[str, str] = {
    "Cat-A": "CommunicationAdapter",
    "Cat-B": "ExternalStateMirrorAdapter",
    "Cat-C": "InboundDataAdapter",
    "Cat-D": "PersonalKnowledgeAdapter",
    "Cat-E": "OutboundActionAdapter",
}
_ABC_TO_CATEGORY: dict[str, str] = {v: k for k, v in _CATEGORY_TO_ABC.items()}
_ABC_NAMES: frozenset[str] = frozenset(_ABC_TO_CATEGORY.keys())


def _base_name(base: ast.expr) -> str | None:
    """Simple name from ``ast.Name`` / ``ast.Attribute``; ``None`` otherwise."""
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return None


def _find_adapter_class(tree: ast.Module) -> ast.ClassDef | None:
    """First top-level class with an ABC base; else the first top-level class."""
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    for cls in classes:
        if any(_base_name(b) in _ABC_NAMES for b in cls.bases):
            return cls
    return classes[0] if classes else None


def _inherited_abcs(class_def: ast.ClassDef) -> list[str]:
    return [
        name for base in class_def.bases if (name := _base_name(base)) in _ABC_NAMES
    ]


def _finding(
    severity: str, message: str, field_path: str | None = None
) -> ValidationFinding:
    return ValidationFinding(
        severity=severity,  # type: ignore[arg-type]
        check=_CHECK_NAME,
        message=message,
        field_path=field_path,
    )


def check_base_class_conformance(
    pack_dir: Path,
    manifest: AdapterManifest,
) -> list[ValidationFinding]:
    """Run the base-class conformance rule. See module docstring for the ladder."""
    pack_dir = Path(pack_dir)
    adapter_py = pack_dir / "adapter.py"
    if not adapter_py.exists():
        return [
            _finding(
                "error",
                f"adapter.py not found under {pack_dir}; every adapter "
                "pack must ship a top-level adapter.py.",
            )
        ]

    try:
        tree = ast.parse(adapter_py.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        return [
            _finding(
                "error",
                f"adapter.py at {adapter_py} could not be parsed: "
                f"{type(exc).__name__}: {exc}",
            )
        ]

    class_def = _find_adapter_class(tree)
    if class_def is None:
        return [
            _finding(
                "error",
                f"adapter.py at {adapter_py} declares no top-level class.",
            )
        ]

    inherited = _inherited_abcs(class_def)
    primary = manifest.capabilities[0].kind if manifest.capabilities else None

    if not inherited:
        if not manifest.capabilities:
            return [
                _finding(
                    "advisory",
                    f"adapter class {class_def.name!r} inherits no framework "
                    "ABC and the manifest declares no capabilities; "
                    "advisory_pending_retrofit per Cohort C — the rebase "
                    "onto the framework ABCs closes the gap.",
                )
            ]
        expected = _CATEGORY_TO_ABC.get(primary or "", "<unknown>")
        return [
            _finding(
                "error",
                f"adapter class {class_def.name!r} inherits no framework "
                f"ABC yet the manifest declares {len(manifest.capabilities)} "
                f"capability block(s); primary kind={primary!r} requires "
                f"inheritance from {expected!r} per memo 05 §3.2 + [PM-34].",
                "capabilities[0].kind",
            )
        ]

    if len(inherited) > 1:
        return [
            _finding(
                "error",
                f"adapter class {class_def.name!r} inherits from multiple "
                f"framework ABCs {inherited!r}; the five ABCs are mutually "
                "exclusive per [PM-34].",
            )
        ]

    inherited_abc = inherited[0]
    expected_category = _ABC_TO_CATEGORY[inherited_abc]
    if primary is not None and primary != expected_category:
        return [
            _finding(
                "error",
                f"adapter class {class_def.name!r} inherits {inherited_abc!r} "
                f"(Cat={expected_category}) but the manifest's primary "
                f"capability is {primary!r}; expected "
                f"{_CATEGORY_TO_ABC[primary]!r} per memo 05 §3.2 + [PM-34].",
                "capabilities[0].kind",
            )
        ]

    return []


__all__ = ["check_base_class_conformance"]
