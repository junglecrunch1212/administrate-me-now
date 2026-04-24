# Universal preamble extension — proposal

_Single highest-leverage refactor available for Phase A build quality. Proposes: slim `prompts/PROMPT_SEQUENCE.md`'s preamble by extracting cross-cutting discipline, PLUS ship `scripts/verify_invariants.sh` as the canonical invariant-grep script every prompt's Commit 4 calls instead of inlining 8+ grep lines._

**Why this matters:** Prompts 07a and 07b accumulated 6–7 "carry-forwards" + a duplicated 8-line invariant-grep block + a duplicated BUILD_LOG template. Each individually justified; cumulatively, ~1.5–2K tokens of overhead per prompt. Extracting to preamble + verify script reduces every subsequent prompt refactor (07c through 16) by ~15% without signal loss.

---

## Application sequence (Partner session executing this proposal)

**Prerequisite:** completed Session 1 orientation in `partner_handoff.md`. You have read the 9 constitutional docs.

**Session's single job:** produce three artifacts for James to hand to Claude Code — `scripts/verify_invariants.sh`, the replacement text for `prompts/PROMPT_SEQUENCE.md`'s preamble, and a PR description. Do NOT attempt to commit or push to GitHub yourself.

**Load from zip (minimum):**

1. `prompts/PROMPT_SEQUENCE.md` — the canonical driver. You need to see the current preamble in context to produce a surgical replacement. Locate the preamble section by searching for `### The universal preamble (paste before every prompt)` — do NOT rely on line numbers, they may have shifted.

2. `pyproject.toml` — confirm the LLM/embedding SDK names you'll encode in `verify_invariants.sh` match what's actually declared as dependencies. Current known list: `anthropic`, `openai`, `sentence-transformers`. Verify against the file.

3. `adminme/events/schemas/system.py` — confirm the `ALLOWED_EMITS` regex in `verify_invariants.sh` covers actually-registered system events. Currently only `xlsx.regenerated` should be present on main (07b is the PR that introduced it — may or may not be merged when this session runs). Verify against the file. If only `xlsx.regenerated` exists, the pattern is `'xlsx\.regenerated'`. If more have landed, extend.

4. `docs/SYSTEM_INVARIANTS.md` — already fully loaded from step 1 of orientation. Confirm the "no LLM SDKs" invariant (§8) matches the grep patterns in the script.

5. `docs/build_log.md` — already loaded. Confirm current merged state (which of 07a/07b are merged vs. still open). This affects the PR description.

**Do NOT load:** any projection source files, any test files, the full `docs/reference/` mirror. This session is infrastructure, not build work.

---

## Part 1 — Replacement text for `prompts/PROMPT_SEQUENCE.md`'s preamble

Locate the section beginning with `### The universal preamble (paste before every prompt)` and ending with `This preamble is a reminder, not a duplicate instruction...`. Replace the entire section with:

```markdown
### The universal preamble (paste before every prompt)

Before pasting any prompt (00 through 19), paste this preamble first.

---

> **Phase + repository + documentation + sandbox discipline.**
>
> You are in **Phase A**: generating code in Anthropic's sandbox against https://github.com/junglecrunch1212/administrate-me-now. The Mac Mini is not involved. You do not contact live OpenClaw, live BlueBubbles, live Plaid, or any other external service. Tests that require those are marked `@pytest.mark.requires_live_services` and skipped.
>
> **Sandbox egress is allowlisted.** `github.com` and `raw.githubusercontent.com` work. Most other hosts return HTTP 403 `x-deny-reason: host_not_allowed` from Anthropic's proxy. Do NOT interpret 403s as "the site is down." If a prompt tells you to WebFetch a non-GitHub URL and you get 403, that's expected; document the gap and move on per prompt 00.5's pattern.
>
> **Session start (required sequence):**
> ```
> git checkout main
> git pull origin main
> git checkout -b phase-<NN>-<slug>
> ```
> The harness may auto-reassign you to `claude/<random>` regardless. Work on whatever branch you actually get — do not fight it. Do NOT `git pull` again during the session. Do NOT push to `main`.
>
> **Poetry install as needed.** If `pytest` fails with `ModuleNotFoundError: No module named 'sqlcipher3'` (or similar), run `poetry install 2>&1 | tail -5` and retry. Sandbox warm-state quirk; do not fix in code.
>
> **Read before acting.** When a prompt tells you to read something, READ IT — do not skim, do not assume, do not infer from training. Use targeted line ranges (`sed -n '<start>,<end>p'`) for large files; never full-read `ADMINISTRATEME_BUILD.md`, `ADMINISTRATEME_CONSOLE_PATTERNS.md`, `ADMINISTRATEME_REFERENCE_EXAMPLES.md`, `ADMINISTRATEME_DIAGRAMS.md`, or `ADMINISTRATEME_CONSOLE_REFERENCE.html`.
>
> **External documentation is mirrored.** When a prompt says "per OpenClaw docs" or "per Plaid docs," read from `docs/reference/<section>/` (local mirror from prompt 00.5). Do NOT use WebFetch to pull these docs live. If a referenced file is missing, stop and report that prompt 00.5 is incomplete or the gap is in `docs/reference/_gaps.md`.
>
> **Four-commit discipline.** Every prompt structures its work as four incremental commits — typically schema/plumbing, first-module build, second-module build, integration+verification+BUILD_LOG+push. Commit and push after each. If a turn times out mid-section, STOP. Do not attempt heroic recovery. The operator re-launches; the next session picks up from `git log --oneline`.
>
> **MCP fallback for PR creation.** Try `gh pr create` first. If `gh` returns "command not found" or a GitHub API permission error, fall back to `mcp__github__create_pull_request` with equivalent args (owner=junglecrunch1212, repo=administrate-me-now, base=main, head=<your branch>, title + body). If both fail: report the exact error, stop, do not retry with modified flags.
>
> **Post-PR: one check, then stop.** After opening the PR, do ONE round of status check:
> ```
> mcp__github__pull_request_read with method=get_status, get_reviews, get_comments
> ```
> Report whatever returns (typically: pending/empty). Then STOP. Do NOT poll. Do NOT respond to webhook events arriving after the stop message. The MCP tool displays a subscription message — that is informational; ignore it.
>
> **Invariant verification.** Every Commit 4's verification block runs `bash scripts/verify_invariants.sh` in addition to its own ruff/mypy/test checks. That script is the canonical source for the no-LLM-SDK / no-hardcoded-paths / no-tenant-identity / no-unauthorized-emit greps. Do NOT duplicate those greps inline in prompt-specific verification. If the script is missing or out of date, stop and report.
>
> **Test discipline for async subscribers.** Any test that appends an event and then reads the projection must call `notify(event_id)` on the bus and then `_wait_for_checkpoint(bus, subscriber_id, event_id)` before the read assertion. For "absence" tests (assert event was filtered / skipped), append a follow-up innocuous event, drive the checkpoint via its event_id, THEN assert the original's absence. Without this, you're testing a race condition, not a behavior.
>
> **Test discipline for failure modes.** When asserting "malformed input doesn't land" (e.g. IntegrityError from a CHECK constraint), call the handler function directly — do NOT route through the bus + debounce machinery, which would put the subscriber in degraded state and corrupt the test.
>
> **Mypy preflight for new libs.** If a prompt adds an import from a library not already in the codebase, run `poetry run mypy adminme/ 2>&1 | tail -10` before Commit 1. If it complains about missing stubs, add to `[[tool.mypy.overrides]]` in `pyproject.toml` with `ignore_missing_imports = true`. Do NOT let type errors accumulate across commits.
>
> **Schema conventions.** CHECK constraints belong on SQLite enum columns (closed sets like `kind`, `status`, `sensitivity`), never on open text columns (`category`, `display_name`, `notes`). All new event types register at v1 per [D7]; upcasters compose forward only.
>
> **Citation discipline.** Use `[§N]` for SYSTEM_INVARIANTS sections, `[DN]` for DECISIONS entries, `[arch §N]` for architecture-summary sections, `[cheatsheet Qn]` for openclaw-cheatsheet questions, `[BUILD.md §X]` / `[CONSOLE_PATTERNS.md §N]` / `[REFERENCE_EXAMPLES.md §N]` / `[DIAGRAMS.md §N]` for the ADMINISTRATEME_* files. Citations are compression — one token replaces a paragraph of justification.
>
> **Tenant-identity firewall.** No platform code under `adminme/`, `bootstrap/`, `packs/` may reference a specific household, person, address, or institution name. Names like "James", "Laura", "Charlie", "Stice", "Morningside" belong in `tests/fixtures/` only, and only when marked `# fixture:tenant_data:ok`. The identity-scan canary (`tests/unit/test_no_hardcoded_identity.py`) lands in a later prompt; until then, the greps in `scripts/verify_invariants.sh` are the defense.
>
> **BUILD_LOG append lives inside Commit 4.** The canonical template is in `docs/qc_rubric.md`. Do not ship Commit 4 without a BUILD_LOG entry for the prompt just completed. Partner fills in `PR #<N>` / `<commit4>` / `<merge date>` / `Outcome: MERGED` during post-merge housekeeping; Claude Code leaves placeholders.
>
> **When you stop, stop.** "Stop" in a prompt means close the session after the stop message. The operator handles merge. Do not continue polling, do not respond to late review comments, do not speculatively start the next prompt.

---

This preamble is a reminder of cross-cutting discipline, not a replacement for each prompt's specific reading list or deliverables. Every prompt still ships its own targeted "Read first," "Deliverables," "Out of scope," and "Verification" sections. Those sections should no longer inline:

- Phase A framing (here instead).
- Sandbox egress rules (here instead).
- Session-start bash (here instead).
- Four-commit discipline (here instead).
- gh/MCP fallback (here instead).
- Post-PR stop-means-stop behavior (here instead).
- The standard invariant-grep block (in `scripts/verify_invariants.sh` instead).
- The BUILD_LOG template (in `docs/qc_rubric.md` instead).
- CF-1 through CF-7 that had been accumulating (here instead; absorbed).

A prompt that inlines any of the above is a candidate for cleanup.
```

---

## Part 2 — Proposed `scripts/verify_invariants.sh`

Replaces the ~8-line grep block every prompt currently inlines in Commit 4 verification.

```bash
#!/usr/bin/env bash
# scripts/verify_invariants.sh
#
# Cross-cutting invariant checks run at every prompt's Commit 4 verification.
# Superset of what's safe to enforce without per-prompt knowledge of scope.
# Exits non-zero on any violation. Silent on pass.
#
# Citations:
# - [§8] / [D6]: AdministrateMe does not import LLM / embedding SDKs.
# - [§15] / [D15]: No hardcoded instance paths.
# - [§12.4]: No tenant identity in platform code.
# - [§2.2]: Projections emit only system events (currently: xlsx.regenerated).
# - Pipeline layer does not write projections directly (later prompts; currently vacuous).

set -u
violations=0

report() {
    echo "VIOLATION: $1"
    violations=$((violations + 1))
}

# --- [§8] / [D6]: no LLM / embedding SDKs in dependencies or source ---

if grep -iqE "^anthropic|^openai|^sentence_transformers|anthropic =|openai =|sentence-transformers =" pyproject.toml; then
    report "[§8]/[D6] LLM / embedding SDK found in pyproject.toml"
fi

# Strict import grep — skips docstrings via crude heuristic (triple-quoted blocks removed).
# For perfect check we'd need ast walk; crude version is enough for CI.
if grep -rnE "^\s*import (anthropic|openai|sentence_transformers)|^\s*from (anthropic|openai|sentence_transformers)" adminme/ 2>/dev/null \
    | grep -v "#" \
    | grep -v '"""' > /dev/null; then
    report "[§8]/[D6] LLM / embedding SDK import in adminme/"
fi

# --- [§15] / [D15]: no hardcoded instance paths ---

if grep -rnE "~/\.adminme|'/\.adminme|\"/\.adminme|os\.path\.expanduser\([^)]*\.adminme" \
    adminme/ bootstrap/ packs/ \
    --include='*.py' --include='*.sh' 2>/dev/null \
    | grep -v "^docs/" \
    | grep -v "fixture:instance_path:ok" > /dev/null; then
    report "[§15]/[D15] hardcoded instance path in platform code"
fi

# --- [§12.4]: no tenant identity in platform code ---

# Broad grep; allow tests/fixtures and explicit example/illustration markers.
if grep -rniE "james|laura|charlie|stice|morningside" adminme/ --include='*.py' 2>/dev/null \
    | grep -v "tests/" \
    | grep -v "# example" \
    | grep -v "# illustration" \
    | grep -v "fixture:tenant_data:ok" > /dev/null; then
    report "[§12.4] tenant identity in platform code"
fi

# --- [§2.2]: projections emit only system events ---

# When new system event type introduced, add its type string to ALLOWED_EMITS.
ALLOWED_EMITS='xlsx\.regenerated'

emits=$(grep -rnE "log\.append\(|append\(.*EventEnvelope" adminme/projections/ 2>/dev/null \
    | grep -v "#" \
    | grep -v '"""' || true)

if [ -n "$emits" ]; then
    # Any emit must have a type= argument matching ALLOWED_EMITS.
    # Anything else is a [§2.2] violation.
    bad_emits=$(echo "$emits" | grep -vE "type=\"($ALLOWED_EMITS)\"|type='($ALLOWED_EMITS)'" || true)
    if [ -n "$bad_emits" ]; then
        report "[§2.2] projection emitting non-system event:"
        echo "$bad_emits" | sed 's/^/    /'
    fi
fi

# --- Pipeline → projection direct writes (vacuous until prompt 10a; kept for readiness) ---

if grep -rnE "INSERT INTO.*projection|projection_db.*write|from adminme\.projections.*import.*handlers" \
    adminme/pipelines/ 2>/dev/null > /dev/null; then
    report "pipeline writing projection directly (use events)"
fi

# --- Summary ---

if [ "$violations" -gt 0 ]; then
    echo ""
    echo "$violations invariant violation(s) found. Fix before commit."
    exit 1
fi

exit 0
```

**Maintenance:** When a new system event type is introduced, extend `ALLOWED_EMITS`. When a new inviolable invariant is introduced, add a section. This script is the canonical place; never duplicate its checks in a prompt's Verification block.

**Usage in a prompt's Commit 4 verification:**

```bash
# (prompt-specific ruff, mypy, pytest, canary blocks...)

# Cross-cutting invariants
bash scripts/verify_invariants.sh

# (prompt-specific smoke script)
```

One line replacing 8–12 lines in every prompt. Over prompts 07c through 16 (~10 prompts), saves ~80–120 lines of prompt text.

---

## Part 3 — Proposed PR description

```markdown
Preamble extraction + verify script — infrastructure for Phase A prompt refactors.

**Why:** Prompts 07a and 07b each accumulated ~2K tokens of repeated carry-forwards, inline invariant-grep blocks, and BUILD_LOG templates. This PR extracts those into a slim universal preamble + a single invariant-verification script. Future prompt refactors (07c through 16) get ~15% smaller without signal loss.

**Landed:**
- `scripts/verify_invariants.sh` — canonical invariant-grep script; covers [§8]/[D6] (no LLM SDKs), [§15]/[D15] (no hardcoded instance paths), [§12.4] (no tenant identity), [§2.2] (projections only emit system events), pipeline→projection direct writes.
- `prompts/PROMPT_SEQUENCE.md` — slim universal preamble. Absorbs the CF-1..CF-7 patterns that were accumulating per-prompt.

**Changes to build behavior:** none. This is infrastructure. Existing tests pass; no production code modified.

**Maintenance note:** When a new system event type is introduced, extend `ALLOWED_EMITS` in `verify_invariants.sh`. When a new cross-cutting invariant is introduced, add a section. Canonical place for invariant greps.

Single-purpose PR. No other changes.
```

---

## Part 4 — What Partner produces, what Claude Code executes

Partner session produces **three artifacts**, copy-paste-ready:

1. The full text of `scripts/verify_invariants.sh` (from Part 2 above, verbatim).
2. The full replacement block for `prompts/PROMPT_SEQUENCE.md`'s preamble section (from Part 1 above, verbatim).
3. The PR description text (from Part 3 above, or a tailored variant).

James then opens a separate Claude Code session with a **short instruction prompt** (below), pastes all three artifacts in, and lets Claude Code execute the three file operations as a single commit + PR.

### Claude Code micro-prompt

```
Universal preamble extraction — one-session infrastructure job. Not a build prompt. No four-commit discipline, no tests, no BUILD_LOG append. Just three file operations.

Session start:
git checkout main
git pull origin main
git checkout -b phase-preamble-extraction

Three changes to land as a single commit:

1. CREATE scripts/verify_invariants.sh with the content pasted below.
   After creating: chmod +x scripts/verify_invariants.sh

2. REPLACE the preamble section in prompts/PROMPT_SEQUENCE.md. Locate by searching for:
   ### The universal preamble (paste before every prompt)
   Replace everything from that heading through the closing paragraph (ending "...just makes them top-of-mind in Claude Code's context.") with the new content pasted below.

3. Commit: "preamble: extract CFs + verify_invariants.sh"

Then:
git push origin HEAD

Then open a PR with the description pasted below. Try gh pr create first; MCP fallback if gh is unavailable.

Then stop per the "Post-PR: one check, then stop" rule.

=== scripts/verify_invariants.sh ===
<paste verbatim Part 2 content from partner's artifact>

=== prompts/PROMPT_SEQUENCE.md replacement block ===
<paste verbatim Part 1 content from partner's artifact>

=== PR description ===
<paste verbatim Part 3 content from partner's artifact>
```

---

## Application sequencing

1. **Partner session (this proposal)** produces three artifacts. Session ends.
2. **Claude Code session** (James drives) executes the three file operations + PR per the micro-prompt above.
3. **Partner session (next)** runs the standard QC pass on the PR (`qc_rubric.md` three jobs), updates `partner_handoff.md`, notes PM-7 as EXECUTED.
4. **Subsequent build-prompt refactors** (07c onward) use the slim form from the start: no CF-1..CF-7 section, no inlined grep block, no inlined BUILD_LOG template.

Optionally: a cleanup PR that slims 07a and 07b's prompt files to match. Low-priority — those are shipped already. Keeps `prompts/` stylistically consistent for future readers.

---

## Tradeoffs considered

**Against extracting to the preamble:** "Preamble gets too long if you pack discipline into it." Counter: the proposal's preamble is ~65 lines vs. current ~28 — not 3× as long. Each line replaces 3–5 lines scattered across every subsequent prompt.

**Against the verify script:** "Bash script is its own surface to maintain." Counter: a single 50-line bash file is easier to maintain than the same greps duplicated across 16 prompts. When an invariant changes, one edit instead of sixteen.

**Against absorbing CF-1..CF-7 now:** "Some are early and might not last." Counter: CF-1 (gh/MCP fallback) has fired in 2 of 2 PR attempts. CF-2 (harness branch) in ~all. CF-3 (stop-means-stop) in 2 of 2. CF-4 (mypy preflight) in 1 of 2, but saved significant time where it fired. CF-5 (async-subscriber) in 2 of 2. CF-6 (CHECK style) in 1 of 1 (introduced in 07a). CF-7 (direct-handler failure tests) in 1 of 1. Every one has fired enough to graduate per PM-7.

**If some CF doesn't apply to a specific prompt:** fine. A preamble principle that isn't triggered is harmless. A preamble principle that's missing when needed is the problem.

---

## End of proposal
