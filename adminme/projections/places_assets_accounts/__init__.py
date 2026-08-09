"""
places_assets_accounts projection — three linked entity families (places,
assets, accounts) with association tables (place_associations, asset_owners).

Per ADMINISTRATEME_BUILD.md §3.8 and SYSTEM_INVARIANTS.md §2, §12.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adminme.projections.base import Projection
from adminme.projections.places_assets_accounts import handlers


class PlacesAssetsAccountsProjection(Projection):
    name = "places_assets_accounts"
    version = 2
    subscribes_to = [
        "place.added",
        "place.updated",
        "place.archived",
        "place.restored",
        "asset.added",
        "asset.updated",
        "asset.archived",
        "asset.restored",
        "account.added",
        "account.updated",
        "account.archived",
        "account.restored",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    @staticmethod
    def _add_lifecycle_columns(conn: Any) -> None:
        for table in ("places", "assets", "accounts"):
            existing = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if not existing:
                continue
            for column, sql_type in {
                "lifecycle_status": "TEXT NOT NULL DEFAULT 'active'",
                "archived_at": "TEXT",
                "archived_by": "TEXT",
                "archive_reason": "TEXT",
            }.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")

    def on_connection_opened(self, conn: Any) -> None:
        self._add_lifecycle_columns(conn)

    def migrate_schema(self, conn: Any) -> None:
        self._add_lifecycle_columns(conn)

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)


__all__ = ["PlacesAssetsAccountsProjection"]
