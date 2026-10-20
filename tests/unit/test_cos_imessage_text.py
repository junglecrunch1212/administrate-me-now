from __future__ import annotations

import json
from pathlib import Path

import pytest

from adminme.cos_responses import validate_imessage_response_text

CORPUS = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures/cos_imessage_text.json"
    ).read_text()
)


@pytest.mark.parametrize("case", CORPUS["cases"])
def test_pinned_imessage_literal_subset(case: dict[str, object]) -> None:
    body = str(case["text"])
    if case["allowed"]:
        validate_imessage_response_text(body)
    else:
        with pytest.raises(ValueError, match="one plain-text line"):
            validate_imessage_response_text(body)


def test_imessage_limit_matches_native_utf16_units() -> None:
    validate_imessage_response_text("😀" * 4000)
    with pytest.raises(ValueError):
        validate_imessage_response_text("😀" * 4001)
    with pytest.raises(ValueError):
        validate_imessage_response_text("unsafe\ud800")
