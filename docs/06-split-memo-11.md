# Split memo — prompt 11 (Tier C, 4-way)

**Parent prompt:** `prompts/11-standalone-adapters.md` (draft, 5907 bytes).
**Decision date:** 2026-05-15.
**Authored by:** Partner (Cowork chat), operator-approved.
**Status:** OPEN — sub-prompt refactors pending operator merge of this memo.

---

## §1 — Why split

Pre-authorship gate ran against `prompts/11-standalone-adapters.md` on 2026-05-15 (post 10c-iii merge + 10d audit). All five cited reference directories (`docs/reference/{google-gmail,plaid,apple-eventkit,google-calendar,caldav}/`) verified present on main. All cited code paths (`adminme/adapters/base.py`, `ADMINISTRATEME_BUILD.md`, `ADMINISTRATEME_REFERENCE_EXAMPLES.md`) verified present. Gate 2 PASS.

But the draft's deliverable list does NOT fit the §2.9 delivery-gate caps for a single v4 4-commit prompt:

- **Draft estimate:** 5-6 hours wall-clock.
- **Deliverable count:** 5 adapters (Gmail, Plaid, Apple Reminders, Google Calendar, CalDAV) + 1 supervisor (`adminme/daemons/adapter_supervisor.py`).
- **Per-adapter footprint estimate** (averaging the BUILD.md detailed specs and the BlueBubbles reference example): 200-400 lines of adapter code + 80-150 lines of tests + 20-40 lines of config schema = ~300-500 lines per adapter.
- **Total estimated prompt body**: 1,800-3,000+ lines of refactored prompt content. §2.9 cap: ≤350 lines. **Overrun: 5-9x.**
- **Files-to-read per commit estimate:** ~10-15 per adapter (the mirrored reference docs alone for each adapter add 4-8 files). §2.9 cap: ≤6 files-to-read per commit. **Overrun: 2-3x per c-band.**

This pattern matches the 10b and 10c splits exactly: a parent prompt that names a family of related artifacts where each artifact deserves its own 4-commit cycle. Precedent decisions:

- `docs/01-split-memo-10b.md` — 10b → 10b-i (identity/noise) + 10b-ii (commitment + thank-you)
- `docs/02-split-memo-10b-ii.md` — 10b-ii → 10b-ii-α (commitment_extraction) + 10b-ii-β (thank_you_detection)
- `docs/03-split-memo-10c.md` — 10c → 10c-i (foundation) + 10c-ii (morning_digest + paralysis) + 10c-iii (reminder + crm + custody)

Each sub-prompt: ~4 commits, ~150-300 lines of refactored prompt, fits the §2.9 budget cleanly. Same pattern applied here.

---

## §2 — Split structure

| Sub-prompt | Slug | Scope | Est. commits | Est. spend per cycle |
|---|---|---|---|---|
| **11a** | `adapter-foundation-gmail` | Adapter supervisor (`adminme/daemons/adapter_supervisor.py`) + Adapter protocol firming up (`adminme/adapters/base.py` from stub → full) + Gmail adapter pack (`packs/adapters/messaging/gmail/`). Foundation: establishes the adapter-pack pattern + config wiring + supervisor lifecycle. Ships 3 schemas (`adapter.error`, `adapter.authenticated`, `adapter.cursor_advanced`) + Gmail-specific `messaging.received` shape extension. | 4 (c1: supervisor + base + 3 schemas, c2: Gmail oauth + auth tests, c3: Gmail ingest + cursor + error path, c4: integration + BUILD_LOG) | $60-100 |
| **11b** | `adapter-plaid` | Plaid adapter pack (`packs/adapters/financial/plaid/`). Per `ADMINISTRATEME_BUILD.md` "PLAID — DETAILED SPEC": Link token flow, institution management, `/transactions/sync` cursor-based pagination, webhook handler (Funnel endpoint), institution health tracking. Emits `money_flow.plaid_synced` (new event type, v1). | 4 (c1: schema + program + config, c2: Link/auth/institution mgmt, c3: cursor sync + webhook handler, c4: institution health + integration + BUILD_LOG) | $80-120 |
| **11c** | `adapter-reminders-bidirectional` | Apple Reminders adapter pack (`packs/adapters/reminders/apple-eventkit/`). Per `ADMINISTRATEME_BUILD.md` "APPLE REMINDERS BIDIRECTIONAL — DETAILED SPEC": bidirectional iCloud sync via EventKit, 30s outbound loop, inbound polling with dedupe by remind_id, **never-sync rules** (list-name substrings like "Work"/"Case"/"Client", item tags like `#private`, items owned by child/ambient). Emits `reminder.list_item.synced` (new event type, v1). Highest per-c-band complexity in the cohort. | 4 (c1: schema + program + never-sync rules, c2: outbound EventKit write path, c3: inbound polling + dedupe + never-sync filter, c4: integration + BUILD_LOG) | $80-120 |
| **11d** | `adapter-calendars` | Google Calendar adapter pack + CalDAV adapter pack (`packs/adapters/calendaring/google/`, `packs/adapters/calendaring/caldav/`). After 11a establishes the pattern, both are clone-shape adapters emitting existing `calendar.event_*` events. Google: OAuth + push notifications. CalDAV: polling (server-side incremental sync is poor). | 4 (c1: shared calendar adapter config schema, c2: Google adapter, c3: CalDAV adapter, c4: integration tests for both + BUILD_LOG) | $60-100 |

**Total estimated cycle cost:** $280-440 across all four sub-prompts. Within the original prompt 11's 5-6h envelope but split so each sub-prompt fits §2.9 and can be Reviewer-verified independently.

---

## §3 — Sub-prompt dependencies

**STACKED** in build order; **NOT** stacked at the branch level (each sub-prompt is its own independent v4 prompt with its own 4-commit cycle):

```
11a (foundation + Gmail)
  ↓ depends-on
  ├── 11b (Plaid)        — independent of each other; either order after 11a
  ├── 11c (Reminders)    — independent of each other
  └── 11d (Calendars)    — independent of each other
```

**Why 11a first:** establishes `adminme/daemons/adapter_supervisor.py`, hardens `adminme/adapters/base.py` from stub → full protocol, defines the three cross-cutting schemas (`adapter.error`, `adapter.authenticated`, `adapter.cursor_advanced`) that 11b/c/d emit. Without 11a's foundation, the other three would each invent their own supervisor patterns and adapter-error schemas — drift risk.

**11b/c/d order is flexible.** Recommended: 11c (Reminders) last because it's the most complex per c-band and benefits from the most stable foundation. But not strictly required.

**Branch strategy per sub-prompt:** each sub-prompt is STACKED c1→c4 (per `docs/BRANCH-STRATEGY.md`), but the sub-prompts themselves are independent at the partner/refactor-* level.

---

## §4 — Adapter code location decision

Per operator approval in Cowork chat (2026-05-15): adapter implementations live at `packs/adapters/<family>/<name>/` per the original draft's structure. Rationale:

- Matches the established 10c-iii pattern: `packs/` for pluggable artifacts (skills, pipelines, adapters), `adminme/` for runtime + protocols + supervisors.
- The pre-scaffolded `adminme/adapters/<family>/` subdirs from prompt 02 (`calendaring/`, `contacts/`, `documents/`, `financial/`, `iot/`, `messaging/`, `reminders/`, `telephony/`, `webhook/`) become dead code under this decision.
- 11a's c1 includes a cleanup step: remove the pre-scaffolded `adminme/adapters/<family>/` subdirs that aren't being used. Document the cleanup in the c1 BUILD_LOG.

**Soft finding (S-1):** if the pre-scaffold from prompt 02 was meant to be the actual location, this decision reverses that. Worth confirming with prompt 02's original author intent if any constitutional ambiguity surfaces during 11a authoring. Default per this memo: pack code at `packs/`.

---

## §5 — Constitutional citations

- §2.9 delivery-gate caps: enforced by `PARTNER-PRE-AUTHORSHIP-CHECKLIST.md` self-check.
- Tier C split precedent: `docs/01-split-memo-10b.md`, `docs/02-split-memo-10b-ii.md`, `docs/03-split-memo-10c.md`.
- BUILD.md adapter sections cited per sub-prompt: "L1: ADAPTERS" (all four), "PLAID — DETAILED SPEC" (11b), "APPLE REMINDERS BIDIRECTIONAL — DETAILED SPEC" (11c).
- New event schemas register at v1 per [D7] in the relevant family file (`ingest.py` for messaging-extension; `domain.py` or a new `adapters.py` for adapter-cross-cutting events — decision deferred to 11a §1 grounding).
- Branch strategy per `docs/BRANCH-STRATEGY.md`: STACKED within each sub-prompt's 4 commits.
- Reviewer-strict-block: vision_drift_score = 3 halts per-sub-prompt per `docs/DRIFT-CONTROL-CHECKLIST.md`.

---

## §6 — Stop / next move

This memo documents the split decision. Operator merges this PR (`partner/split-memo-11`) to land the memo on `main`.

After merge, Partner authors `prompts/11a-adapter-foundation-gmail.md` as the next sub-prompt draft (NOT in `prompts/_active/` yet — that's the v4 refactor step). Once the draft is ready, Partner runs the pre-authorship gate against it, refactors to `prompts/_active/11a-adapter-foundation-gmail.md` per the v4 ten-section structure, and opens `partner/refactor-11a` PR.

After operator merges `partner/refactor-11a`, James fires `executor-cycle.yml` with `prompt_id=11a`. Loop continues per the established autonomous-loop flow.

Estimated wall-clock from this memo → 11a executor-cycle complete:

- Partner authoring of 11a draft + v4 refactor: 30-60 min (Cowork chat, free against subscription cap).
- Operator review + merge of refactor PR: 5-15 min.
- Executor cycle for 11a (4 c-bands): 60-90 min, $60-100 API spend.
- Operator review + merge of 4 c-band PRs (remember: change each PR's base to `main` before merging, per the lesson from PR #86): 10-20 min.

Total ~2-3 hours for 11a end-to-end. Then 11b/c/d follow in any order.

**Critical operator note (PR #86 carry-forward):** when merging stacked c-band PRs, edit each PR's `base` to `main` before clicking Merge. Otherwise the merge goes into the previous c-band's branch, not into `main` (the bug that bit 10c-iii). A workflow change to avoid this is on the deferred list — see PR #80's discussion of "Option B." If we land that workflow change before 11a's executor cycle runs, the manual base-change step goes away.
