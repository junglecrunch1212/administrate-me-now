# UX behavior contract

Gate 3 backend behavior evidence (2026-07-30): pending commitment review supports explicit edit, approve, and dismiss commands; active commitments support complete, defer, snooze, block, and unblock; every write is authorized under `authMember` rather than `viewMember`; observation mode returns and audits a suppressed delivery intent; what-now returns six stable horizons with structured explanations. This gate makes no new production visual-fidelity claim, so the immutable Gate 1 console evidence below remains the visual reference.

## Evidence status

`ADMINISTRATEME_CONSOLE_REFERENCE.html` was read completely and its inline DOM/state handlers were audited at SHA-256 `98ac268f39c0d367f5c904ab2af5e0e35389eae8ff6bffd4cede7ef8bfdd0a3b`. It demonstrates eight destinations (Today, Inbox, CRM, Capture, Finance, Calendar, Scoreboard, Settings), carousel/compressed/child modes, view-as, approval interaction, reward toasts, calendar redaction, observation/degraded banners, settings panes, and chat.

Gate 1 rendered the exact file through the dev/test-only harness in `tools/console-reference-harness/`. The harness served the repository root at `http://127.0.0.1:4173`, launched installed Microsoft Edge `150.0.4078.99` headlessly through pinned `playwright-core@1.62.0`, and exercised all twelve behaviors declared in the HTML. Result: **12/12 interactions passed, 68/68 DOM assertions passed, 0 JavaScript console errors, 0 uncaught page errors, and 0 failed requests**. Page tabs were activated through keyboard focus/Enter; state-changing controls were exercised through their rendered controls.

Machine-readable results, the interaction transcript, runtime/determinism manifest, and deterministic desktop/narrow captures are in `docs/refactor/evidence/console-reference/`. Consecutive complete runs produced identical screenshot hashes: desktop `8239050463ba5dac56b5759564fec11b94eb76a3c6361182922fa830acdd2f4b`; narrow `97d1fe1129cfe2f6bc94d0797037d6fb6e7464ac0218d8dd11b49f25fc597701`.

The reference is a mock, not a security implementation. Its `innerHTML` patterns and client-side role toggles may not be copied into production without escaping and server-side authorization.

## Required surfaces

| Surface | Required behavior | Superseded behavior |
|---|---|---|
| Today | Contextual what-now for now/hour/daypart/day/week; carousel, compressed, and child presentations; energy/capacity, calendar, blockers, evidence, and profile-aware micro-steps. | A static hardcoded task carousel is demonstration data only. |
| Inbox | Unified operational items, proposal/review queue, approve/reject with evidence, state and delivery outcome; no generic transcript store. | Client-only “Approve & Send” state changes. |
| CRM | Party/relationship/interaction/commitment/artifact views, provenance, overdue relationship cues, birthday/hosting balance where authorized. | Exemplar household data and mock counts. |
| Capture/Knowledge | Member-owned knowledge, authorized household knowledge, lists, recipes, provenance, structured/semantic search. Keep the navigation concept, label it “Knowledge” where clarity helps. | Quick-capture prefix routing, direct voice ingest, and triage queue are retired by D17. |
| Finance | Scope-filtered accounts/transactions/budgets/subscriptions and workbook state; privileged owner-only details and audit. | Any client-only masking or demo values. |
| Calendar | Authorized time context, private-event busy redaction, linked domain objects, and conflict/freshness visibility. | Treating calendar as the universal task model. |
| Scoreboard | Age/role-appropriate household progress and dual-path rewards; child-safe data only. | Client-side hidden navigation as a security boundary. |
| Settings | Member/profile/persona, connectors, packs, observation, approvals, privacy/audit, backup/restore, diagnostics; progressive disclosure and explicit unavailable states. | Stale adapter inventory and broad OpenClaw operator controls. |

Generic chat remains an OpenClaw surface. A narrow chat pane may embed/hand off to OpenClaw for authorized members, but AdministrateMe must not persist a second generic transcript or expose operator administration. Members without safe operator access use a narrow member portal.

## View modes

- **Carousel (`adhd_executive`):** one primary next action, large touch target, explicit why/energy/effort/micro-step, bounded navigation, endowed progress, minimal guilt-free copy, optional reward animation respecting reduced motion.
- **Compressed (`minimalist_parent`):** dense decision queue, minimal animation, concise risk/approval summaries, keyboard-efficient action.
- **Child (`kid_scoreboard`):** Today/Scoreboard-only allowlist as configured, warm age-tuned language, no finance/CRM/admin/other-member knowledge. Forbidden routes return server-side 403/404 and no sensitive payload, even when requested directly.

Profile switches disclose gained/lost behavior and require confirmation. `authMember` remains the authority subject while `viewMember` controls the authorized view. View-as is conspicuous, reversible, audited, and never grants the viewed member’s privileged scope.

## Interaction requirements

Each `UX-*` item is executable through browser/API/accessibility tests in Gate 3.

| ID | Requirement |
|---|---|
| UX-01 | All eight required destinations are addressable by stable routes, responsive at phone/tablet/desktop widths, and keyboard navigable. |
| UX-02 | Navigation and API route availability derive from server-authorized role/capability data; direct child requests to hidden routes are denied without leaking counts or labels. |
| UX-03 | Today exposes now, next-hour, daypart, day, and week recommendations with an explanation payload; changed context deterministically changes ranking. |
| UX-04 | A proposal shows source evidence, confidence, affected object, intended external action, sender identity, approval requirements, and expiry before authorization. |
| UX-05 | Approval, rejection, completion, defer, snooze, block, correction, suppression, delivery, and failure render distinct states and link to immutable evidence. |
| UX-06 | Calendar returns a busy block—not title, attendee, location, notes, or link—for an unauthorized private event; filtering happens server-side. |
| UX-07 | Observation banner is persistent on every action-capable surface, explains the 5–7 day trust period, and links to complete would-have-happened records. |
| UX-08 | Degraded banner names the affected capability, freshness/watermark, last success, safe remaining functions, and operator action; empty, disabled, and never-configured are visibly different. |
| UX-09 | Task/commitment completion returns an immediate reward preview and canonical `reward.ready` SSE/event delivery. The UI deduplicates both paths by event/idempotency id and renders once. |
| UX-10 | Evidence/provenance views show source adapter/account, timestamps, correlation/causation, schema/version, transformation/skill provenance, correction history, and current projection freshness without exposing secrets. |
| UX-11 | Capture/Knowledge contains no quick-prefix mutation, direct voice upload, or triage affordance unless a future ADR reintroduces it with member-bridge semantics. |
| UX-12 | Approval UI cannot auto-send as a principal. Assistant-identity send is labeled; draft-for-principal is review-only; send-as-principal is unavailable/hard-refused. |
| UX-13 | Chat handoff uses per-channel-peer OpenClaw sessions and explicitly selected domain events; generic history is not copied into AdministrateMe. |
| UX-14 | All state-changing controls are idempotent under double click, reconnect, and retry and surface pending/confirmed/failed reconciliation. |
| UX-15 | Inputs and evidence are safely escaped; no untrusted value reaches `innerHTML` or equivalent unsanitized rendering. |
| UX-16 | WCAG 2.2 AA checks cover landmarks, names/roles/values, focus order/visibility, contrast, target size, error association, live regions, reduced motion, and 200% zoom/reflow. |

## State model

Every surface must implement explicit states: loading, empty, disabled-by-policy, not-configured, stale, degraded-but-readable, held-for-review, observation-suppressed, unauthorized, offline, conflict, error-with-retry, and healthy. Empty does not mean healthy; stale does not mean absent; disabled does not invite retry.

Activity feed entries are evidence-rich summaries of typed events. Notifications are separately modeled as signal -> decision -> delivery, so a delivered notification is not confused with task/commitment completion.

## Visual acceptance

Gate 1 reference evidence is complete at `docs/refactor/evidence/console-reference/run-manifest.json`: the exact baseline HTML was rendered at fixed desktop and narrow viewports, all twelve declared behaviors were exercised, and the transcript/DOM/console results were retained. This proves the reference vocabulary only. Quick-prefix capture, direct voice ingest, triage, client-only authorization, and other items named in the superseded column remain superseded and are not restored by this evidence. Later implementation acceptance uses these captures as vocabulary evidence while the superseded-flow table controls semantics. Pixel equality is not required; behavior, hierarchy, privacy, responsiveness, and accessibility are.

## Gate 2 implementation status

Gate 2 establishes the server-side route topology and truthful system health needed by the future UI: `/api/core`, `/api/comms`, `/api/capture`, `/api/automation`, `/api/bridge`, and `/api/system` share one application. It does not claim AC-18: the Gate 1 captures remain immutable design evidence, and the real browser application, role-aware navigation, accessibility, and all interaction states remain later work.
