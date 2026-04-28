# Tier C split memo — Prompt 10c: Proactive pipelines (OpenClaw standing orders)

**Author:** AdministrateMe Build Supervision Partner
**Date:** 2026-04-28
**Disposition:** Pre-split candidate per `D-prompt-tier-and-pattern-index.md` v3 row for 10c (UT-13).
**Verdict:** SPLIT INTO 10c-i / 10c-ii / 10c-iii.

---

## Context

10b-ii-β (merged 2026-04-28) closed the reactive-pipeline build. All four reactive packs (`identity_resolution`, `noise_filtering`, `commitment_extraction`, `thank_you_detection`) live; `PipelineRunner` has the `parties_conn_factory` seam through `PipelineContext`. `outbound()` shipped in 08b. `run_skill()` shipped in 09a.

The on-disk 10c draft (`prompts/10c-proactive-pipelines.md`, 64 lines, pre-PM-7) lists six proactive pipelines (`morning_digest`, `paralysis_detection`, `reminder_dispatch`, `reward_dispatch`, `crm_surface`, `custody_brief`) plus four new skills, in one Claude Code session. Two blockers:

1. **Architectural correctness.** The draft says (line 41-43) the pipeline runner calls "OpenClaw's standing-order registration API." There is no such API. Per `[cheatsheet Q3]`, `[D1]`, and `adminme/pipelines/runner.py:7-10`, proactive registration is workspace-prose `AGENTS.md` entries plus `openclaw cron add` invocations, executed by `bootstrap §8` from version-controlled prose at `bootstrap/openclaw/programs/<order>.md` plus a sidecar `bootstrap/openclaw/cron.yaml`. The runner never registers anything; `discover()` already skips proactive packs.
2. **Empirical sizing.** Six pipelines + four skills is over budget. Reference baselines: 09b ~660 lines / 8 tests for one skill pack; 10b-i 320 lines / 22 tests; 10b-ii-α 370 lines / 25-30 tests. Six proactive pipelines, each calling at least one new compose skill, with version-controlled prose AND a cron sidecar, would mirror the 07c saga (two timeouts before α/β split landed). PM-15 was written for exactly this signal.

## UT-2 disposition

UT-2 is CLOSED by D1 (decided 2026-04-23, status CONFIRMED). D1 prescribes the directory layout, per-program file shape, concatenation rule, and cron sidecar this memo treats as binding. The UT-13 framing in `partner_handoff.md` and `D-prompt-tier-and-pattern-index.md` was stale; this memo's sequence-update PR closes UT-13.

## The split

**10c-i — Standing-orders infrastructure + `reward_dispatch`** (~480-520 lines / 25-30 tests)

Ships `bootstrap/openclaw/programs/<six>.md` (one full = `reward_dispatch`, five stubs with TODO markers for 10c-ii/10c-iii), `bootstrap/openclaw/cron.yaml` (five scheduled-program entries; `reward_dispatch` not in cron — it's reactive in-runner), `bootstrap/openclaw/README.md` (consumption contract for §8), `RewardReadyV1` event schema at v1 per `[D7]`, and `packs/pipelines/reward_dispatch/`. `reward_dispatch` is the simplest consumer: subscribes to `task.completed` + `commitment.completed`; rolls tier deterministically from member profile's `reward_distribution`; picks template from persona pack's `reward_templates`; emits `reward.ready`. No `outbound()` (console SSE layer in 14a/14b consumes the event). No skill calls.

Same shape decision as 10b-ii-α landing the parties-DB seam with its first consumer (`commitment_extraction`).

**10c-ii — `morning_digest` + `paralysis_detection`** (~520-580 lines / 30-35 tests)

Two member-targeting briefs: each loads member profile, queries projection state, calls `compose_<digest|nudge>` skill, emits `<...>.composed` event, delivers via `outbound()`. Both schedule-driven; both have `triggers.proactive: true` (no `triggers.events`); in-process runner skips them. Ships two pipeline packs, two compose skill packs in 09b shape, two new event schemas at v1, plus rewrites the morning_digest + paralysis_detection program-prose stubs from 10c-i to full programs.

**10c-iii — `reminder_dispatch` + `crm_surface` + `custody_brief`** (~550-620 lines / 35-45 tests; top of empirical window — watch flag)

Three scheduled scans of projection state. Each handler queries one or more projections, composes a per-entry message, delivers via `outbound()`. All three deliver via `outbound()`; observation-mode suppression must be tested for each. Ships three pipeline packs, three compose skill packs, three new event schemas at v1, plus rewrites the three remaining program-prose stubs. If depth-read at refactor reveals it's still over budget, splittable into 10c-iii-α (`reminder_dispatch` alone) + 10c-iii-β (`crm_surface` + `custody_brief`).

## Dependency impact

- 10c-i depends on 10b-ii-β merged. Resolves UT-2 (closed by D1) + UT-13 (closed at this memo's sequence-update PR).
- 10c-ii depends on 10c-i merged.
- 10c-iii depends on 10c-ii merged.
- 10d (checkpoint) audit scope expands from "6 reactive pipelines" to "6 reactive + 6 proactive pipelines + standing-orders infrastructure."
- `scripts/verify_invariants.sh`: no change. New event types register at v1 in the registry; verify script does not enumerate them.
- `bootstrap/openclaw/`: new top-level subdir. 10c-i creates it. Bootstrap §8 (prompt 16) wires it — concatenates `programs/*.md` into `~/Chief/AGENTS.md`, runs `openclaw cron add` per `cron.yaml` entry. Carry-forward for prompt 16.
- `PipelineRunner`: no change. The runner's existing skip path (`runner.py:131-138`) already handles proactive packs.

## Carry-forward updates needed

After 10c-i, 10c-ii, 10c-iii merge, `partner_handoff.md` advances three times. `PROMPT_SEQUENCE.md` sequence table gets three new rows replacing the single 10c row; dependency-graph ASCII updates from `10b-ii-β → 10c → 10d` to `10b-ii-β → 10c-i → 10c-ii → 10c-iii → 10d`. `D-prompt-tier-and-pattern-index.md` 10c row flips from "Pre-split candidate" to "Was split on arrival"; UT-13 removed from "Open tensions"; UT-2 row annotated CLOSED-by-D1.

## Self-check (delivery-gate per `E-session-protocol.md` §2.9)

- ✅ Split decision made BEFORE drafting a full single-prompt 10c (init prompt §11.5).
- ✅ Three sub-prompts each within Claude Code's empirical session window.
- ✅ Dependency chain explicit: 10c-i → 10c-ii → 10c-iii.
- ✅ D1 (CONFIRMED 2026-04-23) prescribes the standing-orders mechanism; 10c does not invent a registration API. Runner already skips proactive packs.
- ✅ All new event types register at v1 per `[D7]`.
- ✅ PM-14 honored: pipelines under `packs/pipelines/`; standing-orders prose under `bootstrap/openclaw/programs/`.
- ✅ PM-15 honored: infrastructure (standing-orders directory + cron sidecar) ships with first consumer (`reward_dispatch`).
- ✅ PM-16: each sub-prompt's "Read first" written against what actually shipped on main when the prompt is drafted.

**Memo verdict: split approved.**
