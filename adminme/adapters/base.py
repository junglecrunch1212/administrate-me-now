"""
Adapter Protocol — the L1 contract every standalone adapter pack satisfies.

Per ADMINISTRATEME_BUILD.md §L1 and SYSTEM_INVARIANTS.md §1 (event log
append-only), §6 (observation mode + sensitivity floor invariant 10), §8
(no LLM SDKs in adminme/; channel transport is OpenClaw's). Plugin
messaging adapters (BlueBubbles, Telegram, Discord) live as OpenClaw
plugins per §8 invariant 3 and do NOT implement this Protocol.

Key rules adapters must observe:

- Adapters NEVER write projections [§7.3, D17], NEVER call pipelines, and
  NEVER compose outbound — channel transport is OpenClaw's territory.
- Adapters set ``correlation_id`` on entry if the inbound request lacks
  one [D8 addition 1] — the supervisor injects a factory via
  ``AdapterContext.correlation_id_factory`` for this purpose.
- Privileged adapters hardcode ``sensitivity_floor`` at the pack level;
  the config loader rejects any configuration that would lower it
  [§6 invariant 10].
- ``adapter.cursor_advanced`` emission is milestone-only (every 100
  events OR every 5 minutes per adapter, whichever comes first) — see
  the cross-cutting schemas in ``adminme/events/schemas/adapters.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from adminme.events.writer import EventSink


SensitivityFloor = Literal["normal", "privileged"]
"""Adapter-level sensitivity floor. ``privileged`` adapters cannot be
downgraded via configuration per [§6 invariant 10]."""


AdapterState = Literal["starting", "running", "backoff", "stopped", "crashed"]
"""Lifecycle state visible via ``Adapter.status()`` and the supervisor's
``/status`` endpoint. Mirrors the supervisor's per-adapter task state."""


@dataclass(frozen=True)
class AdapterContext:
    """Per-adapter dependency bag handed to ``Adapter.start()``.

    Frozen so adapters cannot mutate the context after start. Tests inject
    fakes for ``event_sink``, deterministic factories for
    ``correlation_id_factory``, and a frozen-clock ``now_factory`` to make
    cursor-milestone behavior testable without sleeps.
    """

    event_sink: "EventSink"
    config: dict[str, Any]
    correlation_id_factory: Callable[[], str]
    now_factory: Callable[[], datetime]


@dataclass
class AdapterStatus:
    """Snapshot of an adapter's lifecycle state.

    The supervisor mints one of these per registered adapter and updates
    it on state transitions; ``Adapter.status()`` returns the latest
    snapshot. ``last_error`` carries the most recent ``adapter.error``
    payload (or ``None`` if the adapter has never errored).
    """

    state: AdapterState = "starting"
    last_error: dict[str, Any] | None = None
    last_cursor_advance_at: datetime | None = None
    started_at: datetime | None = None
    restart_count: int = 0
    next_backoff_s: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Adapter(Protocol):
    """L1 adapter protocol implemented by every standalone adapter pack.

    Implementations live under ``packs/adapters/<family>/<name>/adapter.py``
    per the operator decision in ``docs/06-split-memo-11.md`` §4. Plugin
    messaging adapters (OpenClaw plugins) do NOT implement this Protocol;
    they implement OpenClaw's plugin interface and are out of scope here.

    Lifecycle contract:

    - ``start(ctx)`` is called once by the supervisor when the adapter is
      first scheduled or after a backoff window. Implementations:
        - Authenticate against the external system using a credential
          reference from ``ctx.config`` (NEVER the secret itself).
        - Emit ``adapter.authenticated`` v1 on success.
        - Begin the polling / streaming loop. Emit ``messaging.received``
          (or the family-specific ingest schema) per inbound event.
        - Emit ``adapter.cursor_advanced`` only at milestones, never per
          event.

    - ``stop()`` is called when the supervisor shuts the adapter down for
      any reason (process shutdown, config reload, restart-after-crash).
      Implementations must release external resources, persist the
      current cursor if applicable, and return promptly.

    - ``status()`` returns the current ``AdapterStatus`` snapshot. The
      supervisor's ``/status`` JSON endpoint aggregates these.

    Required class attributes:

    - ``name``: stable identifier (e.g. ``"messaging-gmail-api"``).
    - ``family``: ``"messaging" | "financial" | "reminders" | "calendaring" | ...``
    - ``version``: pack SemVer (matches ``pack.yaml``'s version).
    - ``sensitivity_floor``: ``"normal"`` for general adapters; ``"privileged"``
      for adapters that handle law-practice email, custody docs, financial
      institutions, etc. Cannot be lowered by configuration.
    """

    name: str
    family: str
    version: str
    sensitivity_floor: SensitivityFloor

    async def start(self, *, ctx: AdapterContext) -> None: ...

    async def stop(self) -> None: ...

    def status(self) -> AdapterStatus: ...
