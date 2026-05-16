"""
Cat-C: :class:`InboundDataAdapter` — read-only ingestion adapter ABC.

Per ``docs/05-architecture-amendment-personal-data-layer.md`` §1.2 (Cat-C
contract), §3.2 (5-ABC pattern), [D19], [PM-34].

Cat-C is the **simplest** category by design — it is the discriminator
from Cat-B. An adapter that writes to the external system (even one
operation) is Cat-B or Cat-E, never Cat-C. Subclasses (Plaid, IoT
ingestion shims, telemetry collectors, etc.) declare a category family
in ``"financial" | "iot" | "telemetry"`` and at least one Cat-C entry in
:attr:`capabilities`.

Because Cat-C is read-only, the ABC deliberately has **no write surface**
— no :meth:`apply_write`, no :meth:`send`. The validator (11-β) enforces
this by rejecting any Cat-C pack manifest that declares
``write_capabilities`` per [UT-27]; this ABC surfaces the same rule at
the class-attribute level (no ``write_capabilities`` ClassVar exists on
Cat-C).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Literal

from adminme.adapters.base import (
    AdapterContext,
    AdapterStatus,
    SensitivityFloor,
)


class InboundDataAdapter(ABC):
    """Cat-C base class for read-only ingestion adapters.

    Subclasses (Plaid, IoT shims, telemetry collectors) ingest from the
    external system and emit typed events via the L2 event log; they
    NEVER write back. Cohort C retrofit prompts will rebase the shipped
    Plaid adapter (``packs/adapters/financial/plaid/adapter.py``, PR #105
    series) onto this base class.

    Required class attributes (in addition to the simple Adapter
    Protocol's ``name`` / ``version`` / ``sensitivity_floor``):

    - :attr:`family` — one of ``"financial" | "iot" | "telemetry"``.
    - :attr:`capabilities` — list of capability blocks with ≥1 Cat-C
      entry.

    There is intentionally NO :attr:`write_capabilities`. A pack manifest
    that declares ``write_capabilities`` for a Cat-C capability is
    rejected by the 11-β validator; surfacing the absence at the
    class-attribute layer keeps the contract legible in code.

    Required abstract methods: :meth:`start`, :meth:`stop`,
    :meth:`status`. No write method.
    """

    name: ClassVar[str]
    family: ClassVar[Literal["financial", "iot", "telemetry"]]
    version: ClassVar[str]
    sensitivity_floor: ClassVar[SensitivityFloor] = "normal"

    capabilities: ClassVar[list[Any]] = []

    @abstractmethod
    async def start(self, *, ctx: AdapterContext) -> None:
        """Authenticate against the external system, emit
        ``adapter.authenticated``, begin the polling / streaming loop.
        Cat-C adapters typically emit family-specific synced events
        (e.g. ``money_flow.plaid_synced``) at milestone intervals plus
        ``adapter.cursor_advanced`` per the cross-cutting schemas."""

    @abstractmethod
    async def stop(self) -> None:
        """Release external resources, persist any cursor."""

    @abstractmethod
    def status(self) -> AdapterStatus:
        """Return the current adapter lifecycle snapshot."""
