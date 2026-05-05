"""Pack-loader canary for paralysis_detection."""

from __future__ import annotations

from pathlib import Path

from adminme.pipelines import invalidate_cache, load_pipeline_pack
from adminme.pipelines.base import Pipeline

PACK_ROOT = Path(__file__).resolve().parents[1]


def test_pack_loads_cleanly() -> None:
    invalidate_cache()
    pack = load_pipeline_pack(PACK_ROOT)
    assert pack.pack_id == "pipeline:paralysis_detection"
    assert pack.version == "1.0.0"
    # Proactive pack: NO triggers.events; the runner skips it.
    assert pack.triggers.get("events") is None
    assert pack.triggers.get("schedule") == "0 15,17 * * *"
    assert pack.triggers.get("proactive") is True
    assert "paralysis.triggered" in pack.events_emitted
    assert isinstance(pack.instance, Pipeline)


def test_pack_handler_exposes_runtime_class() -> None:
    invalidate_cache()
    pack = load_pipeline_pack(PACK_ROOT)
    cls = type(pack.instance)
    assert cls.__name__ == "ParalysisDetectionPipeline"
    assert cls.pack_id == "pipeline:paralysis_detection"
