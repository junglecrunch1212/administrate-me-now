# Gate 5: Mac mini bootstrap and operations evidence

- Date: 2026-08-03
- Branch: `codex/administrateme-v2-runtime`
- Draft PR: https://github.com/junglecrunch1212/administrate-me-now/pull/248
- Test host: Windows, Python 3.12.10, Node 24.14.1
- Target contract: Apple-silicon macOS 14/15/26, Python 3.11, Node 22
- Evidence level: credential-free real composition plus reproducible macOS job;
  no live provider or family Mac

## Gate continuity

Committed ancestry verifies Gate 1 at `c4d682c` plus the later passing console
harness `b653b25`, Gate 2 runtime spine at `023b94f`, Gate 3 household loop at
`42321ee`, and Gate 4 exact OpenClaw seam at `b2fa1b3`. The earlier Gate 1
render blocker commit `8ade777` is followed by passing committed evidence; no
prior gate required code repair before Gate 5.

## Exact pins

`bootstrap/versions.json` is the machine contract. The existing OpenClaw pin
remains version/API `2026.7.2` at commit
`944cf08f94c334cff2b0fdf7e01187a9efa42715`. Python is `>=3.11,<3.12`, Node
is `>=22.22.3,<23`, and SQLCipher is `>=4.12,<5` with cross-platform locked
`sqlcipher3==0.6.2`. This supersedes the old binary-distribution decision
because that package has no Apple artifact, while 0.6.2 publishes an exact
CPython 3.11 Apple-silicon wheel. Homebrew is `>=4.6,<6`, Poetry is `2.2.1`, npm is
`>=10.9,<11`, and Bun is not required.

Primary platform sources reviewed on 2026-07-30:

- https://support.apple.com/en-us/122867
- https://support.apple.com/en-us/116943
- https://docs.brew.sh/Installation
- https://nodejs.org/en/about/previous-releases
- https://nodejs.org/en/blog/release/v22.22.3

The cross-platform SQLCipher wheel inventory was reviewed on 2026-08-03:

- https://pypi.org/project/sqlcipher3/0.6.2/

## Cascade-exemption:

No `prompts/PROMPT_SEQUENCE.md` cascade is required. The `docs/DECISIONS.md`
amendment only replaces the Apple-incompatible `sqlcipher3-binary` distribution
with the same SQLCipher event-log architecture through locked `sqlcipher3`
0.6.2. It does not change Prompt 5 scope, ordering, acceptance obligations, or
citations.

## Process proof

`tests/acceptance/test_macos_bootstrap_operations.py` performs the complete
credential-free install, starts the two fixture service states, requires a
healthy observation-mode Doctor, creates an encrypted backup, restores it,
rebuilds all 13 projections/workbooks, verifies a rollback copy, and runs
Doctor again. The canonical sub-proof uses the real FastAPI composition,
SQLCipher event log, durable bus, projections, fixture `llm-task`, proposal
review, observation suppression, workbook, completion, deterministic what-now,
and projection rebuild. Explicit OpenClaw channel/account/actor mappings bind
to enrolled interactive members, while Tailscale identities bind the normal
member surface. The fixture adapter reports zero deliveries.

The unit matrix covers all fixed entrypoints; wrong platform/architecture and
no partial Section 1 state; ten independent resume boundaries; interrupted
failure/resume; repeat-run no-op; launchd plist/argument safety; member-bound
bridge encryption and kid restrictions; start/stop/restart/crash recovery;
secret-free diagnostic export; observation trust preconditions; and upgrade
incompatibility before replacement.

## Verification snapshot

| Command | Result |
|---|---|
| `python -m pytest -q tests/unit/bootstrap/test_phase5_operations.py tests/acceptance/test_macos_bootstrap_operations.py` | PASS: 12 |
| `PYTHONPATH=bridge python -m pytest -q --disable-warnings --maxfail=20` | PASS: 1,256 passed, 3 skipped, 1 expected Phase 6 xfailed, 4 warnings |
| `python -m ruff check bootstrap tests/unit/bootstrap tests/acceptance/test_macos_bootstrap_operations.py` | PASS |
| `python -m mypy bootstrap --no-error-summary` | PASS |
| ShellCheck 0.11.0 over `bootstrap/*.sh` and `scripts/acceptance/*.sh` | PASS |
| Bash syntax over the same scripts | PASS |
| `bash scripts/verify_invariants.sh` | PASS |
| OpenClaw `npm run check`, `npm test`, `npm run build`, `npm pack --dry-run` | PASS: exact contract, 10 tests, build, 58-file package |
| Retained Gate 1 console harness | PASS: 12 interactions, 68 DOM assertions, zero browser/console/request errors |
| Manual credential-free bootstrap followed by Doctor | PASS: healthy observation mode, 16 required checks |
| Manual encrypted backup then restore | PASS: 33 files, 13 projections rebuilt, rollback retained |

The GitHub macOS check is the publish-time evidence for the reproducible Apple
host path. No unexpected xfail is reclassified.

## Safety review

- No recursive target is derived from an unresolved environment variable.
- Cleanup occurs only for validated `mktemp` rehearsal directories; restore
  moves only the exact active `state` directory and retains recovery copies.
- No shell script sources `.env`, carries a key argument, uses broad globs for
  deletion, or requires a developer IDE.
- Plists contain only paths and non-secret configuration. Runtime launchers
  resolve Keychain values after launch.
- Diagnostics compare their serialized output against all resolved secret
  values and their hex forms before returning.
- Skipped seed/integration subsections create follow-ups; disabled and pending
  are not reported as healthy live providers.

## Human action

Review PR #248, then run the live-Mac checklist in
`docs/refactor/MAC-MINI-OPERATIONS.md` on a disposable supported
Apple-silicon Mac. Record real Keychain, launchd, FileVault, permission denial
and grant, login/crash recovery, and exact live OpenClaw RPC evidence. Do not
touch the future family Mac in Gate 5.
