from pathlib import Path

from adminme.lib.skill_runner.pack_loader import invalidate_cache, load_pack


PACK_ROOT = Path(__file__).resolve().parents[1]


def test_pack_loads_and_rejects_ungrounded_shape() -> None:
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.pack_id == "skill:extract_note_semantics"
    assert loaded.version == "1.0.0"
    assert loaded.handler_post_process is not None
    output = loaded.handler_post_process(
        {
            "candidates": [
                {
                    "target_kind": "task",
                    "title": "Call the school",
                    "candidate": {},
                    "confidence": 0.9,
                    "reasons": ["explicit request"],
                }
            ],
            "reasons": [],
        },
        {},
        None,
    )
    assert output["candidates"] == []
