"""
Pipeline protocol + context types.

Per [BUILD.md L4] (lines 1107-1265) and [§7]. A pipeline is a pure
consumer-emitter: it reads events, may call run_skill() for LLM work
through OpenClaw [ADR-0002], and emits derived events back into the log.
It NEVER writes a projection row directly [§7.3] and NEVER imports a
provider SDK [§7.4, §8, D6].

Pack shape (per [BUILD.md L4] lines 1259-1262 and [REFERENCE_EXAMPLES.md
§3]):

    <pack_root>/
      pipeline.yaml      # manifest (id, version, runtime, triggers, ...)
      handler.py         # exposes the class named in runtime.class
      tests/             # fixture-driven tests (optional)

The class implements ``Pipeline``: four required attributes plus an async
``handle(event, ctx)``. The runner constructs a ``PipelineContext`` per
dispatched event and calls ``handle()``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, runtime_checkable

if TYPE_CHECKING:
    from adminme.events.log import EventLog
    from adminme.lib.governance import GuardedWrite
    from adminme.lib.observation import ObservationManager
    from adminme.lib.session import Session
    from adminme.lib.skill_runner import SkillContext, SkillResult


class PipelinePackLoadError(RuntimeError):
    """Raised when a pipeline pack cannot be loaded — malformed yaml,
    missing required files, missing entrypoint, or runtime.class not
    importable / not implementing the Pipeline protocol."""


class Triggers(TypedDict, total=False):
    """Trigger declaration in ``pipeline.yaml``. Reactive packs set
    ``events``; proactive packs set ``schedule`` or ``proactive`` and are
    OUT of scope for the in-process runner [arch §5, cheatsheet Q3]."""

    events: list[str] | None
    schedule: str | None
    proactive: bool | None


@dataclass(frozen=True)
class PipelineContext:
    """Per-dispatch context handed to ``Pipeline.handle()``.

    Carries the seams a pipeline needs to do its work without constructing
    them itself: an internal Session [§6], the shared event log, a
    ``run_skill_fn`` callable bound to ``run_skill`` [ADR-0002], the
    outbound side-effect wrapper [§6.14], and a GuardedWrite [§6.5-6.8].

    ``triggering_event_id`` carries the bus-delivered event's id so child
    pipelines can wire ``causation_id`` on emitted derivative events
    (carry-forward from 09a build_log §"Carry-forward for prompt 10a").
    """

    session: "Session"
    event_log: "EventLog"
    run_skill_fn: Callable[..., Awaitable["SkillResult"]]
    outbound_fn: Callable[..., Awaitable[Any]]
    guarded_write: "GuardedWrite | None"
    observation_manager: "ObservationManager | None"
    triggering_event_id: str
    correlation_id: str | None


@runtime_checkable
class Pipeline(Protocol):
    """Pipeline protocol. The class declared in ``pipeline.yaml`` under
    ``runtime.class`` must implement this surface."""

    pack_id: str
    version: str
    triggers: Triggers

    async def handle(self, event: dict[str, Any], ctx: PipelineContext) -> None: ...


__all__ = [
    "Pipeline",
    "PipelineContext",
    "PipelinePackLoadError",
    "Triggers",
]
