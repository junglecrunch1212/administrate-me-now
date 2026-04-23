build_changelog.md not started until after Prompt 05
Prompt 05 Completed and Merged
Prompt 06 due for review and potential refactor
Permission for Claude Code Opus 4.7 Code Supervision Partner to take over this log now and keep up-to-date with details of each new prompt execution, merge, diff, future prompt refactors, and any other history important to build_changelog.md 

### Prompt 07a — ops spine projections (places_assets_accounts, money, vector_search)
- **Refactored**: by Partner in Claude Chat, pre-session. Prompt file: prompts/07a-projections-ops-spine.md (~600 lines, quality bar = 06).
- **Session merged**: PR #<N>, commits 81290b0 / edd0c34 / 71731fb / <commit4>, merged <merge date>.
- **Outcome**: IN FLIGHT (PR open).
- **Evidence**:
  - 3 projections: places_assets_accounts (3 entity tables + 2 association tables), money (1 table with is_manual + soft-delete), vector_search (vec0 virtual table + embeddings_meta sidecar).
  - 10 new event types registered at v1 per [D7] (place/asset/account × added/updated, money_flow × 3, embedding.generated).
  - 38 new unit tests (places_assets_accounts 15, money 13, vector_search 10) + integration rebuild extended to 10 projections + ~1200 events.
  - Runner gained `on_connection_opened` hook for projection-specific extension loading (vec0). Default no-op, backward compatible.
  - Privileged-filter at handler time on vector_search per [§13.8]. Envelope- or payload-level `sensitivity='privileged'` drops the write with INFO log.
  - CHECK constraints consistent on enum columns per CF-6 (places.kind, assets.kind, accounts.kind, accounts.status, money_flows.kind, sensitivity).
  - login_vault_ref CHECK accepts only `op://`, `1password://`, `vault://` — belt against broken adapter writes.
  - money soft-delete: `money_flow.manually_deleted` UPDATEs `deleted_at` rather than deleting the row. Rebuild correctness preserved. Queries exclude deleted.
  - BUILD_LOG updated as part of Commit 4 per new rule.
  - Ruff clean, mypy clean (95 source files), all inviolable greps OK.
- **Carry-forward for prompt 07b (xlsx forward daemon)**:
  - Forward daemon reads from all 10 projections' query functions. Query signatures stable.
  - `money.manual_flows` + `money_flow.manually_added` event type already wired for 07c's reverse path.
  - The 07b forward daemon subscribes to event types but MUST NOT emit — it's a projection per [§2.2].
- **Carry-forward for prompt 07c (xlsx reverse daemon)**:
  - `money_flow.manually_added` and `money_flow.manually_deleted` events registered; 07c emits these when principals edit the Raw Data sheet.
  - `task.updated`, `task.deleted`, `commitment.edited` (registered in 06) cover the Tasks/Commitments sheets' reverse path.
  - xlsx reverse is an adapter not a projection — it emits.
- **Carry-forward for prompt 08**:
  - 3 new projections × ~6 queries = ~18 more TODO(prompt-08) markers across queries.py files. Total now ~38 across 10 projections.
- **Carry-forward for future embedding daemon**:
  - `embedding.generated` schema requires pre-computed vector in payload. AdministrateMe does not import embedding SDKs. Daemon will call OpenClaw's embedding endpoint per [§8].
