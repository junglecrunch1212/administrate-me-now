"""
money projection — the transaction ledger.

Per ADMINISTRATEME_BUILD.md §3.9 and SYSTEM_INVARIANTS.md §2, §12.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from adminme.finance.worker import MoneyCalculation

from adminme.projections.base import Projection
from adminme.projections.money import handlers


class MoneyProjection(Projection):
    name = "money"
    version = 7
    subscribes_to = [
        "money_flow.recorded",
        "money_flow.manually_added",
        "money_flow.manually_deleted",
        "money_flow.provider_observed",
        "money_flow.provider_removed",
        "financial.account_observed",
        "money_flow.classification_set",
        "money_flow.manually_updated",
        "finance.assumption_set",
        "finance.budget_set",
        "finance.scenario_set",
        "finance.goal_set",
        "finance.goal_paused",
        "finance.goal_completed",
        "finance.goal_reopened",
        "finance.workbook_published",
        "finance.provenance_archived",
        "finance.publication_accepted",
        "finance.projection_verified",
        "finance.goal_recorded",
        "finance.preference_recorded",
    ]
    schema_path = Path(__file__).parent / "schema.sql"

    def __init__(self) -> None:
        self._calculation: MoneyCalculation | None = None

    def on_connection_opened(self, conn: Any) -> None:
        # EventLog and Money together target 1 MiB of cached pages, leaving
        # room for other retained state. SQLite may spill dirty pages normally.
        conn.execute("PRAGMA cache_size=-512").close()

    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        if "_payload_json" not in envelope:
            self.finish_delivery()
            handlers.apply_event(envelope, conn)
            return
        from adminme.finance.worker import MoneyCalculation

        scope = (
            envelope["tenant_id"],
            envelope["owner_scope"].removeprefix("private:"),
            envelope["source_account_id"],
        )
        if self._calculation is not None and (
            self._calculation.scope != scope or self._calculation.count >= 131
        ):
            self.finish_delivery()
        if self._calculation is None:
            self._calculation = MoneyCalculation(scope)
        try:
            self._calculation.apply(envelope, conn)
        except BaseException:
            calculation, self._calculation = self._calculation, None
            calculation.close(failed=True)
            raise

    def finish_delivery(self):
        calculation, self._calculation = self._calculation, None
        if calculation is not None:
            calculation.close()

    def after_replay(self, conn, *, succeeded):
        self.finish_delivery()

    def migrate_schema(self, conn: Any) -> None:
        additions = {
            "memo": "TEXT",
            "member_id": "TEXT",
            "merchant_name": "TEXT",
            "merchant_category": "TEXT",
            "provider_category": "TEXT",
            "assigned_category": "TEXT",
            "provider_transaction_id": "TEXT",
            "provider_item_id": "TEXT",
            "pending": "INTEGER NOT NULL DEFAULT 0",
            "included": "INTEGER NOT NULL DEFAULT 1",
            "provider_revision": "TEXT",
            "updated_at": "TEXT",
            "removed_at": "TEXT",
        }
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(money_flows)")
        }
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE money_flows ADD COLUMN {name} {declaration}")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_money_flows_provider_transaction "
            "ON money_flows(tenant_id, source_adapter, provider_transaction_id) "
            "WHERE provider_transaction_id IS NOT NULL"
        )
        goal_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(finance_goals)")
        }
        for name, declaration in {
            "lifecycle_at": "TEXT",
            "lifecycle_actor": "TEXT",
            "lifecycle_reason": "TEXT",
        }.items():
            if name not in goal_columns:
                conn.execute(
                    f"ALTER TABLE finance_goals ADD COLUMN {name} {declaration}"
                )
        # Historical rows lack the envelope's authority. Deny them until
        # replay restores their actual scopes; a publisher ID is not a grant.
        workbook_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(finance_workbook_state)")
        }
        for name, declaration in {
            "owner_scope": "TEXT NOT NULL DEFAULT ''",
            "visibility_scope": "TEXT NOT NULL DEFAULT ''",
            "sensitivity": "TEXT NOT NULL DEFAULT 'privileged'",
        }.items():
            if name not in workbook_columns:
                conn.execute(
                    f"ALTER TABLE finance_workbook_state ADD COLUMN {name} {declaration}"
                )


__all__ = ["MoneyProjection"]
