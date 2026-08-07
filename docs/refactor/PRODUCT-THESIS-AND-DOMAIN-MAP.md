# Product thesis and domain map

Gate 3 evidence: the first full household signal-to-outcome loop is executable without credentials. A synthetic inbound communication becomes a scoped Interaction with a resolved Party, an LLM-derived proposal with recorded provenance, a principal-reviewed commitment, an observation-safe delivery intent, API/xlsx read models, lifecycle outcomes, and a changed six-horizon what-now recommendation. This proves one representative slice; it does not claim the still-deferred member-private knowledge slice or optional providers.

## Product promise

AdministrateMe is a private, household-scale Chief of Staff that turns distributed family signals into trustworthy next actions. It is simultaneously six things; none is optional:

| Product function | User outcome | Required domain evidence |
|---|---|---|
| Household Chief of Staff | The family sees what matters, what is owed, and what happens next across time horizons. | Tasks, commitments, recurrences, calendars, lists, approvals, outcomes, contextual what-now. |
| ADHD cognitive neuroprosthetic | Work is reduced to approachable next steps without guilt or infantilization. | Carousel, micro-script, energy/effort fit, endowed progress, variable rewards, paralysis support, concise digests, guilt filter. |
| Household/professional CRM | Relationships have continuity across people, organizations, family roles, conversations, promises, and artifacts. | Parties, Identifiers, Memberships, Relationships, Interactions, Commitments, Artifacts. |
| Unified communications | A member-isolated, account/workspace-aware cockpit carries working memory across person-to-person channels without duplicating assistant chat or letting a model decide priority. | Loss-aware interaction normalization, deterministic Focus/Waiting/Snoozed/Quieted rules, provider capability negotiation, Triage proposals, and governed requested/completed/failed delivery outcomes. |
| Working memory/Notes | Each member writes information once, recalls it across authorized sources, and may send exact evidence to the household human gate without remembering the source app. | Cat-D member bridges, provider-neutral `member_knowledge`, explicit per-note grants, privileged exclusion, deterministic FTS, revocable `vector_search`, provenance/corrections, bounded Notes-to-Triage extraction. |
| Sensor/signal/action layer | Finance, home, calendar, lists, and other sources can be observed and acted on safely. | Cat-B/C/E adapters, typed facts, explicit verbs, observation, authorization, verification events. |

The target household is multi-member, including principals, children, helpers, devices, and ambient entities. “Single hub” describes the source-of-truth deployment, not a single-user security model.

## Closed executive-function loop

Every useful path must close this loop:

1. **Observe:** adapters or OpenClaw bridge a typed, provenance-bearing fact.
2. **Understand:** deterministic normalization and, where justified, a schema-bound OpenClaw skill classify/extract meaning.
3. **Organize:** idempotent projections create domain read state without changing the fact.
4. **Propose:** pipelines emit a proposal or review request; LLM output never writes live state.
5. **Authorize:** identity/scope, guardedWrite, AdministrateMe domain approval, OpenClaw generic approval where applicable, and the content/privacy guard all pass independently.
6. **Act/project:** the system performs an allowed external action or updates a rebuildable local projection/workbook.
7. **Verify outcome:** delivery, completion, correction, suppression, failure, or external reconciliation becomes a typed event.
8. **Recalculate next action:** affected projections update and contextual what-now changes deterministically.

A test that stops at proposal, approval, API 202, or outbound request does not prove the loop.

### Six-lens member loop

Today, Triage, Comms, Reminders, Calendar, and Notes are bounded lenses over that
one loop, not six sources of truth. Comms, Calendar, Reminders, and Notes expose
authorized provider observations without collapsing their domains. A
schema-bound OpenClaw skill may extract language features, but deterministic
pipeline rules alone decide whether a candidate is eligible for Triage.
Triage is the only cross-domain human proposal gate and resolves an exact
member/provider/account/container from authorized directories. Calendar
previews time effects; Reminders preserves provider capability; Comms preserves
thread/account/workspace context. Notes preserves member/source/folder/revision
identity, exact evidence, and independently authorized sharing. Today then recomposes verified projection
state and deep-links back to the exact source. Shared read contracts may reduce
working-memory load, but they never rank by model, authorize a write, or become
a universal work object.

| Loop stage | Primary member lens | Cross-surface guarantee |
|---|---|---|
| Observe/recall | Comms, Reminders, Calendar, Notes | Provider/member/account/container or note-source/folder/revision identity remains visible and authoritative. |
| Understand | Source lens plus bounded semantic provenance | A model reports typed language features only; deterministic code owns attention and eligibility. |
| Organize | Source lens and deterministic projections | Read filters cannot change truth, time capacity, or authority. |
| Propose | Triage | Only evidence-backed derived candidates wait; raw messages, notes, provider facts, and already-official work do not. |
| Authorize | Triage plus source-specific action review | Exact route, capability, observation effect, and immutable evidence precede confirmation. |
| Act/project | Typed domain/API seam | Task, commitment, calendar event, reminder/list item, recurrence, and scheduler job remain distinct. |
| Verify | Triage Recent and source reconciliation state | Requested, suppressed, confirmed, failed, and reconciled are not interchangeable. |
| Recalculate | Today | Timed/context calendar facts, effective reminders, actionable Comms, and decisions recompute without copied truth. |

## Temporal model and contextual what-now

What-now is not one sorted task list. For an authorized `viewMember`, the deterministic candidate set uses:

- current clock, timezone, daypart, calendar availability, travel/buffer, due/overdue windows, recurrence windows, and custody/household context;
- task/commitment state, blocking/waiting dependencies, list promotion/linkage, urgency, importance, energy, effort, micro-script, and progress;
- member role/profile, view mode, current energy/capacity, location/device context when explicitly available, notification history, nudge caps, and quiet hours;
- privacy/scope, observation status, approvals, degraded adapters/projections, freshness/watermarks, and confidence/provenance.

It produces explainable recommendations for six horizons: **now** (one concrete action), **next hour** (small compatible sequence), **daypart** (capacity-aware block), **day** (today commitments and risks), **week** (recurrences/deadlines/relationship balance), and **longer** (goals, renewals, projects, financial/household trajectories). The UI may emphasize a subset, but the engine and API must preserve all horizons and evidence.

## Semantic object map

| Object | Meaning | Source of truth | May link to | Must not become |
|---|---|---|---|---|
| Event-log fact | Immutable statement that something was observed, proposed, decided, attempted, or happened. | Encrypted append-only log. | Every domain object through ids/correlation/causation. | Mutable workflow row or projection. |
| Calendar event | Time reservation/occurrence with attendee and visibility semantics. | Typed calendar facts; external calendar may be authoritative for a Cat-B mirror. | Task, commitment, recurrence, Party. | Universal task/list/reminder model. |
| Task | Household work with execution attributes such as energy, effort, micro-script, waiting-on, and goal reference. | Task lifecycle events. | Commitment, list item, recurrence, Party, calendar event. | Commitment or simple list item. |
| Commitment | Obligation made to or by a Party, with proposal/review and fulfillment lifecycle. | Commitment events. | Interaction, Party, task, artifact, calendar event. | Generic todo. |
| List item | Simple item in a list with ordering/status/sharing-model semantics; often externally authoritative. | List/list-item events and Cat-B reconciliation. | Task through explicit promotion/link. | Task by default. |
| Recurrence | Domain pattern/expectation that can generate or contextualize work. | Recurrence events. | Task, commitment, account, asset, household. | OpenClaw cron entry. |
| Projection | Deterministic, disposable read model. | Replay of event log from zero. | Other projections for read composition only where contract allows. | Write API or source of truth. |
| Scheduler job | OpenClaw-owned trigger for a standing order. | Pinned OpenClaw config plus install/reconcile events. | Pipeline endpoint and idempotency key. | Recurrence domain object. |

## CRM spine

- **Party:** stable person, organization, or household identity.
- **Identifier:** normalized addressable identity (email, phone, channel peer, external id) with source/confidence.
- **Membership:** a Party’s role in a household or organization, including `member_id` for authorization.
- **Relationship:** typed connection between Parties with time/provenance.
- **Interaction:** deduplicated communication or touchpoint.
- **Commitment:** obligation linked to the Parties and Interaction that established it.
- **Artifact:** document/form/record linked to Parties and operational work.

All products use this spine through events and scope-filtered projections. No product privately forks CRM truth.

## Projection capability map

| Capability | Purpose | Gate 1 state |
|---|---|---|
| `parties` | CRM identities, identifiers, memberships, relationships | Code/schema present; not in default runtime registry. |
| `interactions` | Deduplicated, member/account/workspace-bound communication facts and explicit local/provider state | Default-registered; D28 Comms composition, scope/replay, provider command and process acceptance implemented. |
| `artifacts` | Documents/forms/records and links | Code/schema present; not in default runtime registry. |
| `commitments` | Proposed, reviewed, active, completed obligations | Code/schema present; not in default runtime registry. |
| `tasks` | Household execution work and ADHD attributes | Code/schema present; not in default runtime registry. |
| `recurrences` | Repeating domain meaning | Code/schema present; not in default runtime registry. |
| `calendars` | Calendar facts, availability, privacy-aware time views | Code/schema present; not in default runtime registry. |
| `places_assets_accounts` | Physical/operational/financial entity spine | Code/schema present; not in default runtime registry. |
| `money` | Transactions and household finance read state | Code/schema present; not in default runtime registry. |
| `vector_search` | Scope-filtered, non-privileged semantic domain search | Code/schema present; not in default runtime registry. |
| `xlsx_workbooks` | Rebuildable ops/finance workbook output and reverse-sync support | Substantial code present; Windows portability failure; not composed. |
| `member_knowledge` | Owner-scoped knowledge from member bridges | Missing. |
| `lists` | Mirrored/CoS list and list-item state | Present and the only class in `DEFAULT_PROJECTION_CLASSES`. |

Storage/process changes may be made, but a capability can be declared complete only when registration, live apply, replay equivalence, scope behavior, and shutdown are proven in the production composition root.

## Lifecycle vocabulary

Required typed outcomes include: proposed, approved, rejected, held-for-review, completed, deferred, snoozed, blocked/unblocked, corrected/superseded, suppressed, dispatched, delivered, failed, reconciled, and verified. Each carries actor, timestamp, scope/sensitivity, correlation, causation, provenance, and idempotency identity as applicable. Completion or failure is feedback: it must update the relevant projections/workbook and alter what-now when relevant.

## Profiles, personas, and tenant neutrality

Required built-in profiles are `adhd_executive`, `minimalist_parent`, `power_user`, `kid_scoreboard`, and `ambient_entity`. Required built-in persona archetypes are `poopsy`, `butler_classic`, `friendly_robot`, and `quiet_assistant`. These are tenant-neutral templates; examples such as a named household belong only in explicitly marked external/fixture packs. Profile controls per-member UX/engines; persona controls instance voice/theme/templates. Neither controls authority, governance, or model routing.

## Gate 2 implementation status

The runtime now provides the infrastructure needed to preserve these distinctions: every projection capability has an explicit registration, `member_knowledge` has owner/scope-aware schema, handlers, and queries, and derivative pipeline facts travel through the same causation-aware writer. This is infrastructure completion, not product-loop completion: the communication-to-reviewed-commitment and member-bridge-to-authorized-knowledge slices remain strict Phase 3 acceptance targets.
