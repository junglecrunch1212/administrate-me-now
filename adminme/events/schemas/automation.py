"""
Home-automation canonical event family — emitted by Cat-E adapters that own
side-effecting actuator state (Home Assistant today per prompt 11g; future
home-automation packs reuse the same ``action.executed`` / ``action.failed``
shapes).

Per ADMINISTRATEME_BUILD.md §L1, SYSTEM_INVARIANTS.md §1 invariants 3 + 9
(event log append-only, typed registry), §6.20 (observation wrap is the
required gate for Cat-E side effects), §7.3 + §8.2 (adapters never write
projections; no LLM SDKs in adminme/), DECISIONS.md §D6 (no LLM SDK), §D7
(schemas immutable at v1 — this module ADDS three new v1 schemas), §D8
(causation_id / correlation_id propagation across the action.requested →
action.executed | action.failed pair; the ``correlation_id`` field on
``ActionExecutedV1`` / ``ActionFailedV1`` carries the request's
correlation_id forward), §D17 (adapters never write projections), §D19 (the
five-capability taxonomy that introduces Cat-E), §D24 (Home Assistant
multi-capability — Cat-C state reads + Cat-E service calls in the same
pack). Memo 05 §3.2 (event-family naming), §8 (Cat-E observation gate).

This module is the home-automation peer of ``adminme/events/schemas/lists.py``
(list-item taxonomy), ``ingest.py`` (calendar / messaging / artifact ingest
events), ``crm.py``, ``domain.py``, ``governance.py``. The
``ha.state_changed`` namespace is HA-specific (a single canonical Cat-C
adapter today), while ``action.executed`` / ``action.failed`` are the
generic Cat-E execution-outcome taxonomy reused by every future Cat-E
adapter (smart-home, calendar-write, messaging-send, etc.).

Three event types register here at v1:

- ``ha.state_changed`` — emitted by the Home Assistant Cat-C inbound poll
  on each transition (first observation OR ``last_changed`` differs from
  the cached value). One emission per (entity_id, last_changed) pair.
- ``action.executed`` — emitted by any Cat-E adapter after a successful
  side-effecting call to the external system. ``external_id`` carries the
  remote correlation token if the target system returned one; ``None``
  otherwise (HA's ``POST /api/services`` returns only changed-state dicts,
  not a correlation id).
- ``action.failed`` — emitted by any Cat-E adapter when a side-effecting
  call raises. ``severity`` classifies the failure for the supervisor's
  backoff policy: ``fatal`` (no retry — auth, malformed request) vs
  ``transient`` (retry — 429, 5xx, network). ``error_kind`` is the
  classifier's discriminant (``auth_failed`` / ``rate_limited`` /
  ``network`` / etc.) and is intentionally an open string field so future
  Cat-E adapters can introduce family-specific classifications without
  altering the schema.

The observation-suppressed path emits NEITHER ``action.executed`` NOR
``action.failed`` per F-3: ``adminme.lib.observation.outbound()`` itself
emits ``observation.suppressed@v1`` for the audit trail, and the adapter
short-circuits to ``ActionResult(executed=False, suppressed=True)`` without
appending either of the two schemas registered here. The c3 framework
tests pin this contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class HaStateChangedV1(BaseModel):
    model_config = {"extra": "forbid"}

    entity_id: str = Field(min_length=1)
    state: str = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)
    last_changed: datetime
    last_updated: datetime
    source: Literal["home_assistant"]


class ActionExecutedV1(BaseModel):
    model_config = {"extra": "forbid"}

    action_kind: str = Field(min_length=1)
    target_external_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    external_id: str | None = None
    executed_at: datetime
    correlation_id: str | None = None


class ActionFailedV1(BaseModel):
    model_config = {"extra": "forbid"}

    action_kind: str = Field(min_length=1)
    target_external_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    error_message: str = Field(min_length=1, max_length=2000)
    severity: Literal["fatal", "transient"]
    error_kind: str = Field(min_length=1)
    failed_at: datetime
    correlation_id: str | None = None


registry.register("ha.state_changed", 1, HaStateChangedV1)
registry.register("action.executed", 1, ActionExecutedV1)
registry.register("action.failed", 1, ActionFailedV1)
