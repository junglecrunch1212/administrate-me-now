"""
Automation product — Ambient Signal Layer FastAPI service at loopback :3336.

Implemented in prompt 13b per ADMINISTRATEME_BUILD.md §L5-continued and
architecture-summary.md §9.

Owns Plaid integration surfaces, financial projections + dashboards, budget
enforcement, subscription audit, Home Assistant bridge, Privacy.com monitoring.

Routers: /api/automation/{plaid/institutions, plaid/sync, plaid/go-live,
money-flows, budget, balance-sheet, pro-forma, subscriptions,
household-status, ha/*}.

Proactive jobs (OpenClaw standing orders): Plaid transactions sync every 4h
(live) or daily (observation); Plaid balance sync every 1h; Plaid investments
+ liabilities weekly Sun 05:00; uncategorized categorization nightly 04:30;
subscription audit monthly 1st; budget pace check Mon/Thu 10:00; balance
sheet rollup nightly 06:00.

Binds to LOOPBACK only (§9 invariant 1).

Do not implement in this scaffolding prompt. Prompt 13b will fill in.
"""
