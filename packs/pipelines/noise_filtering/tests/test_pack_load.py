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
    assert pack.version == "19.0.0"
    assert pack.replay_on_version_change is True
    assert pack.delivery_concurrency == 8
    assert pack.triggers.get("events") == [
        "messaging.received",
        "messaging.sent",
        "telephony.sms_received",
    ]
    assert "messaging.classified" in pack.events_emitted
    assert "messaging.semantic_features_extracted" in pack.events_emitted
    assert "triage.item.proposed" in pack.events_emitted
    assert isinstance(pack.instance, Pipeline)
