"""Typed finance intent and preparation facts; ingestion remains separately gated."""

from adminme.events.registry import registry
from adminme.finance.workflows import (
    FinanceDecisionApprovedV1,
    FinanceDecisionOutcomeRecordedV1,
    FinanceDecisionProposedV1,
    FinanceGoalRecordedV1,
    FinancePacketPreparedV1,
    FinancePreferenceRecordedV1,
)

registry.register("finance.goal_recorded", 1, FinanceGoalRecordedV1)
registry.register("finance.preference_recorded", 1, FinancePreferenceRecordedV1)
registry.register("finance.decision_proposed", 1, FinanceDecisionProposedV1)
registry.register("finance.decision_approved", 1, FinanceDecisionApprovedV1)
registry.register(
    "finance.decision_outcome_recorded", 1, FinanceDecisionOutcomeRecordedV1
)
registry.register("finance.packet_prepared", 1, FinancePacketPreparedV1)
