# PARTNER-PRE-AUTHORSHIP-CHECKLIST.md

**v4.1 — the strict authorship-time gate. Run this BEFORE writing a single line of any refactored prompt's body.**

This checklist exists because the v3 test drive surfaced the hardest-to-catch Partner failure mode: producing a confident-sounding refactored prompt whose Read-first section cites files, symbols, and contracts that do not exist on `main`. The DRIFT-CONTROL checklist catches vision drift. The §2.9 size budget catches execution-capacity blowouts. Neither catches *hallucinated codebase citations*. This checklist does.

This is operationally distinct from the harness's PreFlight agent. PreFlight runs **after** Partner authors and **before** Executor commits. This checklist runs **before** Partner authors. PreFlight is a downstream gate; this is the upstream gate. Both fire on the same defect class. Both must pass.

---

## When this runs

After the orientation report has been confirmed by the operator (per `SESSION-1-ORIENTATION-PROTOCOL.md`), and BEFORE producing any content in §2 Read first / §3 Reconciliation notes / §4 Assumptions to verify / §7 Deliverables of the refactored prompt.

If a draft already exists from a prior session (rare; usually means a previous Partner session halted mid-authorship), run this checklist against the existing draft's §2 Read first list before resuming.

---

## The five gates

Every gate is mechanical. Every gate produces an artifact the operator can audit. Halt on any miss; do NOT proceed to authorship.

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

Produce a verification table:

```
| Path or symbol | Verified? | Notes |
|---|---|---|
| `adminme/pipelines/base.py` | ✓ | exists |
| `adminme/pipelines/proactive.py` | ✗ | NOT on main; closest match is `adminme/pipelines/base.py` |
| `adminme/events/schemas/domain.py::DigestComposedV1` | ✓ | found at line 211 |
| `adminme/events/schemas/proactive.py::ParalysisNudgeComposedV1` | ✗ | file does not exist; real class is `domain.py::ParalysisTriggeredV1` |
```

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

For each cross-checked item, add a row to a contracts table:

```
| Cited contract | On-main reality | Match? |
|---|---|---|
| Subclass `ProactivePipeline` | Class `MorningDigestPipeline` in `packs/pipelines/morning_digest/handler.py` does not inherit from any base; on-main pattern is standalone class. | ✗ |
| `ctx.commitments_conn_factory` | `PipelineContext` (`adminme/pipelines/base.py:56`) has only `parties_conn_factory: Callable[...] \| None = None`. | ✗ |
| `outbound_fn` parameter | `PipelineContext` has `outbound_fn: Callable[..., Awaitable[Any]]` (`adminme/pipelines/base.py:80`). | ✓ |
```

Halt on any `✗` per Gate 3.

### Gate 5 — Produce the verification artifact

Before authoring, produce two artifacts inline (no PR yet):

1. **Verification table** (Gate 2 output)
2. **Contracts table** (Gate 4 output)

If both are 100% `✓`, declare:

> **Pre-authorship gate: PASS.** All N file paths verified on main; all M cited contracts confirmed against on-main signatures. Proceeding to author `prompts/_active/<NN>-<slug>.md`.

If either has any `✗`, do not declare PASS. Produce the Findings list and halt.

The operator reviews the verification + contracts tables in the chat before authorship begins. This is the eyes-on moment that catches stale citations before they cost a session.

---

## What this does not check

This checklist is necessary, not sufficient. It does not catch:

- **Vision drift.** Use `DRIFT-CONTROL-CHECKLIST.md` pre-PR and Reviewer Job 4 post-merge.
- **Size-envelope blowouts.** Use the §2.9 delivery-gate self-check (line count, per-commit estimate, files-to-read per commit, inline code, single-cycle judgment, autolinker grep) before declaring the refactor final.
- **Branch-strategy mistakes.** Use `BRANCH-STRATEGY.md` and the prompt's §8.5 declaration.
- **Constitutional citation correctness.** Use the PreFlight agent's Check 3 (citation validity) — PreFlight runs after authorship and verifies `[§N]`, `[DN]`, `[arch §N]` ranges against the current SYSTEM_INVARIANTS / DECISIONS / architecture-summary on main.

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

## Why this exists (one paragraph)

The 10c-iii v4 test drive of 2026-05-14 produced a refactored prompt whose §2 Read first cited at least eight file paths that did not exist on `main` (`adminme/pipelines/proactive.py`, `adminme/outbound/dispatcher.py`, `adminme/lib/skill_runner/openclaw_client.py`, `adminme/projections/calendar/queries.py`, `adminme/projections/members/queries.py`, five query functions that do not exist in `adminme/`), and asserted a `ProactivePipeline` base class that is fictional (the on-main `morning_digest` handler is a standalone class with no base). The Cowork session passed its DRIFT-CONTROL checklist 6/6 (score 0=aligned) and produced an orientation report claiming "all 14 pinned docs read." Neither catch caught the structural defect. The session correctly halted at Phase A push due to the unrelated MCP-unavailability issue; had the MCP been connected and the prompt pushed, Executor would have spent its session window fabricating the missing infrastructure (the c4 mis-labeling pattern from POSTMORTEM §1.6) or halting on the first missing-file read. This checklist closes the gap.
