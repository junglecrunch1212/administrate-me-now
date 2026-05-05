"""Top-level pack-load + runner-skip canary for paralysis_detection.

Same pattern as `test_morning_digest_pack_load.py`: assert
`pack.pack_id NOT in runner._packs` after `discover()` per
`runner.py:131-138`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.pipelines import invalidate_cache, load_pipeline_pack
from adminme.pipelines.runner import PipelineRunner

REPO_ROOT = Path(__file__).resolve().parents[3]
PACK_ROOT = REPO_ROOT / "packs" / "pipelines" / "paralysis_detection"
TEST_KEY = b"p" * 32


@pytest.fixture(autouse=True)
def _clear_caches():
    invalidate_cache()
    yield
    invalidate_cache()


def test_pack_loads_with_proactive_triggers() -> None:
    pack = load_pipeline_pack(PACK_ROOT)
    assert pack.pack_id == "pipeline:paralysis_detection"
    assert pack.triggers.get("events") is None
    assert pack.triggers.get("schedule") == "0 15,17 * * *"
    assert pack.triggers.get("proactive") is True
    # Deterministic — no skill dependencies declared.
    manifest = pack.manifest
    assert manifest["depends_on"]["skills"] == []


async def test_runner_discover_skips_paralysis_detection(tmp_path: Path) -> None:
    """Per `runner.py:131-138`: proactive packs are skipped by
    `discover()`. Assert via `pack_id not in runner._packs`."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, config)
    try:
        await runner.discover(
            builtin_root=PACK_ROOT.parent.parent / "pipelines" / "paralysis_detection",
            installed_root=tmp_path / "no-installed-packs",
        )
        assert "pipeline:paralysis_detection" not in runner.registered_pack_ids()
    finally:
        await bus.stop()
        await log.close()
