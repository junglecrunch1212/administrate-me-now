"""
Financial L1 event schemas — emitted by adapters in the ``financial`` family.

Per ADMINISTRATEME_BUILD.md §L1 + ``PLAID — DETAILED SPEC``,
SYSTEM_INVARIANTS.md §1 invariants 3 + 9 (event log append-only, typed
registry), §6 invariant 10 (financial adapters hardcode
``sensitivity_floor: privileged``; the config loader rejects any override
that would lower it), §8 invariant 3 (no LLM SDKs in adminme/),
DECISIONS.md §D6 (no LLM SDK), §D7 (schemas register at v1), §D8
(correlation_id propagation).

This module is the financial-family peer of ``adminme/events/schemas/adapters.py``
(cross-cutting), ``ingest.py`` (messaging / calendar / telephony / artifact),
``crm.py``, ``domain.py``, ``governance.py``. Plugin-introduced financial
event types still register via the ``hearth.event_types`` entry point
per §D9; this module covers first-party L1 financial adapters only.

CO-006 extends the adapter summary with the provider observations and workbook
planning inputs required to make the finance workbook and member projection
truthful.  Quiet-by-default is a presentation rule: transaction observations
remain typed, privileged facts in the append-only log and are not individually
pushed at the member.

- ``money_flow.plaid_synced`` — emitted once per ``/transactions/sync``
  cycle per Plaid Item (NOT per transaction — that would be the
  anti-vision "stream of everything" pattern per VISION §"Quiet by
  default"). The event carries the post-sync cursor plus
  added/modified/removed transaction counts; full per-transaction detail
  flows into the ``money_flow`` projection (out of scope for prompt 11b,
  deferred to a later projection slice) via downstream consumers.
  ``webhook_triggered=True`` when the sync was triggered by Plaid's
  ``SYNC_UPDATES_AVAILABLE`` webhook; ``False`` when the supervisor's
  backstop daily-poll fired in the absence of a webhook.

Count fields (``transactions_added``, ``transactions_modified``,
``transactions_removed``, ``accounts_modified_count``) are bounded ``ge=0``;
zero is a valid value (a poll that found no changes still emits one
event for the cursor advance).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.registry import registry


class MoneyFlowPlaidSyncedV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    plaid_item_id: str = Field(min_length=1)
    institution_id: str = Field(min_length=1)
    institution_name: str = Field(min_length=1)
    sync_cursor: str = Field(min_length=1)
    transactions_added: int = Field(ge=0)
    transactions_modified: int = Field(ge=0)
    transactions_removed: int = Field(ge=0)
    accounts_modified_count: int = Field(ge=0)
    synced_at: str = Field(min_length=1)
    webhook_triggered: bool


class MoneyFlowProviderObservedV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    plaid_item_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    transaction_id: str = Field(min_length=1)
    amount_minor: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    occurred_at: str = Field(min_length=1)
    authorized_at: str | None = None
    kind: Literal["paid", "received"]
    merchant_name: str | None = None
    merchant_category: str | None = None
    original_description: str | None = None
    provider_category: str | None = None
    pending: bool = False
    provider_revision: str = Field(min_length=1)
    observed_at: str = Field(min_length=1)


class MoneyFlowProviderRemovedV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    plaid_item_id: str = Field(min_length=1)
    transaction_id: str = Field(min_length=1)
    removed_at: str = Field(min_length=1)


class FinancialAccountObservedV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    plaid_item_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    institution_id: str = Field(min_length=1)
    institution_name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    account_type: Literal["depository", "credit", "loan", "investment", "other"]
    account_subtype: str | None = None
    last4: str | None = Field(default=None, pattern=r"^[0-9]{4}$")
    current_balance_minor: int | None = None
    available_balance_minor: int | None = None
    limit_minor: int | None = None
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    as_of: str = Field(min_length=1)
    link_healthy: bool = True


class MoneyFlowClassificationSetV1(BaseModel):
    model_config = {"extra": "forbid"}

    flow_id: str = Field(min_length=1)
    category: str | None = None
    memo: str | None = None
    notes: str | None = None
    included: bool | None = None
    updated_at: str = Field(min_length=1)
    updated_by_party_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def at_least_one_change(self) -> "MoneyFlowClassificationSetV1":
        if all(
            value is None
            for value in (self.category, self.memo, self.notes, self.included)
        ):
            raise ValueError("at least one classification field is required")
        return self


class MoneyFlowManuallyUpdatedV1(BaseModel):
    model_config = {"extra": "forbid"}

    flow_id: str = Field(min_length=1)
    amount_minor: int | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    occurred_at: str | None = None
    kind: Literal["paid", "received", "owed", "reimbursable"] | None = None
    linked_account: str | None = None
    merchant_name: str | None = None
    merchant_category: str | None = None
    category: str | None = None
    memo: str | None = None
    notes: str | None = None
    included: bool | None = None
    updated_at: str = Field(min_length=1)
    updated_by_party_id: str = Field(min_length=1)


class FinanceAssumptionSetV1(BaseModel):
    model_config = {"extra": "forbid"}

    assumption_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    label: str = Field(min_length=1)
    value_decimal: str = Field(pattern=r"^-?[0-9]+(?:\.[0-9]+)?$")
    unit: Literal["USD", "USD/month", "USD/year", "ratio", "months", "count"]
    notes: str | None = None
    as_of: str = Field(min_length=1)


class FinanceBudgetSetV1(BaseModel):
    model_config = {"extra": "forbid"}

    budget_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    amount_minor: int = Field(ge=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    cadence: Literal["monthly"] = "monthly"
    active: bool = True
    notes: str | None = None
    as_of: str = Field(min_length=1)


class FinanceScenarioSetV1(BaseModel):
    model_config = {"extra": "forbid"}

    scenario_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    is_baseline: bool = False
    horizon_months: int = Field(ge=1, le=600)
    opening_cash_delta_minor: int = 0
    monthly_income_delta_minor: int = 0
    monthly_expense_delta_minor: int = 0
    one_time_month: int | None = Field(default=None, ge=1, le=600)
    one_time_amount_minor: int = 0
    notes: str | None = None
    active: bool = True


class FinanceGoalSetV1(BaseModel):
    model_config = {"extra": "forbid"}

    goal_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    target_minor: int = Field(ge=0)
    current_minor: int = Field(default=0, ge=0)
    target_date: str | None = None
    priority: int = Field(default=100, ge=0, le=1000)
    status: Literal["active", "paused", "complete"] = "active"
    notes: str | None = None


class FinanceGoalPausedV1(BaseModel):
    model_config = {"extra": "forbid"}

    goal_id: str = Field(min_length=1)
    paused_at: str = Field(min_length=1)
    paused_by_member_id: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=500)


class FinanceGoalCompletedV1(BaseModel):
    model_config = {"extra": "forbid"}

    goal_id: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)
    completed_by_member_id: str = Field(min_length=1)
    note: str | None = Field(default=None, max_length=500)


class FinanceGoalReopenedV1(BaseModel):
    model_config = {"extra": "forbid"}

    goal_id: str = Field(min_length=1)
    reopened_at: str = Field(min_length=1)
    reopened_by_member_id: str = Field(min_length=1)
    previous_status: Literal["paused", "complete"]


class FinanceWorkbookPublishedV1(BaseModel):
    model_config = {"extra": "forbid"}

    workbook_name: str = Field(min_length=1)
    workbook_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    workbook_schema_version: int = Field(ge=1)
    published_at: str = Field(min_length=1)
    published_by_party_id: str = Field(min_length=1)
    validation_state: Literal["valid", "rejected"]
    validation_failures: list[str] = Field(default_factory=list, max_length=100)


registry.register("money_flow.plaid_synced", 1, MoneyFlowPlaidSyncedV1)
registry.register("money_flow.provider_observed", 1, MoneyFlowProviderObservedV1)
registry.register("money_flow.provider_removed", 1, MoneyFlowProviderRemovedV1)
registry.register("financial.account_observed", 1, FinancialAccountObservedV1)
registry.register("money_flow.classification_set", 1, MoneyFlowClassificationSetV1)
registry.register("money_flow.manually_updated", 1, MoneyFlowManuallyUpdatedV1)
registry.register("finance.assumption_set", 1, FinanceAssumptionSetV1)
registry.register("finance.budget_set", 1, FinanceBudgetSetV1)
registry.register("finance.scenario_set", 1, FinanceScenarioSetV1)
registry.register("finance.goal_set", 1, FinanceGoalSetV1)
registry.register("finance.goal_paused", 1, FinanceGoalPausedV1)
registry.register("finance.goal_completed", 1, FinanceGoalCompletedV1)
registry.register("finance.goal_reopened", 1, FinanceGoalReopenedV1)
registry.register("finance.workbook_published", 1, FinanceWorkbookPublishedV1)
