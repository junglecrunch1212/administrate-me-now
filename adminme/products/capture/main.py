"""
Capture product — Working Memory + CRM Surfaces FastAPI service at loopback :3335.

Implemented in prompt 13b per ADMINISTRATEME_BUILD.md §L5-continued and
architecture-summary.md §9.

Owns quick-capture (natural-language prefix routing: `grocery:`, `call:`,
`idea:`, `recipe:`), voice-note ingest, triage queue, recipes, CRM Parties/
Places/Assets/Accounts views, semantic + structured search over Interactions/
Artifacts/Parties.

Routers: /api/capture/{capture, voice, triage, recipes, parties,
parties/:id, places, assets, accounts, search}.

Per DECISIONS.md §D4: Capture owns the HUMAN-FACING CRM surfaces but does
NOT own CRM data. CRM data lives in shared L3 projections (parties,
interactions, artifacts, commitments); Core/Comms/Automation are peers of
Capture for CRM reads.

Proactive jobs (OpenClaw standing orders): relationship_summarization
(nightly 02:00), closeness_scoring (weekly Sun 04:00), crm_surface
(daily 09:00 + on-demand), graph_miner (nightly 03:00 on adminme-vault if
present), recurrence_extraction (daily 04:00).

Binds to LOOPBACK only (§9 invariant 1).

Do not implement in this scaffolding prompt. Prompt 13b will fill in.
"""
