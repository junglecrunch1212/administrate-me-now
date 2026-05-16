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

One event type registers here at v1:

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

from pydantic import BaseModel, Field

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


registry.register("money_flow.plaid_synced", 1, MoneyFlowPlaidSyncedV1)
