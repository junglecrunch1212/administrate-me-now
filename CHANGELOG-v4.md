# CHANGELOG — AdministrateMe-Autonomous

## v4 — 2026-05-14 (after the first end-to-end test drive)

Anchored to `POSTMORTEM-v3-to-v4.md`. Every change below has a section reference there for the root-cause analysis.

### New files

- **POSTMORTEM-v3-to-v4.md** — Comprehensive postmortem of the v3 test drive (10c-iii cycle producing PRs #63-#67). Sections: problems found (§1), solutions tested (§2), solutions found (§3, canonical for v4), process improvements (§4), what stays the same (§5), files changed (§6), open questions for v5 (§7).
- **BRANCH-STRATEGY.md** — Explicit stacked vs parallel branch rules. The v3 implicit "branch from main" caused c4 to re-do c1's work; v4 makes the strategy mandatory in every refactored prompt's `## Branch strategy` section.
- **RUNBOOK-EXECUTOR.md** — Step-by-step invocation runbook with the exact `--model opus --permission-mode bypassPermissions --max-budget-usd N` invocation patterns proven during the v3 test drive. Includes autonomous-mode driver script, eyes-on calibration runs, recovery procedures.
- **DRIFT-CONTROL-CHECKLIST.md** — The six vision-alignment questions (family utility, privacy, ADHD-aware, observation mode, channel-native, multi-member) used by Partner pre-refactor and Reviewer's strict Job 4. Rubric for `vision_drift_score` 0-3.
- **HANDOFF-TEMPLATE.md** — Standard format for session-end docs (replaces ad-hoc v3 docs like SETUP-COMPLETE.md, TEST-DRIVE-REPORT.md). Sections: summary table, PRs in flight, caveats, follow-ups, three-things-learned, suggested next move, time budget actual-vs-estimated.

### Edited files

- **codespace-executor/devcontainer/post-create.sh** — Added pre-flight verdict (GREEN/YELLOW/RED) at end. New steps: §2.5 chown `~/.claude` to current user (fixes silent claude CLI hang per POSTMORTEM §1.1); §4.5 remove broken LFS hooks if git-lfs not installed (fixes && chain breaks per POSTMORTEM §1.4); §4.6 disable VS Code credential.helper for this repo (Executor pushes via explicit URL per POSTMORTEM §1.5); §10 claude CLI smoke test.
- **codespace-executor/devcontainer/devcontainer.json** — Added `git-lfs:1` feature (so LFS hooks work instead of fail). Added container env vars `ADMINME_MODEL_EXECUTOR=opus` and `ADMINME_MODEL_REVIEWER=opus` with explicit "NEVER claude-opus-4-7" comment per POSTMORTEM §1.2.
- **codespace-executor/agents/executor-c1.md** — Rewrote invocation section: `--print --model opus --permission-mode bypassPermissions`. Added pre-flight checks (~/.claude perms, tokens, branch name). Added branch-strategy awareness (stacked vs parallel). Added v4-mandatory commit message format `phase <PROMPT_ID> commit N: <summary>`. Added push pattern (explicit-URL + tracking-config scrub) and curl-based PR creation. Each c-band is now its own PR.
- **codespace-executor/agents/executor-c2.md** — Same v4 invocation header; stacked branch base = c1's branch.
- **codespace-executor/agents/executor-c3.md** — Same; base = c2's branch.
- **codespace-executor/agents/executor-c4.md** — Same; base = c3's branch. Added mandatory pre-flight check verifying stack integrity (`git log main..HEAD` must show ≥3 prior commits) per POSTMORTEM §1.6. Replaced `gh pr create` with curl + GitHub API for headless reliability.
- **codespace-executor/agents/preflight.md** — Fixed `claude-sonnet-4-6` reference and added "NEVER claude-sonnet-4-7" guidance per POSTMORTEM §1.2.
- **codespace-executor/agents/splitter.md** — Same model-name correction.
- **cowork-partner/PROJECT-INSTRUCTIONS.md** — Added §"pre-refactor drift check" mandating the 6-question walk before opening a refactor PR (links to DRIFT-CONTROL-CHECKLIST.md). Added §8.5 "Branch strategy" to the prompt-refactor template (declares STACKED vs PARALLEL). Changed branch name convention from `refactor-<NN>` to `partner/refactor-<NN>` to disambiguate from Executor sidecar branches.
- **routines/post-merge-reviewer.md** — Rewrote Job 4 to STRICT mode with `vision_drift_score` 0-3. Score 3 triggers STRICT-FAIL PR comment and forces `overall_verdict=FAIL_CRITICAL`. Fixed model reference from `claude-opus-4-7` to `claude-opus-4-6`/`opus` per POSTMORTEM §1.2.
- **schemas/reviewer_verdict.json** — Added `vision_drift_score` (int 0-3), `strict_block` (bool), `questions` (per-question yes/no/unsure + evidence), `recommended_action`, `operator_override` to `job_4_vision_check`. Added `REVIEWER_STRICT_VISION_BLOCK` halt_class. Added `reviewer_session` section for bookkeeping (model, elapsed, timestamp). Added `commit_message_format` category to Job 1 findings.
- **01-SETUP.md** — Added v4-patches preamble explaining the changes (chown, model alias, runtime smoke test in Phase 7, runbook-based Phase 8, new Phase 9 handoff doc).
- **02-OPERATE.md** — Appended v4-patches section with canonical Executor invocation, permission-mode discipline table, branch-strategy reference, strict drift control, session handoff doc convention, token discipline.
- **04-HALTS.md** — Appended "Non-halt incidents" section: API 529 retry policy, terminal-rendering glitch fix (F5), Chrome MCP renderer freeze workaround.
- **06-TROUBLESHOOTING.md** — Appended v4 entries: post-create yellow/red diagnostics, claude --print hang (0 CPU), model alias bug, permission-mode denial, push fails despite gh auth, git checkout exit-1 from LFS hook, Reviewer strict-block handling.

### Schema changes (backwards-compatible)

The `schemas/reviewer_verdict.json` change is additive: existing v3 fields preserved; v4 adds `vision_drift_score`, `strict_block`, `questions`, `recommended_action`, `operator_override` to `job_4_vision_check`. A v3 verdict JSON missing the new fields would FAIL validation against v4 — Reviewer v4 must produce the new fields. This is a forward-only schema change (no v3-compat shim).

### Things that did NOT change (intentional preservation)

- Three-role model (Partner, Executor, Reviewer)
- Four-commit decomposition per prompt
- Routine-driven Reviewer architecture
- Constitutional + vision separation; vision still tiebreaks ambiguity
- 14-doc Cowork Partner Project pinning
- `prompts/_active/<NN>-<slug>.md` convention
- `docs/build_log.md` and `docs/partner_handoff.md` roles
- Sandbox + tenant-identity firewall

### Operator follow-ups before next prompt cycle

Per POSTMORTEM §3.1 and the v3 test drive's exposed-token incidents:

1. **Rotate `CLAUDE_CODE_OAUTH_TOKEN`** in Codespaces secrets. The v3 test drive exposed the token value twice. 30-second job: `claude setup-token` on a fresh terminal → update secret value at github.com/settings/codespaces.
2. **Codespaces: Rebuild Container** in the Codespace to pick up the v4 devcontainer.json (git-lfs feature, env vars) and the patched post-create.sh.
3. **Run `bash .devcontainer/post-create.sh` once and confirm GREEN verdict.**
4. **Re-pin the Cowork Partner Project's documents.** The v4 instructions and DRIFT-CONTROL-CHECKLIST.md must be pinned. Easiest: download from `cowork-partner/PROJECT-INSTRUCTIONS.md` (updated) and the new `DRIFT-CONTROL-CHECKLIST.md`, drag into the Project, drop the old.

After these four, you're ready for the next prompt cycle. The next prompt by the sequence is 10d (Checkpoint — pipeline ↔ skill consistency) per `prompts/PROMPT_SEQUENCE.md`.

---

## v3 — 2026-05 (pre-test-drive baseline)

First version that shipped end-to-end via the Cowork-driven setup. v3 deliverables: setup manual, executor agent set, Cowork Partner Project instructions, 4 routine configs, JSON schemas, validation script, troubleshooting guide.

v3 was sound architecturally but had several silent-failure modes that surfaced only in the first real end-to-end test (2026-05-14, prompt 10c-iii cycle). v4 fixes those without changing the architecture.

## v2 and earlier

Pre-Cowork setup. Manual operator-driven Partner sessions in Claude Chat, Executor in JRS Codespace. Required the operator to be the courier between Partner Chat and Executor Codespace. v3 made Cowork the Partner and freed the operator to supervise rather than courier.
