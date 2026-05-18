"""
Central :3337 bridge FastAPI app — uvicorn entrypoint.

Per memo 04 §2.2: binds ``127.0.0.1:3337`` (operator-side ``tailscale
serve`` terminates TLS in front of this loopback socket — [§9 invariant
1] "Python product APIs bind loopback"). [§7.3, D17 corollary 1] this
service writes to the event log only, never to projection SQLite.

Production wiring lives in bootstrap §10 (16-§10) — it sets
``app.state.event_log`` + ``app.state.member_id_resolver`` before the
lifespan opens. Tests construct an app via ``create_app()`` and pin
state directly. With no wiring, ingest endpoints return 503
(``event_log_unavailable`` / ``member_id_resolver_unavailable``) — loud,
operator-facing, not silent.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

import uvicorn
from fastapi import FastAPI

from adminme.events.log import EventLog
from adminme.lib.instance_config import InstanceConfig
from adminme.products.bridge.auth import MemberIdResolver
from adminme.products.bridge.routers import ingest_router

logger = logging.getLogger(__name__)

EventLogFactory = Callable[[InstanceConfig], EventLog]
ResolverFactory = Callable[[InstanceConfig], MemberIdResolver]


def create_app(
    *,
    config: InstanceConfig | None = None,
    event_log_factory: EventLogFactory | None = None,
    resolver_factory: ResolverFactory | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[dict[str, Any]]:
        if config is not None and event_log_factory is not None and not getattr(
            app.state, "event_log", None
        ):
            app.state.event_log = event_log_factory(config)
        if config is not None and resolver_factory is not None and not getattr(
            app.state, "member_id_resolver", None
        ):
            app.state.member_id_resolver = resolver_factory(config)
        if not hasattr(app.state, "ingest_count"):
            app.state.ingest_count = 0
        try:
            yield {}
        finally:
            log = getattr(app.state, "event_log", None)
            close = getattr(log, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:  # noqa: BLE001 — best-effort teardown
                    logger.exception("bridge: event_log.close() raised")

    app = FastAPI(
        title="AdministrateMe Bridge Ingest",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(ingest_router)
    return app


app = create_app()


def main() -> None:  # pragma: no cover — entrypoint shape only
    """Uvicorn entrypoint per memo 04 §2.2. Binds 127.0.0.1:3337 only."""
    uvicorn.run(
        "adminme.products.bridge.app:app",
        host="127.0.0.1",
        port=3337,
        log_level="info",
    )


__all__ = ["app", "create_app", "main", "EventLogFactory", "ResolverFactory"]
