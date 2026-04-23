"""
vector_search projection — semantic index over non-privileged content.

Per ADMINISTRATEME_BUILD.md §3.10 and SYSTEM_INVARIANTS.md §6.3, §8, §13.8.

Uses sqlite-vec's vec0 virtual table (with an ``embeddings_meta`` sidecar)
to answer nearest-neighbor queries over interaction summaries, artifact
extracted_text, and party notes. Vectors are pre-computed by a separate
embedding daemon (future prompt); this projection stores and queries only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sqlite_vec  # type: ignore[import-untyped]

from adminme.projections.base import Projection
from adminme.projections.vector_search import handlers


class VectorSearchProjection(Projection):
    name = "vector_search"
    version = 1
    subscribes_to = ["embedding.generated"]
    schema_path = Path(__file__).parent / "schema.sql"

    def on_connection_opened(self, conn: Any) -> None:
        """Load the sqlite-vec extension before schema.sql runs — vec0 is
        a virtual table defined by the extension."""
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)


__all__ = ["VectorSearchProjection"]
