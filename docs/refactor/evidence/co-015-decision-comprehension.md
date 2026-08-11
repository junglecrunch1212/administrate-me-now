# CO-015 decision comprehension evidence

Status: released and authenticated-live-verified in observation mode.

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
| No unintended provider effects | Observation stayed active; the provider-called terminal count was zero before activation and remained zero after deployment, Doctor, and authenticated browser acceptance. |

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

Node 24.19.0 tools/console-reference-harness/run-gate7.mjs
PASS: 229 assertions, 36 screenshots, 0 console errors, 0 page errors,
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

## Reviewed release and live activation

- G-001 authorization bound PR #331 to exact head
  `ca1a29033e48d5d6d32a0cd77de3fc0c1266778f`. The independent review
  receipt covered the full diff and repair delta, complete local gates, hosted
  state, and zero review threads. Both hosted checks passed. Receipt:
  `https://github.com/junglecrunch1212/administrate-me-now/pull/331#issuecomment-5251891510`.
- GitHub created merge commit
  `2fede3f6a72f3ee6df99c0cefb7fca71471ff7fe`; it was verified on
  `origin/main` before Phase B.
- The attested same-runtime updater created encrypted backup
  `administrateme-20260811T102316Z.ambackup`, required no runtime or OpenClaw
  migration, retained predecessor `0e0f13e`, and reported rollback available.
- Independent post-switch status and full Doctor passed in observation mode:
  15/15 projections, 8/8 required pipelines, 62,580/62,580 source obligations
  accounted for, zero pending/missing/failed, exact OpenClaw and authenticated
  `llm-task`, and zero synthetic external side effects.
- Authenticated read-only live browser acceptance verified Day Brief and
  Carousel decision context, candidate + matter + view routing, exact Triage
  render/focus, explicit missing-candidate no-substitution behavior, and a
  375 px document inside a 390 px phone viewport, with no browser warnings or
  errors.
- Observation remained active. The provider-called terminal count was zero at
  the pre-switch watermark and remained zero at the final watermark. No member
  decision or provider action was performed.

## Evidence boundary

Candidate runs used generated tenant-neutral fixtures in an encrypted temporary
instance and generated artifacts outside the repository worktree. No household
content, credential, token, provider mutation, private event payload, or live
runtime path is included. Live evidence above records only content-free counts,
states, immutable commits, and the encrypted backup receipt.
