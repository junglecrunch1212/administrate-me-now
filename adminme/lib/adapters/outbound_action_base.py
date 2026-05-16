"""
Cat-E: :class:`OutboundActionAdapter` — outbound-action adapter ABC.

Per ``docs/05-architecture-amendment-personal-data-layer.md`` §1.2 (Cat-E
contract), §3.2 (5-ABC pattern), [D19] (five-category taxonomy), [PM-34]
(5 ABCs at ``adminme/lib/adapters/``), and SYSTEM_INVARIANTS.md §6.20
(observation mode wraps the final outbound filter).

Cat-E adapters consume ``action.requested`` events from the L2 event log
and execute external side effects: turn on a Home Assistant scene,
dispatch a Twilio SMS, kick a webhook. They do NOT mirror external state
(that is Cat-B) and they do NOT ingest data (Cat-C). The bidirectional
shape of Cat-A messaging is also not Cat-E — Cat-A inbound and outbound
share a single channel; Cat-E is one-way external dispatch.

The Cat-E contract pins :attr:`observation_mode_required` to ``True`` by
default: an action adapter without observation suppression cannot run in
observation mode safely. The :meth:`execute_action` implementation MUST
route through :func:`adminme.lib.observation.outbound` so the toggle
suppresses the external side effect when active per §6.20. The 11-β
validator verifies the wiring; this ABC declares the contract.
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


@dataclass(frozen=True)
class ActionRequest:
    """Request handed to :meth:`OutboundActionAdapter.execute_action`.

    Constructed by the subclass's ``action.requested`` consumer per
    inbound event. ``kind`` distinguishes between e.g.
    ``"home_assistant.scene"`` and ``"telephony.sms"``; ``payload``
    carries the family-specific parameters. ``correlation_id``
    propagates through to the eventual ``external.sent`` /
    ``observation.suppressed`` event per [D8].
    """

    kind: str
    target_external_id: str | None
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None


@dataclass(frozen=True)
class ActionResult:
    """Result of an :meth:`OutboundActionAdapter.execute_action` call.

    ``executed=True`` means the external API accepted the action (200 or
    family-equivalent). ``suppressed=True`` means observation mode
    short-circuited the call before it reached the external system per
    §6.20.
    """

    executed: bool
    external_id: str | None = None
    suppressed: bool = False


class OutboundActionAdapter(ABC):
    """Cat-E base class for outbound-action adapters.

    Subclasses (Home Assistant scene/automation dispatcher, Twilio SMS
    sender, Apple HomeKit bridge, generic webhook firer) consume
    ``action.requested`` events and execute external side effects.
    Cohort D's 11g ships the first canonical Cat-E adapter (Home
    Assistant) built against this base class.

    Required class attributes (in addition to the simple Adapter
    Protocol's ``name`` / ``version`` / ``sensitivity_floor``):

    - :attr:`family` — one of ``"home_automation" | "telephony" |
      "external_action"``.
    - :attr:`capabilities` — list of capability blocks; ≥1 Cat-E entry.
    - :attr:`observation_mode_required` — defaults to ``True``. A Cat-E
      pack cannot opt out of observation suppression; the validator
      rejects any manifest setting this to ``False``.

    Required abstract methods: :meth:`start`, :meth:`stop`,
    :meth:`status`, :meth:`execute_action`.
    """

    name: ClassVar[str]
    family: ClassVar[Literal["home_automation", "telephony", "external_action"]]
    version: ClassVar[str]
    sensitivity_floor: ClassVar[SensitivityFloor] = "normal"

    capabilities: ClassVar[list[Any]] = []

    observation_mode_required: ClassVar[bool] = True
    """Cat-E adapters MUST route :meth:`execute_action` through
    :func:`adminme.lib.observation.outbound`. The default is ``True``
    and the 11-β validator rejects any Cat-E pack manifest that sets it
    to ``False``."""

    @abstractmethod
    async def start(self, *, ctx: AdapterContext) -> None:
        """Authenticate against the external system, emit
        ``adapter.authenticated``, subscribe to the
        ``action.requested`` event stream the L2 routes to this
        adapter."""

    @abstractmethod
    async def stop(self) -> None:
        """Release external resources, drain in-flight actions."""

    @abstractmethod
    def status(self) -> AdapterStatus:
        """Return the current adapter lifecycle snapshot."""

    @abstractmethod
    async def execute_action(self, request: ActionRequest) -> ActionResult:
        """Execute an outbound action.

        Subclasses MUST route the actual external API call through
        :func:`adminme.lib.observation.outbound` so observation mode
        suppresses the side effect when active per §6.20. The
        install-time validator (11-β) verifies this wiring; this ABC
        only declares the contract.
        """
