"""write_capabilities method-presence check (5 of 6).

Per [UT-27] the write surface on a Cat-B adapter is asymmetric: the pack
manifest declares a per-operation ``write_capabilities`` list (e.g.
``["create_list", "delete_item", "toggle_completion"]``) and each
declared operation MUST be backed by EITHER:

- an ``async def _handle_<operation>(self, operation)`` method on the
  adapter class (one method per operation), OR
- a single ``async def _apply_write_impl(self, operation)`` method whose
  body dispatches on ``operation.kind`` (the
  :class:`adminme.lib.adapters.ExternalStateMirrorAdapter` base-class
  precedent).

Missing both shapes for a declared operation yields one ``error``
finding per missing operation, named in the message + field-path
``write_capabilities[N]``.

Category gate per memo 05 §2.1: only Cat-B carries a write surface. A
manifest with non-empty ``write_capabilities`` and a primary capability
of Cat-A / Cat-C / Cat-D / Cat-E surfaces as ``error`` on the pack-level
``write_capabilities`` field — the manifest is wrong-shape.

AST-walk only: no ``importlib.import_module`` on the adapter; we parse
the file content with stdlib :mod:`ast`.
"""

from __future__ import annotations

import ast
from pathlib import Path

from adminme.lib.pack_registry.models import AdapterManifest
from adminme.lib.pack_registry.validator.result import ValidationFinding

_CHECK_NAME = "write_capabilities"

# Categories that legitimately carry a write surface per memo 05 §2.1.
# Cat-B is the only "bidirectional state mirror" today; future write-
# carrying categories require a constitutional amendment.
_WRITE_CARRYING_KINDS: frozenset[str] = frozenset({"Cat-B"})


def _finding(
    severity: str, message: str, field_path: str | None = None
) -> ValidationFinding:
    return ValidationFinding(
        severity=severity,  # type: ignore[arg-type]
        check=_CHECK_NAME,
        message=message,
        field_path=field_path,
    )


def _find_adapter_class(tree: ast.Module) -> ast.ClassDef | None:
    """First top-level class whose name does NOT start with ``_``.

    Matches the convention in :mod:`base_class_conformance`: a pack's
    adapter.py declares the adapter class as the top-level public class.
    """
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            return node
    return None


def _method_names(class_def: ast.ClassDef) -> set[str]:
    return {
        stmt.name
        for stmt in class_def.body
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _apply_write_impl_dispatches_on_kind(class_def: ast.ClassDef) -> bool:
    """True iff ``_apply_write_impl`` exists and references ``operation.kind``.

    The dispatch shape is the precedent from :class:`ExternalStateMirrorAdapter`:
    one private method whose body branches on ``operation.kind``. We
    AST-walk the body for any attribute access ``<x>.kind`` where ``<x>``
    is named ``operation``; a bare presence of the method without that
    dispatch is treated as missing.
    """
    for stmt in class_def.body:
        if not isinstance(stmt, ast.AsyncFunctionDef):
            continue
        if stmt.name != "_apply_write_impl":
            continue
        for node in ast.walk(stmt):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "kind"
                and isinstance(node.value, ast.Name)
                and node.value.id == "operation"
            ):
                return True
        return False
    return False


def check_write_capabilities(
    pack_dir: Path,
    manifest: AdapterManifest,
) -> list[ValidationFinding]:
    """Run the write_capabilities method-presence rule against ``pack_dir`` / ``manifest``."""
    if not manifest.write_capabilities:
        return []

    findings: list[ValidationFinding] = []
    primary = manifest.capabilities[0].kind if manifest.capabilities else None
    if primary is not None and primary not in _WRITE_CARRYING_KINDS:
        findings.append(
            _finding(
                "error",
                f"manifest declares write_capabilities={manifest.write_capabilities!r} "
                f"but the primary capability kind is {primary!r}; only "
                f"{sorted(_WRITE_CARRYING_KINDS)!r} carry a write surface per "
                "memo 05 §2.1.",
                "write_capabilities",
            )
        )
        return findings

    pack_dir = Path(pack_dir)
    adapter_py = pack_dir / "adapter.py"
    if not adapter_py.exists():
        findings.append(
            _finding(
                "error",
                f"adapter.py not found under {pack_dir}; cannot verify "
                "write_capabilities method-presence.",
            )
        )
        return findings

    try:
        tree = ast.parse(adapter_py.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        findings.append(
            _finding(
                "error",
                f"adapter.py at {adapter_py} could not be parsed: "
                f"{type(exc).__name__}: {exc}",
            )
        )
        return findings

    class_def = _find_adapter_class(tree)
    if class_def is None:
        findings.append(
            _finding(
                "error",
                f"adapter.py at {adapter_py} declares no public top-level class.",
            )
        )
        return findings

    methods = _method_names(class_def)
    has_dispatch = _apply_write_impl_dispatches_on_kind(class_def)
    if has_dispatch:
        return findings

    for op_index, operation in enumerate(manifest.write_capabilities):
        if f"_handle_{operation}" in methods:
            continue
        findings.append(
            _finding(
                "error",
                f"adapter class {class_def.name!r} declares no "
                f"async def _handle_{operation}(self, operation) method "
                f"and no async def _apply_write_impl dispatch on "
                f"operation.kind; declared operation "
                f"{operation!r} is unimplemented per [UT-27].",
                f"write_capabilities[{op_index}]",
            )
        )

    return findings


__all__ = ["check_write_capabilities"]
