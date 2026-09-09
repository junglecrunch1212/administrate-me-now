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

**MODERNIZE candidate priority; PRESERVE separate obligations:** a promise or
request elsewhere in a message cannot promote a sibling candidate. Verified
candidate assessments govern their own actionability. Invalid assessment
qualifiers remain clarification and cannot recover exact-promise or known-human
request priority from source text or source-level flags. Legacy and
language-disabled deterministic proposals require exact candidate-owned evidence
grounded in authored source text for those priorities. A proposed Commitment
kind alone does not justify the member-facing **Your promise** label; independently
typed commitments and authenticated instructions retain their authority.
The production composition proof uses a mixed iMessage source and verifies both
the genuine promise and the unsupported sibling through the member API.

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

**MODERNIZE retrieval; PRESERVE authority and response semantics:** the member
evidence endpoint resolves its exact immutable event ID through the event log's
existing primary-key lookup, rather than scanning preceding private history.
[CO-011](CHANGE-ORDER-011-CANDIDATE-IDENTIFICATION-AND-TRIAGE-CONTEXT.md),
[CO-012](CHANGE-ORDER-012-CONTEXTUAL-MESSAGE-REASONING.md), and the private-evidence
boundary above still govern visibility,
owner, and sensitivity checks, including privileged evidence under view-as.
Missing and unauthorized evidence return the same 404 response; permitted
responses retain the existing field allowlist. The production-composition
regression in `test_triage_vertical_slice.py` places evidence after more than
one history page and rejects any request-path history scan.

## Member workspace and mutation behavior

With human management active, the main lenses are **Needs you**, **Alerts**,
**History**, and **All candidates**. Additional interpretation, details,
possibility, recurring-issue, and older shadow-rule views remain reachable.
Updates and source history link to the actual Comms inventory; they are not
relabelled as obligations. History retains immutable decision receipts and All
candidates retains the complete recovery inventory and filters.

A verified action whose condition is known to be unmet belongs in Other
possibilities. Unknown conditions retain a clarification path in Needs you.
Reference rules change attention without removing the candidate from All
candidates or History. Open reference candidates remain reviewable; completed
decisions and retired alerts retain their audit trail without reappearing as
live alerts.

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
An immutable original action fingerprint preserves that guard when extraction
keys or display punctuation change. It uses the original typed action and
source clause; it does not block a different obligation from the same message.

Triage advances to **v12** and Context Fabric to **v5**, keeping the existing
projection inventory. New columns/tables and indexes must support rebuild from
the encrypted append-only log, restart, isolation, and encrypted restore.
Settings remains a replayable cache of typed private policy events. Historical
source-family preferences and legacy rule receipts remain readable; migration
does not copy them indiscriminately across accounts.

Both the production environment loader and the actual launchd composition root
(`bootstrap.runtime_service`) default
`ADMINME_RECONCILIATION_ADMISSION_ENABLED` and
`ADMINME_HUMAN_TRIAGE_MANAGEMENT_ENABLED` to `1`; explicit `0` remains a behavior
rollback control. Programmatic/credential-free runtime construction keeps its
explicit opt-in defaults for compatibility tests. Settings reports effective
activation truthfully. These are domain attention flags, not provider-write,
enrollment, credential, permission, dependency, or model-routing controls.

The production-launcher regression must exercise `runtime_service.main` through
the real runtime and member API, replacing only host, secret, and server-listener
boundaries with synthetic fixtures. An environment-loader test alone does not
prove launchd activation. Consumer **2.0.1** revisited retained shadow decisions
under active admission. Consumer **2.0.2** revisits candidate priority with exact
candidate-owned actionability, giving retained decisions new immutable identities
without rerunning the language classifier or changing its replay generation.
Existing member dismissals and edits remain protected.
This is **MODERNIZE** under CO-021 and this change order: it completes the
documented production default without changing provider-write authority.

Presentation migration preserves the queue lane already established by active
reconciliation and subsequent member events. Reopening a rebuilt projection
must not replace an explicit member priority with a legacy text classification.
The production-launcher acceptance compares complete live, rebuilt, and reopened
rows, including queue lanes and member content revisions. This is **PRESERVE**
under the deterministic projection/replay contract; the migration hook runs on
every connection open and does not require a new projection version or replay.

At an actual complete fixed-watermark Triage replay, the projection first clears
only its own disposable derived tables in one committed transaction. Its events
then reconstruct proposal identity, content revision, applied member edits, and
refused compare-and-apply attempts in order. This also initializes revision guards
when the retained v11 projection upgrades to v12; replaying over its final rows
would skip both original proposals and already-applied edits. Ordinary opens do
not clear rows. A failed replay retains the prior version marker so retry starts
from the same empty derived state. This is **PRESERVE** under the append-only truth,
member correction, and deterministic replay invariants; no event, provider state,
or operational checkpoint is edited by this projection hook.

Historical unreliable-source replay uses the existing owner-scoped immutable
proposal cache, refreshing only its event-log suffix. Fresh descendant and
content-revision checks remain authoritative for retirement. This is
**PRESERVE**: it changes lookup cost without changing candidate eligibility or
member correction semantics.

Context replay checks episode eligibility and explicit member confirmations
before parsing corroborating text. A source outside the time window cannot
contribute context unless a current member confirmation admits it; confirmed
cross-window sources retain their existing evidence and correction behavior.
Corroborating identifiers and tokens are computed only for admitted related
sources. Terminal reconciliation reuses the same immutable source/proposal
profile cache as proposal admission. These are **PRESERVE** under CO-018 and
this change order: selected context, evidence relationships, privacy checks,
member decisions, and immutable consumer identities remain unchanged. Regression
proofs must measure avoided work as well as equivalent production outcomes;
increasing a timeout does not establish bounded replay cost.

New supersession V4 writers also retain `expected_last_event_id` for retained
predecessors that read this field for every schema version at or above V3. The
original proposal event is a conservative legacy comparison: a predecessor may
skip retirement after intervening routing or member changes. Current readers
continue to use the independent content-revision guard. The compatibility field
is optional in the V4 schema so already-recorded immutable facts remain valid;
all new writers must include it. Rollback acceptance must open the synthetic
post-candidate encrypted state through the actual retained predecessor runtime
and authenticate its member API. This is **PRESERVE** under the deployment and
correction invariants; a healthy candidate rebuild alone is insufficient.

Phase A runs only in Codex/cloud or a disposable lab with synthetic inputs.
Deployment uses the immutable merge commit from protected `main`, only after
the required checks and owner deployment authorization, through the guarded
updater. Verified encrypted backup, retained rollback, observation mode,
bounded source/replay settlement, full Doctor, privacy/tenant isolation,
authenticated read-only browser acceptance, and zero unintended provider
effects remain prerequisites and post-switch gates. New degradation invokes
automatic rollback. Installed-release edits or unrelated live repairs are not
an acceptable way to satisfy these gates.

Backup restoration uses the same bounded settlement policy as activation after
restarting services. New source observations may arrive while restored adapters
resume; an initial source-assurance gap is not by itself a failed restoration.
The shared policy polls content-free readiness within its fixed settlement limit,
fails closed on structural degradation or unavailable proof, and runs the full
Doctor proof once ready. A failed restoration quiesces services before replacing
state directories. These are **PRESERVE** under the bootstrap backup, recovery,
observation, and source-accounting invariants: neither required proof nor rollback
is waived, and installed releases and durable checkpoints are never patched.

When a newer recovery controller restores an older selected release, its Doctor
must execute with that installed release's Python, registry, packs, and plugin
path. Validate the selected release before replacing state and throughout the
settlement wait. An unavailable runtime or changed selection fails closed; the
controller checkout is not a substitute for the selected runtime's proof.

The deployment deadline policy is **MODERNIZE** under the
[bootstrap deadline contract](BOOTSTRAP-AND-TOPOLOGY-CONTRACT.md#deployment-and-recovery-deadlines).
Assessment of several thousand retained anchors in bounded batches can exceed
the former one-hour wait at observed per-call latency even while consumers
advance. The shared policy
therefore allows six hours per settlement phase, with a ten-minute progress
watchdog for each pending consumer. Activation reserves a separate six-hour
predecessor recovery window and ten minutes for final proof/cleanup inside one
immutable thirteen-hour guardian deadline before switching. Neither progress nor
another phase or child process within that attempt renews the budget. A separate
supported interrupted-journal recovery starts a new bounded attempt. Automatic
recovery and the complete
selected-release Doctor remain required; timeout is a failure, never successful
settlement. Source admission, language routing, bounded consumer execution,
replay versions, and durable member decisions are **PRESERVE**.

Exact learning scope includes an optional `protected_source_id`, independently
of account, enrollment, and conversation. Reconfiguring a connection can leave
retained observations from several protected sources under the same account;
a rule made for one source must not silently match another. This is
**MODERNIZE** under CO-007's canonical payload-version rule and this order's exact
source scope. Existing V1 rule and feedback models remain registered unchanged;
protected-source payloads use V2 with explicit upcasters. Omitted scope preserves
existing matching and request fingerprints without inferring historical source
identity. The member sees the draft's source and conversation scope before preview.

Member readiness reads are **MODERNIZE** under CO-008's shared runtime and this
order's usable member controls. Each subscriber status query returns the exact
checkpoint used for its single lag count, even if delivery advances during the
await. It must not wait for replay to pause or pair that count with a newer
checkpoint. Failure and poison information observed after the count remains
visible. Capability collection and publication stay serialized. Settings uses
one capability refresh and reuses that observation for its diagnostics instead
of repeating the collection. Capability refresh omits unused projection table
counts; ordinary full projection status retains its existing count output.
Omitted counts mean not collected, never zero. The source inventory, privacy
checks, assessment versions, and full deployment health/watchdog requirements
are **PRESERVE**. Production acceptance must force checkpoint advancement
during status queries and prove bounded Settings work, member isolation, and
zero external effects without raising browser timeouts.

Classification execution is **MODERNIZE** under CO-012's pinned language seam
and this order's bounded source assessment. Skill 8.0.1 retains the 8.0.0 prompt,
schemas, model routing, and token limit, with a 120-second completion budget and
a total HTTP wait bounded at 122 seconds. Each source retains three sequential
attempts and bounded backoff; the existing eight-source normal batch does not
serialize every source's retries through one global lock. Its language waits
therefore total at most 368.5 seconds, plus local processing, within the unchanged
ten-minute progress watchdog. Explicit request-bound recovery retains its own
two-call limit and is not covered by this normal-batch calculation.

Exact compatible 8.0.0 successes may be reused only with the same scoped inputs,
validated current schemas, and original receipt and version provenance. A paired
failure receipt is not a reusable success. Previously spent failures cannot be
silently converted to success; authorized recovery remains request-bound.
Existing v23 assessment event payloads and replay generation are **PRESERVE**:
new receipt metadata must not rewrite earlier idempotent assessment facts.
If an older failed call also recorded a defensive default and already completed
an exact v23 assessment, refusal of that default must preserve the existing
assessment and proposals. This narrow historical guard requires the validated
scoped invocation receipt to precede that exact assessment; it appends no new
success and grants no recovery or provider authority.

Source failure precedence is **MODERNIZE** under
[CO-008](CHANGE-ORDER-008-SOURCE-ASSURANCE-AND-UNIVERSAL-CAPTURE.md) and
[CO-017](CHANGE-ORDER-017-SOURCE-ASSESSMENT-RECOVERY.md). When a required language
assessment fails, a new terminal source assessment remains `failed` even if
deterministic extraction found useful candidates. Those proposals remain
available separately; their existence cannot establish complete source
assessment or clear failure health. Restart preserves the failure, and only the
existing explicit recovery path may append a replacement assessment.

The classifier explicitly requires a clean skill result. Handler and output
validation failures append their existing typed failure receipt and raise;
they cannot return a defensive default as a completed required assessment.
Same-version cached results with an exact paired failure are likewise refused
without another model call. This option defaults off for other skill consumers,
preserving their existing defensive-default behavior. Nonretryable failures do
not gain attempts.

Earlier v23 receipts remain immutable even when their recorded completion enum
contradicts a proven required language failure. The shared current assurance and
recovery read model derives failed health only from exact source, privacy,
producer, assessment-policy, and failed-invocation evidence. It does not rewrite
historical bytes or infer failure from candidate confidence. Retained proposals
remain separately usable. Only the existing explicit recovery command may
replace a retryable failure; refusal of a legacy defensive default grants no
new provider or correction authority.

Crash-safe proposal publication is **MODERNIZE** under
[CO-007](CHANGE-ORDER-007-TRUSTWORTHY-CAPTURE-TRIAGE-AND-SSOT.md), CO-008, and
CO-017. A newly assessed source with pending proposals atomically appends its
unchanged v3/v4 assessment and a private `triage.source_publication.planned`
receipt. The plan holds exact, bounded proposal drafts and their existing
idempotency identities. Publication fills only the generated extraction ID and
causation from that assessment; it does not recalculate source context, model
output, routing, or candidate eligibility. Normal source processing, explicit
recovery, and plan delivery serialize on the same exact source. Existing member
decisions remain authoritative; a newer valid assessment can retire an old
unpublished suffix. Fresh append ordering preserves late delivery after a cold
reopen while historical occurrence times remain unchanged.

A private, content-free completion receipt accounts for every planned draft as
published, already present, member-decided, or superseded. Startup finds
unfinished plans through one fixed metadata watermark and bounded header
lookups, without decrypting completed draft history or depending on the normal
pipeline cursor. This preserves resumption after a predecessor ignores the new
internal event and advances its existing cursor. The pipeline/replay version
remains 23; old assessment schemas, proposal schemas, member state, and provider
authority are **PRESERVE**. Existing assessments without plans are not rewritten
or given invented draft identities. Production regressions cover interruption
before the first proposal and after one proposal plus member dismissal, normal
and explicit recovery, retained cursor advancement, immutable receipts, current
failure health, and zero external effects.

Settlement failure evidence is **MODERNIZE** under the bootstrap recovery
contract. Persist the allowlisted runtime observation that triggered failure
separately from the retained Doctor report. Counts and freshness from an earlier
Doctor must not be presented as the triggering observation. Consumer metadata
retains versions, counts, and checkpoint or poison presence without source IDs
or content. Revalidate this evidence before persistence. Immediate failure,
full Doctor, immutable deadlines, progress watchdogs, and privacy are **PRESERVE**.

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
