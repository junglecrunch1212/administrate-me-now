"""Pack-loader canary for reward_dispatch."""

from __future__ import annotations

from pathlib import Path

from adminme.pipelines import invalidate_cache, load_pipeline_pack
from adminme.pipelines.base import Pipeline

PACK_ROOT = Path(__file__).resolve().parents[1]


def test_pack_loads_cleanly() -> None:
    invalidate_cache()
    pack = load_pipeline_pack(PACK_ROOT)
    assert pack.pack_id == "pipeline:reward_dispatch"
    assert pack.version == "1.0.0"
    assert pack.triggers["events"] == ["task.completed", "commitment.completed"]
    assert "reward.ready" in pack.events_emitted
    assert isinstance(pack.instance, Pipeline)


def test_pack_handler_exposes_runtime_class() -> None:
    invalidate_cache()
    pack = load_pipeline_pack(PACK_ROOT)
    cls = type(pack.instance)
    assert cls.__name__ == "RewardDispatchPipeline"
    assert cls.pack_id == "pipeline:reward_dispatch"
    assert cls.triggers == {
        "events": ["task.completed", "commitment.completed"]
    }
