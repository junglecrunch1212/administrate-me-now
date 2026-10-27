"""Exact, reviewed runtime exceptions for source-verified retained releases."""

from __future__ import annotations

import re
from typing import Any


class RetainedRuntimeError(ValueError):
    pass


def retained_python_pin(
    controller: dict[str, Any], retained: dict[str, Any], commit: str
) -> str:
    """Select a pin only after the caller proves the complete retained Git tree.

    This is not candidate-preparation policy or a compatibility-range fallback.
    Unlisted commits retain their original exact pin, including older rollbacks.
    """
    python = controller["python"]
    declarations = python.get("reviewed_retained_releases", [])
    if not isinstance(declarations, list):
        raise RetainedRuntimeError("retained Python declarations must be a list")
    seen: set[str] = set()
    selected: dict[str, str] | None = None
    for declaration in declarations:
        if not isinstance(declaration, dict) or set(declaration) != {
            "commit",
            "declared_preferred",
            "runtime_preferred",
        }:
            raise RetainedRuntimeError("retained Python declaration is invalid")
        target, before, after = (
            declaration[key]
            for key in ("commit", "declared_preferred", "runtime_preferred")
        )
        if (
            not isinstance(target, str)
            or re.fullmatch(r"[0-9a-f]{40}", target) is None
            or target in seen
            or not isinstance(before, str)
            or not isinstance(after, str)
            or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", before) is None
            or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", after) is None
        ):
            raise RetainedRuntimeError("retained Python declaration is invalid")
        old = tuple(map(int, before.split(".")))
        new = tuple(map(int, after.split(".")))
        if old[:2] != new[:2] or old >= new or after != python["preferred"]:
            raise RetainedRuntimeError(
                "retained Python declaration is not an exact forward patch"
            )
        seen.add(target)
        if target == commit:
            selected = declaration
    if selected is None:
        return str(retained["python"]["preferred"])
    if retained["python"]["preferred"] != selected["declared_preferred"] or any(
        retained["python"][key] != python[key] for key in ("formula", "compatible")
    ):
        raise RetainedRuntimeError(
            "retained Python declaration contradicts the release"
        )
    return selected["runtime_preferred"]


def retained_node_pin(
    controller: dict[str, Any], retained: dict[str, Any], commit: str
) -> str:
    """Select a pin only after the caller proves the complete retained Git tree.

    This is not candidate-preparation policy or a compatibility-range fallback.
    Unlisted commits retain their original exact pin, including older rollbacks.
    """
    node = controller["node"]
    declarations = node.get("reviewed_retained_releases", [])
    if not isinstance(declarations, list):
        raise RetainedRuntimeError("retained Node declarations must be a list")
    seen: set[str] = set()
    selected: dict[str, str] | None = None
    for declaration in declarations:
        if not isinstance(declaration, dict) or set(declaration) != {
            "commit",
            "declared_preferred",
            "runtime_preferred",
        }:
            raise RetainedRuntimeError("retained Node declaration is invalid")
        target, before, after = (
            declaration[key]
            for key in ("commit", "declared_preferred", "runtime_preferred")
        )
        if (
            not isinstance(target, str)
            or re.fullmatch(r"[0-9a-f]{40}", target) is None
            or target in seen
            or not isinstance(before, str)
            or not isinstance(after, str)
            or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", before) is None
            or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", after) is None
        ):
            raise RetainedRuntimeError("retained Node declaration is invalid")
        old = tuple(map(int, before.split(".")))
        new = tuple(map(int, after.split(".")))
        if (
            old[0] != new[0]
            or old >= new
            or after != node["preferred"]
            or node["formula"] != f"node@{new[0]}"
        ):
            raise RetainedRuntimeError(
                "retained Node declaration is not an exact forward same-major maintenance update"
            )
        seen.add(target)
        if target == commit:
            selected = declaration
    if selected is None:
        return str(retained["node"]["preferred"])
    if retained["node"]["preferred"] != selected["declared_preferred"] or any(
        retained["node"][key] != node[key] for key in ("formula", "compatible")
    ):
        raise RetainedRuntimeError("retained Node declaration contradicts the release")
    return selected["runtime_preferred"]
