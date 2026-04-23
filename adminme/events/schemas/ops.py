"""
Ops spine schemas — places, assets, accounts, money flows, embeddings.

Per SYSTEM_INVARIANTS.md §2, §6, §8, §13 and ADMINISTRATEME_BUILD.md §§3.8-3.10.
Registered at v1 per DECISIONS.md §D7. These event types feed the
``places_assets_accounts``, ``money``, and ``vector_search`` projections
(prompt 07a).

Per [§8] AdministrateMe does not call embedding models directly; the
vector carried by ``embedding.generated`` is pre-computed by a separate
embedding daemon (future prompt) that calls OpenClaw's embedding endpoint.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


# ------------------------------------------------------------------
# places
# ------------------------------------------------------------------
class PlaceAddedV1(BaseModel):
    model_config = {"extra": "forbid"}
    place_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    kind: Literal[
        "home",
        "second_home",
        "office",
        "school",
        "medical",
        "gym",
        "church",
        "cemetery",
        "storage",
        "other",
    ]
    address_json: dict[str, Any]
    geo_lat: float | None = None
    geo_lon: float | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class PlaceUpdatedV1(BaseModel):
    model_config = {"extra": "forbid"}
    place_id: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    field_updates: dict[str, Any]


# ------------------------------------------------------------------
# assets
# ------------------------------------------------------------------
class AssetAddedV1(BaseModel):
    model_config = {"extra": "forbid"}
    asset_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    kind: Literal[
        "vehicle",
        "appliance",
        "instrument",
        "boat",
        "firearm",
        "pet",
        "other",
    ]
    linked_place: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class AssetUpdatedV1(BaseModel):
    model_config = {"extra": "forbid"}
    asset_id: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    field_updates: dict[str, Any]


# ------------------------------------------------------------------
# accounts
# ------------------------------------------------------------------
class AccountAddedV1(BaseModel):
    model_config = {"extra": "forbid"}
    account_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    organization_party_id: str = Field(min_length=1)
    kind: Literal[
        "utility",
        "subscription",
        "insurance",
        "license",
        "bank",
        "credit_card",
        "loan",
        "brokerage",
        "other",
    ]
    status: Literal["active", "dormant", "cancelled", "pending"] = "active"
    billing_rrule: str | None = None
    next_renewal: str | None = None
    login_vault_ref: str | None = None
    linked_asset: str | None = None
    linked_place: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class AccountUpdatedV1(BaseModel):
    model_config = {"extra": "forbid"}
    account_id: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    field_updates: dict[str, Any]


# ------------------------------------------------------------------
# money flows
# ------------------------------------------------------------------
class MoneyFlowRecordedV1(BaseModel):
    model_config = {"extra": "forbid"}
    flow_id: str = Field(min_length=1)
    from_party_id: str | None = None
    to_party_id: str | None = None
    amount_minor: int
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    occurred_at: str = Field(min_length=1)
    kind: Literal["paid", "received", "owed", "reimbursable"]
    category: str | None = None
    linked_artifact: str | None = None
    linked_account: str | None = None
    linked_interaction: str | None = None
    notes: str | None = None
    source_adapter: str = Field(min_length=1)


class MoneyFlowManuallyAddedV1(BaseModel):
    """Emitted by prompt 07c's xlsx reverse daemon when a principal adds a
    row in the Raw Data sheet with is_manual=TRUE. Separate event type from
    .recorded so downstream consumers can distinguish human-entered
    transactions from adapter-ingested ones."""

    model_config = {"extra": "forbid"}
    flow_id: str = Field(min_length=1)
    from_party_id: str | None = None
    to_party_id: str | None = None
    amount_minor: int
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    occurred_at: str = Field(min_length=1)
    kind: Literal["paid", "received", "owed", "reimbursable"]
    category: str | None = None
    notes: str | None = None
    added_by_party_id: str = Field(min_length=1)


class MoneyFlowManuallyDeletedV1(BaseModel):
    """Emitted by prompt 07c when a principal deletes a manual row.
    Plaid-sourced rows cannot be deleted via xlsx (prompt 07c enforces)."""

    model_config = {"extra": "forbid"}
    flow_id: str = Field(min_length=1)
    deleted_at: str = Field(min_length=1)
    deleted_by_party_id: str = Field(min_length=1)


# ------------------------------------------------------------------
# vector_search
# ------------------------------------------------------------------
class EmbeddingGeneratedV1(BaseModel):
    """Emitted by the embedding daemon (future prompt) after calling out
    to OpenClaw's embedding endpoint. The vector is pre-computed; the
    projection only stores, it does not embed. Per [§8], AdministrateMe
    itself does not import embedding SDKs."""

    model_config = {"extra": "forbid"}
    embedding_id: str = Field(min_length=1)
    linked_kind: Literal["interaction", "artifact", "party_notes"]
    linked_id: str = Field(min_length=1)
    embedding_dimensions: int = Field(ge=1)
    embedding: list[float]
    model_name: str = Field(min_length=1)
    sensitivity: Literal["normal", "sensitive", "privileged"]
    source_text_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


registry.register("place.added", 1, PlaceAddedV1)
registry.register("place.updated", 1, PlaceUpdatedV1)
registry.register("asset.added", 1, AssetAddedV1)
registry.register("asset.updated", 1, AssetUpdatedV1)
registry.register("account.added", 1, AccountAddedV1)
registry.register("account.updated", 1, AccountUpdatedV1)
registry.register("money_flow.recorded", 1, MoneyFlowRecordedV1)
registry.register("money_flow.manually_added", 1, MoneyFlowManuallyAddedV1)
registry.register("money_flow.manually_deleted", 1, MoneyFlowManuallyDeletedV1)
registry.register("embedding.generated", 1, EmbeddingGeneratedV1)
