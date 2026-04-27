"""Pack-loader canary for noise_filtering."""

from __future__ import annotations

from pathlib import Path

from adminme.pipelines import invalidate_cache, load_pipeline_pack
from adminme.pipelines.base import Pipeline

PACK_ROOT = Path(__file__).resolve().parents[1]


def test_pack_loads_cleanly() -> None:
    invalidate_cache()
    pack = load_pipeline_pack(PACK_ROOT)
    assert pack.pack_id == "pipeline:noise_filtering"
    assert pack.version == "1.0.0"
    assert pack.triggers["events"] == [
        "messaging.received",
        "telephony.sms_received",
    ]
    assert "messaging.classified" in pack.events_emitted
    assert isinstance(pack.instance, Pipeline)
