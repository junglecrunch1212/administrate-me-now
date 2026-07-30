"""Bridge-router test shell.

Production includes this router in :mod:`adminme.runtime.application`; there is
no separately deployable central bridge service in v2.  ``create_app`` remains
only so focused router tests can supply an EventSink and identity resolver.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from adminme.products.bridge.routers import ingest_router


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not hasattr(app.state, "ingest_count"):
            app.state.ingest_count = 0
        yield

    app = FastAPI(
        title="AdministrateMe Bridge Router Test Shell",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.include_router(ingest_router)
    return app


app = create_app()


__all__ = ["app", "create_app"]
