"""Bound the live release selector while journals retain exact recovery history."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any


MAX_POINTER_DEPTH = 64


def retained_predecessor(pointer: dict[str, Any]) -> dict[str, Any]:
    """Copy at most 63 predecessors after validating the entire original chain.

    Only journaled activation may use this view. Its durable journal keeps the
    original pointer bytes and digest before selection; rollback and interrupted
    recovery restore those bytes, including the tail omitted from the live view.
    No release directory, journal, backup or original pointer is modified here.
    """

    current: object = pointer
    seen: set[str] = set()
    parent: Path | None = None
    for _depth in range(MAX_POINTER_DEPTH):
        if (
            not isinstance(current, dict)
            or not {"commit", "path"} <= current.keys()
            or not current.keys() <= {"commit", "path", "previous"}
        ):
            raise ValueError("release history schema is invalid")
        commit = current["commit"]
        raw_path = current["path"]
        if (
            not isinstance(commit, str)
            or re.fullmatch(r"[0-9a-f]{40}", commit) is None
            or commit in seen
            or not isinstance(raw_path, str)
        ):
            raise ValueError("release history identity is invalid")
        path = Path(raw_path)
        if parent is None:
            parent = path.parent
        if not path.is_absolute() or path != parent / commit:
            raise ValueError("release history coordinate is invalid")
        seen.add(commit)
        previous = current.get("previous")
        if previous is None:
            break
        current = previous
    else:
        raise ValueError("release history exceeds the safe depth")

    result = copy.deepcopy(pointer)
    if len(seen) == MAX_POINTER_DEPTH:
        retained = result
        for _depth in range(MAX_POINTER_DEPTH - 2):
            retained = retained["previous"]
        retained["previous"] = None
    return result
