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
    version = 1
    subscribes_to = [
        "place.added",
        "place.updated",
        "asset.added",
        "asset.updated",
        "account.added",
        "account.updated",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        handlers.apply_event(envelope, conn)


__all__ = ["PlacesAssetsAccountsProjection"]
