"""
xlsx reverse projector — user-edit-driven event emission.

Implemented in prompt 07 per ADMINISTRATEME_BUILD.md §3.11 and SYSTEM_INVARIANTS.md §10.

Watches the workbook via `watchdog` and debounces 2s on file edits before
emitting events for legitimate user changes (§10 invariant 5).

Key rules:
- Reads the sidecar `.xlsx-state/<workbook>/<sheet>.json` to distinguish a
  user edit from a forward-regeneration; skips when the forward lock is held
  (§10 invariant 7).
- Derived cells (columns tagged `[derived]` in the header row) are silently
  dropped on reverse — UX clarity, not a security boundary (§10 invariant 3).
- Plaid-sourced transaction fields (`date`, `account_last4`, `merchant_name`,
  `amount`, `plaid_category`) are protected; principals may only edit
  `assigned_category`, `notes`, `memo` (§10 invariant 4).
- Bidirectionality is bounded: `xlsx_workbooks` is the only bidirectional
  projection and the only projection writing to disk files (§13 invariant 5).

Do not implement in this scaffolding prompt. Prompt 07 will fill in.
"""
