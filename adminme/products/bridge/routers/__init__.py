"""Bridge ingest routers — currently only /api/bridge/* lives here."""

from adminme.products.bridge.routers.ingest import router as ingest_router

__all__ = ["ingest_router"]
