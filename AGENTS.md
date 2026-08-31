# AdministrateMe engineering instructions

This repository is a household-scale, local-first, event-sourced platform. Treat documentation and comments as evidence; executable behavior and tests decide whether a change works. The v2 reconciliation in `docs/refactor/` governs product architecture. Historical delivery procedures are not current authority.

## Working rules

- Start each change from current protected `main`, use one `codex/` branch and one pull request, and preserve unrelated changes. Never rewrite published history, force-push, or push directly to `main`.
- The implementation task may use bounded subagents for implementation, testing, research, or advisory review. A separate user-created review task, exact-head review artifact, staged label, or prescribed authorization phrase is not required.
- GitHub is the delivery control plane: fix failures on the same branch, require the protected PR checks to pass, resolve actionable review comments, and merge through the pull request. Normal commits invalidate only the checks GitHub reruns; they do not start a new review ceremony.
- A repository owner may authorize merge in ordinary language. A merge request does not authorize deployment or another operational change.
- Never place household names, emails, phone numbers, addresses, credentials, or tokens in platform code, bootstrap code, built-ins, or tests.
- Keep the dependency direction: adapters -> encrypted append-only event log/durable bus -> deterministic projections -> pipelines -> family surfaces. Sources emit events; projections are never a source of truth.
- OpenClaw owns channels, conversations, generic memory, sessions, model routing, cron/background execution, agent orchestration, generic tool approval, device nodes, and runtime administration. AdministrateMe owns typed household truth, domain authorization, scope/privacy, projections, workbooks, provenance, correction, observation, and outcome feedback.
- All LLM work goes through the pinned OpenClaw `llm-task` seam. Never add a provider SDK or a second model router.
- Do not collapse event facts, calendar events, tasks, commitments, list items, recurrences, or scheduler jobs into one model.
- External writes require the documented allowlist -> governance -> rate-limit ordering, independent OpenClaw and domain approval, a final content/privacy guard, and observation-mode handling. Audit every terminal result.
- Preserve all six product functions, all thirteen projection capabilities, all five adapter categories, all six pack kinds, the five built-in profiles, and four built-in personas. A missing implementation must be reported, not silently omitted.
- A central v2 deployment is one long-lived AdministrateMe Python runtime behind Tailscale Serve plus one exactly pinned OpenClaw Gateway. Member bridge daemons remain separate, per-member edge processes. Node may build browser assets but is not another central application runtime.
- Phase A runs only in Codex/cloud or a disposable lab. Never experiment on the family Mac. Phase B begins only after merge and a clear, separate owner request to deploy.

## Deployment safety

A deployment operates on the immutable merge commit from protected `main`. Use the existing guarded updater; keep observation mode active; verify an encrypted backup before switching; and roll back automatically on a new degradation or failed prerequisite. After activation, require bounded settlement, full Doctor, privacy and tenant-isolation checks, authenticated read-only browser acceptance for changed behavior, backup/rollback evidence, and zero unintended provider effects.

Runtime or dependency migration, provider enrollment, write enablement, Keychain or permission changes, and unrelated live repairs always require their own explicit owner request. Never edit an installed release, active virtual environment, LaunchAgent, encrypted state, or provider configuration to make activation pass. Hosted CI and subagents must not access the live family instance or credentials.

## Repository map

- `adminme/events/`: envelopes, schema registry, SQLCipher log, durable in-process bus.
- `adminme/projections/`: deterministic read models and runner.
- `adminme/pipelines/`, `packs/pipelines/`, `packs/skills/`: reactive domain logic and typed OpenClaw skill calls.
- `adminme/adapters/`, `adminme/lib/adapters/`, `packs/adapters/`: adapter protocol, five-category contracts, implementations.
- `adminme/products/`: product router namespaces served by the one Python runtime.
- `adminme/lib/`: session/scope, governance, observation, pack registry, OpenClaw skill seam.
- `bridge/`: per-member bridge package and tests.
- Browser behavior remains governed by `docs/refactor/UX-BEHAVIOR-CONTRACT.md`; the false Node application scaffold was removed at Gate 6.
- `bootstrap/`: baseline prose/config scaffolding. Its target is `docs/refactor/BOOTSTRAP-AND-TOPOLOGY-CONTRACT.md`.
- `tests/acceptance/`: golden process gates. Run with `--runxfail` for release gating.

## Supported checks

Use Python 3.14 and Node 24, matching the candidate manifests and ADR-0006. Existing Python 3.11/Node 22 deployments remain untouched until the separately approved runtime-migration gate. The bridge package must be importable when running the root suite.

```bash
poetry sync --with dev
PYTHONPATH=bridge poetry run pytest -q
PYTHONPATH=bridge poetry run pytest tests/acceptance --runxfail -q
poetry run ruff check adminme bridge/adminme_bridge
poetry run mypy adminme bridge/adminme_bridge
bash scripts/verify_invariants.sh
npm --prefix openclaw/administrateme ci
npm --prefix openclaw/administrateme run check
npm --prefix openclaw/administrateme test
```

Before a deployment, also run the full unscoped lint suite, all pack/package validators, identity and instance-path scans, replay/isolation/backup tests, and the clean macOS lab rehearsal in `docs/refactor/ACCEPTANCE-CONTRACT.md`. Record environment-specific skips and failures verbatim.

## Definition of done

A claim is complete only when a process-level test demonstrates it through the production composition root. Unit tests for isolated layers are necessary but insufficient. Update governing documentation when behavior or scope changes, but do not create evidence-only follow-up work. Classify semantic changes as PRESERVE, MODERNIZE, SUPERSEDE, or DEFER and cite the governing source. DEFER is never valid for a required product function, projection capability, privacy/governance invariant, workbook round trip, or bootstrap safety.

## Mandatory sandbox testing

When a user asks a Codex task to "test in the sandbox", the task must use the
single preflight wrapper; the test command belongs after the separator:

```bash
PYTHONPATH=bridge poetry run adminme sandbox test -- <test command>
```

Use `--require-gateway` before `--` only when the test needs current model
semantics. The wrapper must reconcile a fresh signed profile from the exact
active immutable release, prove dominance/privacy/isolation, clone the
canonical encrypted baseline, run candidate code against only that clone, and
discard it. Never test against the live family instance, bypass reconciliation,
use a stale baseline or the nine-message demo, claim fidelity when the active
profiler or a current signed artifact is unavailable, or mutate/serve the
canonical baseline. A host without authorized read-only profiling must fail
closed or be supplied a current signed artifact; it must not silently fall back.
