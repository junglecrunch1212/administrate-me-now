# UX behavior contract

Gate 7 production status (amended 2026-08-06): the real member portal now implements
the ten stable adult surfaces, role-derived navigation/routes, carousel,
compressed, and child modes, evidence dialog, lifecycle-to-next-action
feedback, workbook/capability states, the narrow OpenClaw chat relay, and
private disposable quest navigation. `tools/console-reference-harness/
run-gate7.mjs` now renders and inspects 22 deterministic captures across
1440×1000, 1280×900, 1024×768, 820×1180, and 390×844. Its accessibility and
all 97 browser checks pass with no unexpected console/page/request failures. The
pixel review caught and corrected a disabled-provider/Ready labeling defect;
Settings now shows distinct disabled, healthy, degraded, blocked,
unavailable, and not-built states with reasons.

Today amendment (2026-08-06): Today is now a deterministic Daily Brief, not a
raw list dump or a profile/debug label. The default Day Brief synthesizes the
member's local date, timed calendar events, executable work, individual
recurrences, ordered Routine occurrences, actionable messages, and clarifying decisions from authorized
projections. An explicit Day Brief/Carousel switch keeps the same typed queue;
What Now opens Carousel on the deterministic current card: a timed event in
progress or starting within 30 minutes, otherwise the highest-ranked work card. Carousel actions
are derived by item kind, and `Later` moves only the viewport—it never mutates
due dates, status, or household truth. The portal and pinned OpenClaw dashboard
consume the same read-time brief contract. No model chooses, orders, completes,
or infers attendance for these items. Routine `Step done` and `Skip step`
advance only the current step of the dated occurrence; they never complete a
linked Task implicitly.

Gate 3 backend behavior evidence (2026-07-30): pending commitment review supports explicit edit, approve, and dismiss commands; active commitments support complete, defer, snooze, block, and unblock; every write is authorized under `authMember` rather than `viewMember`; observation mode returns and audits a suppressed delivery intent; what-now returns six stable horizons with structured explanations. This gate makes no new production visual-fidelity claim, so the immutable Gate 1 console evidence below remains the visual reference.

## Evidence status

`ADMINISTRATEME_CONSOLE_REFERENCE.html` was read completely and its inline DOM/state handlers were audited at SHA-256 `98ac268f39c0d367f5c904ab2af5e0e35389eae8ff6bffd4cede7ef8bfdd0a3b`. It demonstrates eight historical destinations (Today, the former Inbox now called Triage, CRM, Capture, Finance, Calendar, Scoreboard, Settings), carousel/compressed/child modes, view-as, approval interaction, reward toasts, calendar redaction, observation/degraded banners, settings panes, and chat.

Gate 1 rendered the exact file through the dev/test-only harness in `tools/console-reference-harness/`. The harness served the repository root at `http://127.0.0.1:4173`, launched installed Microsoft Edge `150.0.4078.99` headlessly through pinned `playwright-core@1.62.0`, and exercised all twelve behaviors declared in the HTML. Result: **12/12 interactions passed, 68/68 DOM assertions passed, 0 JavaScript console errors, 0 uncaught page errors, and 0 failed requests**. Page tabs were activated through keyboard focus/Enter; state-changing controls were exercised through their rendered controls.

Machine-readable results, the interaction transcript, runtime/determinism manifest, and deterministic desktop/narrow captures are in `docs/refactor/evidence/console-reference/`. Consecutive complete runs produced identical screenshot hashes: desktop `8239050463ba5dac56b5759564fec11b94eb76a3c6361182922fa830acdd2f4b`; narrow `97d1fe1129cfe2f6bc94d0797037d6fb6e7464ac0218d8dd11b49f25fc597701`.

The reference is a mock, not a security implementation. Its `innerHTML` patterns and client-side role toggles may not be copied into production without escaping and server-side authorization.

## Required surfaces

| Surface | Required behavior | Superseded behavior |
|---|---|---|
| Today | A local-time Daily Brief synthesizing calendar, executable tasks/commitments/list items, individual recurrences, ordered Routine occurrences, actionable messages, and clarifying decisions; explicit Day Brief/Carousel switching; contextual what-now for now/hour/daypart/day/week; energy/capacity, blockers, evidence, and profile-aware micro-steps. | A static hardcoded task carousel, a raw “Open lists” card, a recurrence presented as an ordered Routine, or a profile/member identifier presented as the current view. |
| Triage | Member-owned queue containing only extracted/derived candidates; confirm, enrich/reroute, or dismiss with source evidence and terminal outcome. Raw provider observations remain facts; already-official tasks never appear here. | The former Inbox mixture of proposals plus open work; client-only “Approve & Send”; generic transcript storage. |
| Comms | Member-isolated unified communications cockpit with combined and per-account/workspace views, deterministic Focus/Waiting/Snoozed/Quieted/All lenses, thread reader, explicit provider capabilities, inline governed actions, local focus controls, and linked Triage extraction status. | A copied global mailbox, model-ranked inbox, second assistant transcript, implicit provider capability, cross-member/workspace union, or direct extraction-to-calendar/task/list write. |
| Reminders | Member-isolated Apple Reminders and Google Tasks cockpit with combined/per-account/per-list filters; deterministic Now/Scheduled/Places/Anytime/Completed lenses; explicit explanations; quick capture; provider-native edit, move, completion, list, alarm, recurrence, nesting, and ordering actions only where supported; typed expiring context cues from authorized location, presence, device, network, calendar, weather, or member-state facts. | A model-ranked list, fabricated provider parity, silent conversion to a Task/Commitment/calendar event, inferred location/presence, provider state copied into a second truth store, or a UI click treated as terminal provider success. |
| CRM | Party/relationship/interaction/commitment/artifact views, provenance, overdue relationship cues, birthday/hosting balance where authorized. | Exemplar household data and mock counts. |
| Capture/Knowledge | Member-owned knowledge, authorized household knowledge, lists, recipes, provenance, structured/semantic search. Keep the navigation concept, label it “Knowledge” where clarity helps. | Quick-capture prefix routing, direct voice ingest, and triage queue are retired by D17. |
| Finance | Scope-filtered accounts/transactions/budgets/subscriptions and workbook state; privileged owner-only details and audit. | Any client-only masking or demo values. |
| Calendar | Member-isolated Day/Week/Month/Agenda/Find time cockpit plus progressively disclosed Rules over every authorized provider calendar; anchored date navigation; spatial time grids and all-day lanes; route-stable source colors and visibility controls; route-scoped provenance; duplicate-copy grouping; explicit hard-block, soft-load, context, and ignore semantics; tentative/custody policy; typed buffers; protected household windows; conflict/pressure explanations; provider capability truth; and reviewed write destinations shared with Triage. | Treating calendar as the universal task model, treating every observed event as busy, equating a hidden display calendar with free time, model-decided availability, silent duplicate deletion, or writing to an unreviewed calendar. |
| Scoreboard | Age/role-appropriate household progress and dual-path rewards; child-safe data only. | Client-side hidden navigation as a security boundary. |
| Settings | Member/profile/persona, connectors, packs, observation, approvals, privacy/audit, backup/restore, diagnostics; progressive disclosure and explicit unavailable states. | Stale adapter inventory and broad OpenClaw operator controls. |

Generic chat remains an OpenClaw surface. A narrow chat pane may embed/hand off to OpenClaw for authorized members, but AdministrateMe must not persist a second generic transcript or expose operator administration. Members without safe operator access use a narrow member portal.

Gate 7 implements that exception as an authenticated streaming relay only.
The browser receives rendered text and safe stream phases; OpenClaw retains
history and all provider/runtime responsibilities. View-as chat is refused.
Child chat is guardian-enabled and its server tool allowlist is narrower than
adult portal navigation.

## View modes

- **Carousel (`adhd_executive`):** an explicit member-selected Today lens with one primary next action, large touch target, explicit why/energy/effort/micro-step, bounded navigation, endowed progress, minimal guilt-free copy, optional reward animation respecting reduced motion. `Later` is view-only; a real defer/snooze command is labeled separately and writes typed state.
- **Compressed (`minimalist_parent`):** dense decision queue, minimal animation, concise risk/approval summaries, keyboard-efficient action.
- **Child (`kid_scoreboard`):** Today/Scoreboard-only allowlist as configured, warm age-tuned language, no finance/CRM/admin/other-member knowledge. Forbidden routes return server-side 403/404 and no sensitive payload, even when requested directly.

Profile switches disclose gained/lost behavior and require confirmation. `authMember` remains the authority subject while `viewMember` controls the authorized view. View-as is conspicuous, reversible, audited, and never grants the viewed member’s privileged scope.

## Interaction requirements

Each `UX-*` item is executable through browser/API/accessibility tests in Gate 3.

| ID | Requirement |
|---|---|
| UX-01 | All ten required adult destinations are addressable by stable routes, responsive at phone/tablet/desktop widths, and keyboard navigable. |
| UX-02 | Navigation and API route availability derive from server-authorized role/capability data; direct child requests to hidden routes are denied without leaking counts or labels. |
| UX-03 | Today defaults to a local-time Day Brief and can switch to one-item Carousel without changing truth. It exposes calendar, executable work, individual recurrence, ordered Routine occurrence, actionable-message, and clarifying-decision sections plus now, next-hour, daypart, day, and week recommendations with explanation payloads. What Now deterministically prioritizes an in-progress or next-30-minute timed event before ranked work; changed context changes ranking. Routine actions handle only the current step and preserve linked Tasks. |
| UX-04 | A Triage candidate shows source evidence, confidence, affected object, intended target member/provider/container, external action, sender identity, approval requirements, and expiry before authorization. |
| UX-05 | Approval, rejection, completion, defer, snooze, block, correction, suppression, delivery, and failure render distinct states and link to immutable evidence. |
| UX-06 | Calendar returns a busy block—not title, attendee, location, notes, or link—for an unauthorized private event; filtering happens server-side. |
| UX-07 | Observation banner is persistent on every action-capable surface, explains the 5–7 day trust period, and links to complete would-have-happened records. |
| UX-08 | Degraded banner names the affected capability, freshness/watermark, last success, safe remaining functions, and operator action; empty, disabled, and never-configured are visibly different. |
| UX-09 | Task/commitment completion returns an immediate reward preview and canonical `reward.ready` SSE/event delivery. The UI deduplicates both paths by event/idempotency id and renders once. |
| UX-10 | Evidence/provenance views show source adapter/account, timestamps, correlation/causation, schema/version, transformation/skill provenance, correction history, and current projection freshness without exposing secrets. |
| UX-11 | Capture/Knowledge contains no quick-prefix mutation, direct voice upload, or triage affordance unless a future ADR reintroduces it with member-bridge semantics. |
| UX-12 | Approval UI cannot auto-send as a principal. Assistant-identity send is labeled; draft-for-principal is review-only; send-as-principal is unavailable/hard-refused. |
| UX-13 | Chat handoff uses one isolated OpenClaw agent per member plus account-aware peer sessions for external DMs and explicitly selected domain events; generic history is not copied into AdministrateMe. |
| UX-14 | All state-changing controls are idempotent under double click, reconnect, and retry and surface pending/confirmed/failed reconciliation. |
| UX-15 | Inputs and evidence are safely escaped; no untrusted value reaches `innerHTML` or equivalent unsanitized rendering. |
| UX-16 | WCAG 2.2 AA checks cover landmarks, names/roles/values, focus order/visibility, contrast, target size, error association, live regions, reduced motion, and 200% zoom/reflow. |
| UX-17 | Comms authorizes every account independently before query-time union; retains member/provider/account/workspace/thread/message provenance; exposes only exact-account capabilities; and denies children, devices, cross-member reads, and all view-as mutations. |
| UX-18 | Comms Focus and noise suppression are fixed, explained rules over typed state. Snooze/quiet are local event-sourced state; reply/read/archive are governed provider commands with pending/completed/failed reconciliation; extracted work can become canonical only through Triage. |
| UX-19 | Reminders preserves provider/member/account/list/item/revision provenance and negotiates exact provider capabilities. Apple EventKit and Google Tasks limitations remain visible; actions are self-only, optimistic, independently approved, governance-guarded, observation-aware, and terminal only after provider evidence. |
| UX-20 | Reminders activation lenses and contextual cues are deterministic code over typed, scoped, expiring facts. A cue changes only presentation/notification eligibility; it cannot change provider truth or materialize a Task, Commitment, calendar event, recurrence, or scheduler job. |
| UX-21 | Calendar discovery records account/calendar identity and provider read/write capability independently from per-member policy. Availability, duplicate grouping, buffers, conflicts, protected windows, and find-time results are deterministic functions of provider facts, principal-approved rules, schedule policy, timezone, and an explicit clock; no model ranks or chooses time. |
| UX-22 | Triage exposes only reviewed writable calendar destinations and one confirmation authorizes both canonical materialization and the independently governed provider command. Today consumes the same deduplicated effective-event contract; transparent/context events never become hard blocks, and soft load remains visible without silently forbidding a member choice. |
| UX-23 | Calendar date navigation requests an explicit, timezone-local Day, Monday-based Week, six-week Month, or fourteen-day Agenda/Find-time range from the production composition root. Per-calendar visibility and capacity-versus-source color lenses are presentation-only: hidden facts still participate in deterministic capacity, conflicts, guardrails, Today, and Triage routing. Color is never the sole signal; event blocks retain text labels, effect language, source provenance, and keyboard-operable detail. |

## State model

Every surface must implement explicit states: loading, empty, disabled-by-policy, not-configured, stale, degraded-but-readable, held-for-review, observation-suppressed, unauthorized, offline, conflict, error-with-retry, and healthy. Empty does not mean healthy; stale does not mean absent; disabled does not invite retry.

Activity feed entries are evidence-rich summaries of typed events. Notifications are separately modeled as signal -> decision -> delivery, so a delivered notification is not confused with task/commitment completion.

## Visual acceptance

Gate 1 reference evidence is complete at `docs/refactor/evidence/console-reference/run-manifest.json`: the exact baseline HTML was rendered at fixed desktop and narrow viewports, all twelve declared behaviors were exercised, and the transcript/DOM/console results were retained. This proves the reference vocabulary only. Quick-prefix Capture, direct voice ingest, Capture-owned triage, client-only authorization, and other items named in the superseded column remain superseded and are not restored by this evidence. D26's separate Triage adjudication surface does not restore Capture as an ingest product. Later implementation acceptance uses these captures as vocabulary evidence while the superseded-flow table controls semantics. Pixel equality is not required; behavior, hierarchy, privacy, responsiveness, and accessibility are.

The member shell binds its stylesheet and JavaScript to a shared content hash
and serves both HTML and assets with `no-store`. An immutable runtime release
must never run against a browser-cached client bundle from another release.

## Gate 2 implementation status

Gate 2 establishes the server-side route topology and truthful system health needed by the future UI: `/api/core`, `/api/comms`, `/api/capture`, `/api/automation`, `/api/bridge`, and `/api/system` share one application. It does not claim AC-18: the Gate 1 captures remain immutable design evidence, and the real browser application, role-aware navigation, accessibility, and all interaction states remain later work.

## Gate 7 implementation status

AC-18's production portal portion is complete. Material values are built with
safe DOM text assignment; page and JSON routes independently authorize roles;
disabled/degraded/empty states are separate; the observation state is visible;
model loss leaves deterministic domain views available; and expired/archived
quests leave no navigation entry or household payload. The retained Gate 1
mock remains vocabulary evidence only. Live assistive-technology review and a
supported-Mac/provider rehearsal remain Phase 8 release evidence, not a reason
to reintroduce the deleted Node application.

The 2026-08-06 Today amendment is process-proven by
`tests/acceptance/test_gate7_household_experience.py`: the production
composition returns one deterministic brief to the portal and OpenClaw,
preserves tasks/list items/recurrences/routines/calendar events as distinct
kinds, and executes idempotent recurrence, ordered Routine-step, and
member-confirmed calendar-outcome events.
`tests/unit/test_today_brief.py` pins the pure composition clock/timezone and
item-action contract. The Gate 7 visual harness renders the Day Brief and the
interactive Carousel at desktop, tablet, narrow, and reduced-motion states.

Comms amendment (2026-08-06): Comms is the ninth adult destination, directly
after Triage. It composes an authorized member/account/workspace union over
the interactions projection at read time. Focus, Waiting, Snoozed, Quieted,
and All are deterministic views; personal and work accounts remain separately
filterable even in the combined view. The transcript reader, inline reply,
read/archive controls, local snooze/quiet controls, capability remedies, and
Triage links expose only actions the exact account can support. Provider
writes require self context, optimistic concurrency, independent approval,
the full external-write guard, and terminal reconciliation. No model owns the
sort order, sends a reply, or materializes extracted household truth.

Reminders amendment (2026-08-06): Reminders is the tenth adult destination,
directly after Comms. `deterministic-reminders-cockpit-v1` composes the scoped
Lists projection into Now, Scheduled, Places, Anytime, and Completed without a
model. Provider accounts and lists remain independently filterable. Google
Tasks retains date-only scheduling, opaque ordering, nesting, source links,
completion, and assignment provenance while explicitly lacking native
priority, alarms, recurrence, and geofences. Apple EventKit retains supported
dates, priorities, alarms, recurrence, and location geofences while not
claiming Reminders.app-only tags, sections, subtasks, templates, assignments,
or smart-list rules. AdministrateMe context cues compare typed, authorized,
expiring signals using fixed operators; they never change provider truth.
