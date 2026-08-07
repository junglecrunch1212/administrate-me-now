# CO-005 — Constitutional audit, trust repair, and staged runtime modernization

Status: **APPROVED AND IMPLEMENTED IN CANDIDATE; LIVE CUTOVER NOT AUTHORIZED**

Date: 2026-08-07

## Purpose

Audit the running member experience and the complete production composition
against the v2 constitutional reconciliation. Process tests are necessary but
not accepted as proof when direct code or live behavior contradicts them. Fix
bounded safety and cross-surface defects now, make documentary truth match
executable truth, and stage the future runtime without replacing the healthy
household release.

## Evidence collected

- The active immutable release and its exact instance paths were resolved
  before inspection. Doctor reported a healthy encrypted runtime in observation
  mode, one loopback Python service, one pinned OpenClaw Gateway, fourteen
  projections, five OpenClaw scheduler entries, three configured central
  connections, four enrolled members, and no active member bridge.
- The pre-change aggregate release gate passed: 1,553 root tests, 60 bridge
  tests, all acceptance tests, lint, type checks, invariant scans, package
  validation, plugin contract/build/tests, clean install, tenant isolation,
  backup/restore, and architecture checks.
- Every adult member route was exercised through the trusted Tailscale portal:
  Today, Triage, Comms, Reminders, Calendar, People, Notes, Finance,
  Scoreboard, and Settings. Active navigation, authorization, empty/degraded
  states, data composition, overflow, and console warnings were inspected.
- Production composition, event schemas, provider command routes, write gates,
  projection freshness, identity resolution, bootstrap, upgrade, versions,
  crosswalk, migration map, and acceptance contracts were compared directly.

## Findings and implemented corrections

| Finding | Constitutional impact | Candidate correction |
|---|---|---|
| Production constructed `GuardedWrite` with empty governance and hard-coded partial authority. Unknown governance actions defaulted to allow. | Violated C062 and made passing unit fixtures stronger than the live composition. | Runtime loads reviewed instance policy with tenant-neutral fallback; unknown actions deny; exact rules override bounded family rules; Settings mutations use the same gate. |
| A generic member endpoint accepted arbitrary provider operations, including Apple Notes, without the typed provider capability/privacy/approval contracts. | Bypassed C063–C067 and contradicted C077/ADR-0003. | Endpoint and request model removed. Notes is rejected by the external-write schema. Calendar, Reminders, Comms, and Triage retain their specific guarded command paths. |
| Calendar freshness fell back to future event start time. | Presented a schedule fact as source health and could hide a stale adapter. | Calendar and Today use provider/source update watermarks only; rows without a watermark report `unknown`, never `fresh`. |
| Legacy reminder rows with a missing account id appeared beside the same current provider item and duplicated Today/What Now. | Increased ADHD action noise while silently failing the promised non-destructive copy warning. | Read-only grouping joins a missing-account row only when one concrete account is possible. Multiple concrete accounts remain isolated; every provider row remains intact and review-required. |
| People labeled all unresolved endpoints “Needs review” and linked to an empty Triage queue. | Conflated observed endpoints with eligible identity proposals under ADR-0004. | Surface now reports “Unresolved senders” without a false action. Only deterministic evidence-backed identity candidates may enter Triage. |
| Root/bridge/pack/bootstrap/CI version manifests preserved 3.11/22 and stale Homebrew/Poetry/npm assumptions. | Kept a historical sandbox lock as future architecture and made upgrade behavior implicit. | ADR-0006/D30 set Python 3.14 and Node 24, Homebrew 6, Poetry 2.4.1, npm 11. Toolchains are side by side; Poetry is isolated; `poetry sync` replaces deprecated install-sync. |
| Upgrade compared only OpenClaw compatibility and assumed the updater's interpreter. | Could not safely express a runtime migration or prove it was intentional. | Candidate preparation resolves its own Python/Node formulas into a new release-local environment. Version-changing activation fails before service replacement without explicit migration approval; rollback remains version-aware. |
| Candidate stable wrappers prepended Node 24 before resolving an older active release. | A read-only Doctor run could inspect the staged runtime instead of the live release and falsely report degradation. | Wrappers now resolve `current-release.json` first and select the Node formula from that release's own version manifest. Post-fix Doctor proves the live 3.11/22 release healthy while the 3.14/24 candidate remains staged. |
| Built-in pack fixtures and tests retained household-specific identity tokens while the scanner omitted packs, bridge, and tests. | Violated the tenant-neutrality rule and made C083 a partial claim. | Fixtures are genericized; the executable canary scans code, bootstrap, bridge, packs, and tests without a fixture bypass. |
| Crosswalk and migration-map baseline cells still said registered/composed capabilities were absent. | Made documentary status contradictory and unusable as release evidence. | CO-005 amendments distinguish historical baseline from current executable state and retain open gaps explicitly. |
| The immutable console-reference harness hashed platform-specific line endings and allowed Node 22. | The same checked-in artifact failed integrity verification on macOS and retained the retired runtime in active tooling. | The harness normalizes the original CRLF form before checking the constitutional digest and now requires the Node 24 baseline. The current Edge-specific historical run is environment-skipped because Edge is not installed; Gate 7's Chrome-backed production-surface run passed. |

## Whole-platform constitutional result

| Area | Result after candidate corrections | Required follow-up |
|---|---|---|
| Event log, bus, replay, projections | Conformant in production composition; encrypted append-before-notify, fourteen rebuildable projections, restart/restore gates. | Repeat full replay/restore on the 3.14 candidate and at live cutover. |
| Scope, privacy, identity | Conformant for existing routes; view-as writes fail closed, Notes/People scopes are explicit, tenant canary widened. | Enroll and prove each real member bridge separately. |
| Governance and observation | Three-layer ordering is now composed and fail-closed; external seams retain independent approval/privacy/observation. | Run provider propagation tests only after separate consent; do not disable observation as part of CO-005. |
| OpenClaw boundary | Conformant and exact-pinned; channels/models/cron remain OpenClaw-owned, household truth remains AdministrateMe-owned. | Keep source/version fixed during runtime migration. |
| Today/Triage/Comms/Reminders/Calendar | Product core is coherent and linked; CO-005 fixes false freshness and duplicate/action-state noise. | Live provider delivery/propagation remains evidence, not assumed success. |
| People | Qualified directory and relationship memory conform; unresolved endpoints no longer masquerade as proposals. | A future explicit endpoint-review initiation flow must remain deterministic and separate from raw observation. |
| Notes | Domain projection, authorized search/grants, Cat-D ingest, and Triage extraction boundary conform. | No bridge is active and no Notes source is configured on this instance. Notes must remain ingest/query-only. |
| Finance | Money projection and basic member surface exist and are scoped. | Product function is materially thinner than the constitutional CoS loop; it is **REQUIRED INCOMPLETE**, not deferred. Scope a dedicated Finance change order. |
| Scoreboard | Role-safe task summary and reward language exist. | Outcome feedback, household goals, reinforcement controls, and child-safe experience are **REQUIRED INCOMPLETE**, not deferred. Scope a dedicated Scoreboard change order. |
| Settings | Identity, connections, permissions, bridge request, profiles, preferences, health, and audit controls are real and now governance-gated. | Actual bridge package export/acceptance stays outside the browser and requires target-device authorization. |
| Bootstrap/operations | Clean fixture path, exact source, policy installation, Doctor, backup/restore, launchd, and rollback are executable. | Candidate 3.14/24 clean-Mac rehearsal and explicit live migration decision remain mandatory. |

## Runtime migration boundary

Python 3.14.6 and Node 24.19.0 may be installed beside the active versions for
candidate testing. Node 24 must remain keg-only while Node 22 serves the active
release. No `brew link`, current-release pointer update, LaunchAgent rewrite,
service restart, observation change, or production venv synchronization is
authorized by this change order.

Promotion requires all of the following in order:

1. locked root and bridge install on Python 3.14;
2. plugin install/check/build/test on Node 24/npm 11;
3. full release candidate and invariant suite;
4. isolated clean bootstrap plus policy-file and interrupted-resume proof;
5. encrypted backup/restore, projection replay, and two-release rollback;
6. browser simulation of all ten member surfaces at desktop and narrow widths;
7. pre-switch backup and explicit `--approve-runtime-migration`;
8. post-switch Doctor with synthetic proof, followed by a separate decision on
   whether the household instance should remain on the candidate.

## Candidate and live verification

- Complete candidate release gate on Python 3.14.6 / Node 24.19.0: **PASS**.
  This includes 31 acceptance tests, 1,558 root/code/pack tests, 60 standalone
  bridge tests, Ruff formatting/lint, mypy, Pyright, dependency audits,
  performance budgets, package builds, architecture negatives, clean install,
  tenant isolation, backup/restore, shell validation, and the exact pinned
  OpenClaw contract/build/package tests.
- Gate 7 rendered member-surface harness on installed Google Chrome: **PASS**
  at desktop and narrow widths. Generated screenshots were not substituted for
  the retained reviewed evidence set.
- Immutable historical console-reference content digest: **PASS after portable
  CRLF normalization**. Its Edge-only interaction run is **NOT RUN** on this
  Mac because Microsoft Edge is not installed; this is not production-surface
  evidence.
- Post-candidate live Doctor with synthetic proof disabled: **HEALTHY** on the
  unchanged Python 3.11.15 / Node 22.23.2 release. Fourteen projections, five
  managed OpenClaw jobs, four secure references, encrypted event log, plugin
  discovery, loopback services, and observation state are healthy.

## Classification

- **PRESERVE:** local-first topology, event authority, scope/privacy,
  deterministic decisions, typed domain separation, exact OpenClaw seam,
  observation, provider authority, backup, and rollback.
- **MODERNIZE:** runtime/tooling baseline, fail-closed governance composition,
  freshness semantics, duplicate presentation, identity review wording, CI,
  and documentary evidence.
- **SUPERSEDE:** default-allow unknown governance, generic external mutation,
  Notes provider writes, future-start freshness, unresolved-endpoint-as-review,
  and D10 as future architecture.
- **DEFER:** optional provider enrollment and the live runtime cutover only.
  Finance and Scoreboard product gaps are required incomplete work and are not
  classified as deferrable provider edges.
