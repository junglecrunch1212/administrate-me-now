# Prompt 19: Phase B smoke test (build the script Claude Code writes; the operator runs it)

**Phase:** Phase A prompt that produces a Phase B deliverable. Claude Code writes the smoke test script; the operator runs it on the Mac Mini after bootstrap completes.
**Depends on:** Prompt 18.
**Estimated duration:** 1 hour to write; operator's run time depends on their setup.
**Stop condition:** `scripts/phase-b-smoke-test.sh` exists in the repo, is executable, and Claude Code has validated it runs cleanly against the Phase A lab-mode instance (proving the happy path). The README chapter in `docs/PHASE_B_SMOKE_TEST.md` tells the operator what to do and how to read results.

---

## Read first (required)

1. `ADMINISTRATEME_BUILD.md` **"SAMPLE INSTANCE — WHAT `~/.adminme/` LOOKS LIKE AFTER BOOTSTRAP"** — the target post-bootstrap state.
2. `ADMINISTRATEME_FIELD_MANUAL.md` chapter 4 — what the operator did to set up. Chapter 8 — what they do when things go wrong.
3. The prompt 18 test — `tests/integration/test_full_stack.py`. Much of the smoke test is a shell-script adaptation of that test, pointing at the **live** Phase B instance instead of lab-mode mocks.

## Operating context

The operator has just finished running `./bootstrap/install.sh` on the Mac Mini. The bootstrap wizard said everything completed. Before the operator trusts the instance with the family, they run this smoke test. It walks through every layer of the stack using **real** components (real OpenClaw, real BlueBubbles, real Plaid sandbox or real institutions depending on config), emitting live events and checking the results.

Unlike prompt 18's integration test (which runs in CI with mocks), this smoke test runs exactly once per new deploy and is operator-driven.

Your job in this prompt (Phase A) is to write the script, verify it's syntactically correct, and validate it against the lab-mode instance that Phase A produces (the lab-mode instance is the closest approximation to a real Phase B instance that Claude Code can exercise). You do NOT run it against a real Phase B instance — that's the operator's job later.

## Objective

1. Produce `scripts/phase-b-smoke-test.sh` — a single shell script the operator runs with `./scripts/phase-b-smoke-test.sh`. It performs 12 checks covering every layer. For each check: print status, print result, continue. At the end: print a summary (✓ / ✗ per check) and exit 0 if all pass, nonzero if any fail.
2. Produce `docs/PHASE_B_SMOKE_TEST.md` — operator-facing docs. What the script does, how to interpret failures, when to call it complete, when to back out and roll the bootstrap back.

## Out of scope

- Do NOT change the bootstrap wizard in this prompt.
- Do NOT add new CLI subcommands — use what prompt 17 built.
- Do NOT make the smoke test interactive. It runs non-interactively, produces a report, exits.

## Deliverables

### `scripts/phase-b-smoke-test.sh`

```bash
#!/usr/bin/env bash
#
# Phase B smoke test for AdministrateMe.
# Run this on the Mac Mini after ./bootstrap/install.sh completes.
# Non-interactive. Produces a report. Exits 0 on full pass.
#
set -uo pipefail

PASS=0
FAIL=0
REPORT="/tmp/phase-b-smoke-$(date +%s).log"
echo "Phase B smoke test — $(date)" | tee "$REPORT"
echo "Writing detailed log to: $REPORT" | tee -a "$REPORT"
echo "" | tee -a "$REPORT"

check() {
  local name="$1"; shift
  echo -n "[$name] ... " | tee -a "$REPORT"
  if "$@" >> "$REPORT" 2>&1; then
    echo "✓ PASS" | tee -a "$REPORT"
    PASS=$((PASS + 1))
  else
    echo "✗ FAIL — see $REPORT for details" | tee -a "$REPORT"
    FAIL=$((FAIL + 1))
  fi
}

# ─── Layer 1: Environment ─────────────────────────────
check "OpenClaw gateway reachable" curl -fsS http://127.0.0.1:18789/health
check "BlueBubbles server reachable" curl -fsS http://127.0.0.1:1234/api/v1/server/info  # adjust per deployment
check "Tailscale authenticated" bash -c "tailscale status | grep -qv 'Logged out'"

# ─── Layer 2: Instance basics ─────────────────────────
check "adminme CLI available" adminme --help
check "Services running" adminme service status | grep -q "all-green"
check "Instance status healthy" adminme instance status | grep -q "STATUS: .*GREEN"

# ─── Layer 3: Event log ───────────────────────────────
check "Event log responsive" adminme event count-by-type
check "Bootstrap events present" bash -c "adminme event query --type 'bootstrap.*' --count | grep -q '[1-9]'"

# ─── Layer 4: Projections ─────────────────────────────
check "Parties projection populated" bash -c "adminme projection query parties --count | grep -q '[1-9]'"
check "All projections reporting healthy" bash -c "adminme projection status --json | jq -e 'all(.[]; .status == \"healthy\")'"

# ─── Layer 5: OpenClaw integration ────────────────────
check "OpenClaw audit clean" adminme openclaw audit
check "Persona activated" bash -c "adminme openclaw persona-list | grep -q 'poopsy'"

# ─── Layer 6: Observation mode ────────────────────────
check "Observation mode active (expected for first 7 days)" bash -c "adminme observation status | grep -q 'ACTIVE'"

# ─── Layer 7: End-to-end ingest → projection ──────────
# Use the CLI's test-ingest to inject a synthetic event that exercises the full pipeline
check "Test ingest → projection update" adminme event append \
  --type messaging.received \
  --owner-scope household \
  --payload-fixture tests/fixtures/smoke_test_inbound.json \
  --wait-for-projection parties

echo "" | tee -a "$REPORT"
echo "════════════════════════════════════════" | tee -a "$REPORT"
echo "Results: $PASS passed, $FAIL failed" | tee -a "$REPORT"
echo "════════════════════════════════════════" | tee -a "$REPORT"

if [ "$FAIL" -gt 0 ]; then
  echo "" | tee -a "$REPORT"
  echo "DO NOT proceed to normal use. Review $REPORT, then see" | tee -a "$REPORT"
  echo "docs/PHASE_B_SMOKE_TEST.md for interpretation + recovery." | tee -a "$REPORT"
  exit 1
fi

echo "" | tee -a "$REPORT"
echo "🟢 All checks passed. Instance is ready for observation-mode period." | tee -a "$REPORT"
echo "" | tee -a "$REPORT"
echo "Next: leave observation mode ON for 7 days. Review \`adminme observation log\`" | tee -a "$REPORT"
echo "daily. When suppressions look correct, run \`adminme observation off\`." | tee -a "$REPORT"
```

### `docs/PHASE_B_SMOKE_TEST.md`

Operator-facing guide. Walks through:

1. **When to run it.** Right after bootstrap completes. Also: after any `adminme deploy` to the Mac Mini.
2. **How to read it.** What ✓ means, what ✗ means. The report file paths.
3. **Each check's purpose + what to do if it fails.** For example: "OpenClaw gateway unreachable" → check if the gateway service is running; `launchctl list | grep openclaw`; common fixes. "BlueBubbles server unreachable" → check the BlueBubbles macOS app is running; its API is configured on the right port; etc.
4. **What "passing" does NOT guarantee.** The smoke test verifies the stack responds correctly to a synthetic ingest. It does not verify that real incoming iMessages work — that comes during the observation-mode period, watching live traffic.
5. **Backing out.** If smoke test fails catastrophically: the operator can `git reset --hard` the repo to the pre-bootstrap state, delete `~/.adminme/`, and re-run bootstrap from scratch. This is safe because no real writes have occurred yet (observation mode is active, and the bootstrap itself only reads from external services).

### Fixture file

`tests/fixtures/smoke_test_inbound.json` — a synthetic inbound message event payload, well-formed, targeting a fixture party ID that's in the seed data. Used to verify end-to-end ingest works.

## Verification

In Phase A sandbox:

```bash
# Validate the script's syntax
bash -n scripts/phase-b-smoke-test.sh
chmod +x scripts/phase-b-smoke-test.sh

# Run it against the lab-mode instance that prompt 18 created (closest Phase A approximation)
# First, re-bootstrap lab mode to get a fresh fixture state:
./bootstrap/install.sh --lab-mode --non-interactive --seed=stice-family
adminme service start

# Then run the smoke test:
./scripts/phase-b-smoke-test.sh
# In lab mode, the OpenClaw + BlueBubbles checks are expected to fail (no live services).
# The remaining 10 checks should all pass. This validates the script's syntax and logic.

# Commit
git add scripts/phase-b-smoke-test.sh docs/PHASE_B_SMOKE_TEST.md tests/fixtures/smoke_test_inbound.json
git commit -m "phase 19: Phase B smoke test script"
git push
```

## Stop

**Explicit stop message:**

> Phase B smoke test script written. Lab-mode verification passes the Phase A subset of checks. When the operator runs this on the Mac Mini after real bootstrap, the full set should pass.
>
> **This is the last Phase A prompt.** The repo is now build-complete. Next step is the operator running `./bootstrap/install.sh` (real mode) on the Mac Mini — that's Phase B, documented in `ADMINISTRATEME_FIELD_MANUAL.md` chapter 4 and `docs/PHASE_B_SMOKE_TEST.md`.
