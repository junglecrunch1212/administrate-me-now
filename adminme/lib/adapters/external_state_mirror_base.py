"""
Cat-B: :class:`ExternalStateMirrorAdapter` — bidirectional state-mirror ABC.

Per ``docs/05-architecture-amendment-personal-data-layer.md`` §1.2 (Cat-B
contract), §2.3 (asymmetric per-operation ``write_capabilities`` per
[UT-27]), §2.5 (``sharing_model_discriminator`` on Cat-B projections),
§3.2 (5-ABC pattern), [D19], [PM-34], and SYSTEM_INVARIANTS.md §7.3 / [D17]
(adapters NEVER write projections directly — they emit events; the L3
projector reduces events into projection rows).

Cat-B is the most complex of the five categories because it ships BOTH
inbound state mirroring (the ``_on_external_change`` hook) AND outbound
writes (the :meth:`apply_write` gate). The write side is asymmetric per
[UT-27]: the pack declares a per-operation ``write_capabilities`` list
(e.g. ``["create_list", "delete_item", "toggle_completion"]``) and the
base class gates :meth:`apply_write` on operation membership before
invoking the subclass's per-operation implementation. A Cat-B adapter
that declares ``write_capabilities=[]`` is read-only and is rejected by
the validator at install time (the validator runs in 11-β; this ABC
provides the empty-list discriminator field).

Cat-B's ``sharing_model_discriminator`` names the row-level column on the
projection that distinguishes per-member vs shared rows (e.g. ``owner_id``
on the ``reminder_lists`` projection per memo 05 §2.5). The discriminator
is declared on the adapter pack manifest AND surfaced via the class
attribute so the validator can check coherence.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from adminme.adapters.base import (
    AdapterContext,
    AdapterStatus,
    SensitivityFloor,
)


class WriteCapabilityError(RuntimeError):
    """Raised by :meth:`ExternalStateMirrorAdapter.apply_write` when the
    requested operation kind is not declared in :attr:`write_capabilities`.

    Per [UT-27] the write surface is asymmetric — declaring Cat-B does
    NOT imply every operation is permitted; each per-operation capability
    must be explicitly listed. A pack that wants ``create_list`` but not
    ``delete_list`` declares the first and omits the second; this error
    flags any code path that tries to invoke an undeclared operation.
    """


@dataclass(frozen=True)
class WriteOperation:
    """A write request handed to
    :meth:`ExternalStateMirrorAdapter.apply_write`.

    ``kind`` is the per-operation capability string (e.g.
    ``"create_list"``). It must be a member of the adapter's
    :attr:`write_capabilities` list or the base class raises
    :class:`WriteCapabilityError` before invoking the subclass.
    """

    kind: str
    target_external_id: str | None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WriteResult:
    """Result of an :meth:`ExternalStateMirrorAdapter.apply_write` call."""

    applied: bool
    external_id: str | None = None
    suppressed: bool = False


@dataclass(frozen=True)
class ChangeEnvelope:
    """Envelope handed to
    :meth:`ExternalStateMirrorAdapter._on_external_change`.

    Constructed by the subclass's polling loop / webhook handler per
    inbound state-change event from the external system. Carries the
    change ``kind`` (e.g. ``"item_completed"``, ``"list_renamed"``) and
    the family-specific payload.
    """

    kind: str
    external_id: str
    received_at: str
    payload: dict[str, Any]


class ExternalStateMirrorAdapter(ABC):
    """Cat-B base class for bidirectional external-state-mirror adapters.

    Subclasses (Apple Reminders via EventKit bridge, Google Tasks, Google
    Calendar, CalDAV, etc.) declare a category-specific family and at
    least one Cat-B entry in :attr:`capabilities`. Cohort C retrofit
    prompts will rebase the shipped Apple Reminders adapter onto this
    base class.

    Required class attributes (in addition to the simple Adapter
    Protocol's ``name`` / ``version`` / ``sensitivity_floor``):

    - :attr:`family` — one of ``"reminders" | "calendaring" | "contacts"``.
    - :attr:`capabilities` — list of capability blocks; ≥1 Cat-B entry.
    - :attr:`write_capabilities` — per-operation strings; the
      :meth:`apply_write` gate checks membership before dispatch.
    - :attr:`sharing_model_discriminator` — column name on the Cat-B
      projection that distinguishes per-member vs shared rows.

    Required abstract methods: :meth:`start`, :meth:`stop`,
    :meth:`status`, :meth:`_apply_write_impl`, :meth:`_on_external_change`.
    """

    name: ClassVar[str]
    family: ClassVar[Literal["reminders", "calendaring", "contacts"]]
    version: ClassVar[str]
    sensitivity_floor: ClassVar[SensitivityFloor] = "normal"

    capabilities: ClassVar[list[Any]] = []
    write_capabilities: ClassVar[list[str]] = []
    sharing_model_discriminator: ClassVar[str]

    @abstractmethod
    async def start(self, *, ctx: AdapterContext) -> None:
        """Authenticate, register webhooks / start polling loop."""

    @abstractmethod
    async def stop(self) -> None:
        """Release external resources, deregister webhooks."""

    @abstractmethod
    def status(self) -> AdapterStatus:
        """Return the current adapter lifecycle snapshot."""

    async def apply_write(self, operation: WriteOperation) -> WriteResult:
        """Apply a write to the external system.

        Concrete on the base class so the per-operation capability check
        is centralized: if ``operation.kind`` is not declared in
        :attr:`write_capabilities`, the call raises
        :class:`WriteCapabilityError` BEFORE the external API is touched.
        Subclasses provide the actual external call via
        :meth:`_apply_write_impl`, which the base method invokes after
        the gate passes.

        Subclasses' :meth:`_apply_write_impl` MUST route the external
        side effect through :func:`adminme.lib.observation.outbound` so
        observation mode suppresses the write when active per §6.20.
        """
        if operation.kind not in self.write_capabilities:
            raise WriteCapabilityError(
                f"operation '{operation.kind}' not declared in "
                f"{type(self).__name__}.write_capabilities={self.write_capabilities!r}"
            )
        return await self._apply_write_impl(operation)

    @abstractmethod
    async def _apply_write_impl(
        self, operation: WriteOperation
    ) -> WriteResult:
        """Per-operation write implementation. Invoked by
        :meth:`apply_write` only after the per-operation capability gate
        passes. Subclasses MUST route the actual external API call
        through :func:`adminme.lib.observation.outbound`."""

    @abstractmethod
    async def _on_external_change(self, envelope: ChangeEnvelope) -> None:
        """Hook invoked by the polling / webhook handler per inbound
        change. Subclass typically emits the family-specific synced
        event (e.g. ``reminder.list_item.synced``) here."""
