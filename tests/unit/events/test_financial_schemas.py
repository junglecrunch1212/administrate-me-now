"""``money_flow.plaid_synced`` schema-registration canaries.

Asserts the financial-family event type added in prompt 11b commit 1
registers at v1 per [D7], the Pydantic model enforces the
required-field contracts from the prompt §7 Deliverables table, count
fields reject negatives per the SUMMARY-event invariant, and
``extra="forbid"`` rejects unknown payload keys — matching the shape
established by ``test_adapter_schemas.py`` (11a c1) and
``test_10c_iii_schemas.py``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adminme.events.registry import ensure_autoloaded, registry
from adminme.events.schemas.financial import MoneyFlowPlaidSyncedV1


# ---------------------------------------------------------------------------
# money_flow.plaid_synced v1
# ---------------------------------------------------------------------------


def test_money_flow_plaid_synced_registered_at_v1() -> None:
    ensure_autoloaded()
    assert registry.get("money_flow.plaid_synced", 1) is MoneyFlowPlaidSyncedV1
    assert registry.latest_version("money_flow.plaid_synced") == 1


def test_money_flow_plaid_synced_minimum_payload_validates() -> None:
    instance = MoneyFlowPlaidSyncedV1(
        member_id="member-001",
        plaid_item_id="item-sandbox-abc",
        institution_id="ins_109508",
        institution_name="First Platypus Bank",
        sync_cursor="cursor-page-7",
        transactions_added=4,
        transactions_modified=1,
        transactions_removed=0,
        accounts_modified_count=2,
        synced_at="2026-05-16T12:00:00Z",
        webhook_triggered=True,
    )
    assert instance.transactions_added == 4
    assert instance.webhook_triggered is True
    assert instance.sync_cursor == "cursor-page-7"


def test_money_flow_plaid_synced_zero_counts_valid() -> None:
    """A backstop poll that found no changes still emits one event for
    the cursor-advance milestone — counts may all be zero."""
    instance = MoneyFlowPlaidSyncedV1(
        member_id="member-001",
        plaid_item_id="item-sandbox-abc",
        institution_id="ins_109508",
        institution_name="First Platypus Bank",
        sync_cursor="cursor-page-7",
        transactions_added=0,
        transactions_modified=0,
        transactions_removed=0,
        accounts_modified_count=0,
        synced_at="2026-05-16T12:00:00Z",
        webhook_triggered=False,
    )
    assert instance.transactions_added == 0
    assert instance.webhook_triggered is False


@pytest.mark.parametrize(
    "field_name",
    [
        "transactions_added",
        "transactions_modified",
        "transactions_removed",
        "accounts_modified_count",
    ],
)
def test_money_flow_plaid_synced_rejects_negative_counts(field_name: str) -> None:
    base: dict[str, object] = dict(
        member_id="member-001",
        plaid_item_id="item-sandbox-abc",
        institution_id="ins_109508",
        institution_name="First Platypus Bank",
        sync_cursor="cursor-page-7",
        transactions_added=0,
        transactions_modified=0,
        transactions_removed=0,
        accounts_modified_count=0,
        synced_at="2026-05-16T12:00:00Z",
        webhook_triggered=False,
    )
    base[field_name] = -1
    with pytest.raises(ValidationError):
        MoneyFlowPlaidSyncedV1(**base)  # type: ignore[arg-type]


def test_money_flow_plaid_synced_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        MoneyFlowPlaidSyncedV1(
            member_id="member-001",
            plaid_item_id="item-sandbox-abc",
            institution_id="ins_109508",
            institution_name="First Platypus Bank",
            sync_cursor="cursor-page-7",
            transactions_added=0,
            transactions_modified=0,
            transactions_removed=0,
            accounts_modified_count=0,
            synced_at="2026-05-16T12:00:00Z",
            webhook_triggered=False,
            raw_transaction_blob="leaked",  # type: ignore[call-arg]
        )


def test_money_flow_plaid_synced_rejects_empty_required_strings() -> None:
    """Required strings have ``min_length=1`` — empty strings must be
    rejected to keep downstream consumers from receiving zero-info
    identifiers."""
    with pytest.raises(ValidationError):
        MoneyFlowPlaidSyncedV1(
            member_id="",
            plaid_item_id="item-sandbox-abc",
            institution_id="ins_109508",
            institution_name="First Platypus Bank",
            sync_cursor="cursor-page-7",
            transactions_added=0,
            transactions_modified=0,
            transactions_removed=0,
            accounts_modified_count=0,
            synced_at="2026-05-16T12:00:00Z",
            webhook_triggered=False,
        )
