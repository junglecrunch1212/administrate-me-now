"""Tests for :mod:`~.projection_subscription_compatibility`.

Each test constructs a ``tmp_path``-rooted stub projections tree:
``<tmp_path>/<projection_name>/__init__.py`` files containing a class
inheriting from ``Projection`` with a ``subscribes_to`` literal. The
check AST-walks the tree, so the stubs do NOT need to be importable as
Python modules — they only need to parse cleanly. This keeps the test
suite independent of the on-main projection tree.
"""

from __future__ import annotations

from pathlib import Path

from adminme.lib.pack_registry.models import AdapterManifest
from adminme.lib.pack_registry.validator.projection_subscription_compatibility import (
    check_projection_subscription_compatibility,
)


def _write_projection(
    projections_dir: Path,
    name: str,
    *,
    subscribes_to: list[str] | str,
    base: str = "Projection",
) -> None:
    """Write a stub ``<name>/__init__.py`` declaring a Projection subclass."""
    pkg = projections_dir / name
    pkg.mkdir(parents=True, exist_ok=True)
    if isinstance(subscribes_to, str):
        rendered = repr(subscribes_to)
    else:
        rendered = "[" + ", ".join(repr(f) for f in subscribes_to) + "]"
    init_path = pkg / "__init__.py"
    init_path.write_text(
        "from __future__ import annotations\n"
        "\n"
        f"class {name.title().replace('_', '')}Projection({base}):\n"
        f"    subscribes_to = {rendered}\n",
        encoding="utf-8",
    )


def test_happy_path_cat_b_family_subscribed_by_existing_projection(
    tmp_path: Path,
) -> None:
    _write_projection(tmp_path, "tasks", subscribes_to=["task.created"])
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-B",
                    "runtime": "central",
                    "event_families": ["task.created"],
                }
            ]
        }
    )
    findings = check_projection_subscription_compatibility(
        manifest, projections_dir=tmp_path
    )
    assert findings == []


def test_cat_b_family_unmatched_yields_error_with_correct_field_path(
    tmp_path: Path,
) -> None:
    _write_projection(tmp_path, "tasks", subscribes_to=["task.created"])
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-B",
                    "runtime": "central",
                    "event_families": ["nonexistent.family"],
                }
            ]
        }
    )
    findings = check_projection_subscription_compatibility(
        manifest, projections_dir=tmp_path
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "error"
    assert finding.check == "projection_subscription_compatibility"
    assert finding.field_path == "capabilities[0].event_families"
    assert "nonexistent.family" in finding.message


def test_cat_d_pending_member_knowledge_yields_advisory_not_error(
    tmp_path: Path,
) -> None:
    _write_projection(tmp_path, "tasks", subscribes_to=["task.created"])
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-D",
                    "runtime": "bridge",
                    "event_families": [
                        "member_knowledge.note_added",
                        "member_knowledge.note_updated",
                    ],
                }
            ],
            "owner_scope_overridability_at_install": False,
        }
    )
    findings = check_projection_subscription_compatibility(
        manifest, projections_dir=tmp_path
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "advisory"
    assert finding.check == "projection_subscription_compatibility"
    assert "advisory_pending_projection" in finding.message
    assert finding.field_path == "capabilities[0].event_families"


def test_cat_d_pending_lists_yields_advisory(tmp_path: Path) -> None:
    _write_projection(tmp_path, "tasks", subscribes_to=["task.created"])
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-D",
                    "runtime": "bridge",
                    "event_families": ["lists.item_added"],
                }
            ],
            "owner_scope_overridability_at_install": False,
        }
    )
    findings = check_projection_subscription_compatibility(
        manifest, projections_dir=tmp_path
    )
    assert len(findings) == 1
    assert findings[0].severity == "advisory"


def test_cat_a_capability_is_skipped_even_when_family_unmatched(
    tmp_path: Path,
) -> None:
    _write_projection(tmp_path, "tasks", subscribes_to=["task.created"])
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received"],
                }
            ]
        }
    )
    findings = check_projection_subscription_compatibility(
        manifest, projections_dir=tmp_path
    )
    assert findings == []


def test_cat_c_and_cat_e_capabilities_are_skipped(tmp_path: Path) -> None:
    _write_projection(tmp_path, "tasks", subscribes_to=["task.created"])
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-C",
                    "runtime": "bridge",
                    "event_families": ["home_assistant.state_changed"],
                },
                {
                    "kind": "Cat-E",
                    "runtime": "central",
                    "event_families": ["outbound.dispatched"],
                },
            ],
            "observation_mode_required": True,
        }
    )
    findings = check_projection_subscription_compatibility(
        manifest, projections_dir=tmp_path
    )
    assert findings == []


def test_empty_event_families_skips_the_capability(tmp_path: Path) -> None:
    _write_projection(tmp_path, "tasks", subscribes_to=["task.created"])
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-B",
                    "runtime": "central",
                    "event_families": [],
                }
            ]
        }
    )
    findings = check_projection_subscription_compatibility(
        manifest, projections_dir=tmp_path
    )
    assert findings == []


def test_mixed_pending_and_unknown_families_still_error(tmp_path: Path) -> None:
    """The pending carve-out is forward-compat for projections not-yet-shipped,
    not a blanket excuse. A Cat-D capability declaring one pending family AND
    one definitively-unknown family must surface as an error."""
    _write_projection(tmp_path, "tasks", subscribes_to=["task.created"])
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-D",
                    "runtime": "bridge",
                    "event_families": [
                        "member_knowledge.note_added",
                        "wholly_unknown.thing",
                    ],
                }
            ],
            "owner_scope_overridability_at_install": False,
        }
    )
    findings = check_projection_subscription_compatibility(
        manifest, projections_dir=tmp_path
    )
    assert len(findings) == 1
    assert findings[0].severity == "error"


def test_subscribes_to_string_wildcard_shape_is_collected(tmp_path: Path) -> None:
    """A projection declaring ``subscribes_to = "*"`` (the wildcard shape
    the Projection ABC's annotation allows) does NOT count as subscribing
    to a specific family — the wildcard is a runtime concept and the
    validator is a manifest-vs-code-shape check. The capability still
    surfaces as a finding when no concrete subscription matches."""
    _write_projection(tmp_path, "fanout", subscribes_to="*")
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-B",
                    "runtime": "central",
                    "event_families": ["task.created"],
                }
            ]
        }
    )
    findings = check_projection_subscription_compatibility(
        manifest, projections_dir=tmp_path
    )
    assert len(findings) == 1
    assert findings[0].severity == "error"


def test_missing_projections_dir_yields_error_for_cat_b_capability(
    tmp_path: Path,
) -> None:
    """When the projections_dir does not exist (or is empty), there are
    no subscriptions to match against; a Cat-B capability with a concrete
    family must surface as an error rather than silently pass."""
    nonexistent = tmp_path / "no_such_dir"
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-B",
                    "runtime": "central",
                    "event_families": ["task.created"],
                }
            ]
        }
    )
    findings = check_projection_subscription_compatibility(
        manifest, projections_dir=nonexistent
    )
    assert len(findings) == 1
    assert findings[0].severity == "error"


def test_dotted_projection_base_attribute_is_recognized(tmp_path: Path) -> None:
    """Projection subclasses declared with ``base.Projection`` (the
    fully-qualified attribute access shape) MUST be discovered just like
    the bare-name shape. The on-main 11 projections use the bare name,
    but the validator should not assume that convention."""
    pkg = tmp_path / "dotted"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from adminme.projections import base\n"
        "\n"
        "class DottedProjection(base.Projection):\n"
        "    subscribes_to = ['dotted.event']\n",
        encoding="utf-8",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-B",
                    "runtime": "central",
                    "event_families": ["dotted.event"],
                }
            ]
        }
    )
    findings = check_projection_subscription_compatibility(
        manifest, projections_dir=tmp_path
    )
    assert findings == []


def test_default_projections_dir_resolves_to_on_main_tree() -> None:
    """Smoke test: with no injected projections_dir, the check resolves to
    ``adminme/projections/`` and a Cat-B capability declaring ``task.created``
    (which the on-main TasksProjection subscribes to) passes cleanly."""
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-B",
                    "runtime": "central",
                    "event_families": ["task.created"],
                }
            ]
        }
    )
    findings = check_projection_subscription_compatibility(manifest)
    assert findings == []
