"""Unit tests for adminme.pipelines.pack_loader.

Covers happy-path load, cache hit, and the four PipelinePackLoadError
paths the loader is supposed to raise on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adminme.pipelines import (
    LoadedPipelinePack,
    PipelinePackLoadError,
    invalidate_cache,
    load_pipeline_pack,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ECHO_LOGGER_ROOT = REPO_ROOT / "tests" / "fixtures" / "pipelines" / "echo_logger"


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    invalidate_cache()
    yield
    invalidate_cache()


def test_load_echo_logger_happy_path() -> None:
    pack = load_pipeline_pack(ECHO_LOGGER_ROOT)
    assert isinstance(pack, LoadedPipelinePack)
    assert pack.pack_id == "pipeline:echo_logger"
    assert pack.version == "1.0.0"
    assert pack.triggers["events"] == ["messaging.received"]
    assert pack.events_emitted == []
    assert pack.instance.pack_id == "pipeline:echo_logger"
    assert pack.instance.version == "1.0.0"


def test_load_pipeline_pack_cache_hit() -> None:
    first = load_pipeline_pack(ECHO_LOGGER_ROOT)
    second = load_pipeline_pack(ECHO_LOGGER_ROOT)
    assert first is second
    assert first.instance is second.instance


def test_missing_pipeline_yaml_raises(tmp_path: Path) -> None:
    with pytest.raises(PipelinePackLoadError, match="missing pipeline.yaml"):
        load_pipeline_pack(tmp_path)


def test_malformed_yaml_raises(tmp_path: Path) -> None:
    (tmp_path / "pipeline.yaml").write_text("pack: : :\nthis is not yaml\n")
    with pytest.raises(PipelinePackLoadError, match="failed to read yaml"):
        load_pipeline_pack(tmp_path)


def test_missing_runtime_class_raises(tmp_path: Path) -> None:
    (tmp_path / "pipeline.yaml").write_text(
        "pack:\n"
        "  id: pipeline:bogus\n"
        "  version: 1.0.0\n"
        "  kind: pipeline\n"
        "runtime:\n"
        "  language: python\n"
        "  entrypoint: handler.py\n"
        "  class: NonexistentClass\n"
        "triggers:\n"
        "  events: [messaging.received]\n"
    )
    (tmp_path / "handler.py").write_text(
        "class SomeOtherClass:\n"
        "    pack_id = 'pipeline:bogus'\n"
        "    version = '1.0.0'\n"
        "    triggers = {'events': ['messaging.received']}\n"
        "    async def handle(self, event, ctx):\n"
        "        pass\n"
    )
    with pytest.raises(PipelinePackLoadError, match="not found in"):
        load_pipeline_pack(tmp_path)


def test_class_missing_handle_raises(tmp_path: Path) -> None:
    (tmp_path / "pipeline.yaml").write_text(
        "pack:\n"
        "  id: pipeline:no_handle\n"
        "  version: 1.0.0\n"
        "  kind: pipeline\n"
        "runtime:\n"
        "  language: python\n"
        "  entrypoint: handler.py\n"
        "  class: BrokenPipeline\n"
        "triggers:\n"
        "  events: [messaging.received]\n"
    )
    (tmp_path / "handler.py").write_text(
        "class BrokenPipeline:\n"
        "    pack_id = 'pipeline:no_handle'\n"
        "    version = '1.0.0'\n"
        "    triggers = {'events': ['messaging.received']}\n"
        "    # no async handle method\n"
    )
    with pytest.raises(PipelinePackLoadError, match="does not implement Pipeline protocol"):
        load_pipeline_pack(tmp_path)


def test_missing_entrypoint_file_raises(tmp_path: Path) -> None:
    (tmp_path / "pipeline.yaml").write_text(
        "pack:\n"
        "  id: pipeline:missing_entry\n"
        "  version: 1.0.0\n"
        "  kind: pipeline\n"
        "runtime:\n"
        "  language: python\n"
        "  entrypoint: handler.py\n"
        "  class: WhateverPipeline\n"
        "triggers:\n"
        "  events: [messaging.received]\n"
    )
    with pytest.raises(PipelinePackLoadError, match="runtime.entrypoint not found"):
        load_pipeline_pack(tmp_path)


def test_wrong_kind_raises(tmp_path: Path) -> None:
    (tmp_path / "pipeline.yaml").write_text(
        "pack:\n"
        "  id: pipeline:wrong_kind\n"
        "  version: 1.0.0\n"
        "  kind: skill\n"
        "runtime:\n"
        "  language: python\n"
        "  entrypoint: handler.py\n"
        "  class: WhateverPipeline\n"
    )
    with pytest.raises(PipelinePackLoadError, match="pack.kind must be 'pipeline'"):
        load_pipeline_pack(tmp_path)
