"""A bounded selector never discards the exact journaled rollback authority."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from bootstrap.contracts import atomic_write
from bootstrap.release_history import MAX_POINTER_DEPTH, retained_predecessor
from bootstrap.update_supervisor import SupervisorError, _read_pointer


def _chain(releases: Path, depth: int) -> dict:
    previous = None
    for index in range(depth, 0, -1):
        commit = f"{index:040x}"
        previous = {
            "commit": commit,
            "path": str(releases / commit),
            "previous": previous,
        }
    assert previous is not None
    return previous


def _rows(pointer):
    result = []
    while pointer is not None:
        result.append((pointer["commit"], pointer["path"]))
        pointer = pointer.get("previous")
    return result


@pytest.mark.parametrize("depth", [1, 63, 64])
def test_retained_view_is_bounded_copy_and_preserves_newest_history(tmp_path, depth):
    pointer = _chain(tmp_path / "releases", depth)
    original = copy.deepcopy(pointer)
    retained = retained_predecessor(pointer)
    assert pointer == original
    assert retained is not pointer
    assert _rows(retained) == _rows(original)[: MAX_POINTER_DEPTH - 1]
    if depth < MAX_POINTER_DEPTH:
        assert retained == original
    else:
        assert len(_rows(retained)) == 63
        assert len(_rows(original)) == 64


@pytest.mark.parametrize(
    "invalid", ["oversized", "duplicate_tail", "malformed_tail", "foreign_tail"]
)
def test_retention_validates_tail_before_omitting_it(tmp_path, invalid):
    pointer = _chain(tmp_path / "releases", 65 if invalid == "oversized" else 64)
    tail = pointer
    while tail["previous"] is not None:
        tail = tail["previous"]
    if invalid == "duplicate_tail":
        tail.update({key: pointer[key] for key in ("commit", "path")})
    elif invalid == "malformed_tail":
        tail["unexpected"] = True
    elif invalid == "foreign_tail":
        tail["path"] = str(tmp_path / "foreign" / tail["commit"])
    original = copy.deepcopy(pointer)
    with pytest.raises(ValueError):
        retained_predecessor(pointer)
    assert pointer == original


@pytest.mark.parametrize("depth", [64, 65])
def test_fixed_supervisor_admits_full_valid_chain_but_refuses_excess(tmp_path, depth):
    app = tmp_path / "app"
    releases = app / "releases"
    releases.mkdir(parents=True, mode=0o700)
    pointer = _chain(releases, depth)
    for _commit, raw_path in _rows(pointer):
        Path(raw_path).mkdir(mode=0o700)
    path = app / "current-release.json"
    atomic_write(path, json.dumps(pointer).encode(), mode=0o600)
    before = path.read_bytes()
    descriptor = os.open(app, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if depth == 64:
            document, active = _read_pointer(app, descriptor)
            assert document == pointer
            assert active == Path(pointer["path"])
        else:
            with pytest.raises(SupervisorError, match="safe depth"):
                _read_pointer(app, descriptor)
    finally:
        os.close(descriptor)
    assert path.read_bytes() == before
