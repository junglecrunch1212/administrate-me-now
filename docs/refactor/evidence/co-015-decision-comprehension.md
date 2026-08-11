# CO-015 decision comprehension evidence

Status: isolated candidate PASS; reviewed release and authenticated live proof
pending.

## Requirement matrix

| Requirement | Candidate evidence |
|---|---|
| Today explains the decision | Unit contract proof plus Gate 7 rendered assertion: question, what happened, why present, source, progressive uncertainty/consequences, next step, member authority. |
| Exact Today → Triage route | Gate 7 clicks Review, verifies candidate + matter + view URL state, and finds the exact `data-triage-item`. |
| Cursor-independent lookup | Production-composition acceptance requests a candidate outside a ten-matter page and receives its complete matter. |
| Resolved link | Production-composition acceptance dismisses the focused candidate, reopens the same id, and receives `resolved` plus the immutable Recent row. |
| Cross-member denial | A second principal receives `not_found` and no candidate/matter identity. |
| Deliberate lens escape | Gate 7 confirms an exact-linked item, switches from Recent to Needs details, and reaches the intended Google Tasks candidate without snapback. |
| Comms candidate integrity | Production Comms proof publishes actual candidate/matter inventory with exact `surface-link-v1`. |
| Notes candidate integrity | Gate 7 verifies candidate + matter + view parameters and no inline approval. |
| Scoreboard canonical return | Gate 7 verifies an active canonical Task returns to its exact Reminders item. |
| Ten-route contract | Unit proof constructs `surface-link-v1` for every stable adult route. |
| Responsive/accessibility/degraded behavior | Gate 7 exercises five viewport families, phone width, reduced motion, named controls, focus, landmarks, contrast, role boundaries, observation, chat degradation, and every adult tab. |
| No unintended provider effects | Credential-free runtime plus observation contracts; no new write path exists. Live observation proof remains required before release completion. |

## Candidate commands and results

```text
poetry run ruff check <changed Python and test files>
PASS

PYTHONPATH=bridge poetry run pytest -q \
  tests/unit/test_today_brief.py \
  tests/unit/test_triage_inventory.py \
  tests/unit/test_surface_contracts.py \
  tests/unit/test_comms.py \
  tests/acceptance/test_co014_context_fabric_stress.py::test_co014_production_surface_pages_every_candidate_without_scope_leak \
  tests/acceptance/test_gate7_household_experience.py::test_ac28_five_surface_executive_loop_is_typed_linked_and_human_gated
28 passed

Node 24.14.0 tools/console-reference-harness/run-gate7.mjs
PASS: 228 assertions, 36 screenshots, 0 console errors, 0 page errors,
0 request failures

PYTHONPATH=bridge bash scripts/acceptance/release-candidate.sh
PASS: 49 strict acceptance gates; 1,792 root/code/pack tests; 60 bridge
tests; Ruff/format/mypy/Pyright; dependency and package audits; performance
budgets; architecture checks; clean install; tenant isolation; backup/restore;
pinned OpenClaw contract, 17 plugin tests, build, pack, and audit
```

The first rendered run caught a stale-focus snapback after confirmation: the
server response still described the resolved exact link after the member chose
Needs details. The lens handler now consumes that focus state before rendering
the member's new choice. The complete Gate 7 rerun passes. This is evidence
that the exact-link repair includes an escape path rather than trapping the
member in a stale selection.

## Evidence boundary

The run used generated tenant-neutral fixtures in an encrypted temporary
instance and generated artifacts outside the repository worktree. No household
content, credential, token, provider mutation, or live runtime state is
included. Final release evidence must add reviewed-head checks, backup,
deployment, Doctor, authenticated live Today/Triage review, and observation-
mode side-effect proof without copying private source contents into Git.
