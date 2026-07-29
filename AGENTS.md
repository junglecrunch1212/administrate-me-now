# AdministrateMe engineering instructions

This repository is a household-scale, local-first, event-sourced platform. Treat documentation and comments as evidence; prompt-era prose is not executable proof. The v2 constitutional reconciliation lives in `docs/refactor/` and governs new work. Historical detail remains in `WHAT-IS-ADMINISTRATEME.md`, `ADMINISTRATEME_BUILD.md`, `ADMINISTRATEME_CONSOLE_PATTERNS.md`, `ADMINISTRATEME_CONSOLE_REFERENCE.html`, `ADMINISTRATEME_DIAGRAMS.md`, `ADMINISTRATEME_FIELD_MANUAL.md`, `ADMINISTRATEME_REFERENCE_EXAMPLES.md`, `prompts/PROMPT_SEQUENCE.md`, `docs/00-mental-model.md`, `docs/openclaw-cheatsheet.md`, and `docs/architecture-summary.md`.

## Working rules

- v2 integration work belongs on `codex/administrateme-v2-runtime`, based on `main`. Do not merge, rewrite published history, or weaken tests.
- Preserve unrelated changes. Never place household names, emails, phone numbers, addresses, credentials, or tokens in platform code, bootstrap code, built-ins, or tests.
- Keep the dependency direction: adapters -> encrypted append-only event log/durable bus -> deterministic projections -> pipelines -> family surfaces. Sources emit events; projections are never a source of truth.
- OpenClaw owns channels, conversations, generic memory, sessions, model routing, cron/background execution, agent orchestration, generic tool approval, device nodes, and runtime administration. AdministrateMe owns typed household truth, domain authorization, scope/privacy, projections, workbooks, provenance, correction, observation, and outcome feedback.
- All LLM work goes through the pinned OpenClaw `llm-task` seam. Never add a provider SDK or a second model router.
- Do not collapse event facts, calendar events, tasks, commitments, list items, recurrences, or scheduler jobs into one model.
- External writes require the documented allowlist -> governance -> rate-limit ordering, independent OpenClaw and domain approval, a final content/privacy guard, and observation-mode handling. Audit every terminal result.
- Preserve all six product functions, all thirteen projection capabilities, all five adapter categories, all six pack kinds, the five built-in profiles, and four built-in personas. A missing implementation must be reported, not silently omitted.
- A central v2 deployment is one long-lived AdministrateMe Python runtime behind Tailscale Serve plus one exactly pinned OpenClaw Gateway. Member bridge daemons remain separate, per-member edge processes. Node may build browser assets but is not another central application runtime.
- Phase A runs only in Codex/cloud or a disposable lab. Never experiment on the family Mac. Phase B starts only after review and merge.

## Repository map

- `adminme/events/`: envelopes, schema registry, SQLCipher log, durable in-process bus.
- `adminme/projections/`: deterministic read models and runner.
- `adminme/pipelines/`, `packs/pipelines/`, `packs/skills/`: reactive domain logic and typed OpenClaw skill calls.
- `adminme/adapters/`, `adminme/lib/adapters/`, `packs/adapters/`: adapter protocol, five-category contracts, implementations.
- `adminme/products/`: product router namespaces; several are baseline stubs.
- `adminme/lib/`: session/scope, governance, observation, pack registry, OpenClaw skill seam.
- `bridge/`: per-member bridge package and tests.
- `console/`: baseline Node console scaffold. Its behavior contract is `docs/refactor/UX-BEHAVIOR-CONTRACT.md`.
- `bootstrap/`: baseline prose/config scaffolding. Its target is `docs/refactor/BOOTSTRAP-AND-TOPOLOGY-CONTRACT.md`.
- `tests/acceptance/`: golden process gates. Run with `--runxfail` for release gating.

## Supported checks

Use Python 3.11 and Node 22, matching the manifests. The bridge package must be importable when running the root suite.

```bash
poetry install --with dev
PYTHONPATH=bridge poetry run pytest -q
PYTHONPATH=bridge poetry run pytest tests/acceptance --runxfail -q
poetry run ruff check adminme bridge/adminme_bridge
poetry run mypy adminme bridge/adminme_bridge
bash scripts/verify_invariants.sh
cd console && npm ci && npm test
```

Before a release, also run the full unscoped lint suite, all pack/package validators, identity and instance-path scans, replay/isolation/backup tests, and the clean macOS lab rehearsal in `docs/refactor/ACCEPTANCE-CONTRACT.md`. Record environment-specific skips and failures verbatim.

## Definition of done

A claim is complete only when a process-level test demonstrates it through the production composition root. Unit tests for isolated layers are necessary but insufficient. Update `docs/refactor/HANDOFF.md`, the crosswalk, migration map, and acceptance evidence when behavior or scope changes. Classify semantic changes as PRESERVE, MODERNIZE, SUPERSEDE, or DEFER and cite the governing source. DEFER is never valid for a required product function, projection capability, privacy/governance invariant, workbook round trip, or bootstrap safety.
