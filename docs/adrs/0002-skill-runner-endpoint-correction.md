# ADR-0002: Skill-runner endpoint correction — `/skills/invoke` → `/tools/invoke` with `tool: "llm-task"`

**Date:** 2026-04-25
**Status:** ACCEPTED
**Authored by:** Partner (Claude Chat), session N (originally scheduled to refactor prompt 09a; pivoted to corrections-first when depth-read against `docs/reference/openclaw/` surfaced contract drift)
**Supersedes:** the implicit endpoint contract in `ADMINISTRATEME_BUILD.md` §L4-continued line 1317, `ADMINISTRATEME_REFERENCE_EXAMPLES.md` line 1034, `docs/SYSTEM_INVARIANTS.md` §7.4 (invariant 4)
**Related:** ADR-0001 (use OpenClaw as substrate)

## Context

`ADMINISTRATEME_BUILD.md` §L4-continued (the canonical wrapper-flow contract for the AdministrateMe skill-runner module), `ADMINISTRATEME_REFERENCE_EXAMPLES.md` §3 (the canonical worked skill-pack example), and **`docs/SYSTEM_INVARIANTS.md` §7.4** (binding invariant 4 of §7) all specify that the wrapper invokes OpenClaw at `POST http://127.0.0.1:18789/skills/invoke` with body `{skill_name, inputs, correlation_id, session_context, dmScope}`. Two already-merged code artifacts (`adminme/lib/skill_runner/wrapper.py` stub docstring landed in prompt 02, `adminme/pipelines/runner.py` comment landed in prompt 10a's predecessor scaffolding) repeat the same endpoint string.

When prompt 00.5 mirrored `docs/reference/openclaw/` from upstream OpenClaw on 2026-04-22 (`docs/reference/_status.md` confirms 437 files, GitHub-cloned, fully synced), the assumption became checkable. It does not check out. Exhaustive search across the OpenClaw mirror finds:

- **Zero references** to `/skills/invoke`, `skills.invoke`, `skill runner`, `skill execution`, or `skill invocation`.
- The authoritative HTTP-endpoint catalog (`gateway/index.md` line 88, `gateway/security/index.md` line 981, `gateway/tailscale.md` line 48) lists exactly: `/v1/*` (OpenAI-compatible), `/tools/invoke`, `/api/channels/*`, `/hooks/agent`. Skills are not an HTTP-callable category.
- Skills are **filesystem packs** loaded into the agent's tool/command registry at boot per `tools/skills.md`. The canonical "structured-output JSON LLM call from a workflow engine" tool is **`llm-task`** per `tools/llm-task.md`, invoked through `POST /tools/invoke`.

The drift was introduced in the BUILD.md spec before the OpenClaw mirror existed. Subsequent files (REFERENCE_EXAMPLES, SYSTEM_INVARIANTS §7.4, the merged stub docstring, the merged pipeline-runner comment, and prompts 01b/01c/09a) inherited the error. Prompt 09a is about to ship production code against the stale contract; this ADR pre-corrects.

## Decision

The AdministrateMe skill-runner wrapper invokes OpenClaw via the documented gateway HTTP API:
POST http://127.0.0.1:18789/tools/invoke
Authorization: Bearer <gateway-token>
Content-Type: application/json
{
"tool": "llm-task",
"action": "json",
"args": {
"prompt": "<rendered SKILL.md body>",
"input": { /* caller's typed inputs / },
"schema": { / contents of output.schema.json */ },
"provider": "<provider portion of provider_preferences[i]>",
"model":    "<model portion of provider_preferences[i]>",
"thinking": "<optional reasoning preset>",
"maxTokens": <skill manifest max_tokens>,
"timeoutMs": <skill manifest timeout_seconds * 1000>,
"temperature": <skill manifest temperature>
},
"sessionKey": "<derived from caller's dmScope>",
"dryRun": false
}

The wrapper translates AdministrateMe's `SKILL.md` pack metadata into `llm-task` tool args at call time. Provider-fallback iteration over the manifest's `provider_preferences` list happens inside the wrapper (one `/tools/invoke` call per provider attempted, in order, until one succeeds or the list is exhausted) — `/tools/invoke` itself is single-provider per call.

Provenance fields recorded on the `skill.call.recorded` event:

| Field | Source |
|---|---|
| `skill_name`, `skill_version` | from caller's `skill_id` + manifest |
| `openclaw_invocation_id` | from `/tools/invoke` response (if present in response envelope) |
| `provider`, `model` | from the chosen `provider_preferences[i]` |
| `tokens_in`, `tokens_out`, `cost_usd` | from `/tools/invoke` response if exposed; `None` otherwise (graceful degradation) |
| `duration_ms` | wall-clock at the wrapper |
| `correlation_id` | from caller's `ctx` |
| `inputs`, `output` | as called / as returned (size-capped per BUILD.md §L4) |

A response envelope shape that lacks `tokens_in` / `tokens_out` / `cost_usd` does NOT cause `skill.call.recorded` to be skipped — the event is recorded with `None` for the absent fields. This is deliberate: an event-sourced system needs the call recorded; cost reconciliation can be reconstructed later from OpenClaw's own audit log if needed.

## Why this resolution and not the alternatives

- **Option B — write the wrapper against `/skills/invoke` and assume a future OpenClaw extension will add it.** Rejected. No such extension exists in the mirror, in upstream OpenClaw, or in any AdministrateMe plan. Inventing API surface that nobody has committed to is the kind of speculative coupling this codebase consistently avoids (cf. `[D14]`, `[D15]` on path discipline).
- **Option C — sidecar-class correction (a 15-min fix).** Rejected on second sweep. The drift spans `docs/SYSTEM_INVARIANTS.md` §7.4, which is a binding invariant the QC rubric tests against (§7 is invariant-graded ground). Editing an invariant is constitutional, not housekeeping. ADR is the right vehicle.

## Consequences

**Immediate (the corrections PR ships these):**

- `docs/SYSTEM_INVARIANTS.md` §7.4 invariant 4 endpoint string corrected; rationale line added below.
- `ADMINISTRATEME_BUILD.md` §L4-continued line 1317 step 5 rewritten to specify the `/tools/invoke` + `llm-task` translation.
- `ADMINISTRATEME_REFERENCE_EXAMPLES.md` line 1034 corrected.
- `docs/architecture-summary.md` lines 17 and 110 corrected (both the L4 summary and the detailed wrapper-flow paragraph reference the corrected endpoint).
- `docs/adrs/0001-use-openclaw-as-substrate.md` line 48 corrected (Skills seam description); ADR-0001's status remains ACCEPTED, with ADR-0002 refining the specific seam contract per the parenthetical pointer added inline. ADR supersession is partial — ADR-0001's substrate decision stands; only its stale endpoint claim is corrected.
- `pyproject.toml` line 4 comment corrected.
- `adminme/lib/skill_runner/wrapper.py` line 7 stub docstring corrected. Stub stays a stub.
- `adminme/pipelines/runner.py` lines 14–17 comment corrected.
- `prompts/09a-skill-runner.md` lines 14, 23, and 79 corrected (pre-correcting the unrefactored draft so the next session's refactor builds on a clean base).
- This ADR (`docs/adrs/0002-skill-runner-endpoint-correction.md`) added.

The original task memo specified six artifacts. Verification surfaced four additional sites (`docs/architecture-summary.md` ×2, `docs/adrs/0001-use-openclaw-as-substrate.md`, `pyproject.toml`) that also encoded the stale string. The expanded edit list reflects the actual scope of the drift, not the task memo's understated count. This is a worked example of a Partner failure mode — under-counting a corrections PR's scope when drafting the memo — and is recorded in PM-19 (forthcoming) for future sessions.

**Out of scope (deliberately not touched):**

- `prompts/prompt-01b-architecture-summary.md` line 237, `prompts/prompt-01c-system-invariants.md` line 338, `docs/2026-04-25-prompt-08-split.md` line 120 — sealed historical artifacts. Modifying them would falsify the historical record. Future readers should consult the corrected `docs/SYSTEM_INVARIANTS.md` for the current contract.
- `docs/partner_handoff.md` line 128 — Partner-state forward-looking task description, not an architecture claim. Per `E-session-protocol.md`'s single-purpose-PR rule, `partner_handoff.md` is updated only in `partner-state-<YYYY-MM-DD>` PRs at session close, never mixed with corrections-PR scope. The next Partner session updates this line as part of the standard handoff refresh.
- `prompts/d02-openclaw-invocation-shape-mismatch.md` — diagnostic file. Its content (handling of unexpected response shapes) is forward-compatible with the corrected contract. The `curl http://127.0.0.1:18789/docs` line in d02 references the gateway port (correct) and the `/docs` path (a hypothetical live-API-docs endpoint OpenClaw may or may not expose; the diagnostic is conditional — "if the gateway exposes them"). No change needed.
- `ADMINISTRATEME_BUILD.md` lines 166, 261, 328, 2019, 2173 — these reference port 18789 as the gateway port (correct).
- `ADMINISTRATEME_DIAGRAMS.md`, `ADMINISTRATEME_CONSOLE_PATTERNS.md`, `prompts/00-preflight.md`, `prompts/14c-console-views-secondary.md`, `prompts/19-phase-b-smoke-test.md`, `docs/preflight-report.md` — references to `:18789` here concern the gateway port or the `/agent/chat/stream` SSE endpoint or `/health`. None reference `/skills/invoke`.

**Downstream:**

- Prompt 09a's refactor (next Partner session) consumes the corrected stub + corrected BUILD.md §L4 + corrected SYSTEM_INVARIANTS §7.4 + the new "translate skill-pack manifest → `llm-task` args" requirement. Estimated refactored prompt size: 180–230 lines (well inside the 350-line budget). No prompt-sequence changes needed downstream of 09a — `run_skill(skill_id, inputs, ctx) -> SkillResult` is unchanged at the wrapper's public surface; only its internal HTTP body changes.
- `scripts/verify_invariants.sh` does NOT need an update for this ADR. The invariant logic (no LLM SDK imports in `adminme/`, all skill calls through `run_skill()`) is unchanged. Only the specific endpoint string the wrapper POSTs to changes — the script does not test the endpoint string.

## Verification

A grep for `skills/invoke` across the repo (excluding `docs/reference/openclaw/`, sealed prompts, the 08-split memo, this ADR file, and `docs/partner_handoff.md` per the out-of-scope rationale above) must return zero hits after the corrections PR merges. A grep for `tools/invoke` must show ten artifact-files corrected (two-site files counted once each): `docs/SYSTEM_INVARIANTS.md`, `ADMINISTRATEME_BUILD.md`, `ADMINISTRATEME_REFERENCE_EXAMPLES.md`, `docs/architecture-summary.md`, `docs/adrs/0001-use-openclaw-as-substrate.md`, `pyproject.toml`, `adminme/lib/skill_runner/wrapper.py`, `adminme/pipelines/runner.py`, `prompts/09a-skill-runner.md`, plus this ADR.

## References

- `docs/reference/openclaw/gateway/tools-invoke-http-api.md` — full HTTP contract for `/tools/invoke`.
- `docs/reference/openclaw/tools/llm-task.md` — `llm-task` tool params and response shape.
- `docs/reference/openclaw/gateway/index.md` line 88 — authoritative HTTP-endpoint catalog.
- `docs/reference/openclaw/gateway/security/index.md` lines 981, 992 — `/tools/invoke` auth + scope semantics.
- `docs/reference/_status.md` (2026-04-22) — confirms the OpenClaw mirror is fully synced.
- ADR-0001 — establishes OpenClaw as the substrate; this ADR refines how AdministrateMe talks to it.
