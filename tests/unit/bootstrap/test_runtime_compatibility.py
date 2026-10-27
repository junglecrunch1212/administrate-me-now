"""Retained-release patch review never becomes an open runtime range."""

from __future__ import annotations

import copy

import pytest

from bootstrap.runtime_compatibility import (
    RetainedRuntimeError,
    retained_node_pin,
    retained_python_pin,
)


COMMIT = "a" * 40


def _manifests():
    retained = {
        "python": {
            "preferred": "3.14.6",
            "compatible": ">=3.14,<3.15",
            "formula": "python@3.14",
        }
    }
    controller = copy.deepcopy(retained)
    controller["python"]["preferred"] = "3.14.7"
    controller["python"]["reviewed_retained_releases"] = [
        {
            "commit": COMMIT,
            "declared_preferred": "3.14.6",
            "runtime_preferred": "3.14.7",
        }
    ]
    return controller, retained


def test_only_the_exact_retained_commit_receives_the_reviewed_patch():
    controller, retained = _manifests()
    before = copy.deepcopy(retained)
    assert retained_python_pin(controller, retained, COMMIT) == "3.14.7"
    assert retained == before
    assert retained_python_pin(controller, retained, "b" * 40) == "3.14.6"
    del controller["python"]["reviewed_retained_releases"]
    assert retained_python_pin(controller, retained, COMMIT) == "3.14.6"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("commit", "a" * 39),
        ("commit", "A" * 40),
        ("commit", None),
        ("declared_preferred", "3.13.9"),
        ("declared_preferred", "3.14.7"),
        ("declared_preferred", "3.14.8"),
        ("declared_preferred", "3.14"),
        ("runtime_preferred", "3.14.8"),
        ("runtime_preferred", ">=3.14.7"),
        ("unexpected", True),
    ],
)
def test_invalid_or_broad_declarations_fail_closed(key, value):
    controller, retained = _manifests()
    controller["python"]["reviewed_retained_releases"][0][key] = value
    with pytest.raises(RetainedRuntimeError):
        retained_python_pin(controller, retained, COMMIT)


@pytest.mark.parametrize("declarations", [None, {}, [None], [{}, {}]])
def test_malformed_declaration_collections_fail_closed(declarations):
    controller, retained = _manifests()
    controller["python"]["reviewed_retained_releases"] = declarations
    with pytest.raises(RetainedRuntimeError):
        retained_python_pin(controller, retained, COMMIT)


def test_duplicate_declaration_fails_closed():
    controller, retained = _manifests()
    entries = controller["python"]["reviewed_retained_releases"]
    entries.append(copy.deepcopy(entries[0]))
    with pytest.raises(RetainedRuntimeError):
        retained_python_pin(controller, retained, COMMIT)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("preferred", "3.14.5"),
        ("formula", "python@3.13"),
        ("compatible", ">=3.13,<3.15"),
    ],
)
def test_declaration_must_match_the_retained_manifest(key, value):
    controller, retained = _manifests()
    retained["python"][key] = value
    with pytest.raises(RetainedRuntimeError, match="contradicts"):
        retained_python_pin(controller, retained, COMMIT)


NODE_COMMITS = (
    "471d20ec71d40d59490ff763be524d73a3c3cb7d",
    "02c2957acc4e659a19720db5bfb296ebba710bbd",
)


def _node_manifests():
    retained = {
        "node": {
            "preferred": "24.19.0",
            "formula": "node@24",
            "compatible": ">=24.15,<25",
        }
    }
    controller = copy.deepcopy(retained)
    controller["node"]["preferred"] = "24.22.1"
    controller["node"]["reviewed_retained_releases"] = [
        {
            "commit": commit,
            "declared_preferred": "24.19.0",
            "runtime_preferred": "24.22.1",
        }
        for commit in NODE_COMMITS
    ]
    return controller, retained


def test_node_maintenance_is_exact_and_does_not_mutate_retained_manifests():
    controller, retained = _node_manifests()
    before = copy.deepcopy(retained)
    for commit in NODE_COMMITS:
        assert retained_node_pin(controller, retained, commit) == "24.22.1"
    assert retained == before
    assert retained_node_pin(controller, retained, COMMIT) == "24.19.0"
    del controller["node"]["reviewed_retained_releases"]
    assert retained_node_pin(controller, retained, NODE_COMMITS[0]) == "24.19.0"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("commit", "a" * 39),
        ("commit", "A" * 40),
        ("commit", None),
        ("declared_preferred", "22.23.2"),
        ("declared_preferred", "24.22.1"),
        ("declared_preferred", "24.23.0"),
        ("declared_preferred", "24.19"),
        ("runtime_preferred", "26.12.1"),
        ("runtime_preferred", "24.22.2"),
        ("runtime_preferred", ">=24.22.1"),
        ("unexpected", True),
    ],
)
def test_node_declarations_refuse_unreviewed_or_broad_updates(key, value):
    controller, retained = _node_manifests()
    controller["node"]["reviewed_retained_releases"][0][key] = value
    with pytest.raises(RetainedRuntimeError):
        retained_node_pin(controller, retained, NODE_COMMITS[0])


@pytest.mark.parametrize("declarations", [None, {}, [None], [{}, {}]])
def test_node_declaration_collection_is_closed(declarations):
    controller, retained = _node_manifests()
    controller["node"]["reviewed_retained_releases"] = declarations
    with pytest.raises(RetainedRuntimeError):
        retained_node_pin(controller, retained, NODE_COMMITS[0])


def test_duplicate_node_declaration_is_refused():
    controller, retained = _node_manifests()
    entries = controller["node"]["reviewed_retained_releases"]
    entries.append(copy.deepcopy(entries[0]))
    with pytest.raises(RetainedRuntimeError):
        retained_node_pin(controller, retained, NODE_COMMITS[0])


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("preferred", "24.18.0"),
        ("formula", "node@26"),
        ("compatible", ">=24.15,<27"),
    ],
)
def test_node_declaration_must_match_the_source_verified_manifest(key, value):
    controller, retained = _node_manifests()
    retained["node"][key] = value
    with pytest.raises(RetainedRuntimeError, match="contradicts"):
        retained_node_pin(controller, retained, NODE_COMMITS[0])


def test_node_formula_cannot_be_ambient_or_another_major():
    controller, retained = _node_manifests()
    controller["node"]["formula"] = "node"
    with pytest.raises(RetainedRuntimeError):
        retained_node_pin(controller, retained, NODE_COMMITS[0])
