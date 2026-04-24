"""
XlsxQueryContext — the cross-projection read handles the xlsx forward
daemon needs to build sheets.

The xlsx_workbooks projection is unusual among L3 projections in that it
reads across other projections' databases to build derived workbook state.
Other projections' ``apply()`` handlers touch only their own connection;
xlsx sheets (Tasks, Commitments, Raw Data, etc.) are joined views over
seven projections' worth of data.

Per DECISIONS.md §D4 (CRM/ops is a shared L3 concern): any Python caller
may read these projections' queries via a local connection. The runner
owns the connections; this context is a plain bundle of references passed
to the xlsx projection at construction time.

Only the projections whose queries are referenced by a currently-built
sheet appear here. ``vector_search`` and ``artifacts`` are not referenced
by any sheet this prompt ships, so they are absent; they will be added in
future prompts when sheets that need them land.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlcipher3


@dataclass
class XlsxQueryContext:
    parties_conn: sqlcipher3.Connection
    tasks_conn: sqlcipher3.Connection
    commitments_conn: sqlcipher3.Connection
    recurrences_conn: sqlcipher3.Connection
    calendars_conn: sqlcipher3.Connection
    places_assets_accounts_conn: sqlcipher3.Connection
    money_conn: sqlcipher3.Connection
