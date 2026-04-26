"""Echo-logger fixture pipeline. Trivial implementation used by the
PipelineRunner unit + integration tests; NOT a production pipeline.

Class-level counter is a test convenience: tests construct a fresh
LoadedPipelinePack via load_pipeline_pack() (cached by pack_id+version,
so identity is stable within a process) and read EchoLoggerPipeline.count
to assert dispatch counts. Tests must call ``EchoLoggerPipeline.reset()``
before each run because the cache hands out the same instance.
"""

from __future__ import annotations

from typing import Any

from adminme.pipelines.base import PipelineContext, Triggers


class EchoLoggerPipeline:
    pack_id: str = "pipeline:echo_logger"
    version: str = "1.0.0"
    triggers: Triggers = {"events": ["messaging.received"]}

    count: int = 0
    last_event_id: str | None = None
    last_correlation_id: str | None = None

    def __init__(self) -> None:
        # Per-instance state mirrors the class-level counter so tests can
        # interrogate either; class-level is the convenience for shared
        # fixtures.
        pass

    @classmethod
    def reset(cls) -> None:
        cls.count = 0
        cls.last_event_id = None
        cls.last_correlation_id = None

    @classmethod
    def _count_for_test(cls) -> int:
        return cls.count

    async def handle(self, event: dict[str, Any], ctx: PipelineContext) -> None:
        type(self).count += 1
        type(self).last_event_id = event["event_id"]
        type(self).last_correlation_id = ctx.correlation_id
