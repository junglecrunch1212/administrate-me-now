# Gate 8 final validation report

Historical receipt notice: prior independent-review and merge instructions in
this report describe the procedure active for their named releases. ADR-0013
supersedes those delivery instructions; no current separate reviewer task is
created by this report.

## 2026-08-08 CO-008 upgrade-controller amendment and deployment

PR #299 passed both hosted gates and merged as `d5dd805`. During guarded
activation its v9 runtime made the previously absent source work visible:
56,919 content-free obligations, 56,634 accounted, 25 pending, 260 not yet
assessed, 49 eligible-candidate assessments, seven explicit no-candidate
assessments, 39 privacy quarantines, and zero failed assessments. The pipeline
showed no failure or poison while its ordered replay prefix advanced. This is
direct evidence that the mailbox was not empty and that the source-throughput
repair produced review candidates without authorizing any member decision or
provider write.

The candidate nevertheless rolled back because the update was launched by
`update.sh` from `c484f0a`. Its ordinary path imported that active release's
old `bootstrap.upgrade`, so the old two-minute settlement policy—not PR #299's
reviewed 30-minute policy—controlled the switch. Rollback restored the prior
pointer and both services healthy. The amendment removes that circular
self-update boundary: every path builds a candidate-owned controller, rechecks
the clean commit after synchronization, attests its module source and commit,
and independently refuses missing or mismatched provenance in the manager.
Same-baseline execution remains on the active runtime; no runtime migration is
implied. The exact local aggregate passed 38 strict acceptance tests, 1,687
root/pack tests, 60 bridge tests, and 15 pinned OpenClaw plugin tests, plus
Ruff/format, mypy, Pyright, invariants, performance, dependency audits,
root/bridge packages, clean install, tenant isolation, encrypted backup/restore,
plugin build/package/audit, Bash syntax, and diff hygiene under Python 3.14 and
Node 24. Constitution sync passed in five seconds and the Apple-silicon
credential-free rehearsal passed in 10 minutes 16 seconds. PR #300 merged as
`dc99208`.

Guarded activation from the merged updater created encrypted backup
`administrateme-20260809T044355Z.ambackup` and returned an attested successful
upgrade receipt with rollback retained. Both LaunchAgents and the active pointer
resolve to `dc99208`; runtime readiness is `observation_only`. A settled live
snapshot showed 58,344/58,344 obligations accounted, zero pending/missing/
failed, v9 lag zero, no poison, 23 eligible-candidate assessments, 293 explicit
no-candidate assessments, and 39 quarantines. Full Doctor and the authenticated
OpenClaw synthetic proof passed before the updater returned.

Authenticated browser proof renders Jimmy's private Triage view, 53 visible
unconfirmed rows (20 Ready, 33 Needs details), source-accounting status, and the
complete eleven-kind Add-for-review dialog. No candidate decision or provider
effect was performed. Historical unreviewed noise is still visible for member
feedback rather than being silently deleted. The source-to-disposition and
universal-capture deployment gate is complete; the observation soak remains an
operational learning period, not missing implementation.

## 2026-08-08 CO-008 source-throughput amendment

The third guarded activation selected merged `5c85b05` after encrypted backup
and correctly restored `c484f0a` when source settlement remained incomplete.
Both services are healthy after rollback and observation mode prevented any
provider effect. A content-free encrypted-log audit counted 355 Gmail source
facts: 316 private-member and 39 legacy household-shared. Only 18 v8 terminal
assessments were retained, proving that the remaining failure was executable
throughput and ordering rather than an empty mailbox.

The repair makes scope/member refusal pre-NLP, adds opt-in eight-way delivery
for the idempotent v9 message assessor, preserves an ordered-prefix checkpoint
on mid-batch failure, widens the 4.0.4 skill timeout to 30 seconds, and permits
one slow readiness document only while runtime liveness remains true. Focused
bus, pipeline, updater, pack, and integration tests pass with Ruff. The full
release matrix, review, merge, activation, terminal source accounting, and
portal proof remain pending. The exact candidate matrix passes 37 strict
acceptance tests, 1,681 root/pack tests, 60 bridge tests, and 15 pinned OpenClaw
plugin tests, plus Ruff/format, mypy, Pyright, invariant and architecture
checks, performance budgets, dependency audits, root/bridge builds, clean
install, tenant isolation, backup/restore, plugin build/package/audit, shell
validation, and diff hygiene under Python 3.14 and Node 24.

## 2026-08-08 CO-008 replay-settlement amendment

The second guarded activation created an encrypted backup, selected merged
`4c81ac4`, and started both services, but correctly rolled back when the
two-minute transient window ended before v8 historical replay settled.
Content-free retained evidence showed 53,839 of 54,125 obligations accounted,
286 historical message assessments missing, and zero failed assessments. The
active `c484f0a` runtime and pinned Gateway were restored healthy.

The follow-up uses the same shared terminal-evidence predicate in Doctor and
updater. It runs full Doctor once, polls only `/api/system/ready` every two
seconds for at most 30 minutes while source replay advances, then runs the full
Doctor and synthetic proof once after settlement. It neither carries a
candidate cursor into rollback nor weakens terminal equality. Structural
failure remains immediate and deadline failure remains rollback-triggering.
Focused evidence is 64 passing bootstrap, runtime, and source vertical-slice
tests plus Ruff and mypy. The exact aggregate passes 37 strict acceptance
tests, 1,668 root/pack tests, 60 bridge tests, and 15 pinned OpenClaw plugin
tests, plus all lint/type, invariant, performance, dependency, build,
clean-install, tenant-isolation, backup/restore, plugin package/audit, shell,
and diff gates under Python 3.14 and Node 24. Review, merge, and live activation
remain pending.

## 2026-08-08 CO-008 deployment-repair amendment

The first guarded activation of merged PR #296 rolled back automatically after
the synchronous source-assurance scan made the five-second readiness probe time
out during replay. The prior release and both launch agents were restored. The
repair makes the mandatory initial audit blocking but all later refreshes
single-flight and stale-while-revalidate, with freshness and exception class
visible but no source content exposed. A process test holds the audit open for
500 ms and proves two readiness requests return immediately while only one
refresh runs.

The repair also replaces the ineffective v8-only invocation key with explicit
reviewed failure-retry semantics: prior successful exact fingerprints are
reused, an older causal terminal failure is attempted once, and a terminal
failure from the current fingerprint generation is reused on redelivery.
Bootstrap Doctor now has a named source-assurance check and requires fresh
accounting, equality of accounted and total obligations, and zero pending,
missing, or failed outcomes. Focused evidence is 98 passing runtime, bootstrap,
skill-runner, message-pipeline, and source-assurance tests. The exact aggregate
passes 37 strict acceptance tests, 1,666 root/pack tests, 60 bridge tests, and
15 pinned OpenClaw plugin tests, plus Ruff/format, mypy, Pyright, invariants,
performance budgets, dependency audits, root/bridge builds, clean install,
tenant isolation, backup/restore, plugin build/package/audit, shell validation,
and diff hygiene under Python 3.14 and Node 24. Review, merge, and live
reactivation remain pending and must replace this paragraph before the repair
is called deployed.

## 2026-08-08 CO-008 source-assurance amendment

The candidate adds AC-34 and closes the source-to-disposition class of failure
exposed by the live Gmail/Triage incident. Production-composition acceptance
proves explicit no-candidate and quarantine outcomes for Gmail, proposal-only
capture for every one of the eleven domain kinds, a provider mirror reaching
its named projection, and identical content-free source accounting in Doctor,
Settings, and Triage. Unit and validator evidence covers missing-versus-empty,
legacy failed assessment, privacy-before-language ordering, high-signal email
fallback, exact-source grounding, reviewed retry generations, untouched-only
legacy supersession, all five adapter categories, and fail-closed future pack
scaffolds.

The candidate command matrix is 37/37 strict acceptance tests, 1,661/1,661
root and pack tests, 60/60 bridge tests, and 15/15 exact-pinned OpenClaw plugin
tests. Ruff lint and format, mypy, Pyright, invariant checks, both dependency
audits, performance budgets, root/bridge lock checks and packages,
architecture-negative checks, clean install, tenant isolation, encrypted
backup/restore, plugin contract/build/package, shellcheck, shell syntax, and
diff hygiene pass under Python 3.14 and Node 24. The first aggregate run reached
the final production npm audit and encountered an external registry timeout;
the same audit then returned zero vulnerabilities, and the exact final
aggregate rerun passed. GitHub checks, the then-current review, and the live
deployment/replay evidence described in CO-008 remain release boundaries and
must replace this candidate wording after activation.

## 2026-08-05 primary-surface and install-readiness amendment

The release candidate now includes the complete supported bidirectional schema
and command paths for central Google Calendar and per-member Apple Calendar,
Apple Reminders, and Apple Notes. It adds calendar/list discovery, recurrence,
alarms, reminder time/location triggers, completion/change outcome feedback,
Notes folder and note mutations, durable bridge commands, and deterministic
projection enrichment. Review also found and fixed an install-critical
composition defect: the production service parsed `config/adapters.json` but
did not pass it to `RuntimeSettings`, so centrally configured adapters would
not have started after reboot. Review also removed a best-effort exception
swallow from all three primary sync adapters: a failed event-log or bridge
ingest write must now return a durable event ID before local sync state moves.

The operator read-along now separates the central CoS service from one
per-member macOS bridge, gives exact macOS consent and Tailscale steps, prevents
Google/EventKit duplication, and keeps the initial install credential-free in
observation mode. Provider-native execution still requires live OAuth,
EventKit full access, Notes Automation consent, iCloud propagation, and
member-isolation checks on the actual Macs; these are documented activation
gates rather than preclaimed test results.

Local amendment evidence is 21 strict acceptance tests, 1,455 root/pack tests,
60 bridge tests, and 25 focused primary-surface/bootstrap tests, with Ruff,
format, mypy, production Pyright, dependency audits, root/bridge builds,
architecture-negative checks, clean install, tenant isolation, encrypted
backup/restore, OpenClaw contract/12-test/build/package/audit, and every
performance budget passing. The final Apple-silicon aggregate and shellcheck
remain mandatory green checks on the exact PR head before merge.

## 2026-08-04 bootstrap remediation addendum

The historical Gate 8 report below describes the earlier implementation
boundary. A later operator audit reopened the candidate because OpenClaw cron
declarations were not reconciled, member bridge packages had no safe consumer,
and the pinned OpenClaw version was not available from npm. The current
candidate closes all three code gaps with owned/verified cron reconciliation,
a transactional member enrollment lifecycle, and a checksum-pinned GitHub
source build. It also makes central and member release extraction atomic and
receipt-backed.

The complete remediated tree passed 21 acceptance tests, 1,446 root/pack tests,
60 bridge tests, 12 OpenClaw plugin tests, and 78 rendered-browser checks. Ruff,
format, mypy, Pyright, root/bridge builds and audits, performance budgets,
architecture-negative checks, clean install, tenant isolation, and encrypted
backup/restore also pass. GitHub Apple-silicon `macos-15` run 30964802418 then
passed the exact source build, credential-free bootstrap/recovery, static
checks, and the whole release-candidate suite at implementation commit
`95f2fe8d0a007ecf4a000057ab456a4b49a2d5f2`. Exact results and the remaining
independent/live gates are in `docs/refactor/HANDOFF.md`; the code-review
disposition is in `docs/operations/BOOTSTRAP-READ-ALONG-AUDIT.md`.

The current workflow uses GitHub's maintained Apple-silicon `macos-15` label;
the historical `macos-14` evidence below remains evidence for the older
boundary, not the runner selected for the remediated candidate.

This addendum supersedes old counts and deployment wording in the historical
sections below. It does not convert credential-free evidence into a claim that
a real family Mac, provider login, Apple privacy grant, Tailscale Serve edge,
or live OpenClaw Gateway has passed. Independent review, merge only with green
final-SHA checks, and a disposable Apple-silicon rehearsal remain mandatory.

## Verdict and tested boundary

**Release acceptance verdict: PASS.** All AC-00–AC-23 requirements pass at
tested implementation commit
`cb634433e1f3d8bd0ac34a773147367be91daca2` on
`codex/administrateme-v2-runtime`. Gate 6 commit
`2dd60932ce639de8fed539d3d11dafd28b67824e` is an ancestor. The final
documentation commit is intentionally later and changes no executable code.

The test boundary used no household data, live provider credential, family
Mac, or external mutation. It used real SQLCipher files, event/bus/projection
composition, FastAPI routes, backup archives, restore staging, OpenClaw plugin
code, and rendered browser pages with deterministic provider and household
fixtures.

## Environments

| Environment | Purpose | Result |
|---|---|---|
| Fresh detached Windows worktree at release implementation parent `c8f34820`; Windows 11 x64, Python 3.12.10 (lock-compatible local host), Poetry 2.2.1 | Independent locked install, acceptance, bridge, recovery, performance, and plugin rehearsal | PASS; worktree clean after all checks and then removed. Later deltas affect only CI/release-check portability and locked-environment selection; the Apple-silicon run below tests all of them |
| GitHub `macos-14` Apple-silicon runner; Python 3.11, Node 22.23.1 | Supported architecture, shellcheck/plutil, exact locks, credential-free bootstrap/recovery, and whole-repository release suite | PASS at the tested implementation SHA; run 30933307500 |
| Local Microsoft Edge through `playwright-core@1.62.0` | Responsive, reduced-motion, accessibility, authorization, chat, and temporary-surface rendering | 78/78 PASS; 18 screenshots; zero console/page/request failures |

The local host's Node 24.14.1 is below the plugin's alternate Node 24.15
range, so the authoritative plugin runtime result is the target CI pin,
Node 22.23.1. Local TypeScript checks nevertheless passed.

## Inventory and dependency graph

The audit rebuilt the inventory from the repository rather than copying Gate
6 counts: 1,805 tracked files, 446 Python files, 23 TypeScript/MJS files, and
19 shell entrypoints at the pre-report commit.

Production ownership is:

1. Stable shell commands call `bootstrap.cli`, which owns the ten-section
   wizard, service control, Doctor, activation, backup/restore, and upgrade.
2. User LaunchAgents call `bootstrap.runtime_service` and
   `bootstrap.openclaw_service`. Both load secrets by Keychain reference.
3. `AdministrateMeRuntime` owns the encrypted event log, one event bus,
   thirteen projections, six reactive pipelines, six constitutional product
   functions, and the narrow member portal delivery namespace.
4. The native TypeScript plugin owns only the AdministrateMe/OpenClaw seams;
   OpenClaw retains sessions, transcripts, model routing, retries, memory,
   channels, and standing-order scheduling.
5. `adminme-bridge` loads member-bound connector packs and can send only the
   allowlisted private ingest envelope to the loopback/Tailscale runtime edge.
6. Pack manifests explain dynamically loaded adapters, pipelines, skills,
   profiles, and personas. Reference/vendor snapshots are documentation inputs,
   not executable entrypoints.

Every retained first-party module was classified as runtime-reachable,
shell/CLI-reachable, manifest-loaded, test/rehearsal support, migration or
compatibility code, or a documented reference. The one zero-covered legacy
module, `adminme/lib/crypto.py`, had no retained caller and was deleted.

## Constitutional product matrix

| Surface | Code and executable evidence | Capability state |
|---|---|---|
| Six product functions: Core, Capture, Comms, Automation, Bridge, OpenClaw | Runtime router inventory, runtime spine, communication and member-knowledge slices | Healthy credential-free; provider-specific edges truthfully disabled/degraded |
| Five layers | Adapter, event/bus, projection, pipeline/skill, and product tests | Healthy; dependency direction preserved |
| Cat-A–E adapters | Base/manifest/supervisor/dual-deployment tests and observation seam integration | Contract healthy; live optional providers disabled until explicit activation |
| Thirteen projections | Exact registry, SQLCipher migration, replay/rebuild, scope tests | Healthy; no fourteenth projection |
| Eleven pipeline packs | Six reactive discovery tests and five compiled OpenClaw standing orders | Healthy; OpenClaw alone owns schedules |
| Four OpenClaw seams | Typed skill, slash command, standing order, and scoped member-chat tests | Contract healthy; real provider/Gateway activation remains live-only |
| `adminme-ops.xlsx` and `adminme-finance.xlsx` | Forward/reverse, lock, sidecar, conflict, and replay suites | Healthy and rebuildable |
| Pack install/rollback; ten skills; five profiles; four personas | Registry, validator, loader, compiler, authority-boundary suites | Healthy |
| Eight domain surfaces; three experience modes; twelve interaction contracts | Gate 7 acceptance plus browser harness | Healthy |
| Ten bootstrap sections and member bridge enrollment | macOS operations acceptance and clean-install scripts | Healthy credential-free; human grants remain live-only |
| Calendar/task/commitment/list/recurrence semantics | Schema/projection/API suites | Distinct and linked only by typed outcomes |
| Observe→outcome→next-action loop | Communication slice, completion/deferral, What Now tests | Closed across immediate/day/week horizons |
| Adult CoS chat and temporary quests | Gate 7 abuse/lifecycle suite and browser harness | Private, isolated, bounded, non-executable, and non-contaminating |

All 96 constitutional crosswalk rows C001–C096 were reread against the
complete branch diff. Every `SUPERSEDE` row points to ADR-0000/ADR-0001 or a
newer governing source. The sole `DEFER` row, C085, contains optional providers
only; no core domain, privacy, projection, workbook, chat, bootstrap, or backup
function is deferred.

## Command and result matrix

| Command/check | Result |
|---|---|
| `pytest tests packs -q --import-mode=importlib` with coverage | 1,429 passed; zero skip/xfail; one Starlette deprecation warning |
| Fresh-worktree `pytest tests/acceptance -q --runxfail` | 19 passed |
| Bridge locked suite | 60 passed |
| OpenClaw `check`, contract, Vitest, build, pack dry-run, production audit | PASS; 12 tests; exact 2026.7.2 / `944cf08...`; zero vulnerabilities |
| Browser `test:gate7` | 78/78 checks; 18 screenshots; zero browser errors |
| Ruff lint and format | PASS; 445 first-party Python files formatted |
| mypy | PASS; 192 source files |
| production Pyright | PASS; 0 errors, 0 warnings |
| Root and bridge lock checks, wheel/sdist builds | PASS; metadata deprecation warnings only |
| Root and bridge `pip-audit --local` from fresh locks | Zero known vulnerabilities; local unpublished packages skipped by name |
| Architecture negative checks | PASS |
| Clean install / compatibility path / Doctor | PASS |
| Two-instance tenant isolation | 3 passed; script PASS |
| Backup, mutate, restore, rebuild, Doctor | PASS |
| Performance smoke | PASS within every declared budget |
| `git diff --check`, no-skip scan, fresh-worktree status | PASS / none / clean |
| GitHub Constitution sync | PASS at tested implementation SHA |
| GitHub Apple-silicon bootstrap contract | PASS at tested implementation SHA |

Gate 8 used CI failures as rehearsal evidence. Run 30932523921 found that BSD
`grep -r` rendered the allowed wrapper path as `adminme//lib/...`; the filter
became separator-tolerant without weakening the rule. Run 30932851713 then
found four historical shellcheck style findings after the invariant script was
added to shellcheck. Run 30932954753 found that its final projection-count
import used bare runner Python after bridge setup. Those output paths were
cleaned up and every aggregate recovery script was bound to the locked root
environment. Final run 30933307500 passed every step at `cb634433`.

## Coverage and material uncovered behavior

Coverage is branch coverage's statement-line report, not a release target.
Evidence is retained in `evidence/gate-8/python-coverage.json` and
`bridge-coverage.json`.

| Subsystem | Covered / statements | Coverage |
|---|---:|---:|
| Whole Python release tree | 14,031 / 16,163 | 86.81% |
| Events/log/bus | 1,532 / 1,579 | 97.02% |
| Runtime and products | 1,487 / 1,799 | 82.66% |
| Projections | 1,592 / 1,717 | 92.72% |
| Pipelines and skills | 1,928 / 2,074 | 92.96% |
| Adapters | 3,561 / 4,006 | 88.89% |
| Bootstrap | 1,116 / 1,653 | 67.51% |
| Bridge | 460 / 600 | 76.67% |

`bootstrap/cli.py` is the only zero-reported production file because its
commands are invoked in child processes and that child coverage is not merged
into the parent JSON. The clean-install, section-resume, Doctor, service,
backup/restore, activation, and upgrade process tests execute those paths.
Material uncovered behavior is concentrated in live macOS/provider branches,
not domain authorization or durability seams.

## Fault injection and recovery

| Seam | Evidence and result |
|---|---|
| Durable append / crash | Real child process appends to SQLCipher and calls `os._exit(86)`; committed event survives |
| Bus delivery / checkpoint / poison | Redelivery, append-before-publish, checkpoint persistence, bounded retry, and isolation tests pass |
| Projection transaction / replay | Transaction rollback and deterministic rebuild from event zero pass for all thirteen |
| Pipeline retry / idempotency | Duplicate, stale, retry, terminal, and correlation/causation tests pass |
| OpenClaw transport | Outage/restart, cancellation, bounded queue, protocol refusal, and no-duplicate-send tests pass |
| Approval / observation / notification | Independent approval, final outbound suppression, result ingestion, retry, and terminal audit pass |
| Provider watermark | Cursor milestones, restart, sensitivity floor, and dual-deployment tests pass |
| Shutdown / startup unwind | Repeated stop, partial-start failure, bus/pipeline/projection unwind pass |
| Backup / restore | Wrong key, tamper, staging validation, rollback copy, restore, rebuild, and post-restore Doctor pass |

## Privacy and security review

- Two separately keyed instances accept the same public IDs without crossing
  events, projections, member knowledge, HTTP views, or OpenClaw session keys.
- `authMember` authorizes writes; `viewMember` never does. Principal, adult,
  child, shared-screen/device, and unauthenticated direct calls fail at the
  server edge where required.
- Optional outbound adapters receive the observation session, manager, and
  event sink from the supervisor before start. Missing live configuration stays
  disabled/degraded rather than pretending to send.
- The Gateway and runtime are loopback-only; Tailscale identity is resolved
  server-side. Legacy port 3337 and broad browser/operator tokens are absent.
- Runtime, plugin, support bundle, events, logs, browser storage, and client
  responses expose no API key, Gateway token, hidden prompt, raw provider
  envelope, or service credential. Secrets remain in Keychain/OpenClaw.
- Temporary surfaces accept named allowlisted queries and presentation data
  only. Raw logs, SQL, code, templates, invalid actions, and privilege
  promotion fail closed.
- Direct model SDK imports, duplicate scheduler/chat/model/memory runtimes,
  manual bus notifications, and a fourteenth projection are absent.
- Fresh root and bridge locks plus retained plugin production dependencies
  audit with zero known vulnerabilities. GitHub's 78-alert banner is for
  unmerged `main`; its critical item is in the retired console lockfile and its
  Python alerts refer to locks replaced by this PR. GitHub must recalculate
  after merge.

## Member-chat and temporary-surface proof

Adult session keys are signed from tenant/member/channel/account identity.
History, rendered text, tool results, and cancellation cannot cross members.
Ordinary chat had an event-log delta of exactly zero. A typed explicit proposal
retained private owner scope and traversed the existing authorization,
observation, governance, and idempotency seams. Child injection and direct
tool calls, shared-screen chat, operator/model controls, raw envelopes, and
credential access were denied. Provider loss returned an honest unavailable
state while deterministic domain views remained usable.

Quest creation defaults private. Cross-member discovery/evaluation fails.
Promotion requires explicit review and named audience, with an additional
child-safe allowlist. Expiry/archive removes only navigation/lens state;
replay/restart retains facts, projections, standard surfaces, and audit
history. Changing/evaluating a lens produced no stable-domain event.

## Performance

The fresh-worktree production composition used 100 task appends, 50 Today
queries, ten ordinary chat streams, and 50 five-query temporary-view
evaluations. Provider latency and long-duration idle behavior were excluded.

| Metric | Recorded release evidence | Budget |
|---|---:|---:|
| Cold start | 3,045.68 ms | 30,000 ms |
| Append + projection p95 | 64.23 ms | 1,000 ms |
| Task projection rebuild | 73.72 ms | 15,000 ms |
| Today query p95 | 59.47 ms | 1,000 ms |
| Chat fixture p95 | 13.32 ms | 2,000 ms |
| Five-query lens p95 | 74.23 ms | 1,000 ms |
| Python traced peak | 4.85 MiB | 256 MiB |
| Encrypted event storage after 100 tasks | 2,381,176 bytes | 10 MiB |

The independent fresh worktree also passed with cold start 2,378.84 ms,
append/projection p95 60.97 ms, and peak 5.0 MiB. Long-duration idle CPU,
memory, disk growth, provider backoff, and real network latency remain trust-
period measurements.

## Clean-room rehearsal

The fresh detached worktree installed 93 root and 91 bridge locked packages,
then passed strict acceptance, compatibility install, Doctor/status,
architecture checks, tenant isolation, backup/mutate/restore/Doctor, bridge
tests, performance, and the exact plugin contract/build/audit. Its tracked
status remained empty. The directory was deleted after recording the SHA and
clean result.

The GitHub Apple-silicon job additionally ran shellcheck, Bash syntax, plutil,
Python 3.11, Node 22.23.1, exact locked installs, bootstrap section-resume,
launchd contracts, service lifecycle, recovery, and the aggregate release
script. It used test-mode services and no live provider grant.

## Live versus deterministic boundary

Live-only post-merge checks are: FileVault/Keychain on the deployment account;
Tailscale identity and private Serve; real OpenClaw install/inspect/deep RPC;
server-side model/channel configuration and model probe; OAuth/provider consent;
Calendar/Reminders/Contacts/Notes/microphone grants; member-by-member bridge
enrollment; Safari/VoiceOver; and the five-to-seven-day trust period. Exact
instructions are in `docs/operations/FIRST-MAC-BOOTSTRAP.md`. No secret belongs
in chat, the member portal, a browser setting, a support bundle, or this report.

## Known limitations

`KNOWN-LIMITATIONS.md` is authoritative. No listed item bypasses
authorization, encryption, observation, or backup safety. The important limits
are live provider/macOS consent, Safari/VoiceOver review, unautomated bridge
LaunchAgent installation, long-duration target-Mac resource observation,
legacy Poetry metadata warnings, default-branch Dependabot recalculation, and
deferred optional providers.

## Requirement-by-requirement verdict

| AC | Verdict | Evidence summary |
|---|---|---|
| 00 | PASS | Diff hygiene and clean detached worktree |
| 01 | PASS | Ten-section clean install, resume, Doctor, redaction |
| 02 | PASS | One runtime, bus, 13 projections, products, orderly shutdown |
| 03 | PASS | Communication→proposal→approval→outcome slice |
| 04 | PASS | Owner-stamped bridge knowledge and correction/replay |
| 05 | PASS | Distinct domain semantics and typed feedback |
| 06 | PASS | Contextual What Now and immediate recalculation |
| 07 | PASS | SQLCipher, append-only, exact registry, rebuild |
| 08 | PASS | Role/owner/view/sensitivity/direct-call isolation |
| 09 | PASS | Append-before-publish, checkpoint, retry, forced exit |
| 10 | PASS | Cat-A–E, validators, supervisors, cursors, dual deployment |
| 11 | PASS | Governance, approval, observation, notification terminal state |
| 12 | PASS | Exact native OpenClaw contract; live provider activation separated |
| 13 | PASS | Both workbooks, reverse sync, locks, sidecars, conflicts |
| 14 | PASS | Truthful healthy/degraded/blocked/disabled states |
| 15 | PASS | Encrypted backup, tamper/wrong-key refusal, staged restore |
| 16 | PASS | Two keyed instances and same-ID isolation |
| 17 | PASS | Six pack kinds, install/rollback, skills/profiles/personas |
| 18 | PASS | Eight surfaces, three modes, 78 browser checks |
| 19 | PASS | Loopback/Tailscale edge, spoof denial, retired listener absent |
| 20 | PASS | v1→v3 migration, data preservation, crash/shutdown recovery |
| 21 | PASS | Apple-silicon credential-free GitHub rehearsal |
| 22 | PASS | Architecture negative checks and exact projection count |
| 23 | PASS | Isolated no-event chat and private non-contaminating quests |

## Release decision

No P0 or P1 defect remained in that credential-free release boundary. Under
current ADR-0013, required GitHub CI and protected merge govern delivery;
deployment is separately owner-requested and uses the bounded post-merge
activation checklist.
