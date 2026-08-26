from __future__ import annotations

import os
from pathlib import Path

import pytest

from adminme.simulations.source_pipeline_stress import (
    PROFILES,
    build_source_workload,
    run_source_pipeline_stress,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_source_stress_profiles_are_fixed_and_extended_is_8760_facts() -> None:
    assert len(build_source_workload("fast")) + 1 == PROFILES["fast"].source_facts
    assert len(build_source_workload("extended")) + 1 == 8_760
    assert build_source_workload("extended") == build_source_workload("extended")


def test_fast_source_pipeline_stress_production_composition(tmp_path: Path) -> None:
    result = run_source_pipeline_stress(
        "fast",
        instance_dir=tmp_path / "instance",
        repository_root=REPOSITORY_ROOT,
    )

    assert result["status"] == "PASS", result
    assert result["metrics"]["source_facts"] == PROFILES["fast"].source_facts
    assert (
        result["source_assurance_counts"]["accounted"]
        == (result["source_assurance_counts"]["obligations"])
    )
    assert result["failures"] == []


@pytest.mark.skipif(
    os.environ.get("ADMINME_RUN_EXTENDED_SOURCE_STRESS") != "1",
    reason="8,760-fact source stress is an explicit extended release profile",
)
def test_extended_source_pipeline_stress_production_composition(
    tmp_path: Path,
) -> None:
    result = run_source_pipeline_stress(
        "extended",
        instance_dir=tmp_path / "instance",
        repository_root=REPOSITORY_ROOT,
    )
    assert result["status"] == "PASS", result
