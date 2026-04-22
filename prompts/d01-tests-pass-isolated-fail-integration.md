# Diagnostic d01: Tests pass in isolation but fail in integration

**Symptom.** A unit test for, say, the `parties` projection passes. But when you run the full test suite, a later integration test fails — usually with something like "expected 1 party, found 3" or "row exists that shouldn't".

**When to use this prompt.** You've finished a prompt (any of 03-14), unit tests pass, but integration tests or a full `pytest -v` run fails. Or the demo script for that phase works on a fresh DB but fails on an existing one.

---

## Read first

1. The failing test's source and its error output.
2. `tests/conftest.py` — the shared fixture setup. Most of these bugs originate here.
3. Any fixtures the failing test uses.

## Likely causes (ranked)

1. **Fixture bleeding.** A test mutates global state (the shared event log DB, module-level registry, singleton bus) and doesn't clean up. The next test inherits the dirty state. Symptom: first-run passes, second-run fails, or order-dependent failures.
2. **Autoload timing.** `registry.autoload()` is called at module import in some paths but explicitly in others. Running tests in a different order triggers different registration sequences. Symptom: "duplicate registration" or "schema not found" errors depending on test order.
3. **Async context leakage.** An `asyncio` task from one test is still running when the next test starts. Symptom: logs from test A appear during test B; occasional timeouts.
4. **Shared tmpdir path.** Tests use a fixture tmpdir, but somewhere a hardcoded path (`~/.adminme/...` or `/tmp/adminme.db`) bypasses the fixture.

## Procedure

1. Run the failing test in isolation: `poetry run pytest tests/integration/<file>::<test> -v`. Does it pass? If yes, it's a fixture-isolation bug.
2. Run the full suite with `-p no:randomly --tb=short` to get a deterministic order. Bisect: `poetry run pytest tests/ -x --lf` (last failed).
3. `grep -rn "~/.adminme\|/tmp/adminme\|tempfile.mkdtemp" adminme/ tests/` — look for hardcoded paths.
4. For fixture-bleeding: ensure every DB-using fixture uses `function` scope (not `session` or `module`) unless you really know it's safe. Add teardown that drops tables or deletes files.
5. For registry issues: use a fresh `SchemaRegistry()` per test instead of the module-level singleton.
6. For async: ensure every `async def` test has `@pytest.mark.asyncio` and every background task is `await task.cancel()` in teardown.

## Fix pattern

Move to function-scoped fixtures. Use `tmp_path` (pytest's built-in) everywhere instead of hardcoded paths. For tests that genuinely need a shared state, use a `module`-scoped fixture with an explicit teardown.

## Verify fix

Run the full suite **three times in a row** with different orders:

```bash
poetry run pytest tests/ -p no:randomly  # deterministic
poetry run pytest tests/ --randomly-seed=1  # shuffled
poetry run pytest tests/ --randomly-seed=2  # different shuffle
```

All three must pass. If any fails, the fix is incomplete.

## Escalate if

The failing test uses an external service (real OpenClaw, real BlueBubbles) that has its own state. Those tests belong in the `skipif_no_live_services` marker and should not be part of the deterministic full-suite run.
