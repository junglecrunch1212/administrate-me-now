"""Live integration test for the skill runner wrapper.

Marked `requires_live_services` and skipped in Phase A (the sandbox has
no OpenClaw gateway). Phase B post-bootstrap, this test exercises the
real `classify_test` pack end-to-end against a running OpenClaw and
asserts a `skill.call.recorded` event lands in the event log.

Activated by Phase B's smoke-test prompt (19); kept here so the test
module exists at the documented path from prompt 09a onward.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.requires_live_services


@pytest.mark.asyncio
async def test_classify_test_live_round_trip() -> None:
    """Phase B: invoke `classify_test` against a live OpenClaw gateway."""
    pytest.skip(
        "live OpenClaw gateway required; Phase A sandbox has no gateway. "
        "Phase B's smoke test (prompt 19) activates this test."
    )
