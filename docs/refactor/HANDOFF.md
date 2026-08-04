---
schema_version: 1
current_phase: 8
phase_name: release_candidate
gate: 8
gate_result: PASS
verified_local_date: 2026-08-04
branch: codex/administrateme-v2-runtime
tested_implementation_commit: cb634433e1f3d8bd0ac34a773147367be91daca2
final_commit_resolution: git rev-parse HEAD
pull_request_url: https://github.com/junglecrunch1212/administrate-me-now/pull/248
openclaw_version: 2026.7.2
openclaw_commit: 944cf08f94c334cff2b0fdf7e01187a9efa42715
evidence_level: credential_free_fresh_worktree_apple_silicon_ci_browser_and_exact_contract
next_phase: post_merge_activation_and_trust_period
---

# Release handoff

## Outcome

AdministrateMe v2 is a reviewable release candidate. Gates 1–7 were audited
from implementation and executable evidence, not their earlier PASS markers.
Gate 6 commit `2dd60932ce639de8fed539d3d11dafd28b67824e` remains an ancestor.
All 96 constitutional crosswalk rows and AC-00–AC-23 pass at the tested
implementation commit above.

The result is a household domain system layered on OpenClaw: AdministrateMe
owns encrypted household truth, scopes, outcomes, projections, workbooks,
review, and deterministic views. OpenClaw owns general conversation, member
sessions/transcripts, model routing/retry/accounting, channel transport, and
standing-order scheduling. There is one Python composition root, one event
bus, thirteen projections, six reactive pipelines, five standing orders, ten
skills, five profiles, four personas, two workbooks, and no AdministrateMe-
owned generic model/chat/memory/scheduler runtime.

## Release repairs

- Replaced the non-executable acceptance list with AC-00–AC-23 and an aggregate
  release command.
- Added real migration, cross-instance isolation, forced-exit durability,
  architecture-negative, coverage, performance, and security evidence.
- Removed the zero-covered unused legacy crypto module and all placeholder
  skips. The complete root/pack collection now has no expected skip/xfail.
- Bound optional adapter outbound actions to the shared observation guard at
  supervisor composition.
- Wired Gateway/member allowlists and one Keychain-backed shared token into
  both runtime launchers; added a server-only exact OpenClaw activation/probe.
- Removed the retired listener/port, added the bridge lock and example, and
  updated vulnerable Python locks. Fresh root, bridge, and plugin audits report
  zero known vulnerabilities.
- Replaced the README first path, added the exact first-Mac guide, and recorded
  limitations with impact, workaround, and planned resolution.

## Evidence

- Python root and pack suite: 1,429 passed, zero skipped/xfail.
- Bridge: 60 passed. OpenClaw: exact contract, 12 tests, build/package/audit.
- Browser: 78/78 checks, 18 screenshots, zero console/page/request failures.
- Ruff/format, mypy, Pyright, Poetry locks/builds, shell/architecture checks,
  tenant isolation, backup/restore, crash recovery, and dependency audits pass.
- Coverage: 86.81% overall; events 97.02%, projections 92.72%,
  pipelines/skills 92.96%, adapters 88.89%, bridge 76.67%.
- Fresh detached worktree at release implementation parent `c8f34820`: locked root/bridge installs, 19 acceptance tests,
  clean install/Doctor, tenant isolation, backup/mutate/restore/Doctor,
  performance, exact plugin checks, and clean status pass.
- GitHub Constitution sync and Apple-silicon macOS bootstrap contract pass at
  the tested implementation commit.

Canonical detail is `docs/refactor/FINAL-VALIDATION-REPORT.md`. Gate 8 machine
evidence is under `docs/refactor/evidence/gate-8/`.

## Live-only boundary

Credential-free evidence does not claim success for a real provider, family
identity, or family Mac. After review and merge, the dedicated standard macOS
account still needs FileVault/Keychain confirmation, private Tailscale Serve,
server-side OpenClaw model/channel entry and live probe, provider consent,
member-by-member bridge enrollment, minimum macOS privacy grants, real adult
chat isolation checks, Safari/VoiceOver review, and a five-to-seven-day
observation trust period. Never put a secret in chat, the portal, browser
storage, support evidence, or a repository file.

## Known limitations

`KNOWN-LIMITATIONS.md` is authoritative. Of note: GitHub's current 78-alert
Dependabot banner describes unmerged `main` (including the retired console
lock); GitHub must recalculate after merge. Optional provider/grant behavior,
Safari/VoiceOver, unattended member bridge launchd, and long-duration target-
Mac resource use require bounded post-merge work. None is a bypass of
authorization, encryption, observation, or backup safety.

## Required human action

1. Independently review PR #248 and this report.
2. Merge only if the review accepts the architecture, evidence, rollback, and
   limitations. Do not deploy this branch or a draft.
3. On the dedicated Mac, follow `docs/operations/FIRST-MAC-BOOTSTRAP.md` from
   the exact merged SHA. Keep observation on through the trust period.
4. If review rejects the candidate, close the PR or revert the release commit;
   no family Mac or household data has been changed by Gate 8.

No automated merge is authorized or performed.
