# Legacy executor history

This index preserves the useful history of the retired Claude-era executor. Current Codex work is governed by `AGENTS.md`, `docs/refactor/`, the deterministic Outcome Harness, and executable tests; historical mention here does not make these paths Codex authority.

## Retirement boundary

The following execution surfaces were present in the approved pre-retirement tree and are removed by the subsequent retirement patch:

- `.github/workflows/executor-cycle.yml`
- `.github/workflows/executor-cycle-c2c3c4.yml`
- `.github/workflows/executor-cycle-c3c4.yml`
- `.github/workflows/executor-c4-only.yml`
- `.github/workflows/smoke-test.yml`
- `.github/workflows/constitution-sync.yml`
- `.claude/settings.json`
- `.claude/mcp.json`
- `.claude/hooks/post-edit-verify.sh`
- `.claude/hooks/pre-bash-spend-check.sh`
- `.claude/hooks/stop-checkpoint-pause.sh`
- `.claude/agents/preflight.md`
- `.claude/agents/splitter.md`
- `.claude/agents/executor-c1.md`
- `.claude/agents/executor-c2.md`
- `.claude/agents/executor-c3.md`
- `.claude/agents/executor-c4.md`
- `validation/setup-complete.sh`

The proposed retirement does not include historical product prompts, build logs, constitutional sources, signed release evidence, the credential-free macOS gate, or the release-candidate acceptance path.

A first separate parity run was rejected because the eighteen prerequisite
paths were inspected but not included in its candidate binding. That evidence
cannot authorize deletion. A replacement run must bind every path listed above,
so deleting or changing any one invalidates its receipts and review.

A second bound run, `20260813t180234z-18dafbf8-5xod__dw`, was also rejected.
Its static, focused, immutable-baseline, current production-composition, and
browser proof passed, but the three required OpenClaw breadth commands inherited
Node 22.23.2/npm 10.9.8. The browser's separate Node 24.19.0 receipt did not cure
that mismatch. The run is retained under its system-temporary directory as
rejected evidence and grants no deletion authority. The harness now resolves,
executes, hashes, and validates the supported Python 3.14/Node 24/npm 11
toolchain for every OpenClaw breadth receipt as well as browser proof.

The third pre-retirement run,
`20260813t182422z-739bfefa-22y57puv`, bound all eighteen paths while they were
still present and byte/mode exact to `HEAD`. Its binding digest was
`d54819ab905da1d4e2e7071ce3496846008b4f84e83af45a1415f29188009376`.
It passed the reviewed static, focused, immutable-`HEAD` baseline, current
production-composition, full breadth, and 260-assertion/39-screenshot browser
checks. Every OpenClaw and browser receipt independently attested Python 3.14.6,
Node 24.19.0, and npm 11.17.0. A separate read-only Codex review covered all
eight required scopes and returned `APPROVE` with no findings or unresolved
evidence. The preserved run is
`/var/folders/ts/hllr4l1s4ysfrtswn7wqmnlc0000gp/T/administrateme-harness-20260813T182422Z-739bfefa-22y57puv`;
its canonical review SHA-256 is
`98e1ee1f182fe058d723fa7d904947e3c5f56ed9bd76febb88574f0060cb5fe5`.
That exact approval authorized the separate local retirement patch; any change
to the pre-retirement tree would have invalidated it.

## Useful behavior retained

| Retired behavior | Current replacement |
|---|---|
| Prompt preflight, scope sizing, dependency checks, and split proposals | `adminme-outcome` creates a bound outcome/evidence/preservation contract; explicit changed-path rules select bounded checks. An oversized or ambiguous change fails closed instead of creating an autonomous commit chain. |
| Four executor stages and recovery variants | `adminme-change` operates one bounded preservation manifest through static, focused, journey, breadth, and release tiers. Candidate rebinding invalidates all earlier proof. |
| Per-edit invariant hook | Static harness receipts and `.github/workflows/administrateme-consistency.yml` run the reviewed invariant and generated-state checks without a broad test after every edit. |
| Spend/authentication hook and model smoke workflow | Local harness subprocesses remove model and GitHub credentials. Hosted model review remains explicitly `NOT_CONFIGURED`; new external cost or credential setup requires owner authority. |
| Stop-time checkpoint placeholder | `adminme-review` produces an exact-bound, read-only review artifact; `adminme-evidence` derives the owner handoff only from validated metadata. |
| Repository-wide Claude permissions and GitHub MCP registration | Repository-local skills have bounded responsibilities and no merge, release, deployment, provider-write, credential, or household-state authority. |
| Prompt-era constitution cascade check | The deterministic consistency check requires the current crosswalk, migration map, acceptance contract, and handoff when a current `docs/refactor/` governing source changes. |
| Codespace setup verdict | Exact command evidence is captured by run receipts; OpenClaw breadth and browser proof fail unless each receipt is bound to supported Python 3.14, Node >=24.15,<25, and npm >=11.6,<12 executables. |
| Stacked branch/commit/PR automation | No replacement is granted by the harness. Git operations and G-001 release authority remain outside ordinary implementation and require separate current-task authorization. |

## Incident and design provenance

- `CHANGELOG-v4.md` records the supervised-build executor changes and their motivating failures.
- `docs/RUNBOOK-EXECUTOR.md` and `docs/SETUP-AUTONOMOUS-LOOP.md` remain historical operating records, not current instructions.
- `cowork-partner/PROJECT-INSTRUCTIONS.md` retains the prompt-era constitution-cascade incident explanation.
- `docs/build_log.md` retains build chronology and prior execution evidence.
- Git history retains the exact retired content. Useful introduction and hardening points include `834f8a133db66e390759495825277d25594946de`, `ba702f222732da096749d83d4ef34484a75d82af`, `f2ea0a5b085e2f91329e1ce3e8505acb9f172bf5`, `20fc4c634db34b29cc4b5ec3f1e6cdbea382b9e8`, `342931fcbc36f9143e27aea9e00ad4569253d83c`, `3fba98d93780d3e82b0649c9d4f42e0f29a63d27`, and `78b87a6fa463e577e7148bc80847bf4b2ac693c5`.

To inspect one retired path without restoring it to execution, use Git history for the required commit and path. Restoration is not implied by this index and would require a new preservation and authority review.

## Status

The replacement and retirement are complete in the local candidate. Final
candidate verification and read-only review remain separate from the preserved
pre-retirement approval. This index does not claim a commit, release,
deployment, live-family change, hosted independent G-001 review, or provider
effect.
