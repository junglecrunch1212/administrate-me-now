# PARTNER-PRE-AUTHORSHIP-CHECKLIST.md

**v4.3 — the strict authorship-time gate. Run this BEFORE writing a single line of any refactored prompt's body.**

This checklist exists because the v3 test drive surfaced the hardest-to-catch Partner failure mode: producing a confident-sounding refactored prompt whose Read-first section cites files, symbols, and contracts that do not exist on `main`. The DRIFT-CONTROL checklist catches vision drift. The §2.9 size budget catches execution-capacity blowouts. Neither catches *hallucinated codebase citations*. This checklist does.

**v4.3 update (2026-05-16):** added Gate 0 (workspace-vs-main `PROMPT_SEQUENCE.md` sync check) to close the prompt-11 drift gap. v4.2 covered hallucinated citations and pinned-vs-live conflation at the path level; Gate 0 covers the same conflation at the *scope* level (when workspace and main both have a PROMPT_SEQUENCE.md but disagree on what a given prompt is supposed to ship).

This is operationally distinct from the harness's PreFlight agent. PreFlight runs **after** Partner authors and **before** Executor commits. This checklist runs **before** Partner authors. PreFlight is a downstream gate; this is the upstream gate. Both fire on the same defect class. Both must pass.

---

## When this runs

After the orientation report has been confirmed by the operator (per `SESSION-1-ORIENTATION-PROTOCOL.md`), and BEFORE producing any content in §2 Read first / §3 Reconciliation notes / §4 Assumptions to verify / §7 Deliverables of the refactored prompt.

If a draft already exists from a prior session (rare; usually means a previous Partner session halted mid-authorship), run this checklist against the existing draft's §2 Read first list before resuming.

---

## The six gates (Gate 0 added v4.3)

Every gate is mechanical. Every gate produces an artifact the operator can audit. Halt on any miss; do NOT proceed to authorship.

### Gate 0 (v4.3, 2026-05-16) — Verify PROMPT_SEQUENCE.md is in sync between surfaces

Before running Gate 1, compare workspace pinned `PROMPT_SEQUENCE.md` against live main:

```
mcp__github__get_file_contents(
  owner="junglecrunch1212",
  repo="administrate-me-now",
  path="prompts/PROMPT_SEQUENCE.md",
  ref="main"
)
```

Compare the table row for the prompt being authored (and adjacent rows). If they differ between surfaces in any way that affects the prompt being authored — different sub-prompt slugs, different scope, different cited amendments `[DN]` / `[§N]` / `[arch §N]`, different deliverables — halt and surface to operator:

> "PROMPT_SEQUENCE.md surface drift detected. Workspace cites <list>. Main cites <list>. Authoring against which surface?"

Do not proceed to Gate 1 until the operator confirms canon. Operator options at that decision point:

- Push the workspace's updated PROMPT_SEQUENCE.md to main as a sidecar PR before authoring (workspace is canon).
- Treat workspace as aspirational; author against main's version (main is canon).
- Pivot scope: amend the affected prompt to reflect a different architecture than either surface implies.

Until disposition, do not produce verification table or contracts table.

**Rationale for Gate 0:** the 2026-05-16 prompt-11 drift discovery surfaced the gap that earlier checklist versions did not catch. PROMPT_SEQUENCE.md is uniquely susceptible to surface drift because it's referenced by every prompt's Read-first list AND because architectural amendments often invalidate the canonical sequence without an immediately-obvious cascade. Gates 1-5 verify that paths cited in the prompt exist on main; Gate 0 verifies that the prompt's overall scope is right. The downstream protection is the `.github/workflows/constitution-sync.yml` CI check that prevents future amendments from landing without a PROMPT_SEQUENCE.md cascade.

### Gate 1 — Enumerate every cited path

For the target prompt, walk:

- The draft prompt's existing `Read first` section (if any), or
- The depth-read points listed in `D-prompt-tier-and-pattern-index.md` for this prompt's row, or
- The carry-forwards in `docs/build_log.md` for the most recently merged prompt that mention specific file paths

Produce a list of every file path, directory path, and per-file symbol the upcoming refactored prompt will cite. Group them:

```
PATHS_TO_VERIFY:
  files:
    - <path>
    - <path>
  directories:
    - <path>/
    - <path>/
  symbols (file::symbol):
    - <path>::<class_or_function_name>
    - <path>::<class_or_function_name>
```

Aim for an exhaustive list. Over-listing is cheap; under-listing is the failure mode.

### Gate 2 — Verify every cited path exists on main

For each `file` in PATHS_TO_VERIFY:

```
mcp__github__get_file_contents(
  owner="junglecrunch1212",
  repo="administrate-me-now",
  path="<file>",
  ref="main"
)
```

For each `directory`, list its contents:

```
mcp__github__get_file_contents(
  owner="junglecrunch1212",
  repo="administrate-me-now",
  path="<directory>",
  ref="main"
)
```

(The MCP `get_file_contents` tool returns directory listings when given a directory path.)

For each `file::symbol`, after fetching the file, grep for the symbol literal in the returned content:

- For classes: `class <SymbolName>(` or `class <SymbolName>:`
- For functions: `def <symbol_name>(` or `async def <symbol_name>(`
- For module-level constants/registrations: `<SYMBOL_NAME>` at column 0 of any line

Produce a verification table. **Every row must name its source.** Cite the actual tool call (or pinned-doc name) that produced the verification. A row without a named source is not a gate-pass; it is a documentation gap that blocks authorship.

```
| Path or symbol | Verified? | Source | Notes |
|---|---|---|---|
| `adminme/pipelines/base.py` | ✓ | `mcp__github__get_file_contents(path="adminme/pipelines/base.py", ref="main")` | exists; 102 lines |
| `adminme/pipelines/proactive.py` | ✗ | `mcp__github__get_file_contents(path="adminme/pipelines/proactive.py", ref="main")` returned 404 | NOT on main; closest match is `adminme/pipelines/base.py` |
| `adminme/events/schemas/domain.py::DigestComposedV1` | ✓ | live fetch of `domain.py` + grep for `class DigestComposedV1` | found at line 211 |
| `adminme/events/schemas/proactive.py::ParalysisNudgeComposedV1` | ✗ | live fetch returned 404; module not on main | file does not exist; real class is `domain.py::ParalysisTriggeredV1` |
```

**The Source column is load-bearing.** "I read this in the project's pinned `partner_handoff.md`" is not a valid Source for Gate 2 — pinned content is a snapshot, not live `main`. The only valid Source values are: (a) the specific `mcp__github__*` tool call that returned the content, (b) for symbol grep, the file fetched in (a) plus the grep result line number. If you cannot cite a live tool call as the Source, the row is not verified and the gate has not been satisfied for that row.

### Gate 3 — Halt on any miss; produce a Findings list

If ANY row of the verification table is `✗`, do NOT proceed to authorship. Produce a `<NEEDS_OPERATOR_DECISION reason="pre_authorship_citations_invalid"/>` block with this shape:

```
### Pre-authorship findings (HALT)

The following citations in the draft prompt or carry-forwards do not match `main`:

- F-1: `<cited path or symbol>` — NOT on main. Closest real match: `<the actual path/symbol I found>`. Recommendation: <one of: "cite the real path"; "the work to create this path is out-of-scope for this prompt and belongs in <NN>"; "the cited contract has changed; this prompt's scope needs to shift to <alternative>">.
- F-2: ...
- F-3: ...

Operator: please disposition each finding. I will not author the refactored prompt until every F-N has a disposition.
```

End your turn. Wait for operator disposition. Do NOT author the refactored prompt body.

This is the load-bearing rule: **a citation that fails verification is a stop-the-line, even if the agent thinks the substitution is obvious.** What looks like a typo at authorship time becomes a session-burning hallucination at Executor-commit time.

### Gate 4 — Cross-check on-main contracts against the prompt's intent

For each verified file::symbol, fetch the surrounding context (typically `mcp__github__get_file_contents` returns the whole file; if not, fetch). Read the actual signature, base class, and docstring. Confirm the upcoming prompt's intent matches what's there:

- If the prompt will say "subclass `X`", confirm `X` is a class on main (Gate 2 already verified) AND that subclassing is the intended extension pattern (read the class docstring; if it says "instantiate directly" or "do not subclass", flag).
- If the prompt will say "call `ctx.<attribute>`", confirm the attribute exists on the actual `PipelineContext` (or whatever type `ctx` resolves to) on main. Do not assume attribute existence from the prompt's draft.
- If the prompt will say "extend `<MODULE>` with three new models", confirm the module exists AND has the structural shape the extension assumes (e.g., a `BaseModel` import, an existing `registry.register(...)` call pattern). Cite the line numbers.

For each cross-checked item, add a row to a contracts table. **As with Gate 2, every row must name its source.** The Source is the specific live tool call (and the line range fetched) that produced the on-main reality column.

```
| Cited contract | On-main reality | Source | Match? |
|---|---|---|---|
| Subclass `ProactivePipeline` | Class `MorningDigestPipeline` in `packs/pipelines/morning_digest/handler.py` does not inherit from any base; on-main pattern is standalone class. | `mcp__github__get_file_contents(path="packs/pipelines/morning_digest/handler.py", ref="main")`, lines 95–110 | ✗ |
| `ctx.commitments_conn_factory` | `PipelineContext` (`adminme/pipelines/base.py:56`) has only `parties_conn_factory: Callable[...] \| None = None`. | `mcp__github__get_file_contents(path="adminme/pipelines/base.py", ref="main")`, lines 50–90 | ✗ |
| `outbound_fn` parameter | `PipelineContext` has `outbound_fn: Callable[..., Awaitable[Any]]` (`adminme/pipelines/base.py:80`). | same fetch as above, line 80 | ✓ |
```

Halt on any `✗` per Gate 3. A row whose Source is "recalled from pinned content" is not a `✓` — it is a documentation gap that blocks authorship.

### Gate 5 — Produce the verification artifact

Before authoring, produce three artifacts inline (no PR yet):

1. **Gate 0 surface-sync confirmation** (one line: "workspace and main PROMPT_SEQUENCE.md match" OR drift report + operator disposition)
2. **Verification table** (Gate 2 output)
3. **Contracts table** (Gate 4 output)

If Gate 0 passed AND verification + contracts are 100% `✓`, declare:

> **Pre-authorship gate: PASS.** Surface-sync confirmed; all N file paths verified on main; all M cited contracts confirmed against on-main signatures. Proceeding to author `prompts/_active/<NN>-<slug>.md`.

If either has any `✗`, do not declare PASS. Produce the Findings list and halt.

The operator reviews the verification + contracts tables in the chat before authorship begins. This is the eyes-on moment that catches stale citations before they cost a session.

---

## What this does not check

This checklist is necessary, not sufficient. It does not catch:

- **Vision drift.** Use `DRIFT-CONTROL-CHECKLIST.md` pre-PR and Reviewer Job 4 post-merge.
- **Size-envelope blowouts.** Use the §2.9 delivery-gate self-check (line count, per-commit estimate, files-to-read per commit, inline code, single-cycle judgment, autolinker grep) before declaring the refactor final.
- **Branch-strategy mistakes.** Use `BRANCH-STRATEGY.md` and the prompt's §8.5 declaration.
- **Constitutional citation correctness.** Use the PreFlight agent's Check 3 (citation validity) — PreFlight runs after authorship and verifies `[§N]`, `[DN]`, `[arch §N]` ranges against the current SYSTEM_INVARIANTS / DECISIONS / architecture-summary on main.
- **Constitutional-amendment cascade enforcement** (NEW v4.3). Use `.github/workflows/constitution-sync.yml` CI check that fires on PRs touching constitutional docs without a cascade to PROMPT_SEQUENCE.md.

Run all of these. They are not interchangeable.

---

## The MCP precondition

This checklist is structurally dependent on the local `github-mcp-server` install (per `session-handoff-github-mcp-install.md`). Without `mcp__github__get_file_contents`, none of the gates can run. If you are in a Partner session and find the MCP tools are unavailable:

- Confirm via the tool list. If `mcp__github__get_file_contents` is not listed, the MCP is not connected.
- Report to the operator: `<NEEDS_OPERATOR_DECISION reason="mcp_unavailable_cannot_run_pre_authorship_gate"/>`.
- Do NOT attempt the checklist via Chrome MCP or via the read-only "Add from GitHub" file picker. Both produce stale snapshots and defeat the gate's purpose.
- Do NOT proceed to authorship. Wait.

If the MCP becomes available mid-session (e.g., Claude Desktop restart), restart the orientation report from the top per `SESSION-1-ORIENTATION-PROTOCOL.md` to ensure pinned-doc state and live `main` state agree.

---

## Why this exists (three paragraphs)

The 10c-iii v4 test drive of 2026-05-14 produced a refactored prompt whose §2 Read first cited at least eight file paths that did not exist on `main` (`adminme/pipelines/proactive.py`, `adminme/outbound/dispatcher.py`, `adminme/lib/skill_runner/openclaw_client.py`, `adminme/projections/calendar/queries.py`, `adminme/projections/members/queries.py`, five query functions that do not exist in `adminme/`), and asserted a `ProactivePipeline` base class that is fictional (the on-main `morning_digest` handler is a standalone class with no base). The Cowork session passed its DRIFT-CONTROL checklist 6/6 (score 0=aligned) and produced an orientation report claiming "all 14 pinned docs read." Neither catch caught the structural defect. The session correctly halted at Phase A push due to the unrelated MCP-unavailability issue; had the MCP been connected and the prompt pushed, Executor would have spent its session window fabricating the missing infrastructure (the c4 mis-labeling pattern from POSTMORTEM §1.6) or halting on the first missing-file read. v4.1 of this checklist closed that authorship-time gap.

The v4.1 smoke test on 2026-05-15 surfaced a secondary failure mode: an agent that correctly followed the orient-then-pause protocol AND correctly flagged the absence of the new docs as a SOFT finding, but also reported a file path (`prompts/_active/10c-iii-reminder-crm-custody.md`) as having been merged via PR #63 on `main` when the path existed only in the local Cowork project workspace. The agent had blurred Surface 1 (pinned project content) with Surface 2 (live `main` via MCP). The DRIFT-CONTROL checklist doesn't catch this — it asks vision questions. The v4.1 verification tables didn't catch it either — they recorded `✓` or `✗` without naming the source of the verification. v4.2 added the Source column to Gate 2 and Gate 4: every verification row cites the specific live `mcp__github__*` tool call that produced it, and pinned-content recall is explicitly disqualified as a Source. The corresponding "Three surfaces" glossary in `PROJECT-INSTRUCTIONS.md` gave the agent language for the distinction.

The 2026-05-16 prompt-11 drift discovery surfaced a tertiary failure mode: workspace pinned `PROMPT_SEQUENCE.md` described an expanded 5-abstract-base-class adapter framework architecture (citing amendments [D19]-[D25] from memos 04 and 05 which had landed on main); main's `PROMPT_SEQUENCE.md` did NOT have that expansion. A prior Partner session had updated the workspace version after amendments landed but never opened the sidecar PR to push it to main. The 2026-05-16 Partner session ran the v4.2 pre-authorship gate, which correctly verified that every path cited in the draft prompt 11 existed on main, AND correctly flagged Surface 1/2 distinctions on individual file claims — but read the workspace PROMPT_SEQUENCE.md uncritically and authored 11a against main's narrower scope without noticing the workspace described a different architecture. v4.3 adds Gate 0: explicitly compare workspace and main PROMPT_SEQUENCE.md before any other gate runs. The downstream complement is `.github/workflows/constitution-sync.yml` (added in the same 2026-05-16 sidecar) which prevents future constitutional-doc amendments from landing without a PROMPT_SEQUENCE.md cascade.
