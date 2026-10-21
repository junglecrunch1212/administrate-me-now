"""Unit tests for adminme.pipelines.pack_loader.

Covers happy-path load, cache hit, and the four PipelinePackLoadError
paths the loader is supposed to raise on.
"""

from __future__ import annotations

from pathlib import Path
import shutil

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
    assert pack.replay_on_version_change is False
    assert pack.delivery_concurrency == 1
    assert pack.instance.pack_id == "pipeline:echo_logger"
    assert pack.instance.version == "1.0.0"


def test_load_pipeline_pack_cache_hit() -> None:
    first = load_pipeline_pack(ECHO_LOGGER_ROOT)
    second = load_pipeline_pack(ECHO_LOGGER_ROOT)
    assert first is second
    assert first.instance is second.instance


async def test_same_version_pipeline_never_reuses_another_release_root(
    tmp_path: Path,
) -> None:
    roots = [tmp_path / "installed-release", tmp_path / "operational-release"]
    for root, value in zip(roots, ("installed", "operational"), strict=True):
        shutil.copytree(ECHO_LOGGER_ROOT, root)
        (root / "source.txt").write_text(value, encoding="utf-8")
        (root / "handler.py").write_text(
            "from pathlib import Path\n"
            "_PACK_ROOT = Path(__file__).parent\n"
            "class EchoLoggerPipeline:\n"
            "    pack_id = 'pipeline:echo_logger'\n"
            "    version = '1.0.0'\n"
            "    triggers = {'events': ['messaging.received']}\n"
            "    async def handle(self, event, ctx):\n"
            "        ctx.append((_PACK_ROOT / 'source.txt').read_text())\n",
            encoding="utf-8",
        )
    first = load_pipeline_pack(roots[0])
    shutil.rmtree(roots[0])
    second = load_pipeline_pack(roots[1])
    assert second is not first
    assert second.instance is not first.instance
    assert second.pack_root == roots[1].resolve()
    observed: list[str] = []
    await second.instance.handle({}, observed)  # type: ignore[arg-type]
    assert observed == ["operational"]

    alias = tmp_path / "current-release"
    alias.symlink_to(roots[1], target_is_directory=True)
    assert load_pipeline_pack(alias) is second
    assert load_pipeline_pack(roots[1] / ".." / roots[1].name) is second


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
    with pytest.raises(
        PipelinePackLoadError, match="does not implement Pipeline protocol"
    ):
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


def test_invalid_replay_mode_raises(tmp_path: Path) -> None:
    (tmp_path / "pipeline.yaml").write_text(
        "pack:\n"
        "  id: pipeline:bad_replay\n"
        "  version: 1.0.0\n"
        "  kind: pipeline\n"
        "runtime:\n"
        "  language: python\n"
        "  entrypoint: handler.py\n"
        "  class: BadReplayPipeline\n"
        "triggers:\n"
        "  events: [messaging.received]\n"
        "replay:\n"
        "  on_version_change: recent\n"
    )
    (tmp_path / "handler.py").write_text(
        "class BadReplayPipeline:\n"
        "    pack_id = 'pipeline:bad_replay'\n"
        "    version = '1.0.0'\n"
        "    triggers = {'events': ['messaging.received']}\n"
        "    async def handle(self, event, ctx):\n"
        "        pass\n"
    )
    with pytest.raises(
        PipelinePackLoadError,
        match="replay.on_version_change must be 'none' or 'full'",
    ):
        load_pipeline_pack(tmp_path)


@pytest.mark.parametrize("value", [0, 17, True, "many"])
def test_invalid_delivery_concurrency_raises(tmp_path: Path, value: object) -> None:
    (tmp_path / "pipeline.yaml").write_text(
        "pack:\n"
        "  id: pipeline:bad_delivery\n"
        "  version: 1.0.0\n"
        "  kind: pipeline\n"
        "runtime:\n"
        "  language: python\n"
        "  entrypoint: handler.py\n"
        "  class: BadDeliveryPipeline\n"
        "triggers:\n"
        "  events: [messaging.received]\n"
        "delivery:\n"
        f"  concurrency: {str(value).lower()}\n"
    )
    (tmp_path / "handler.py").write_text(
        "class BadDeliveryPipeline:\n"
        "    pack_id = 'pipeline:bad_delivery'\n"
        "    version = '1.0.0'\n"
        "    triggers = {'events': ['messaging.received']}\n"
        "    async def handle(self, event, ctx):\n"
        "        pass\n"
    )
    with pytest.raises(
        PipelinePackLoadError,
        match="delivery.concurrency must be an integer between 1 and 16",
    ):
        load_pipeline_pack(tmp_path)
