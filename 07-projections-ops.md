# Prompt 07: Projections ops (money, places_assets_accounts, vector_search, xlsx)

**Phase:** BUILD.md PHASE 2 continued; xlsx spec deep dive.
**Depends on:** Prompt 06.
**Estimated duration:** 4-5 hours. XLSX alone is ~2 hours of this.
**Stop condition:** All four projections work; xlsx round-trip completes (forward → human edit → reverse emits events); all previous tests pass.

## Read first

1. `ADMINISTRATEME_BUILD.md`:
   - **"3.8 places_assets_accounts"**, **"3.9 money"**, **"3.10 vector_search"**, **"3.11 xlsx_workbooks"** — the last one is long (~300 lines); READ IT IN FULL.
2. `ADMINISTRATEME_DIAGRAMS.md` §6 — xlsx round-trip diagram.

## Objective

Four more projections, including the architecturally-distinctive xlsx bidirectional one.

## Deliverables

- `platform/projections/places_assets_accounts/` — schema + handlers + queries. Consumes `place.added`, `asset.added`, `account.added` and `_updated` variants.
- `platform/projections/money/` — consumes `money_flow.plaid_synced`, `money_flow.manually_added`, `money_flow.manually_deleted`, `account.plaid_linked`, `account.plaid_unlinked`, `plaid.sync.completed`, `plaid.institution.healthy/unhealthy`.
- `platform/projections/vector_search/` — uses `sqlite-vec`. Consumes events that carry embeddable text: `messaging.received`, `artifact.received`, `interaction.summarized`. Query: `search(query_text, k=10, filter_by_party_ids?, sensitivity_cap?)`.
- `platform/projections/xlsx_workbooks/` — the bidirectional projection. See BUILD.md §3.11 for full spec.

### xlsx specifically

Two daemons:
- `platform/daemons/xlsx_forward.py` — subscribes to bus; debounces 5s; regenerates affected sheets via openpyxl; cell-protection enforcement; sidecar state JSON.
- `platform/daemons/xlsx_reverse.py` — watchdog on both xlsx files; 2s wait; diff vs. sidecar; emit events via standard path.

Two workbooks:
- `adminme-ops.xlsx` — Tasks, Recurrences, Commitments, People, Lists, Members, Metadata
- `adminme-finance.xlsx` — Raw Data, Accounts, Assumptions, Dashboard, Balance Sheet, 5-Year Pro Forma, Budget vs Actual

Follow BUILD.md §3.11 exactly — every sheet's columns, protection rules, reverse-projection diff algorithm.

Tests — per BUILD.md §3.11's "Testing" subsection, implement at least:
- `test_forward_tasks_roundtrip.py`
- `test_reverse_new_task.py`
- `test_reverse_protected_cell_ignored.py`
- `test_lock_contention.py`
- `test_plaid_row_protection.py`
- `test_replay_equivalence.py`
- `test_assumption_pro_forma_math.py`

## Verification

```bash
poetry run pytest tests/unit/test_projection_*.py tests/integration/ -v
# xlsx-specific:
poetry run pytest platform/projections/xlsx_workbooks/tests/ -v
# End-to-end xlsx smoke:
poetry run python scripts/demo_xlsx_roundtrip.py
# script should: seed events → forward regen → observe file → edit file programmatically → reverse detect → event emitted → forward regen → verify roundtrip
git commit -m "phase 07: ops projections including xlsx bidirectional"
```

## Stop

**Explicit stop message:**

> All 11 projections in. Event log → projections path is complete. xlsx round-trip works. Ready for prompt 08 (session + scope + governance + observation mode).
