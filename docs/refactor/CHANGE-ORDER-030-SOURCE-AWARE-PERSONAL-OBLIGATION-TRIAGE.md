# CO-030 — Source-aware personal obligation Triage

Status: implementation candidate; process, browser, release, and deployment
results must be recorded separately. This document makes no passing-test or
live-activation claim.

Date: 2026-09-08

Governing sources: ADR-0001, ADR-0005, ADR-0008, ADR-0010, ADR-0013;
CO-011, CO-012, CO-016, CO-018, CO-020, CO-021, CO-022, CO-028; AC-60.

## Decision and source coverage

Triage must answer whether the source supports a current responsibility for
the member before deciding how that member prefers to see it. Source access,
interpretation, obligation identity, attention, notification, and execution
authority remain separate decisions.

The common engine serves existing and future reviewed adapters, including
email, iMessage/SMS, notes, provider state, and retained evidence from a source
that is no longer connected. The existing source-family candidate ceilings
and manual derivative-work boundaries remain in force: a provider Calendar
event, Task, Reminder, contact, or financial fact is not a new household object
merely because the adapter observed it. Source preferences do not implement,
connect, enroll, or grant consent to an adapter.

All six product functions, thirteen domain projection capabilities, five
adapter categories, six pack kinds, five profiles, and four personas remain
required. Context and People facts keep their appropriate product destinations;
their absence from ordinary Triage does not discard those functions.

## Source-grounded action assessment

The pinned OpenClaw `llm-task` seam supplies bounded interpretation. A
`personal-obligation-assessment-v1` distinguishes message purpose,
actionability, conditionality and its state, beneficiary, date role, literal
evidence, independently supported obligation, verified date fields, and
grounded recurrence. Authorship, ownership, and lifecycle remain explicit in
the enclosing interpretation. Code resolves permitted source aliases to exact
event references and verifies literal quotations. Provider payloads cannot
declare a trusted assessment or grant themselves authority through similarly
named fields.

Each action has its own assessment. A receipt may contain a confirmed booking;
a survey message may also contain required preparation. An informational
clause must not suppress the separate obligation. Automated senders,
promotions, receipts, and provider categories alone cannot decide actionability.
Conversely, an imperative in a footer, quoted history, saved instructions, or
optional offer is not sufficient personal responsibility.

Qualifiers are evidence. Unmet or unknown conditions remain conditional;
unsupported normalized dates do not become verified deadlines; occurrence,
reporting, expiry, and due dates remain distinct. Recurrence requires evidence
of the member's recurring responsibility rather than repeated notification
delivery. Exact source words supporting conditions, negations, completion, and
dates accompany the primary action quote in the member review.

Reconciliation keeps its separate dispositions for admission, existing-object
update, evidence attachment, alert, reference, satisfaction, supersession,
duplicate, conflict, suppression, and unresolved ownership or meaning. Exact
object/occurrence references support consolidation; shared account identity,
provider conversation identity, or weak text similarity does not by itself
merge obligations across billing periods or events. A Task due date does not
create a Calendar block, and an expired appointment does not establish that a
different still-open task was completed.

Printed invoice, appointment, payment, and external references are scoped to
their observed issuer; a shared reference string across unrelated issuers is
not a global identity. Matching an account reference remains contextual rather
than automatically consolidating its distinct obligations.

## Member source controls

Settings presents the authenticated member's exact connection/account and,
when observed, native conversation, project, calendar, list, label, or folder.
iMessage conversations use the same scoped policy machinery; a group thread
does not establish which participant accepted an obligation. An account-level
policy may be inherited by its native containers until an exact child override
exists. It never spreads across unrelated accounts or members.

The attention presets are **Focus on obligations**, **Include possible
opportunities**, and **Only when I ask**. Applying an attention preset preserves
permitted context, source-assessment state, alert selection, and candidate
types. Purpose controls distinguish personal requests, member promises,
appointments, preparation, payment, account alerts, optional opportunities,
status updates, receipts, surveys, reference, recurrence, and unknown purpose.
Their routes are review, reference, or permitted alerts. Independent obligations
in the same source keep their own handling.

Pausing source assessment stops automatic work assessment, not provider
authorization or the independently permitted alert/context routes. Provider
connection and permission controls remain the acquisition boundary. Source
attention does not enable provider writes or silently change notification
timing.

Connected, configured, disconnected, not-connected, and missing-attribution
states must be truthful. Retained candidate/source counts explain existing
history without claiming an active connection. Unconnected catalog entries
have no invented account coordinate. Exact retained scopes remain configurable;
unattributed rows cannot save an account rule. An explicit member request to
add for review remains eligible regardless of a passive-source preference.

Recent-effect previews evaluate authorized retained sources and assessed
actions, including sources with no assessed candidate. They return previous
and proposed routes, matching/nonmatching examples, reasons, and coverage
bounds. A missing assessment is an explicit coverage limitation, not a negative
label or proof that nothing was missed. No synthetic message may be substituted
for an absent recent example in the production interface.

## Rules, corrections, and learning

A rule is a typed conjunction with exact account and adapter plus purpose,
sender digest, or native container. It exposes **Applies to**, **When**,
**Except**, **Then**, and **Duration** before saving. Explicit member rules
outrank inferred suggestions; a strictly more specific compatible conjunction
outranks a broader one. Incomparable conflicting rules require clarification,
not an invisible last-writer or suppression-strength choice. Required
independent obligations, exact member promises, direct instructions, and
separate safety alerts retain their protection.

Corrections carry different meanings. **Already handled** concerns completion
of this obligation. Duplicate, extraction, and ownership corrections concern
their exact facts. **Not useful** concerns this suggestion. None implicitly
creates a sender-wide future preference. **Do this for similar messages**
opens a separate exact-scope rule preview. A member may save that explicit
preference, try it, observe it in shadow, or record it as future-preference
feedback without activation.

Suggested rules require at least three consistent independent recorded future
preferences. Repeated candidate evidence for one known object or source does
not create independent learning votes. Silence, reading, archiving, completion,
and ordinary dismissal are not future-suppression labels. Suggestions remain
inert until the member reviews and approves a concrete preview.

The default UI trial is seven days or fifty future independent observations,
whichever limit is reached first; the typed API bounds trials to one through
thirty days and one through one hundred observations. Existing candidates are
not retroactively counted as future trial traffic. Shadow trials change no
visibility. Expiration removes the trial's effect and recomputes remaining
applicable rules. Retention explicitly makes a trial permanent; suspension and
Undo remove its future effect while preserving the audit trail. A reported
missed obligation can suspend the rule immediately. Rule review uses the exact
last event and stale changes fail closed.

The new events are `triage.attention_rule.saved`,
`triage.attention_rule.reviewed`, and `triage.attention_feedback.recorded`.
Rules, feedback, previews, and source evidence remain member-private. They
cannot expand read scope, transfer private content to another adult, create
household truth, or grant domain/OpenClaw/provider execution permission.

## Member workspace and mutation behavior

With human management active, the main lenses are **Needs you**, **Alerts**,
**History**, and **All candidates**. Additional interpretation, details,
possibility, recurring-issue, and older shadow-rule views remain reachable.
Updates and source history link to the actual Comms inventory; they are not
relabelled as obligations. History retains immutable decision receipts and All
candidates retains the complete recovery inventory and filters.

Rules are inspectable from Settings and Triage with actual examples and
protected/nonmatching cases. Older scope-only suggestions remain visible and
rejectable; new activation uses the exact conjunction contract. Native semantic
controls, literal-text DOM rendering, narrow layouts, keyboard navigation,
reduced motion, exact source links, and the CO-016 serialized-operation contract
remain required. An uncertain mutation or failed post-write refresh keeps the
surface locked until a successful reload rather than inviting duplicate work.

## Replay, runtime, and deployment

Noise-filtering pipeline version **23.0.0** requests full replay on version
change. It reassesses immutable retained message facts through the same scoped
production path. Non-anchor messages retain terminal accounting; the bounded
latest-anchor path owns language assessment. A new assessment may supersede
only untouched prior interpretations using revision guards. Existing member
enrichment, dismissal, confirmation, management, canonical objects, and provider
terminal outcomes remain authoritative and are not silently reopened.

Source context fingerprints alone do not establish that an older interpretation
uses the current assessment contract. Replay compares the interpretation and
action identity, and can retire an untouched obsolete suggestion when the new
source assessment supports no action. `triage.item.superseded` v4 compares the
proposal's content revision, which advances with member enrichment or management
but not background routing receipts. Historical v3 events retain their original
last-event comparison. A rejected retirement cannot create a duplicate replacement
beside the member's retained interpretation.

Triage advances to **v12** and Context Fabric to **v5**, keeping the existing
projection inventory. New columns/tables and indexes must support rebuild from
the encrypted append-only log, restart, isolation, and encrypted restore.
Settings remains a replayable cache of typed private policy events. Historical
source-family preferences and legacy rule receipts remain readable; migration
does not copy them indiscriminately across accounts.

The production environment loader defaults
`ADMINME_RECONCILIATION_ADMISSION_ENABLED` and
`ADMINME_HUMAN_TRIAGE_MANAGEMENT_ENABLED` to `1`; explicit `0` remains a behavior
rollback control. Programmatic/credential-free runtime construction keeps its
explicit opt-in defaults for compatibility tests. Settings reports effective
activation truthfully. These are domain attention flags, not provider-write,
enrollment, credential, permission, dependency, or model-routing controls.

Phase A runs only in Codex/cloud or a disposable lab with synthetic inputs.
Deployment uses the immutable merge commit from protected `main`, only after
the required checks and owner deployment authorization, through the guarded
updater. Verified encrypted backup, retained rollback, observation mode,
bounded source/replay settlement, full Doctor, privacy/tenant isolation,
authenticated read-only browser acceptance, and zero unintended provider
effects remain prerequisites and post-switch gates. New degradation invokes
automatic rollback. Installed-release edits or unrelated live repairs are not
an acceptable way to satisfy these gates.

## Acceptance and constitutional disposition

AC-60 requires production-composition tests for the source-grounded pipeline,
per-source controls, retained iMessage/future-adapter inventory, action-specific
exceptions, exact rules, forward trials, negative feedback, expiration,
conflicts, replay, and isolation. The credential-free Gate 7 browser harness
adds `triage-source-controls.mjs` against real member APIs and synthetic
encrypted events, including desktop/narrow review, independent settings,
actual preview examples, future trial observations, Undo, and separate correction
choices. Source-wide evaluation must count missed obligations among inputs
that produced no candidate; candidate precision alone is insufficient. Numeric
accuracy targets are evaluation goals, not results claimed by this document.

- **PRESERVE:** ADR-0001/0008/0010 and CO-011/012/016/018/028 boundaries for
  encrypted source truth, private evidence, deterministic authority, pinned
  OpenClaw, separate domain objects, exact corrections, source-neutral adapters,
  member decisions, serialized review, and independent provider guards.
- **MODERNIZE:** CO-011/021/022 interpretation, per-purpose member settings,
  lifecycle inventory, preview coverage, typed conjunctive learning, bounded
  future trials, and explicit runtime activation truth.
- **SUPERSEDE:** bundled attention/context presets; automated-source labels as
  a blanket suppression decision; whole-message receipt/survey classification
  suppressing an independent action; learning future suppression from completion
  or dismissal; fixed existing-candidate trial cohorts; new activation of opaque
  scope-only rules; earlier shadow-only production-default descriptions where
  they conflict with this order.

No required product function, projection capability, privacy/governance
invariant, workbook round trip, or bootstrap-safety requirement is deferred.
Test and deployment evidence must identify the exact commit and report failures
or environment-specific omissions rather than treating this contract as proof.
