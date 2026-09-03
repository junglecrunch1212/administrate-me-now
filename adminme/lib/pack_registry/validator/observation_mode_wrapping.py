"""Observation-mode wrapping check (6 of 6).

Per [§6.20] and ADMINISTRATEME_BUILD.md §OBSERVATION the single outbound-
suppression enforcement point is :func:`adminme.lib.observation.outbound`.
Cat-A :meth:`CommunicationAdapter.send` and Cat-E
:meth:`OutboundActionAdapter.execute_action` MUST route the external API
call through that function so observation mode suppresses the side
effect when active.

This check AST-walks ``<pack_dir>/adapter.py``; locates the adapter
class's ``send`` (Cat-A) or ``execute_action`` (Cat-E) method body;
asserts a call to ``outbound`` resolved via one of three import shapes:

- ``from adminme.lib.observation import outbound`` → ``outbound(...)``
- ``from adminme.lib import observation`` → ``observation.outbound(...)``
- ``import adminme.lib.observation`` → ``adminme.lib.observation.outbound(...)``

Cat-B / Cat-C / Cat-D adapters skip this check (no outbound dispatch
surface; Cat-B's :meth:`apply_write` is the wrapping seam there). The sealed
G11 member-bridge reply profile is also skipped because the protected central
bridge-command dispatcher is its final observation gate.
AST-walk only — no ``importlib.import_module``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from adminme.lib.pack_registry.models import AdapterManifest
from adminme.lib.pack_registry.validator.bridge_communication_profile import (
    sealed_bridge_communication_profile,
)
from adminme.lib.pack_registry.validator.result import ValidationFinding

_CHECK_NAME = "observation_mode_wrapping"
_OBS_FN = "outbound"
_OBS_MODULE = ("adminme", "lib", "observation")

_CATEGORY_TO_METHOD: dict[str, str] = {"Cat-A": "send", "Cat-E": "execute_action"}


def _is_bridge_communication(pack_dir: Path, manifest: AdapterManifest) -> bool:
    return sealed_bridge_communication_profile(pack_dir, manifest) is not None


def _finding(severity: str, message: str) -> ValidationFinding:
    return ValidationFinding(
        severity=severity,  # type: ignore[arg-type]
        check=_CHECK_NAME,
        message=message,
        field_path=None,
    )


def _find_adapter_class(tree: ast.Module) -> ast.ClassDef | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            return node
    return None


def _collect_aliases(tree: ast.Module) -> tuple[set[str], set[str]]:
    """``(direct_aliases, module_aliases)`` for the observation import shapes."""
    dotted = ".".join(_OBS_MODULE)
    parent = ".".join(_OBS_MODULE[:-1])
    direct: set[str] = set()
    module: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == dotted:
                for alias in node.names:
                    if alias.name == _OBS_FN:
                        direct.add(alias.asname or alias.name)
            elif node.module == parent:
                for alias in node.names:
                    if alias.name == _OBS_MODULE[-1]:
                        module.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == dotted:
                    module.add(alias.asname or alias.name)
    return direct, module


def _call_targets_outbound(call: ast.Call, direct: set[str], module: set[str]) -> bool:
    func = call.func
    if isinstance(func, ast.Name) and func.id in direct:
        return True
    if isinstance(func, ast.Attribute) and func.attr == _OBS_FN:
        value = func.value
        if isinstance(value, ast.Name) and value.id in module:
            return True
        parts: list[str] = []
        cursor: ast.expr = value
        while isinstance(cursor, ast.Attribute):
            parts.append(cursor.attr)
            cursor = cursor.value
        if isinstance(cursor, ast.Name):
            parts.append(cursor.id)
        if tuple(reversed(parts)) == _OBS_MODULE:
            return True
    return False


def _method_calls_outbound(
    method: ast.AsyncFunctionDef | ast.FunctionDef, tree: ast.Module
) -> bool:
    direct, module = _collect_aliases(tree)
    if not direct and not module:
        return False
    return any(
        isinstance(node, ast.Call) and _call_targets_outbound(node, direct, module)
        for node in ast.walk(method)
    )


def check_observation_mode_wrapping(
    pack_dir: Path,
    manifest: AdapterManifest,
) -> list[ValidationFinding]:
    """Run the observation-mode wrapping rule against ``pack_dir`` / ``manifest``."""
    if _is_bridge_communication(Path(pack_dir), manifest):
        # G10 has no write method. G11's only write is reached through the
        # production bridge-command observation gate; structural validation
        # separately restricts that edge seam to async apply_write.
        return []

    kinds = {cap.kind for cap in manifest.capabilities}
    relevant = kinds & _CATEGORY_TO_METHOD.keys()
    if not relevant:
        return []

    pack_dir = Path(pack_dir)
    adapter_py = pack_dir / "adapter.py"
    if not adapter_py.exists():
        return [_finding("error", f"adapter.py not found under {pack_dir}.")]
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
                f"adapter.py at {adapter_py} declares no public top-level class.",
            )
        ]

    findings: list[ValidationFinding] = []
    methods_by_name: dict[str, ast.AsyncFunctionDef | ast.FunctionDef] = {
        stmt.name: stmt
        for stmt in class_def.body
        if isinstance(stmt, (ast.AsyncFunctionDef, ast.FunctionDef))
    }
    for kind in sorted(relevant):
        method_name = _CATEGORY_TO_METHOD[kind]
        method = methods_by_name.get(method_name)
        if method is None:
            findings.append(
                _finding(
                    "error",
                    f"adapter class {class_def.name!r} declares no "
                    f"{method_name!r} method but a {kind} capability is "
                    "declared; the outbound surface is missing per memo 05 §1.2.",
                )
            )
            continue
        if not _method_calls_outbound(method, tree):
            findings.append(
                _finding(
                    "error",
                    f"adapter class {class_def.name!r}.{method_name} does "
                    f"not call adminme.lib.observation.outbound; {kind} "
                    "adapters MUST route the external side effect through "
                    "the single outbound-suppression seam per [§6.20].",
                )
            )

    return findings


__all__ = ["check_observation_mode_wrapping"]
