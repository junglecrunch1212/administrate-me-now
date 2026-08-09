from __future__ import annotations

from pathlib import Path
from typing import get_args

from adminme.events.registry import ensure_autoloaded, registry
from adminme.events.schemas.triage import TriageTargetKind
from adminme.lifecycle_catalog import (
    CANDIDATE_CHANNELS,
    LIFE_PATTERNS,
    OBJECT_KIND_ALIASES,
    OBJECT_LIFECYCLES,
    candidate_coverage_matrix,
    catalog_document,
)


def test_every_registered_technical_event_has_family_and_property_contract() -> None:
    catalog = catalog_document()
    assert catalog["summary"]["uncataloged_event_types"] == []
    rows = catalog["technical_events"]
    production_types = {
        event_type
        for event_type in registry.known_types()
        if any(
            model is not None and not model.__module__.startswith(("test_", "tests."))
            for (registered_type, _version), model in registry._by_key.items()
            if registered_type == event_type
        )
    }
    assert {row["event_type"] for row in rows} == production_types
    assert all(row["family"] for row in rows)
    assert all(isinstance(row["properties"], dict) for row in rows)
    assert all(isinstance(row["required"], list) for row in rows)


def test_every_candidate_channel_input_is_registered_and_targets_are_closed() -> None:
    ensure_autoloaded()
    registered = set(registry.known_types())
    target_kinds = set(get_args(TriageTargetKind))
    for channel in CANDIDATE_CHANNELS:
        assert set(channel.input_event_types) <= registered
        assert set(channel.candidate_targets) <= target_kinds
        assert channel.reason.strip()
    assert (
        set(
            next(
                channel.candidate_targets
                for channel in CANDIDATE_CHANNELS
                if channel.channel_id == "member_add_for_review"
            )
        )
        == target_kinds
    )


def test_source_candidate_matrix_is_total_and_every_cell_is_explained() -> None:
    target_kinds = set(get_args(TriageTargetKind))
    channel_ids = {channel.channel_id for channel in CANDIDATE_CHANNELS}
    cells = candidate_coverage_matrix()
    assert len(cells) == len(channel_ids) * len(target_kinds)
    assert {(cell.channel_id, cell.target_kind) for cell in cells} == {
        (channel_id, target) for channel_id in channel_ids for target in target_kinds
    }
    assert all(cell.reason.strip() for cell in cells)
    assert all(
        cell.process_test is not None for cell in cells if cell.coverage == "supported"
    )
    catalog = catalog_document()
    assert catalog["summary"]["unproved_supported_source_candidate_cells"] == []


def test_every_declared_lifecycle_event_is_registered_and_gap_is_named() -> None:
    ensure_autoloaded()
    registered = set(registry.known_types())
    pairs: set[tuple[str, str]] = set()
    for lifecycle in OBJECT_LIFECYCLES:
        assert lifecycle.first_surfaces
        assert lifecycle.management_surfaces
        assert lifecycle.history_surfaces
        for transition in lifecycle.transitions:
            pair = (lifecycle.object_kind, transition.action)
            assert pair not in pairs
            pairs.add(pair)
            if transition.event_type is not None:
                assert transition.event_type in registered
            if transition.implementation != "exact":
                assert transition.defect_id is not None
    catalog = catalog_document()
    assert catalog["summary"]["unproved_exact_lifecycle_transitions"] == []
    repository_root = Path(__file__).resolve().parents[2]
    for lifecycle in catalog["object_lifecycles"]:
        for transition in lifecycle["transitions"]:
            if transition["implementation"] != "exact":
                continue
            test_path, test_name = transition["process_test"].split("::", 1)
            source = (repository_root / test_path).read_text(encoding="utf-8")
            assert f"def {test_name}(" in source


def test_every_life_pattern_and_triage_target_maps_to_a_lifecycle() -> None:
    lifecycle_kinds = {value.object_kind for value in OBJECT_LIFECYCLES}
    declared_kinds = {
        kind for pattern in LIFE_PATTERNS for kind in pattern.canonical_kinds
    } | set(get_args(TriageTargetKind))
    assert declared_kinds <= set(OBJECT_KIND_ALIASES)
    assert set(OBJECT_KIND_ALIASES.values()) <= lifecycle_kinds
