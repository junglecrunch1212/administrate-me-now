# Prompt 18: End-to-end integration test (Phase A)

**Phase:** Final Phase A verification before the codebase is considered build-complete. This runs in Claude Code's sandbox using lab-mode + mocks for all external services.
**Depends on:** Prompt 17. Everything exists.
**Estimated duration:** 3-4 hours.
**Stop condition:** One scripted end-to-end scenario in Phase A sandbox runs through lab-mode bootstrap → adapter ingest (mock) → pipeline → projection → surface → outbound (suppressed in observation mode), with all expected events present in the log and no errors; `adminme instance status` reports green across all layers.

---

## Read first (required)

1. `ADMINISTRATEME_BUILD.md` **"SAMPLE INSTANCE — WHAT `~/.adminme/` LOOKS LIKE AFTER BOOTSTRAP"** — the target state after `--lab-mode` bootstrap.
2. `ADMINISTRATEME_BUILD.md` any section mentioning "readiness check" or "sample run-through."
3. `ADMINISTRATEME_DIAGRAMS.md` §1 (five-layer architecture) — use as a mental checklist of "does the event traverse each layer cleanly?"

## Operating context

This is the final Phase A prompt. If it passes, the codebase is build-complete — the repo contains working code that the operator can clone to the Mac Mini and run via `./bootstrap/install.sh` to produce a live instance. The prompt does not verify the real-mode bootstrap (which requires live OpenClaw / Plaid / Google / etc.); that's Phase B, documented in the Field Manual.

The test you write here uses **lab mode exhaustively** — mocked Gmail, sandbox Plaid, scratch iCloud list, mock OpenClaw responses. It proves the code's internals work end-to-end.

## Out of scope

- Real-mode bootstrap verification — Phase B, operator runs this on the Mac Mini.
- Performance testing beyond lab sanity checks.
- Stress / chaos testing.

## Deliverables

### `tests/integration/test_full_stack.py`

One long test that, within the Phase A sandbox:

1. **Lab-mode bootstrap**: `./bootstrap/install.sh --lab-mode --non-interactive --seed=stice-family` — completes with no errors. All bootstrap sections run; mock credentials populate where real ones would. Asserts `$ADMINME_INSTANCE_DIR` is populated per the SAMPLE INSTANCE spec.
2. **Start services**: `adminme service start`. Wait for health. Services run on loopback; no tailnet.
3. **Trigger adapter ingest** (mocked Gmail): inject a fixture email into the Gmail adapter's inbox simulator. Assert `messaging.received` event appears in log within 5 seconds.
4. **Observe reactive pipelines**: assert `noise_filtering` tags or passes the email; if commitment-eligible, assert `commitment.proposed` emitted. Skill calls use mocked OpenClaw responses.
5. **Observe CRM projection**: query the parties projection; verify the email sender is now a party; if new, assert `identity.merge_suggested` for a fuzzy match.
6. **Trigger a proactive pipeline**: `adminme pipeline trigger morning_digest --member=stice-james`. In observation mode: assert `observation.suppressed` event; NOT the actual send.
7. **Complete a task**: `adminme` call to complete a seeded task; assert `task.completed` event; assert reward tier rolled; assert `reward.ready` event; assert SSE would have emitted (mocked consumer).
8. **Query projections via product API**: hit `/core/today-stream` and assert it returns expected shape.
9. **Query via console**: render `/today` for Laura, assert her view shows her tasks but not James's privileged events.
10. **Turn observation off briefly, send, verify external.sent**: via CLI, turn obs off → trigger a reminder → assert `messaging.sent` via OpenClaw mock → turn obs back on.
11. **Shut down + restart + verify catch-up**: stop services, emit 5 events via direct log write, start services, assert projections catch up, observation mode is restored from config.
12. **Instance status**: `adminme instance status` returns all-green.

### Readiness report

`adminme instance readiness` — new CLI command (extends prompt 17's `adminme instance`):

```
AdministrateMe instance readiness report
=========================================
Instance: stice-household-lab
Generated: <timestamp>

Layers
------
L1 adapters:
  gmail:          ✓ authenticated, cursor current, 0 errors last 24h
  plaid:          ✓ 2 institutions healthy, 0 errors
  reminders:      ✓ bidirectional; 0 lag
  (etc)

L2 event log:
  size:           1,247 events, 3.2 MB
  integrity:      ✓ verified
  encryption:     ✓ active
  last append:    3 seconds ago

L3 projections:
  parties:        ✓ 47 rows, checkpoint current
  commitments:    ✓ 12 rows, checkpoint current
  (etc)

L4 pipelines:
  reactive:
    noise_filtering:          ✓ registered, 0 failures last 24h
    identity_resolution:      ✓
    commitment_extraction:    ✓
    thank_you:                ✓
  proactive (OpenClaw standing orders):
    morning_digest:           ✓ registered, next fire: 06:30 tomorrow
    paralysis_detection:      ✓
    (etc)

L5 surfaces:
  core api (:3333):        ✓ serving
  comms api (:3334):       ✓ serving
  capture api (:3335):     ✓ serving
  automation api (:3336):  ✓ serving
  console (:3330):         ✓ serving, Tailscale auth active

OpenClaw integration:
  gateway reachable:        ✓
  persona activated:        ✓ poopsy-v1
  slash commands:           ✓ 22/22 registered
  standing orders:          ✓ 6/6 registered
  plugins:                  ✓ 2/2 installed
  skills:                   ✓ 12/12 installed

Observation mode:           ACTIVE (enabled at <timestamp>)
  Suppressed last 24h:      4 outbound (3 reminder_dispatch, 1 reward_dispatch)
  Latest suppressed:        12 min ago

Governance:
  action_gates configured:  28 rules
  rate limits active:       12 keys tracking
  recent write denials:     0 in last 24h

Storage:
  ~/.adminme size:          4.1 MB
  backup last run:          (none — lab instance)

═══════════════════════════════════════════
STATUS: 🟢 ALL GREEN. Instance ready.
═══════════════════════════════════════════
```

Format must be machine-readable too: `adminme instance readiness --json` returns a structured equivalent.

## Verification

```bash
# Full test
poetry run pytest tests/integration/test_full_stack.py -v

# Manual readiness
./bootstrap/install.sh --lab-mode --non-interactive --seed=stice-family
adminme service start
sleep 5
adminme instance readiness
adminme instance readiness --json | jq .

# All previous tests still pass
poetry run pytest -v

# Final git state
git log --oneline -30
git commit --allow-empty -m "phase 18: end-to-end integration test passes"
git tag v1.0.0-build-complete
```

Expected:
- `test_full_stack.py` passes.
- Readiness report is all green.
- All tests from prompts 03-17 still pass (nothing regressed).

## Stop

**Explicit final message:**

> 🟢 Build complete. AdministrateMe v1.0 is ready for lab evaluation. 
> 
> Before tenant deployment:
> 1. Operator runs `./bootstrap/install.sh` (real mode) against tenant's real accounts.
> 2. Observation mode stays on 7 days; operator reviews suppression log daily.
> 3. Tuning via CLI as issues surface.
> 4. When suppressed log is consistently "I would have sent X, and X looks right", turn observation off.
> 5. Begin normal use.
> 
> See ADMINISTRATEME_FIELD_MANUAL.md for ongoing operations.
