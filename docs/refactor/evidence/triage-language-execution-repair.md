# Triage language execution repair evidence

Date: 2026-08-08

## Finding

An authenticated, synthetic `llm-task` request reached the live pinned Gateway
and its registered bundled tool, then returned HTTP 500. Secret-redacted Gateway
evidence identified an internal SQLite session-scope failure before provider
execution. Source review showed that the former exact OpenClaw snapshot passed a
temporary file target and no agent identity into its embedded runner.

The official same-version successor commit
`ce67ffb70e83e5ebb56ea02e814e577153f84f61` replaces that path with isolated,
tool-free completion. Its source archive SHA-256 is
`3fffb07090f63492bff89daac6ae0879e66e205c743911598bc79e3bb7e2b577`.

## Candidate proof

- OpenClaw package version remains `2026.7.2`; package manager and plugin API
  remain exact.
- Node 24.19.0 verified the archive lockfile and supply-chain policy.
- All 23 bundled `llm-task` tests passed.
- The complete OpenClaw source and Control UI build passed.
- The AdministrateMe release-candidate suite passed under the reviewed Node
  24.19.0 runtime: 36 strict acceptance tests, 1,638 root/pack tests, 60 bridge
  tests, and 15 native plugin-contract tests, plus lint, formatting, Python and
  TypeScript type checks, dependency audits, performance budgets, architecture
  negatives, clean install, tenant isolation, backup/restore, package builds,
  shell validation, and diff validation.
- AdministrateMe wrapper tests prove canonical agent identity and distinct
  authenticated-member session keys.
- Triage handler tests prove baseline, v4, and v5 event generations and
  cross-generation candidate deduplication.
- Doctor tests prove a bounded authenticated JSON request and secret-free
  evidence; missing credentials fail closed.
- Upgrade tests permit only the declared exact predecessor and refuse an
  undeclared OpenClaw commit.

## Safety

The candidate performs no provider write, confirmation, canonical
materialization, or external dispatch. Observation mode remains mandatory.
Historical source facts, failed-call audit facts, and pending Triage proposals
remain immutable. Live replay and final aggregate counts occur only after
independent review, merge, backup, guarded deployment, and a successful Doctor
execution probe.
