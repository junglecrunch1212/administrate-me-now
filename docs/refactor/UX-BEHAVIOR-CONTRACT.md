# UX behavior contract

CO-016 amendment (2026-08-11): a successful Triage confirmation or typed
dismissal clears exact-link focus, stays in the member's active working lens,
and advances to the next independently manageable candidate when one remains.
If the refreshed lens is empty, feedback says that the member is caught up
instead of promising another decision. It never changes the lens to Recent
merely because the prior candidate is now resolved. Recent remains read-only
audit history and distinguishes historical context-or-eligibility replacement
receipts from member-authored feedback and learned-rule state without claiming
unproven migration provenance. Direct exact links to resolved candidates
continue to open their immutable receipt.

CO-015 amendment (2026-08-10): every Today clarifying decision publishes a
`member-decision-context-v1` review contract with a direct member question,
what happened, why it is present, authorized source identity, unresolved
questions, fixed choice consequences, smallest next step, candidate id, and
matter id. Bounded language may supply labelled review-aid prose but never the
choice set, authority, target, route, priority, truth, or effect. Review links
resolve the exact candidate independent of the loaded Triage cursor, survive
reload and resolved state, and fail closed without substituting another item.
Comms and Notes link each extracted candidate and matter; Scoreboard next steps
return to the exact canonical item. `surface-link-v1` covers all ten stable
adult routes and remains presentation-only.

CO-012 amendment (2026-08-10): Triage treats a privacy-scoped provider thread,
not one isolated body, as the bounded interpretation context while preserving
one independently reviewable typed candidate per distinct ask. Compact cards
state the possible decision; expanded cards separately label context summary,
why it may concern the member, smallest next step, thread state, unresolved
questions, deterministic source-edge reasons, and exact evidence. Generated
explanation is a review aid and never evidence or authority. Cross-member,
account, workspace, visibility, sensitivity, and privileged content fails
closed. A later message may retire only untouched prior interpretations;
member decisions remain permanent.

CO-011 amendment (2026-08-09): Settings publishes one member-scoped candidate
identification matrix across every member entry point and current/planned
source family. Adults may select automatic review, explicit-request only, or
no proposals; limit supported target kinds; and independently disable grounded
language contribution. These controls never change source retention,
accounting, privacy, routing, confirmation, or provider-write gates. Triage
master/detail renders authorized channel, direction, adapter/account,
sender/counterparty, subject, observed time, possible meaning, allowlisted
fields, literal evidence, extraction methods, and effective policy. Missing or
withheld context is explicit; a model paraphrase is never presented as source
evidence.

CO-010 amendment (2026-08-09): every visible source detail that may contain
derivative work offers an exact source-linked Triage action; it never copies
the source into truth. Reminders owns member-facing Recurrence and ordered
Routine definition/edit/pause/resume/end controls. Scoreboard retains terminal
outcomes and linked reward evidence. Finance Goal status changes are explicit,
optimistic lifecycle actions and never move money. Empty Triage copy depends
on audited source disposition, not queue length.

CO-009 amendment (2026-08-09): canonical Tasks and provider-owned reminders
share one Reminders workspace only as a typed read-time union. They retain
separate identity, authority, lifecycle events, revisions, and write paths.
Triage publishes a versioned type-specific enrichment contract; the browser
uses native date/time, bounded recurrence, authorized Party, and reviewed route
controls, and the API derives a closed update allowlist from the same contract.
Confirmation returns the exact materialized or pending destination and link.
Visible edits are saved before confirmation; structural destination changes
require a refreshed final review. Configured member identities, not incidental
Party-projection order, populate the household-member selector and missing
identity fails closed. No Task due date silently becomes a Calendar event or
provider item. The internal Task status `inbox` is presented as **Open** and
never resurrects the retired Inbox surface name.

CO-008 amendment (2026-08-08): the adult shared-shell Add for review picker
exposes all eleven supported proposal kinds. Triage and Settings render the
same content-free source-assurance counts: accounted obligations, eligible
candidate sources, explicit no-candidate assessments, quarantines, failures,
in-flight work, and missing dispositions. An empty lens is calm, but it may not
claim healthy unless source accounting supports that claim.

CO-007 amendment (2026-08-08): every authorized adult surface exposes the same
Add for review action. Its only required input is exact text; the optional type
hint never becomes authority. The browser preserves failed input, reports the
number of derived review items, and routes all results to Triage without a
canonical/provider write. Triage has separate Needs interpretation, Needs
details, Ready, and Recent lenses, plus real projection checkpoint/source
freshness. Natural dictation is split only at bounded explicit intent starts by
deterministic code. Recent local outcomes say `confirmed_local`; provider paths
show awaiting approval/provider, suppression, failure, and linked observation
without conflating a domain confirmation with provider success.

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
| Triage | Member-owned queue containing only extracted/derived candidates; schema-driven type-specific property enrichment; authorized Party/member/provider/account/container selectors; confirm, route, or dismiss with source evidence, exact destination receipt, and terminal outcome. Raw provider observations remain facts; already-official tasks never appear here. | The former Inbox mixture of proposals plus open work; raw ISO/RRULE or identifier text boxes; arbitrary candidate dictionaries; client-only “Approve & Send”; generic transcript storage. |
| Comms | Member-isolated unified communications cockpit with combined and per-account/workspace views, deterministic Focus/Waiting/Snoozed/Quieted/All lenses, thread reader, explicit provider capabilities, inline governed actions, local focus controls, and linked Triage extraction status. | A copied global mailbox, model-ranked inbox, second assistant transcript, implicit provider capability, cross-member/workspace union, or direct extraction-to-calendar/task/list write. |
| Reminders | Member-isolated canonical Task plus provider-item workspace with combined/per-account/per-list filters; deterministic Now/Scheduled/Places/Anytime/Waiting/Completed lenses; explicit record-kind/authority/provenance; rich local Task properties and lifecycle; provider-native edit, move, completion, list, alarm, recurrence, nesting, and ordering actions only where supported; typed expiring context cues from authorized location, presence, device, network, calendar, weather, or member-state facts. | A universal merged work model, model-ranked list, fabricated provider parity, silent conversion/copy among Task/Commitment/provider item/calendar event, inferred location/presence, provider state copied into a second truth store, or a UI click treated as terminal provider success. |
| People | Adult-only relationship memory with qualified Party directory, authorized search, source/lens rail, deterministic explicit cues, master/detail context, member-private profile, typed facts, Interactions/open loops, provenance, and reversible lifecycle. | Exemplar household data, sender-as-person, raw-address headlines, generic cards, model closeness/ranking, provider-share authorization, guilt-producing overdue cues, and mock counts. |
| Notes | Member-private, source-aware recall across authorized notes, meeting notes, transcripts, and provider summaries; deterministic search; provenance/correction; explicit per-note adult grants; and exact-evidence candidates linked to Triage. | A generic knowledge dump, provider-share-as-authorization, principal view-as, autonomous corpus mining, quick-capture prefix routing, direct voice upload, or any note-to-domain/provider write that bypasses Triage. |
| Finance | Scope-filtered accounts/transactions/budgets/subscriptions and workbook state; privileged owner-only details and audit. | Any client-only masking or demo values. |
| Calendar | Member-isolated Day/Week/Month/Agenda/Find time cockpit plus progressively disclosed Rules over every authorized provider calendar; anchored date navigation; spatial time grids and all-day lanes; route-stable source colors and visibility controls; route-scoped provenance; duplicate-copy grouping; explicit hard-block, soft-load, context, and ignore semantics; tentative/custody policy; typed buffers; protected household windows; conflict/pressure explanations; provider capability truth; and reviewed write destinations shared with Triage. | Treating calendar as the universal task model, treating every observed event as busy, equating a hidden display calendar with free time, model-decided availability, silent duplicate deletion, or writing to an unreviewed calendar. |
| Scoreboard | Age/role-appropriate household progress and dual-path rewards; child-safe data only. | Client-side hidden navigation as a security boundary. |
| Settings | Nine-section deterministic member control plane: visible authenticated/read/action identity; bounded profile/fine tuning; connection setup/runtime/permission/safety axes; candidate-identification policy; bridge requests; household/privacy invariants; observation, workbooks, registry, and diagnostics with progressive disclosure. | Opaque initials/internal ids, a static or truncated capability dump, config-as-health, secret entry in ordinary forms, principal control over another adult's private source, or broad OpenClaw operator controls. |

Generic chat remains an OpenClaw surface. A narrow chat pane may embed/hand off to OpenClaw for authorized members, but AdministrateMe must not persist a second generic transcript or expose operator administration. Members without safe operator access use a narrow member portal.

Gate 7 implements that exception as an authenticated streaming relay only.
The browser receives rendered text and safe stream phases; OpenClaw retains
history and all provider/runtime responsibilities. View-as chat is refused.
Child chat is guardian-enabled and its server tool allowlist is narrower than
adult portal navigation.

## View modes

- **Carousel (`adhd_executive`):** an explicit member-selected Today lens with one primary next action, large touch target, explicit why/energy/effort/micro-step, bounded navigation, endowed progress, minimal guilt-free copy, optional reward animation respecting reduced motion. `Later` is view-only; a real defer/snooze command is labeled separately and writes typed state.
- **Compressed (`minimalist_parent`):** dense decision queue, minimal animation, concise risk/approval summaries, keyboard-efficient action.
- **Child (`kid_scoreboard`):** Today/Scoreboard-only allowlist as configured, warm age-tuned language, no finance/People/admin/other-member knowledge. Forbidden routes return server-side 403/404 and no sensitive payload, even when requested directly.

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
| UX-11 | Notes contains no quick-prefix mutation or direct voice upload. A bounded Cat-D source version may yield an exact-evidence proposal link to Triage under ADR-0003, but Notes contains no approval, domain materialization, provider route selection, or external write control. |
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
| UX-24 | Today, Triage, Comms, Reminders, and Calendar use typed exact source links plus URL-backed view/filter/selection state. A link changes presentation only and cannot change authorization, routing, provider truth, capacity, ranking, or write eligibility. |
| UX-25 | Comms Focus v2 and Today actionable messages call the same deterministic predicate with the same typed semantic, provider, local-state, and linked-Triage inputs. Unread is visible but is never sufficient priority; temporary security-code attention expires after a fixed 15-minute window. Schema-bound NLP reports features only and cannot select a lens, score, suppress, route, or write. |
| UX-26 | Triage is a compact structured master/detail review surface with Ready, Needs details, and Recent lenses; exact source evidence; authorized member/provider/account/container choices; inline readiness; deterministic Calendar impact preview; observation consequence; and one final confirmation. Provider/container identifiers are not free text and no domain workflow uses native prompt/confirm/alert. |
| UX-27 | Reminders and Today consume one effective-reminder contract with exact provider identity, date semantics, cue reasons, capability, and source link. Possible copies are strict read-only presentation groups: every provider item, list, revision, and available action remains independently inspectable and no copy is merged or mutated implicitly. |
| UX-28 | Today separates timed schedule, all-day/context facts, executable moves, decisions, and actionable messages. Only an in-progress or near-term timed event with an approved capacity effect may displace deterministic executable work; context cannot become “next on the clock.” |
| UX-29 | Every one of the five action-capable surfaces persistently distinguishes observation, healthy-empty, fresh, stale, degraded, and not-configured state and links to evidence. At widths below 1600 px generic OpenClaw chat is an explicit drawer, never a competing permanent rail or a visual continuation of Comms. |
| UX-30 | Notes is owner-private unless one non-privileged note has an active explicit adult read/search grant. Provider-native sharing does not authorize AdministrateMe access or processing; shared-reference extraction is unavailable; principal view-as, child, device, ambient, and generic OpenClaw memory receive no note payload. Revocation, expiry, deletion, or privileged reclassification removes derived access/indexes deterministically. |
| UX-31 | Notes exposes source/account/folder/revision, capability warnings, safe text rendering, authorized search, All/Recent/Shared/Meetings/Needs Triage lenses, and per-note grant/revoke. Every extracted candidate carries an exact source quote and enters Triage only; a changed source version supersedes its pending candidates. |
| UX-32 | People distinguishes Endpoint, provider Contact Record, qualified Party, Identifier Link, Membership, Relationship, member profile, fact, and Interaction. Unknown communication/calendar endpoints never enter the primary directory; an eligible curated contact may qualify a Party; ambiguous or hidden exact matches fail closed and remain review evidence. |
| UX-33 | People search, list, detail, counts, sources, cues, and timelines filter authorization before aggregation. Provider records, identifiers, Interactions, facts, and profiles are owner-private by default; an explicit adult field-group grant is independent from provider sharing; privileged content and all child/device/ambient/operator/view-as paths fail closed. |
| UX-34 | People uses a responsive source/lens → qualified Party list → detail workspace with URL-backed search/selection, safe text rendering, explicit source/freshness, deterministic member-requested cadence cues, Triage review links, and reversible archive/restore. No model infers closeness, relationship, household role, access, priority, outreach, or merge. |
| UX-35 | The shell names the authenticated member, role, Tailscale ingress, read member, and action member. View-as is conspicuous and makes Settings mutations unavailable; writes remain authorized as `authMember`. |
| UX-36 | Every connection presents setup, runtime, permission, and safety independently. Config, credential-reference presence, provider handshake, cursor freshness, effective write capability, and observation are never collapsed into one healthy state; unknown remains unknown. |
| UX-37 | Profile and fine-tuning controls are bounded, revisioned, reversible data. They disclose behavior effects and cannot change authority, privacy, model routing, provider scope, or diagnosis. |
| UX-38 | Add Bridge records one member/capability-scoped request and explains the separate target-device consent ceremony. Adult private bridges are self-owned; principals may govern child bridges; no portal payload contains enrollment tokens or credential references. |
| UX-39 | The adult shared shell provides Add for review with one exact-text field, an optional non-authorizing type hint, dictation-compatible focus, explicit “nothing official yet” copy, preserved text on failure, idempotent submission, and a result count. It appends member intent before extraction and never bypasses Triage or creates a provider command. |
| UX-40 | Triage independently renders Needs interpretation, Needs details, Ready, and Recent. Local confirmation is labeled separately from OpenClaw/provider approval; immutable confirmation, materialization, provider command, suppression/failure, and terminal observation remain distinct linked evidence states. Queue emptiness never substitutes for source, pipeline, or projection health. |
| UX-41 | Every adult surface exposes the same Add for review action with all eleven non-authorizing kinds. Triage and Settings show content-free per-source assurance totals and distinguish explicit no-candidate, quarantine, failed, pending, and missing from empty; source content and account identifiers never enter these diagnostics. |
| UX-42 | A confirmed canonical Task returns an exact Reminders destination receipt and appears under the same Task ID in Reminders, Today, and Scoreboard. Opening that receipt selects the exact Task, activates its deterministic Reminders lens, and clears incompatible presentation filters; later member-selected filters clear the stale item selection. The Reminders union labels the record as AdministrateMe-owned and routes complete/edit/defer/block through `task.*`; a Task deadline and available-from/defer activation are separate, and an elapsed activation re-enters executable reads without rewriting stored lifecycle truth. Provider items retain provider/account/list/revision identity and the governed external-write path. Neither record kind is copied or promoted implicitly, and deep-link state never authorizes or mutates truth. |
| UX-43 | Triage enrichment is rendered from the server-published `triage-enrichment-v3` contract. Dates and times use native pickers plus the reviewed instance timezone; repetition uses a bounded builder; Parties and external routes use authorized selectors; provider-only fields follow the selected route ceiling. Google Tasks is date-only and cannot expose Apple-only start, priority, alarm, recurrence, URL, or geofence controls. The API rejects fields outside the same target contract before append. Immediate and refreshed confirmation feedback names and links the resulting local surface or pending provider path. |
| UX-44 | A Comms message, Note, Calendar detail, or provider Reminder detail can submit its exact visible source to Triage. The request preserves the source event ID, scope, sensitivity, and evidence; it does not authorize, materialize, schedule, or write anything. |
| UX-45 | Settings exposes one revisioned, secret-free candidate-identification matrix for all ten Add-for-review entry points, OpenClaw, current candidate-bearing adapters, and reviewed future sources. Explicit member entry remains locked on. Review/manual/quiet, supported-kind subsets, and optional grounded-language contribution change candidate admission only; source retention/accounting, privacy, deterministic eligibility/routing, human confirmation, governance, observation, and provider effects remain non-configurable. |
| UX-46 | Every Triage proposal exposes decision-grade authorized source context: channel/direction, adapter/account, sender/counterparty, subject/title, time, possible target meaning, allowlisted fields, literal source evidence when applicable, extraction-method disclosure, and the effective policy snapshot. Unavailable or withheld context is named, and generated paraphrase is never labeled evidence. |
| UX-45 | Reminders presents canonical Recurrences and ordered Routines as separate structures with add/edit/pause/resume/end controls, optimistic concurrency, member/household scope disclosure, accessible repeat/window/step inputs, and retained occurrence history. Ending is not deletion and Routine steps never imply Task completion. |
| UX-46 | Scoreboard presents typed completed, abandoned, cancelled, delegated, expired, recurrence, and Routine outcomes. Immediate completion feedback and asynchronous reward delivery link to the same immutable completion evidence and render once. |
| UX-47 | Finance Goal definition and lifecycle remain separate. Pause, complete, and reopen controls disclose that they change planning state only, use optimistic concurrency, retain history, and never transfer money; reviewed workbook status edits emit the same typed lifecycle. |
| UX-48 | Triage message candidates use one bounded, private conversation context with code-owned thread/participant/subject/attachment edges. Each compact card identifies one distinct possible decision; expanded review separates bounded context explanation from exact source quotes, shows apparent owner/lifecycle uncertainty and edge reason codes, and preserves per-candidate correction/confirmation. Related-only, closed, other-owned, cross-scope, or ungrounded content cannot enter the member queue. |
| UX-49 | Triage distinguishes complete Candidate Inventory from the member Decision Queue. Inventory reports exact candidate/matter/duplicate/existing-object counts and stable continuation state; every candidate is reachable without a hidden hard limit. The queue shows one matter on first pass with independently reviewable children. |
| UX-50 | Provider conversation is provenance, not lifelong subject identity. The UI labels deterministic matter episodes, source/candidate counts, and queue lane; a continuous conversation is bounded at 30 days or 100 distinct sources unless a member explicitly merges or splits context. Model similarity cannot merge cards. |
| UX-51 | Ready, Needs details, Needs interpretation, Low signal, Recurring exceptions, Learned rules, All candidates, and Recent are presentation lenses over the same scoped inventory. Fixed due/security/financial/member-promise/member-priority rules and oldest-first anti-starvation govern attention; model confidence cannot admit, hide, rank, or suppress. |
| UX-52 | Exact duplicate and unambiguous existing Task/Calendar/Reminder/Recurrence matches remain nested evidence with explicit labels. They do not create an additional default decision, but remain individually inspectable and recoverable. Ambiguous or cross-scope matches fail to review. |
| UX-53 | Dismissal requires a typed reason. Repeated scoped outcomes may produce a member-visible rule suggestion; preview states the affected history and provider effects. Approval is explicit, reversible, and shadow-only in CO-014. It cannot confirm, suppress, route, materialize, or write a candidate. |
| UX-54 | Context views expose edge type, authority, reasons, source IDs, temporal state, and revocation without copying source bodies. `llm_proposed` edges are visually suggestions and never enter authoritative traversal. Cross-member/account/workspace/sensitivity context is neither counted nor displayed. |
| UX-55 | A Today clarifying-decision card must answer the direct member question, what happened, why the item is present now, authorized source identity, remaining uncertainty, fixed confirmation/correction/dismissal consequences, and smallest next step through `member-decision-context-v1`. Compact view must not dump source bodies; expanded context labels bounded language as review aid and preserves exact evidence separately. |
| UX-56 | Today, Comms, Notes, and any other candidate-bearing surface link the exact Triage candidate and matter. Triage resolves a scoped `triage_item` independently of its ordinary cursor and returns explicit pending, resolved, or unavailable focus state. Reload/history/filter state cannot substitute another candidate, reopen a decision, or change queue truth. |
| UX-57 | A deliberate Triage lens change clears exact-link focus. An asynchronous or stale focus response cannot force the member back to the linked lens after that choice. The selected candidate receives keyboard focus without creating a decision or provider effect. |
| UX-58 | Every stable adult surface participates in `surface-link-v1`. Where a surface shows a canonical item owned by another surface, it exposes an exact return path or explicitly states that no destination surface exists; generic top-of-page navigation is not an object link. |
| UX-59 | After confirm or Dismiss & teach, Triage clears the resolved candidate and matter from URL and client focus, preserves Ready/Needs details/Needs interpretation/Low signal/Recurring exceptions/Learned rules/All candidates, and derives its continuation message from the refreshed visible lens: select the next matter when one remains, say the member is caught up when none remains, or disclose that more matters must be loaded. Recent is entered only by explicit member choice or an exact resolved deep link. Historical `eligibility_rule_replaced` receipts say only that source context or eligibility changed; they do not claim a system migration or a sender-wide rule created by the current dismissal. |

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

Five-surface amendment (2026-08-06): CO-001 advances Today to
`deterministic-daily-brief-v2` and Comms to
`deterministic-comms-cockpit-v2`. Today, Triage, Comms, Reminders, and Calendar
now share non-authorizing link, attention, action-state, and freshness
presentation contracts while preserving every real domain type. Today and
Comms use the same Focus v2 predicate; unread alone is never Focus. Today and
Reminders use one effective-reminder composer, with strict read-only possible
copy groups. Triage uses structured authorized destinations, readiness, Recent
outcomes, and the existing Calendar reasoning preview. URL query state
preserves view and exact selection; it never changes computation or authority.
All five surfaces explain observation/freshness, remove native prompt dialogs,
and collapse generic chat into an explicit drawer below 1600 px. AC-28 owns the
closed-loop production-composition proof.

People amendment (2026-08-06): CO-003 replaces the generic CRM cards and
sender-as-person shortcut with the `deterministic-people-cockpit-v1`
relationship-memory contract. The source/lens rail, authorized search,
qualified-Party list, master/detail context, explicit cues, provenance, and
member-private settings are read composition over Parties v2 and linked domain
projections. Reviewed enrollment becomes idempotent canonical Party,
Membership, and Relationship evidence before readiness; it never makes an
external contact a household member. Unknown endpoints remain outside the
directory; an ambiguous or hidden exact identifier fails closed. All untrusted values use safe DOM text,
children and view-as receive no payload, chat becomes a drawer below 1600 px,
and the 390 px layout becomes one column without page overflow. AC-30 owns the
production, replay, privacy, adapter, and browser proof.

Settings amendment (2026-08-07): CO-004 replaces the static generic cards with
the `deterministic-settings-control-plane-v1` contract. The global shell shows
the authenticated member and Tailscale boundary. Overview, My account, CoS
behavior, Connections, Bridges & devices, Household & privacy, and Safety &
system are URL-backed sections. Connection setup/runtime/permission/safety are
independent; the member can activate validated profiles, save bounded
fine-tuning, pause/resume an exact adapter through supervisor reconciliation,
and request a member-scoped bridge. Secrets and raw secure-reference paths
never enter the response. AC-31 owns process, isolation, status, receipt, and
browser proof.

Triage extraction-health amendment (2026-08-08): UX-25, UX-29, UX-39, and
UX-40 apply to the durable source-to-candidate worker, not only its projection.
An empty queue may be called healthy-empty only when every required extraction
subscriber is registered and non-poisoned. A degraded subscriber must expose
its last good checkpoint and remediation rather than rendering celebratory
zero-state copy. NLP-derived candidates display verbatim source evidence and
remain ordinary human-gated proposals; model confidence never hides a
candidate that passed the fixed exact-quote/type/privacy rules.
