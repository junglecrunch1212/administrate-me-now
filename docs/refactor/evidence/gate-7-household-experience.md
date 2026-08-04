# Gate 7 household experience evidence

Status: **PASS**

- Branch: `codex/administrateme-v2-runtime`
- Commit: resolve with `git rev-parse HEAD` after the Gate 7 evidence commit
- Draft PR: <https://github.com/junglecrunch1212/administrate-me-now/pull/248>
- Pinned OpenClaw: `2026.7.2` at `944cf08f94c334cff2b0fdf7e01187a9efa42715`
- Generated: `2026-08-04T14:53:09.751Z`

## Proven journeys

1. A principal uses the pinned OpenClaw control plane and all eight typed
   AdministrateMe dashboard bindings: Today, Inbox, People, Knowledge,
   Finance, Calendar, Scoreboard, and Settings.
2. A non-admin adult uses the narrow member portal, receives compressed-mode
   domain views, and streams rendered assistant text through an isolated,
   signed OpenClaw-owned session without operator access or provider metadata.
3. Two adults cannot retrieve each other's transcript, session, private tool
   result, cached response, private quest, navigation entry, count, or domain
   identifiers. Ordinary conversation writes no AdministrateMe event; an
   explicit signed task proposal writes exactly one privately scoped event.
4. A child receives Today and Scoreboard only. Direct adult-route requests and
   prompt-injected requests for communications, CRM, finance, settings,
   privileged knowledge, other-member sessions, and outbound controls fail
   server-side. A shared screen receives Scoreboard only. Unauthenticated
   requests fail closed.
5. The daily loop ranks one authorized action, applies complete/defer/snooze/
   block through the existing guarded lifecycle, and immediately returns the
   recalculated next action with its evidence event.
6. A data-only Garage Quest composes existing what-now, task, list, calendar,
   and finance reads without creating domain facts. It is private by default,
   can be explicitly promoted to a named adult, and disappears after archive
   or expiry while all thirteen projections and standard surfaces remain
   intact. Invalid query/action/capability, raw-log, SQL, code, template, and
   cross-scope declarations are rejected.
7. Model-provider loss terminates chat truthfully while deterministic domain
   views stay usable. Settings and Doctor distinguish portal, chat transport,
   provider, domain-view, capability, and workbook states without secrets.

## Verification

| Check | Result |
|---|---:|
| Full Python suite | 1,276 passed, 3 skipped |
| Gate 7 acceptance and sanitizer tests | 6 passed |
| Ruff | PASS |
| mypy | PASS, 178 source files |
| Pyright | PASS, 0 errors/warnings |
| OpenClaw native plugin contract | PASS, exact version and commit |
| OpenClaw plugin tests | 12 passed |
| OpenClaw TypeScript check/build | PASS |
| npm production audit | 0 vulnerabilities |
| Python Poetry check/wheel/sdist | PASS |
| Constitutional invariant script | PASS |
| Inherited console reference | 12/12 interactions, 68/68 DOM assertions |
| Gate 7 browser harness | 78/78 checks, 18 screenshots, PASS |

The full machine-readable browser report and screenshot hashes are in
`docs/refactor/evidence/gate-7/visual/visual-results.json`. The browser matrix
uses installed Microsoft Edge through pinned `playwright-core@1.62.0` at
desktop, tablet, shared-screen, and narrow-phone widths. It covers carousel,
compressed, child, observation, degraded, streamed chat, and private,
promoted, expired, and archived quest states.

Accessibility checks prove named controls, one page heading, screen-reader
landmarks/live region, no horizontal overflow, reduced-motion suppression,
WCAG AA contrast for the core text/status token pairs, and a visible 3px
keyboard focus indicator. There were no unexpected browser console errors,
uncaught page errors, or failed requests. Every generated screenshot was
visually inspected; the final Settings view distinguishes healthy, disabled,
and workbook-not-built states.

## Human action

No household credential, provider, or deployment action is required for Gate
7. A human maintainer must review and merge draft PR 248. Live Mac mini
activation and real household-provider rehearsal remain Phase 8 work, as
recorded in `HANDOFF.md`, and do not block this gate.
