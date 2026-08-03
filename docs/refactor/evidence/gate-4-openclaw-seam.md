# Gate 4: current OpenClaw seam evidence

- Date: 2026-07-30
- Branch: `codex/administrateme-v2-runtime`
- Draft PR: https://github.com/junglecrunch1212/administrate-me-now/pull/248
- OpenClaw version: `2026.7.2`
- OpenClaw commit: `944cf08f94c334cff2b0fdf7e01187a9efa42715`
- Test host: Windows, Node `24.14.1`, Python `3.12`
- Evidence level: official-contract fixture plus real AdministrateMe process;
  live OpenClaw not run

## Source selection

Current official manifest, plugin-building, dashboard, tool-invoke, permission,
skill, slash-command, standing-order, hook, workspace, and repository sources
were reviewed before implementation. Current source was selected over the
earlier stable `v2026.7.1` plan because Prompt 4 requires the current native
dashboard data/action binding and authenticated dashboard actor contracts.
The exact commit is recorded in the package, manifest validator, Python request
contract, tests, ADR, compatibility matrix, and bootstrap gate.

## Process fixture

`tests/acceptance/test_openclaw_native_seam.py` contains three production-root
scenarios:

1. Transport and actor denial; child isolation; observation-only action;
   OpenClaw-format skill wrapper; standing order; explicit bridge; privileged
   exclusion; metadata-only model event; dashboard filtering; full provenance.
2. A real Task completion updates the encrypted event log/projection and
   returns one scope-filtered what-now rerank. Retrying the same lifecycle and
   commitment approval produces one semantic event, one suppression, and one
   OpenClaw audit per correlation.
3. An unavailable Gateway state persists across runtime restart while the
   captured communication remains readable. A future incompatible
   version/commit receives `409`, persists `incompatible`, and does not prevent
   deterministic domain reads or later recovery.

## TypeScript fixture

The narrow declaration fixture under
`openclaw/administrateme/test/fixtures/openclaw-2026.7.2/` contains only the
official plugin SDK surfaces used by this package. Tests register the actual
plugin entrypoint and invoke its factories/handlers. The contract validator
also compares manifest/package declarations, standing-order count and tool
seam, dashboard bindings, strict config, loopback pattern, and forbidden API
strings. This fixture does not emulate or claim a live Gateway.

## Verification

| Command | Result |
|---|---|
| `PYTHONPATH=bridge python -m pytest -q --disable-warnings --maxfail=20` | **PASS:** 1,244 passed, 3 skipped, 1 expected member-knowledge xfail (now Phase 6), 3 warnings |
| `python -m pytest -q tests/acceptance/test_openclaw_native_seam.py --disable-warnings` | **PASS:** 3 process tests |
| `npm.cmd test --prefix openclaw/administrateme` | **PASS:** 10 fixture tests |
| `npm.cmd run check --prefix openclaw/administrateme` | **PASS:** strict TypeScript plus exact contract validator |
| `npm.cmd run build --prefix openclaw/administrateme` | **PASS:** built ESM runtime and declarations |
| `npm.cmd pack --dry-run --prefix openclaw/administrateme` | **PASS:** native manifest, runtime, skill, workspace, and automations included |
| `npm.cmd audit --prefix openclaw/administrateme --audit-level=low` | **PASS:** 0 vulnerabilities after Vitest update |
| `npm.cmd run test:live:mac` on this Windows host | **EXPECTED REFUSAL (exit 2):** `LIVE MAC GATE NOT RUN: requires the disposable supported macOS lab` |
| `python -m ruff check adminme tests/acceptance/test_openclaw_native_seam.py` | **PASS** |
| `python -m mypy adminme --no-error-summary` | **PASS** |
| `scripts/verify_invariants.sh` | **PASS** |
| `npm.cmd test --prefix tools/console-reference-harness` | **PASS:** retained Gate 1 evidence, 12/12 interactions and 68/68 DOM assertions |
| `git diff --check` | **PASS** |

## Live gate disposition

The selected source requires Node `>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0`; this Windows host has Node
`24.14.1`. Installing or claiming a live pinned Gateway here would be false.
`npm run test:live:mac` therefore fails closed unless all of the following are
true: supported macOS, explicit `ADMINME_LIVE_GATE=1`, exact
`OPENCLAW_SOURCE_DIR` commit, and supported Node. It then runs build/contract,
links and inspects the plugin, restarts the Gateway, and requires deep RPC
health. This remains a genuinely required pre-deployment human action after PR
review; the family Mac must remain untouched meanwhile.
