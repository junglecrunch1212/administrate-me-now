# CO-008 — Source assurance and universal member capture

Status: **APPROVED; SOURCE REPAIR MERGED — FOUR GUARDED ACTIVATIONS ROLLED BACK; CONTROLLER-BOUNDARY REPAIR IN REVIEW**

Date: 2026-08-08

Affected paths: every adapter and bridge source, the encrypted event log,
reactive pipelines, projection checkpoints, Triage, Settings, Doctor, the
shared member shell, and the native OpenClaw Add-for-review tool.

## 1. Corrected objective

Make every authorized source—member capture, adapter, bridge, or channel—prove
the deterministic path from immutable source fact to one audited terminal
disposition: proposed for human review, explicitly assessed as no candidate,
quarantined with a reason, projected as provider truth, retained as a canonical
observation/action result, or failed visibly. Provide one universal adult
member capture surface for every supported Triage target kind; inventory every
source and adapter against this contract; eliminate silent drops,
false-health checkpoints, and overbroad fallback noise; expose content-free
source accounting in Doctor, Settings, and Triage; and add process-level gates
so a new source cannot ship without end-to-end proof.

The LLM may interpret language only. Fixed code retains grounding,
eligibility, scope, routing, authority, idempotency, and truth. An event-log
append proves ingestion; it does not by itself prove that the source was
processed.

## 2. Why the launched Gmail path failed

The absence of useful Triage items was not evidence that the mailbox contained
no obligations. It was a compound observability and eligibility failure:

1. Gmail had appended immutable `messaging.received` facts, but the required
   language pipeline previously poisoned before interpretation. Earlier repair
   restored replay, but runtime health still measured subscribers rather than
   whether each source fact received a disposition.
2. Candidate-bearing pipelines emitted a feature event only on selected
   branches. Empty text, invalid scope, language failure, privileged content,
   or zero eligible candidates could return without a terminal assessment.
   “No proposal” therefore conflated quiet, rejected, stalled, and failed.
3. The generic deterministic member-text action-verb rule was reused against
   arbitrary email bodies. It admitted marketing and message chrome as tasks,
   producing 56 low-specificity legacy proposals while still providing no
   per-message accounting.
4. Notes used model confidence as an admission threshold. That made a model
   score an implicit authority and silently discarded otherwise grounded
   source evidence.
5. Adapter manifests declared emitted event families but not what must happen
   after append. A new source could validate while feeding no assessor or the
   wrong projection.
6. Add for review existed in the adult shared shell and native OpenClaw tool,
   but the browser exposed only eight of the eleven supported target kinds.
7. Doctor and Settings could report a healthy adapter/pipeline/projection while
   a particular source fact had no assessment. Queue emptiness was still being
   mistaken for source completeness.
8. Retained live replay evidence then showed that 39 legacy Gmail facts were
   household-shared and 316 were correctly member-private. Scope refusal ran
   after NLP, so a shared fact could consume the serial language worker before
   being quarantined. A five-second readiness request could also expire while
   the live runtime was calculating its content-free audit.

The repair therefore addresses the class of failure, not one mailbox or one
message pattern.

## 3. Source-disposition contract

Every declared adapter capability event has one or more `source_assurance`
rules in its reviewed `pack.yaml`. The category determines the required mode:

| Epistemic role | Required disposition | Meaning |
|---|---|---|
| Cat-A communication | `candidate_assessment` | Every inbound/outbound communication fact receives `triage.intent.features_extracted@v2`, including zero-candidate, quarantine, and failure. |
| Cat-B external mirror | `canonical_projection` or reviewed canonical observation | Provider truth reaches the named mirror projection; it does not enter Triage merely because it exists. |
| Cat-C inbound data | `canonical_projection` or `canonical_observation` | The fact reaches its deterministic read model or is itself the retained observation. |
| Cat-D personal knowledge | `canonical_projection`; non-delete sources explicitly declare `candidate_assessment` or `candidate_not_applicable` | Notes remain queryable provider facts and candidate-bearing text is assessed independently; folder metadata cannot silently masquerade as candidate text. |
| Cat-E outbound action | `terminal_action` | `action.executed` or `action.failed` is already the terminal action fact. |
| Member Add for review | `candidate_assessment` | Exact text is durable before deterministic extraction; an untyped thought remains visible for human interpretation. |
| Governed external command | `action_request_terminal` | Every request reaches completed/failed terminal evidence; observation suppression remains an audited completed outcome. |

The install-time adapter validator now fails when:

- any capability event lacks a disposition;
- a Cat-A source lacks a named assessor or the registered feature terminal;
- a Cat-D source lacks a projection and an explicit candidate assessment or
  not-applicable declaration;
- a named projection does not exist;
- a terminal event schema is unregistered; or
- a source-only terminal mode falsely claims downstream work.

All five scaffold templates include the contract or an explicit empty
fail-closed placeholder, preventing future generated packs from normalizing
the former omission.

## 4. Runtime accounting and failure taxonomy

`source-assurance-v1` audits the encrypted log against manifest obligations and
current production checkpoints. It exposes no source content, address,
subject, account identifier, or credential. Counts and bounded samples use only
event id, event type, disposition mode, and reason code.

Candidate assessments have exactly four outcomes:

- `eligible_candidates`: one or more bounded, grounded proposal candidates;
- `no_candidate`: the assessor completed and fixed eligibility admitted none;
- `quarantined`: processing was intentionally refused because scope,
  sensitivity, or member linkage was unsafe;
- `failed`: required interpretation did not complete or returned an invalid
  contract.

Projection/action obligations are `accounted`, `pending`, or `missing`.
Pending is allowed only inside a five-minute in-flight grace period. A
checkpoint that passed an unassessed fact, an unhealthy required subscriber,
an overdue checkpoint, an absent inline assessment, or an overdue action result
is missing. Any missing obligation or failed assessment degrades
`policy:source-assurance`, `surface:triage-processing`, and the member product.

Legacy v1 feature events are interpreted conservatively: an empty `complete`
result is no-candidate, while `unavailable`, `invalid`, or defensive-default
without candidates is failed. Historical failure can no longer appear as a
healthy empty source.

## 5. Deterministic candidate policy

Member-authored Add for review remains permissive because the member
deliberately selected the proposal gate. Arbitrary communication streams use a
separate high-signal fallback:

- inbound: an explicit directive/request or a due/overdue/expiring obligation;
- outbound: an exact first-person promise or commitment;
- reject common URLs, unsubscribe text, storefront calls to action, client
  signatures, and other message boilerplate;
- preserve the exact original source span and whitespace in evidence;
- merge only with schema-valid, exact-quote-grounded OpenClaw output;
- never use confidence as eligibility;
- never infer member ownership from an inbound sender's promise;
- never create canonical truth or a provider command.

The reviewed v9 Gmail replay reuses any prior successful exact-input
interpretation. A separate explicit retry-generation flag permits one retry of
a causal terminal failure; that retry receives a fingerprint-qualified failed
terminal so later replay cannot call the model repeatedly. Changing a success
idempotency key alone does not bypass a prior causal failure. Exact private
duplicate observations still share one successful result. Untouched legacy proposals
whose only semantic admission rule was `explicit_action_verb_v1` are appended
as `triage.item.superseded@v2` with `eligibility_rule_replaced`. Any proposal a
member enriched, confirmed, materialized, dismissed, or already superseded is
preserved permanently.

Notes v3 evaluates privacy/member scope before the language seam, emits an
assessment on every add/update/correction branch, removes the confidence
threshold, requires an exact quote, and uses a reviewed invocation generation.
Deleted Notes remain projection/revocation facts and are not candidate
sources.

Live v9 adds no semantic authority. It moves Gmail privacy/member checks before
NLP, gives only this idempotent language-bound subscriber eight bounded
concurrent deliveries, and checkpoints only the contiguous successful source
prefix. `classify_message_nature` 4.0.4 uses a 30-second bounded timeout that
matches the pinned OpenClaw completion envelope. These are throughput and
failure-isolation controls, not permission to batch source text, skip a source,
or treat a model result as truth.

## 6. Universal adult capture

The shared-shell Add for review control and native
`administrateme_add_for_review` tool expose the same eleven non-authoritative
type hints:

`task`, `commitment`, `calendar_event`, `reminder`, `list`, `list_item`,
`recurrence`, `party_identifier`, `party_candidate`, `party_relationship`, and
`relationship_fact`.

Exact text is appended as `triage.intent.submitted` before
`triage.intent.features_extracted@v2`. A known hint produces a human-gated
proposal even when more fields must be supplied. Unknown text remains a durable
Needs-interpretation row. Nothing becomes official before enrichment and
confirmation.

This change does not widen child authority. Existing child route and capture
denials remain a privacy/product decision under D26 and CO-007; changing them
requires a separate reviewed role contract.

## 7. Inventory result

The executable catalog covers Gmail communications, platform SMS facts,
Google/Apple calendars, Google Tasks, Apple Reminders, four contact providers,
Plaid, Home Assistant observations/actions, Apple Notes, Voice Memos,
Obsidian, member Add for review, corrected Notes, and external write requests.

The following named product aspirations are not installed source adapters and
remain explicitly unavailable rather than simulated: Outlook mail, native
iMessage/BlueBubbles, Twilio SMS/WhatsApp ingest, Evernote, Otter, and Fathom.
Their existing provider/UI capability descriptions do not prove ingestion.
When implemented, their packs must satisfy this contract before install and
their live accounts must show source/disposition totals before the surface may
claim healthy-empty.

Calendar, reminder/task, contact, financial, sensor, and action facts do not
all belong in Triage. Their manifest disposition proves the appropriate
projection or terminal fact instead. Triage is for candidate-bearing language
and human adjudication, not a duplicate of every provider source.

## 8. Preventative and acceptance mechanisms

The following are release requirements:

1. all central and bridge adapter manifests validate with no source-assurance
   finding;
2. catalog tests cover all five epistemic roles plus member and command sources;
3. a checkpointed source without assessment is degraded/missing, never empty;
4. a normal no-candidate email and a privileged quarantined email both produce
   terminal v2 assessments through the production composition;
5. all eleven member hints produce proposal-only Triage rows;
6. a provider mirror fact reaches its declared projection;
7. Doctor, Settings, and Triage report the same secret-free totals;
8. model loss records failed assessment without blocking source projections;
9. scope/sensitivity rejection occurs before model processing;
10. replay is idempotent and only untouched retired-rule proposals are
    superseded;
11. the full Python, bridge, invariant, acceptance, plugin, browser, and clean
    macOS gates remain green.
12. readiness serves the most recent content-free source proof in bounded time
    while at most one background refresh decrypts and scans the log;
13. Doctor fails deployment until accounting is fresh and every obligation is
    terminal, with zero pending, missing, or failed dispositions; and
14. a reviewed retry generation reuses prior success, retries an older causal
    failure exactly once, and reuses its own terminal failure on redelivery.
15. every updater path executes the clean reviewed candidate's attested policy
    from a commit-versioned controller while preserving the explicit runtime-
    migration gate and active runtime for same-baseline updates.

`tests/acceptance/test_source_assurance_vertical_slice.py` is the process-level
composition proof. Unit tests cover missing-vs-empty accounting, all terminal
outcomes, category validation, conservative fallback, scope ordering,
supersession, retry generations, and exact-source preservation.

## 9. Constitutional classification

- **PRESERVE:** encrypted append-only SSOT, fourteen projections, distinct
  domain kinds, provider truth, human Triage gate, member isolation,
  observation mode, and one pinned OpenClaw model seam.
- **MODERNIZE:** every source fact gains a declared and runtime-audited
  disposition; member capture exposes the complete candidate vocabulary;
  health is source-accounted rather than queue-inferred.
- **SUPERSEDE:** silent pipeline returns, v1 failure-as-empty inference,
  confidence admission thresholds, broad email action-verb fallback,
  registration/checkpoint-only health, and Cat-D future-projection carve-outs
  for required functionality that has already shipped, plus active-release
  ownership of reviewed candidate upgrade policy.
- **DEFER:** only unimplemented provider adapters named in §7. No required
  source disposition, privacy invariant, product function, or acceptance proof
  is deferred.

## 10. Deployment boundary

Merge requires full release gates and review. Activation requires an encrypted
backup, immutable release publication, v9 message and v3 Notes reviewed full
replay, zero subscriber lag, source-assurance reconciliation, full Doctor, and
portal verification. Observation mode remains active. The replay may propose
or supersede review items; it may not confirm, materialize, send, or mutate any
provider.

### 10.1 First guarded activation outcome

PR #296 merged as `1d2b64a`. The updater built and selected that immutable
candidate only after creating encrypted backup
`administrateme-20260809T021351Z.ambackup`. Both launch agents started, but the
post-switch Doctor rejected `launchd_and_process_state`; the updater restored
the prior `c484f0a` release and its service definitions successfully. No source
fact was removed, and all replay/audit appends remain immutable.

The rejected candidate exposed two implementation defects and one missing
release proof:

1. each readiness call invalidated source accounting on the latest event and
   every replay checkpoint, synchronously rescanning the live encrypted log;
   the five-second service probe timed out even though the runtime was live;
2. the v8 invocation key could not bypass the wrapper's separately cached
   causal failure, so the four historical transport failures were not actually
   eligible for retry; and
3. acceptance exercised runtime diagnostics but bootstrap Doctor had no named
   source-disposition check.

The deployment repair uses a mandatory initial audit followed by a
30-second stale-while-revalidate cache with one cancellable background refresh.
Freshness and exception type are content-free health evidence; a refresh
failure degrades the capability. Doctor now requires fresh accounting,
`accounted == obligations`, and zero pending, missing, or failed outcomes. The
updater retries only its named post-switch transients for a bounded two-minute
window. Structural failures still abort immediately and automatic rollback
remains unchanged.

### 10.2 Second guarded activation outcome

PR #297 passed both hosted gates and merged as `4c81ac4`. Its guarded
activation built the immutable release and created another encrypted backup,
but the two-minute post-switch window ended before the reviewed v8 full replay
reached the historical message prefix. Doctor correctly rejected
`source_assurance_terminal_dispositions` and the updater restored `c484f0a`.
The restored runtime and pinned OpenClaw Gateway are healthy.

A content-free offline audit of the retained event log showed 54,125 total
obligations: 53,839 accounted, 286 historical `messaging.received` assessments
missing, zero pending, and zero failed. This is progress, not a semantic or
transport failure. Rollback necessarily returns the one operational pipeline
cursor to v7; the next version change therefore starts the reviewed v8 replay
from the beginning and cannot accumulate cursor progress across repeated short
activation attempts.

The next repair does not weaken Doctor or preserve a candidate cursor across
rollback. It centralizes the same content-free terminal predicate used by
Doctor and updater. After one full post-switch Doctor identifies only named
transients, the updater polls the cheap readiness document for up to 30 minutes
while replay advances, without repeatedly reading the database and probing all
subsystems. Once source accounting is fresh and terminal, it runs the complete
Doctor and synthetic proof once. Structural failures still return immediately;
deadline failure still rolls back.

### 10.3 Third guarded activation and source-throughput repair

PR #298 merged as `5c85b05`; its guarded activation again restored `c484f0a`
after the short active controller rejected incomplete source settlement.
Retained evidence identified 316 private-member Gmail facts and 39 legacy
household-shared facts. Scope refusal was still downstream of NLP, and the
language-bound consumer was serial. PR #299 moved privacy/member refusal before
the model, added explicit eight-way idempotent delivery with ordered-successful-
prefix checkpointing, and aligned the bounded skill timeout with the pinned
Gateway envelope. It passed the complete local and hosted release gates and
merged as `d5dd805`.

### 10.4 Fourth guarded activation and controller-boundary repair

The merged PR #299 runtime reached 49 eligible-candidate assessments, seven
explicit no-candidate assessments, 39 quarantines, and zero failures while its
content-free accounting advanced through 56,919 obligations. It therefore
proved the Gmail path and v9 throughput repair were functioning. It was still
rolled back safely because the command entered through `c484f0a`'s updater;
that ordinary path ran the active release's old two-minute upgrade manager and
could not execute PR #299's reviewed 30-minute settlement policy.

The controller-boundary repair **SUPERSEDES** that circular assumption. Every
upgrade now requires an unchanged clean Git commit, builds the candidate policy
in a commit-versioned controller, rechecks the checkout, attests the imported
module and commit, and makes the candidate manager verify the receipt before
any live mutation. A same-baseline controller still runs on the active Python
and Node runtime; only the separately approved migration form may select the
candidate runtime. Backup, final Doctor, synthetic proof, prior-release
retention, and automatic rollback remain unchanged.
