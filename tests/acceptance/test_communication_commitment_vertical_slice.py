"""Golden process acceptance: communication through completed commitment.

This is intentionally red behind a strict xfail during Gate 1. Release gating
runs pytest with ``--runxfail`` so the explicit failure cannot be reported as a
pass. Phase 2 must replace the sentinel with a black-box fixture that starts the
real composition root; isolated fake logs/buses/projections are not acceptable.
"""

from __future__ import annotations

import pytest


REQUIRED_STAGES = (
    "credential-free channel fixture emits a normalized inbound communication",
    "the production append port commits it to the encrypted event log",
    "post-commit notification wakes the durable bus under at-least-once delivery",
    "parties and interactions projections deterministically resolve the sender",
    "commitment extraction records schema-bound OpenClaw invocation provenance",
    "an LLM-derived commitment is a proposal and cannot mutate a projection directly",
    "AdministrateMe review and any OpenClaw approval pass independently",
    "guardedWrite and the final content/privacy/observation gates audit their outcomes",
    "the authorized commitment appears through the real API and adminme-ops.xlsx",
    "completion records an outcome event rather than editing projection state",
    "commitment/task/workbook read models update from the outcome event",
    "contextual what-now recalculates and no longer recommends completed work",
    "restart and redelivery preserve one semantic interaction/commitment/outcome",
)


@pytest.mark.xfail(
    strict=True,
    reason="MISSING_CAPABILITY: no production runtime closes the communication-to-outcome loop",
)
def test_communication_to_commitment_completion_recalculates_whatnow() -> None:
    """Start real processes and prove every stage in ``REQUIRED_STAGES``.

    The eventual test must additionally assert owner/sensitivity scope,
    correlation/causation, event/schema/pack versions, idempotency keys, actual
    provider/model provenance, durable offsets, and the absence of plaintext
    fixture content in raw database bytes. It must run with fake channel and
    fake pinned OpenClaw endpoints, no credentials and no network.
    """

    pytest.fail("MISSING_CAPABILITY:\n- " + "\n- ".join(REQUIRED_STAGES))
