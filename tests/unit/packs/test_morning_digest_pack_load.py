"""Top-level pack-load + runner-skip canary for morning_digest.

The pack-internal `packs/pipelines/morning_digest/tests/test_pack_load.py`
covers structural fields. This file asserts the runner-side behavior:
proactive packs (no `triggers.events`) are SKIPPED by `discover()` per
`runner.py:131-138`. The assertion is `pack.pack_id NOT in
runner._packs` after `discover()`, NOT a manifest-field shape check
(`pack.triggers["proactive"] is True`) — the runner's contract is
keyed on absence of `events`, not on presence of the `proactive`
marker.
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
PACK_ROOT = REPO_ROOT / "packs" / "pipelines" / "morning_digest"
TEST_KEY = b"m" * 32


@pytest.fixture(autouse=True)
def _clear_caches():
    invalidate_cache()
    yield
    invalidate_cache()


def test_pack_loads_with_proactive_triggers() -> None:
    pack = load_pipeline_pack(PACK_ROOT)
    assert pack.pack_id == "pipeline:morning_digest"
    # Proactive shape: NO triggers.events → runner skip path applies.
    assert pack.triggers.get("events") is None
    assert pack.triggers.get("schedule") == "0 7 * * *"
    assert pack.triggers.get("proactive") is True


async def test_runner_discover_skips_morning_digest(tmp_path: Path) -> None:
    """Per `runner.py:131-138`: proactive packs (no `triggers.events`)
    are skipped by `discover()`. Assert via `pack_id not in
    runner._packs`."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = PipelineRunner(bus, log, config)
    try:
        await runner.discover(
            builtin_root=PACK_ROOT.parent.parent / "pipelines" / "morning_digest",
            installed_root=tmp_path / "no-installed-packs",
        )
        # Runner's discover walks for `pipeline.yaml` files. Even though
        # we point it directly at the morning_digest pack, the proactive
        # skip path means it does NOT register.
        assert "pipeline:morning_digest" not in runner.registered_pack_ids()
    finally:
        await bus.stop()
        await log.close()
