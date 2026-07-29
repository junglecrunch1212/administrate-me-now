# Deletion and deferral map

Gate 1 deletes no production behavior. A future delete lands only in the same PR as a passing replacement/negative test and a crosswalk/handoff update.

## Delete after replacement

| Target | Why it must go | Preconditions |
|---|---|---|
| Python-plugin claim and empty packages in `adminme/openclaw_plugins/` | Incompatible with the native current TypeScript plugin contract. | One TS plugin passes AC-12; history preserved in Git. |
| Comment-only `console/server.js` as application runtime | It starts no server and conflicts with the one-Python-process topology. | Python composition serves tested frontend/API; AC-02/18/19 pass. Node build tooling may remain. |
| Four docstring-only product `main.py` service stubs | They advertise separate processes and stale routes without behavior. | Router namespaces exist with route compatibility tests; ownership remains. |
| `adminme/daemons/pipeline_supervisor.py` prose stub | A separate supervisor duplicates composition ownership and contains no code. | Composition root owns runner lifecycle and AC-02/09/20 pass. |
| AdministrateMe-side provider-fallback loop in `adminme/lib/skill_runner/wrapper.py` | OpenClaw owns model routing/fallback. | Pinned OpenClaw seam proves configured routing and records actual provenance. |
| Quick-capture prefix, direct voice-ingest, and triage route/UI claims | Superseded by member-owned tools and Cat-D bridge ingestion. | Knowledge/CRM/Lists surfaces and member bridge slice pass AC-04/18. |
| Product-scheduling references to APScheduler or AdministrateMe cron | Duplicate OpenClaw responsibility. | All standing orders reconcile through pinned OpenClaw; internal debounce/retry timers explicitly exempt. |
| Legacy central `:3333–:3337` process topology | More failure domains and duplicated lifecycle without semantic value. | One root on `:3330`; compatibility aliases tested/removed deliberately; AC-19. |
| Placeholder console test that only echoes success | Falsely signals coverage. | Real unit/browser suites exist and AC-18 runs them. |
| Windows-illegal scaffold template filenames containing `<NAME>` | Prevent normal checkout and makes scaffolding tests non-portable. | Portable placeholder convention produces identical generated names; Linux/macOS/Windows tests pass. |

## Preserve, do not delete

- `member_knowledge` and `vector_search`: domain read models, not generic OpenClaw memory.
- Recurrences: domain meaning, not scheduler duplication.
- Product route/ownership contracts when services become namespaces.
- All thirteen projection capabilities, six product functions, five adapter categories, six pack kinds, five profiles, and four personas.
- Event schemas, governance, normalization, workbook semantics, and meaningful tests unless a cited migration proves replacement compatibility.
- Historical prompts/memos as provenance; they lose authority but are not erased.

## Optional deferrals

| Optional item | Deferral | Non-negotiable substitute/boundary |
|---|---|---|
| CalDAV provider | DEFER to community/v2 provider work. | Apple Calendar and Google Calendar capabilities remain; calendar projection cannot defer. |
| Google Drive document source | DEFER. | Apple Notes/Voice/optional Obsidian member knowledge path and artifacts remain; knowledge function cannot defer. |
| Telegram and Discord channel enablement | DEFER per deployment if not selected. | The scoped channel-bridge seam and at least one credential-free channel contract remain. |
| Twilio/SMS fallback, ElevenLabs, Deepgram, Privacy.com, Lob | DEFER when not configured. | Relevant action/signal interfaces remain explicit, disabled states honest, no required product function disappears. |
| Home Assistant, optional search provider, optional secondary LLM provider | DEFER per household selection. | Cat-E contract, OpenClaw-only model seam, and sensor/action product capability remain tested with fakes. |
| Obsidian member source | DEFER per member; prohibited for kid bridges by default. | Cat-D bridge and member_knowledge capability remain. |
| Physical legacy-port aliases | DEFER only while a documented migration client needs them. | Product route namespaces and one-process topology remain. |

DEFER is prohibited for privacy/authorization/governance, observation, bootstrap safety, backup/restore, workbook round trip, clean install, the core OpenClaw plugin/four seams, or any required capability listed above.
