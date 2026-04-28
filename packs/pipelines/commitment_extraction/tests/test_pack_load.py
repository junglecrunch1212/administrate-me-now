"""Pack-loader canary for commitment_extraction."""

from __future__ import annotations

from pathlib import Path

from adminme.pipelines import invalidate_cache, load_pipeline_pack
from adminme.pipelines.base import Pipeline

PACK_ROOT = Path(__file__).resolve().parents[1]


def test_pack_loads_cleanly() -> None:
    invalidate_cache()
    pack = load_pipeline_pack(PACK_ROOT)
    assert pack.pack_id == "pipeline:commitment_extraction"
    assert pack.version == "4.2.0"
    assert pack.triggers["events"] == [
        "messaging.received",
        "messaging.sent",
    ]
    assert "commitment.proposed" in pack.events_emitted
    assert "commitment.suppressed" in pack.events_emitted
    assert isinstance(pack.instance, Pipeline)
