# AdministrateMe build — prompt sequence

**Purpose.** This is the driver for building AdministrateMe with Claude Code (Opus 4.7). It is not a replacement for the five artifact files (BUILD.md, CONSOLE_PATTERNS.md, CONSOLE_REFERENCE.html, REFERENCE_EXAMPLES.md, DIAGRAMS.md) — those remain the authoritative specification. This sequence is a series of narrow, verifiable prompts that progressively build the system, with human review between each prompt.

**Why a sequence instead of one mega-prompt.** One prompt for the whole build fails predictably: context pressure, lost-in-the-middle attention, drift from earlier constraints, and a tendency to race to completion without stopping to verify. This sequence addresses all four by giving each prompt a narrow scope, a specific reading list, explicit deliverables, and a required stop for human review.

---

## The two-phase model

AdministrateMe is built and deployed in two distinct phases:

**Phase A — Claude Code generates the repo.** Claude Code runs in Anthropic's sandbox, working entirely against the GitHub repository at https://github.com/junglecrunch1212/administrate-me-now. It produces all the code, the bootstrap wizard, the CLI, the persona packs, the documentation mirror. It commits and pushes to GitHub. The operator reviews PRs asynchronously. The Mac Mini is not involved. No live OpenClaw gateway. No Tailscale. No Plaid or BlueBubbles credentials. Tests that require those services are marked `@pytest.mark.requires_live_services` and skipped during Phase A. **Every prompt in this sequence (00 through 18) is a Phase A prompt.** When prompt 18 passes, the repo is build-complete.

**Phase B — operator bootstraps the Mac Mini.** The operator (James) goes to the Mac Mini, installs OpenClaw, installs BlueBubbles, authenticates Tailscale, clones the repo, runs `./bootstrap/install.sh`. The wizard installs packs into OpenClaw, registers slash commands, pairs channels, prompts for Plaid / Google / Apple credentials. At the end, the instance is live. Phase B is operator-driven — Claude Code does not participate. Prompt 19 (optional) gives the operator a smoke test script to run after Phase B completes.

**Why two phases:**
- Separation of concerns. Code generation is one job; live-system bootstrap is another. Mixing them makes both fragile.
- Claude Code doesn't need a Mac Mini to write code. It just needs GitHub.
- The operator can review the repo's state at their own pace before deploying.
- Phase B can be re-run if something goes wrong without involving Claude Code again.

**What each phase tests:**
- Phase A verifies: code is correct, tests pass in sandbox with mocks, documentation is consistent, the lab-mode bootstrap completes against fixtures.
- Phase B verifies: real integrations work — real OpenClaw accepts the packs, real BlueBubbles receives a test message, real Plaid link flow completes, the family actually gets a morning digest.

**If a Phase A prompt is ambiguous about which phase it belongs to,** the answer is always Phase A. Claude Code never needs to SSH into a Mac Mini. Claude Code never needs to contact a live OpenClaw. Claude Code's job ends at "committed and pushed to GitHub."

---

**How to use.** Run prompts **one at a time**, in order. After each prompt, review what Claude Code produced before proceeding. If a phase fails verification, do NOT proceed — either ask Claude Code to fix the issue in the same session, or consult the diagnostic appendix. Skipping a failed phase compounds errors downstream.

**Operator note.** For Phase A, you'll monitor Claude Code's progress via GitHub — reviewing PRs as they land. You don't need to be at your Mac while Claude Code works; it runs against GitHub. For Phase B, you set aside a Saturday morning, sit down at the Mac Mini, and run the bootstrap wizard. See `ADMINISTRATEME_FIELD_MANUAL.md` chapter 4 for the Phase B setup checklist.

---

## Prerequisites (Phase A)

Before prompt 00, Claude Code needs:

1. GitHub repo exists and is accessible: https://github.com/junglecrunch1212/administrate-me-now
2. All five artifact files are committed to the repo root:
   - `ADMINISTRATEME_BUILD.md`
   - `ADMINISTRATEME_CONSOLE_REFERENCE.html`
   - `ADMINISTRATEME_CONSOLE_PATTERNS.md`
   - `ADMINISTRATEME_REFERENCE_EXAMPLES.md`
   - `ADMINISTRATEME_DIAGRAMS.md`
3. Claude Code's sandbox has Python 3.11+, Node 22+, Poetry, npm, git, gh.
4. Claude Code is authenticated to GitHub with push access to the repo (either via PR workflow on branches, or direct push to main — prompt 00 verifies).

**Phase B prerequisites** (for the operator, not Claude Code — verified on the Mac Mini during bootstrap):
- macOS 14+ on the Mac Mini.
- OpenClaw gateway installed and running on :18789.
- BlueBubbles server running (for iMessage channel).
- Tailscale authenticated; Funnel configured (for Plaid webhook).
- 1Password CLI authenticated for secret storage.
- Plaid Link credentials; Google Workspace OAuth; Apple ID credentials available.
- FileVault enabled on the Mac Mini.

These are listed in `ADMINISTRATEME_FIELD_MANUAL.md` chapter 4. Claude Code does NOT verify them.

If any Phase A prerequisite fails, do not proceed — fix it first.

---

## Repository + documentation mirror discipline

**The repo is the only source of truth.** All artifact reads come from the local checkout of `junglecrunch1212/administrate-me-now`, which must be kept fresh. Before every prompt, Claude Code runs:

```bash
cd ~/Documents/adminme-lab
git pull origin main
```

This ensures that if the operator has refined a spec between prompts (fixing a typo, expanding a section), Claude Code picks up the change.

**External documentation is mirrored, not fetched live.** Prompt 00.5 pre-fetches every external doc the build needs — OpenClaw, Plaid, BlueBubbles, Google APIs, Apple EventKit, Tailscale, Textual, SQLCipher, sqlite-vec, CalDAV — and stores them as files in `docs/reference/`. Every subsequent prompt that needs to "consult the OpenClaw docs" or "check Plaid's endpoint reference" reads from `docs/reference/<section>/`, not from the internet.

Rationale: the Claude Code sandbox has an egress allowlist. `github.com` and `raw.githubusercontent.com` are allowed; most other hosts return HTTP 403 `host_not_allowed`. This would ordinarily prevent Claude Code from fetching external documentation entirely. Prompt 00.5 works around it by using the **GitHub-first approach**: the vast majority of the documentation we need lives as source markdown or generated code in public GitHub repos (e.g., `openclaw/openclaw/docs/`, `plaid/plaid-openapi`, `Textualize/textual/docs/`, `googleapis/google-api-nodejs-client/src/apis/gmail/v1.ts`). Fetching from these repos works within the sandbox today. The remainder — primarily Apple's EventKit docs (which Apple does not publish as source anywhere) and optionally a handful of Tailscale KB pages — are documented as gaps in `docs/reference/_gaps.md` for the operator to handle via manual clipping or allowlist expansion.

**If a prompt says "per OpenClaw docs" or "per Plaid docs":** read from `docs/reference/<section>/`. If the referenced file doesn't exist, either the mirror is incomplete (stop and finish prompt 00.5) or the content is a documented gap (check `docs/reference/_gaps.md` — may be acceptable to proceed with noted gap, or may require operator action first).

---

## The sequence

| # | Prompt | Objective | Duration est. | Verifies |
|---|---|---|---|---|
| 00 | `00-preflight.md` | Confirm environment + artifacts; produce `docs/preflight-report.md` | 20 min | Environment |
| 00.5 | `00.5-mirror-docs.md` | Mirror external docs into `docs/reference/` via **GitHub-first** fetching (openclaw/openclaw, plaid/plaid-openapi, Textualize/textual, etc.); document the small Apple/Tailscale residual gap | 45-75 min | Mirror complete + gap list |
| 01 | `01-read-artifacts.md` | Read all five artifact files + `docs/reference/openclaw/`; produce `docs/openclaw-cheatsheet.md` and `docs/architecture-summary.md` | 2-3 hrs | Working knowledge |
| 01b | `01b-system-invariants.md` | Produce `docs/SYSTEM_INVARIANTS.md` — the constitutional reference every later prompt reads first | 2 hrs | Cross-cutting contracts explicit |
| 02 | `02-repo-scaffold.md` | Create directory structure, dependencies, stub modules | 1-2 hrs | Layer hygiene |
| 03 | `03-event-log-bus.md` | Implement L2: SQLCipher event log + in-process event bus | 3-4 hrs | Append/read, replay, subscribe |
| 04 | `04-event-schemas.md` | Pydantic schemas for all ~60 event types + schema registry | 2-3 hrs | All events validate |
| 05 | `05-projections-core.md` | L3: parties, interactions, artifacts projections | 4-5 hrs | CRM primitives |
| 06 | `06-projections-domain.md` | L3: commitments, tasks, recurrences, calendars projections | 3-4 hrs | Domain state |
| 07 | `07-projections-ops.md` | L3: money, places_assets_accounts, vector_search, xlsx (bidirectional) projections | 4-5 hrs | Ops state + bidirectional xlsx |
| 07.5 | `07.5-checkpoint-projection-consistency.md` | **Checkpoint:** audit all 11 projections for schema match, FK integrity, rebuild determinism | 30-45 min | Projection layer internally consistent |
| 08 | `08-session-scope-governance.md` | Session + scope enforcement + authority gate + observation mode | 3-4 hrs | Security layers |
| 09a | `09a-skill-runner.md` | Skill runner wrapper around OpenClaw's skill system | 2-3 hrs | First skill call succeeds |
| 09b | `09b-first-skill-pack.md` | `classify_thank_you_candidate` skill pack end-to-end | 2 hrs | Skill pack install + invoke |
| 10a | `10a-pipeline-runner.md` | Pipeline runner + event subscription machinery | 2-3 hrs | Pipelines receive events |
| 10b | `10b-reactive-pipelines.md` | identity_resolution, noise_filtering, commitment_extraction, thank_you | 4-5 hrs | Reactive pipelines working |
| 10c | `10c-proactive-pipelines.md` | morning_digest, paralysis_detection, reminder_dispatch, reward_dispatch, crm_surface, custody_brief — registered as OpenClaw standing orders | 4-5 hrs | Proactive pipelines firing |
| 10d | `10d-checkpoint-pipeline-skill-consistency.md` | **Checkpoint:** audit pipeline ↔ skill ↔ schema wiring; confirm no projection writes / LLM calls from pipelines | 30-45 min | Pipeline layer internally consistent |
| 11 | `11-standalone-adapters.md` | L1 standalone Python adapters: Gmail, Plaid, Apple Reminders, Google Calendar, CalDAV | 5-6 hrs | External ingest working |
| 12 | `12-openclaw-plugin-adapters.md` | L1 OpenClaw plugin adapters: memory bridge, channel bridges for BlueBubbles/Telegram/Discord | 3-4 hrs | OpenClaw channels feeding event log |
| 13a | `13a-product-apis-core-comms.md` | Core (:3333) + Comms (:3334) FastAPI services | 4-5 hrs | Read/write endpoints |
| 13b | `13b-product-apis-capture-auto.md` | Capture (:3335) + Automation (:3336) FastAPI services | 3-4 hrs | Capture + sensors |
| 14a | `14a-console-framework.md` | Express at :3330, Tailscale identity, Session, guardedWrite, RateLimiter | 3-4 hrs | Auth + write gates |
| 14b | `14b-console-views-primary.md` | Today, Inbox, CRM, Capture, Finance views | 5-6 hrs | Primary surfaces |
| 14c | `14c-console-views-secondary.md` | Calendar, Scoreboard, Settings + SSE chat proxy to OpenClaw | 4-5 hrs | Remaining surfaces + chat |
| 14d | `14d-console-reward-observation.md` | Reward toast system + observation banner + degraded mode | 2-3 hrs | UX polish |
| 14e | `14e-checkpoint-console-api-contracts.md` | **Checkpoint:** verify every console ↔ Python API endpoint contract matches | 45-60 min | Console/API boundary correct |
| 15 | `15-openclaw-integration.md` | SOUL.md compile, slash command registration, standing order registration, plugins install | 3-4 hrs | OpenClaw↔AdminMe integration |
| 15.5 | `15.5-checkpoint-final-integration.md` | **Checkpoint:** re-verify every invariant from SYSTEM_INVARIANTS.md; confirm Phase B registration queues complete | 1 hr | Phase A integrally sound |
| 16 | `16-bootstrap-wizard.md` | Nine-section Textual TUI bootstrap wizard with resumability | 6-8 hrs | Bootstrap produces healthy instance |
| 17 | `17-cli-deploy-migrations.md` | `adminme` CLI (16 subcommand groups), deploy scripts, migration framework | 4-5 hrs | Operator surface |
| 18 | `18-integration-test.md` | End-to-end test in Phase A sandbox using lab mode + mocks: bootstrap → ingest → pipeline → projection → surface → outbound | 3-4 hrs | Phase A build-complete |
| 19 | `19-phase-b-smoke-test.md` | Phase B smoke test script — operator runs after Mac Mini bootstrap to verify live instance health | 1 hr (to write; operator runs it on the Mac Mini) | Phase B readiness |

**Total estimate:** 93-123 hours of Claude Code work (Phase A), spread over 2-4 weeks of Saturday deploys (one or two prompts per slot). Breakdown: ~88-118 hrs of build prompts, ~2 hrs for the invariants document (01b), ~3-4 hrs across the four checkpoints (07.5, 10d, 14e, 15.5). Prompt 00.5 adds 1-2 hours plus any manual clipping. Prompt 19 is quick (~1 hour to write) plus whatever bootstrap time Phase B takes on the Mac Mini (typically 2-4 hours if all credentials are at hand).

The extra ~5 hours of architectural-safety work (01b + 4 checkpoints) is what separates "decent chance the build works first try" from "good chance the build works first try." These prompts catch cross-cutting architectural drift before it compounds.

---

## Dependency graph

```
00 ──► 00.5 ──► 01 ──► 01b ──► 02 ──► 03 ──► 04 ──► 05 ──► 06 ──► 07 ──► 07.5
                                                                          │
                                                                          ▼
                                                                          08
                                                                          │
                                                                          ▼
                                                                          09a ──► 09b
                                                                                   │
                                                                                   ▼
                                                                          10a ──► 10b ──► 10c ──► 10d
                                                                                                    │
                                                                 ┌─────────┬──────────────────────┤
                                                                 ▼         ▼                      ▼
                                                                 11        12                 (needs 10c
                                                                 │         │                   to fully
                                                                 ▼         ▼                   verify)
                                                                 13a ◄────13b
                                                                  │
                                                                  ▼
                                                                 14a ──► 14b ──► 14c ──► 14d ──► 14e
                                                                                                    │
                                                                                                    ▼
                                                                                                   15 ──► 15.5
                                                                                                            │
                                                                                                            ▼
                                                                                                           16
                                                                                                            │
                                                                                                            ▼
                                                                                                           17
                                                                                                            │
                                                                                                            ▼
                                                                                                           18
                                                                                                            │
                                                                                                            ▼
                                                                                                           19 (Phase B smoke test — operator runs on Mac Mini)
```

**Phase boundary:** Prompts 00 through 18 run in Claude Code's sandbox against GitHub. Prompt 19 produces a script the operator runs on the Mac Mini after Phase B bootstrap.

**Checkpoint prompts (01b, 07.5, 10d, 14e, 15.5):** Unlike build prompts, checkpoints add no functionality — they verify the build so far is internally consistent. Each produces a report document. If the report shows critical issues, fix before proceeding. If all clear, proceed to the next build prompt.

**Parallelizable sections** (can be in different sessions if you're spending time on each):

- 11 and 12 after 10c — adapter work is independent.
- 13a and 13b can be different sessions.
- 14b and 14c can be in different sessions once 14a is solid.

**Hard sequential dependencies** (do NOT parallelize):

- 03 before 04. Event log must exist before schemas that validate against it.
- 04 before 05. Schemas must exist before projections consume events.
- 07 before 08. All projections must exist before session/scope queries against them.
- 09a before 09b. Runner must exist before skill pack uses it.
- 10a before 10b. Pipeline machinery before specific pipelines.
- 14a before 14b/14c. Framework before views.
- 15 before 16. OpenClaw integration must be working before bootstrap wizard configures it.
- 17 before 18. CLI must exist for integration test to call it.

---

## Per-prompt structure

Every prompt in this sequence follows the same shape:

```markdown
# Prompt NN: <title>

**Phase:** <BUILD.md phase reference>
**Depends on:** <prior prompts that must have succeeded>
**Estimated duration:** <hours>
**Stop condition:** <what proves this prompt is done>

---

## Read first (required)

- <specific file sections, in order>
- <required prior artifacts>

## Operating context

<brief framing: who you are, what substrate, what phase of the build>

## Objective

<one paragraph; narrow>

## Out of scope

- <things this prompt should NOT touch>

## Deliverables

<file list, test list, demo commands>

## Verification

<exact commands to run; expected output>

## Stop

<what to do when done; explicit "do not proceed to next prompt without human approval">
```

---

## When things fail

If a prompt fails verification:

1. **Do NOT proceed.** Skipping compounds errors.
2. **Check the diagnostic appendix.** `prompts/diagnostics/` contains specific failure-mode prompts:
   - `d01-tests-pass-isolated-fail-integration.md`
   - `d02-openclaw-invocation-shape-mismatch.md`
   - `d03-projection-rebuild-diverges.md`
   - `d04-migration-failed-partial-state.md`
   - `d05-skill-nonsense-output.md`
   - `d06-observation-not-suppressing.md`
   - `d07-event-log-slow-query.md`
   - `d08-xlsx-spurious-events.md`
3. **If no diagnostic matches,** consult ADMINISTRATEME_FIELD_MANUAL.md chapter 8 ("When things go wrong"), then return to Claude Code with a specific error message and the three-category framing.

---

## After the Phase A sequence completes

When prompt 19 passes, **Phase A is done**. The repo contains:
- All application code (L1-L5).
- The bootstrap wizard.
- The CLI.
- Persona packs, skill packs, pipeline packs.
- Mirrored external documentation (`docs/reference/`).
- The Phase B smoke test script.
- All Phase A tests passing.

The instance is **build-complete but not yet tenant-live**. Phase B is how it goes live.

### Phase B — operator bootstraps the Mac Mini

This is a separate operation, driven by the operator (James), not Claude Code. See `ADMINISTRATEME_FIELD_MANUAL.md` chapter 4 for the full walkthrough. In brief:

1. Mac Mini setup per chapter 3 of the Field Manual (Tailscale, OpenClaw, BlueBubbles, 1Password CLI, etc.).
2. Clone the repo: `gh repo clone junglecrunch1212/administrate-me-now ~/Documents/adminme`.
3. Install deps: `poetry install` and `cd console && npm install`.
4. Run bootstrap: `./bootstrap/install.sh`. Wizard walks through the nine sections interactively (~2-4 hours depending on credentials at hand). This is the REAL mode of the wizard built in prompt 16.
5. Run smoke test: `./scripts/phase-b-smoke-test.sh`. All checks should pass.
6. Observation period: leave observation mode ON for 7 days. Review `adminme observation log` daily. The family is not yet served by the system — only receiving suppressed suggestions that you review.
7. When suppressed log looks consistently correct, turn observation off: `adminme observation off`. Family now uses the system.

See `ADMINISTRATEME_FIELD_MANUAL.md` chapter 7 for ongoing operations (deploys, backups, monitoring).

---

## Notes on using this with Opus 4.7 specifically

- **One prompt per session.** Do not try to run multiple prompts in one Claude Code session; each prompt is a fresh session with fresh context.
- **Paste the whole prompt.** Don't summarize it for Claude Code. The explicit reading list and deliverables matter.
- **Let it verify.** Each prompt ends with explicit verification commands. Claude Code should run them and paste the output before claiming done.
- **Trust the stop conditions.** If a prompt says "stop, await human approval," do not let Claude Code proceed. The stop is part of the design, not a limitation.
- **If Claude Code starts to drift**, stop the session, ask it to commit what's working, start a fresh session, and reload the prompt. Opus 4.7 handles fresh starts well; it handles 4-hour single sessions poorly.

### The universal preamble (paste before every prompt)

Before pasting any prompt (00 through 19), paste this preamble first:

> **Phase + repository + documentation + sandbox discipline.**
>
> You are in **Phase A**: generating code in Anthropic's sandbox against https://github.com/junglecrunch1212/administrate-me-now. The Mac Mini is not involved. You do not contact live OpenClaw, live BlueBubbles, live Plaid, or any other external service. Tests that require those are marked `@pytest.mark.requires_live_services` and skipped.
>
> **Sandbox egress is allowlisted.** `github.com` and `raw.githubusercontent.com` work. Most other hosts return HTTP 403 `x-deny-reason: host_not_allowed` from Anthropic's proxy. Do NOT interpret 403s as "the site is down" — they mean the sandbox won't reach non-allowlisted hosts. If a prompt tells you to WebFetch a non-GitHub URL and you get 403, that's expected; document the gap and move on per prompt 00.5's pattern.
>
> **Session start (required sequence):** Before reading any artifact or committing any code, do:
> ```
> git checkout main
> git pull origin main
> git checkout -b phase-<NN>-<slug>   # e.g. phase-03-event-log-bus
> ```
> This gives you a clean workspace based on the operator's latest approved state, isolated on a branch that cannot accidentally modify `main`. Do NOT `git pull` again during the session — if the operator merges other work while yours is in progress, you will pick it up on the *next* session.
>
> When a prompt tells you to read something, READ IT — do not skim, do not assume, do not infer from your training. If it says "Read `ADMINISTRATEME_BUILD.md` section X," open the file and read section X.
>
> When a prompt says "per OpenClaw docs" or "per Plaid docs" or any external documentation reference, read from `docs/reference/<section>/` — the local mirror populated by prompt 00.5 (GitHub-first fetch). **Do NOT use WebFetch to pull these docs live.** The mirror is the version-pinned source of truth. If a referenced file is missing from `docs/reference/`, stop the session and report that prompt 00.5 is incomplete or the gap is documented in `docs/reference/_gaps.md`.
>
> Commit your work frequently with honest messages on the branch. When the prompt's stop condition is met:
> ```
> git push origin phase-<NN>-<slug>
> ```
> Do NOT open the PR yourself and do NOT push to `main`. The operator reviews the branch, opens the PR, and merges when they've reviewed the work.

This preamble is a reminder, not a duplicate instruction. The behaviors are baked into the prompts themselves; the preamble just makes them top-of-mind in Claude Code's context.
