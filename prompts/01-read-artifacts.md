# Prompt 01: Read the artifacts

**Phase:** BUILD.md "BEFORE YOU START: LEARN OPENCLAW" section, expanded.
**Depends on:** Prompt 00 passed (environment ready).
**Estimated duration:** 2-3 hours.
**Stop condition:** `docs/openclaw-cheatsheet.md` and `docs/architecture-summary.md` exist, both under their max line counts, both cross-referenced with specific citations.

---

## Read first (required)

Read, in order, the full contents of:

1. `ADMINISTRATEME_BUILD.md` — entire file, cover to cover. 3,476 lines.
2. `ADMINISTRATEME_CONSOLE_PATTERNS.md` — entire file. ~1,836 lines.
3. `ADMINISTRATEME_REFERENCE_EXAMPLES.md` — entire file. ~2,938 lines.
4. `ADMINISTRATEME_DIAGRAMS.md` — entire file. ~1,118 lines.
5. `ADMINISTRATEME_CONSOLE_REFERENCE.html` — skim the HTML for layout/interaction patterns (do not need to read all CSS; focus on the nav tabs, view modes, reward toast, inbox approval flow).

Then read all OpenClaw documentation from the local mirror at `docs/reference/openclaw/` (populated by prompt 00.5). Read every `.md` file in that directory in whatever order makes sense — usually `_index.md` first for orientation, then everything else. Take notes as you go — not verbatim; summaries.

**Do NOT use WebFetch to pull OpenClaw docs live.** The mirror is the source of truth; going around it means the operator can't pin documentation versions and leaves you vulnerable to Cloudflare blocks. If `docs/reference/openclaw/` is missing or sparse, stop — prompt 00.5 is incomplete and must be finished (including any manual clipping) before this prompt can proceed.

The same rule applies for any later prompt that refers to "Plaid docs," "BlueBubbles docs," "EventKit docs," etc.: read from `docs/reference/<section>/`, not live.

## Operating context

This prompt is the foundation for every prompt that follows. If you skip it or rush it, every subsequent phase will be weaker. The point is not to memorize — it's to build working knowledge so that when prompt 03 says "implement the L2 event log per BUILD.md §2," you already know what that means without re-reading the whole spec.

Two-part reading: (a) the AdministrateMe artifact set, written for this build, and (b) the OpenClaw gateway docs, which describe the substrate AdministrateMe runs on top of. You need both to understand how the pieces compose.

## Objective

Produce two summary documents:

1. `docs/openclaw-cheatsheet.md` (max 100 lines) — a concise reference for the eight questions listed in BUILD.md's "BEFORE YOU START: LEARN OPENCLAW" section. This becomes your own cheat sheet for all subsequent prompts that involve OpenClaw integration.

2. `docs/architecture-summary.md` (max 600 lines) — a structured summary of the AdministrateMe architecture organized by the five layers, plus the OpenClaw integration seams, plus the pack registry, plus the security/privacy model. Every section cites the canonical source ("per BUILD.md §X" or "per CONSOLE_PATTERNS.md §N"). This is your reference for all subsequent prompts.

## Out of scope

- Do NOT write any production code.
- Do NOT create directory structures, stub modules, or configuration files.
- Do NOT install any packs, skills, plugins, or OpenClaw extensions.
- Do NOT attempt to run the bootstrap wizard.
- Do NOT summarize anything you haven't actually read. If an OpenClaw URL is unreachable, note that in the cheatsheet and flag it for the operator.

## Deliverables

### `docs/openclaw-cheatsheet.md`

Max 100 lines. Answers exactly these eight questions, each in ≤10 lines:

1. How does an AdministrateMe skill pack get installed into OpenClaw? (Exact commands; file locations.)
2. How does a slash command handler get registered?
3. How does a standing order get registered?
4. How does a plugin get registered?
5. What is the exact shape of a `SKILL.md` file that OpenClaw accepts? (Frontmatter fields, body conventions.)
6. What is `dmScope: per-channel-peer` and when does it apply vs. `shared`?
7. How does the gateway's approval-gates system interact with `guardedWrite` (CONSOLE_PATTERNS.md §3)? (Where does each run in the request flow?)
8. Where does OpenClaw store its state on disk, and what of it needs to be backed up (vs. what is derived from `~/.adminme/` and can be rebuilt)?

End with a `Sources` section listing the 4-8 most useful OpenClaw URLs you referenced.

### `docs/architecture-summary.md`

Max 600 lines. Structure:

```markdown
# AdministrateMe architecture summary

_Produced by prompt 01. Read this before starting any later prompt. Update if the specs change._

## 1. The five-layer model

Per BUILD.md "THE ARCHITECTURE — FIVE LAYERS":
- L1 (adapters): <2 sentences>
- L2 (event log + bus): <2 sentences>
- L3 (projections): <2 sentences + list of 11 projections>
- L4 (pipelines + skills): <2 sentences + reactive vs. standing-order distinction>
- L5 (surfaces): <2 sentences + Node console / Python product APIs split>

## 2. How OpenClaw fits

Per BUILD.md "OPENCLAW IS THE ASSISTANT SUBSTRATE":
- The four seams: <skills, slash commands, standing orders, channels — 2-3 sentences each>
- What OpenClaw provides: <list>
- What AdministrateMe adds: <list>
- State boundary: <OpenClaw memory vs. AdministrateMe event log; memory-bridge plugin>

## 3. Event log invariants

Per BUILD.md L2 sections:
- Append-only
- SQLCipher encrypted
- Partitioned by owner_scope
- Schema: type, version, event_id, event_at_ms, tenant_id, correlation_id?, source?, payload
- <any other load-bearing invariants>

## 4. The 11 projections

Per BUILD.md §3 and DIAGRAMS.md §1:
- For each projection: owner, key event types consumed, key columns, any special properties (bidirectional for xlsx, vector for vector_search)

## 5. Pipelines

Per BUILD.md L4:
- Reactive pipelines (list of 10ish)
- Proactive pipelines (list of 6ish), registered as OpenClaw standing orders
- Skill runner wrapper pattern (one paragraph; cites BUILD.md §L4 skill runner)

## 6. The security + privacy model

Per BUILD.md "AUTHORITY, OBSERVATION, GOVERNANCE" + CONSOLE_PATTERNS.md §3, §4, §6:
- guardedWrite: three layers
- authMember / viewMember: split
- Session + scope enforcement
- Observation mode: where it intercepts
- HIDDEN_FOR_CHILD: what, when

## 7. Packs

Per BUILD.md "PACK REGISTRY" + REFERENCE_EXAMPLES.md:
- Kinds: adapter, pipeline, skill, projection, profile, persona
- Install lifecycle (state machine from DIAGRAMS.md §8)
- Per-kind directory conventions
- pack.yaml required fields

## 8. The console

Per CONSOLE_PATTERNS.md + CONSOLE_REFERENCE.html:
- Node Express at :3330
- 12 patterns (list them; one line each)
- Eight nav surfaces (today, inbox, crm, capture, finance, calendar, scoreboard, settings)
- Three view modes (carousel, compressed, child)

## 9. Python product APIs

Per BUILD.md L5:
- Core :3333, Comms :3334, Capture :3335, Automation :3336
- Loopback only; console is the only tailnet-facing surface

## 10. Bootstrap wizard

Per BUILD.md "BOOTSTRAP WIZARD":
- Nine sections (list with one-line objective each)
- Resumability: bootstrap-answers.yaml.enc + event log
- Idempotency

## 11. Open questions

List anything that was unclear in the spec that you'll need to ask the operator about as you build. Each should cite the file+section that was unclear. Examples: "CONSOLE_PATTERNS.md §11 references a 'bus' but doesn't specify whether bus events from L2 are the same object as SSE events to the client." If you have no open questions, write "None."
```

## Verification

After writing both files, run:

```bash
wc -l docs/openclaw-cheatsheet.md docs/architecture-summary.md
head -50 docs/openclaw-cheatsheet.md
head -100 docs/architecture-summary.md
```

Confirm:
1. `docs/openclaw-cheatsheet.md` is ≤ 100 lines.
2. `docs/architecture-summary.md` is ≤ 600 lines.
3. Every section in architecture-summary.md has at least one explicit citation like "per BUILD.md §X" or "per CONSOLE_PATTERNS.md §N". Sections without citations indicate you're writing from guess, not from the spec.
4. The `## 11. Open questions` section exists even if empty.

Then commit to the repo:

```bash
git add docs/
git commit -m "phase 01: openclaw cheatsheet + architecture summary"
```

## Stop

**Explicit stop message:**

> Reading phase complete. `docs/openclaw-cheatsheet.md` and `docs/architecture-summary.md` are ready for operator review. If the open-questions section is non-empty, the operator should answer those before proceeding to prompt 02. If the operator sees any section that is vague, missing citations, or contradicts something they know from the specs, they should ask me to revise this phase.

Do not begin repo scaffolding in this session. That is prompt 02.
