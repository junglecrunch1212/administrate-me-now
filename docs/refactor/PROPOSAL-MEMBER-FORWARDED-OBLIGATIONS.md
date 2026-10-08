# Member forwarded obligations design proposal

Status: **ACCEPTED FOR IMPLEMENTATION; NOT ACTIVATED**. The owner requested
execution on 2026-10-08. [CO-031](CHANGE-ORDER-031-MEMBER-FORWARDED-OBLIGATIONS.md)
governs the implementation and its explicit support/activation boundaries.
The current-runtime descriptions below record the pre-change design baseline.

Date: 2026-10-08

Code baseline: protected `main` at `d0496d277acda048bdb1d69ca616521c77129b68`.

## Decision

Refactor the existing application. Make a member's deliberate submission the admission boundary for communication-derived obligations. Members forward email or messages, share calendar material, or explicitly add something for review. The CoS extracts proposed work from that submission; the existing Triage and governed delivery paths handle the decision and execution.

Keep the encrypted event history, distinct domain models, member isolation, Comms, calendars, reminders, People, Notes, finance, household signals, workbooks, profiles, personas, and OpenClaw integration. A new repository would require rebuilding and migrating these capabilities while still needing the same intake, provenance, and delivery work.

The product tradeoff is explicit: a personal message that a member does not submit creates no new obligation proposal. The system can continue tracking already accepted work and authorized provider state. Forwarding means “interpret this for my review”; it does not approve a provider write or prove that the obligation is still outstanding.

## Current implementation and runtime

| Area | Verified foundation | Gap for the requested workflow |
|---|---|---|
| CoS email | Service-owned Gmail ingestion and exact configured member routes exist. | Routing currently matches the RFC From header; verified submission identity must replace the stronger authentication assumption. |
| Direct intake | `DirectIntakeProcessor` creates an isolated `triage.intent.submitted` cue. | It collapses whitespace, keeps only 2,000 characters, and stops at pending interpretation. |
| Extraction | Member capture already uses deterministic features plus the pinned OpenClaw skill seam. | That workflow is embedded in the member HTTP handler; no durable intent extraction subscriber serves CoS intake. |
| Forwarded evidence | Gmail retains body alternatives and attachment metadata. | Ordinary message interpretation strips forwarded/quoted material. Attached email, calendar files, images, and documents need explicit content handling. |
| Triage | Typed proposals, edits, decisions, exact destinations, and replayable lifecycle exist. | Submitted material needs reliable interpretation status and grouped proposals without a duplicate parent decision. |
| Delivery | Google/Apple Calendar and Google Tasks/Apple Reminders routes exist. | Exact supported operations and observation/reconciliation coverage differ by destination. |
| CoS iMessage | A planned service channel exists; member Messages ingestion and exact-chat reply code exist. | Member Messages access is not a CoS-owned channel. CoS transport integration remains required. |

On inspection, the running release pointer and LaunchAgents selected `e12156545c02bd6ed0673ce7b5c1dc3f69313903`; current main is 14 commits ahead. Both managed services reported running and their local liveness endpoints responded. The executable reported Python 3.14.7; the release manifest pins OpenClaw 2026.7.2 and the Node 24 baseline. These are liveness/configuration observations, not a full Doctor or release acceptance result.

Observation mode is active. The configuration includes CoS Gmail with two reviewed sender routes, plus CoS Google Calendar and Tasks; CoS iMessage is marked planned and unenrolled. However, the instance also has a `disposable-test-acquisition-v1` restriction selecting 200 personal Gmail messages. Its effective-configuration code disables every other adapter. Therefore configured CoS accounts do not establish a currently polling, end-to-end intake service. Changing that acquisition restriction or activating channels belongs in the separately authorized operational cutover, not this design pass. The bounded sample must not become a claim of complete household backlog coverage.

Primary code evidence:

- [Direct intake](../../adminme/direct_intake.py), especially `_bounded_text` and `handle`.
- [Member capture and Triage](../../adminme/products/member/main.py), `_submit_triage_intent_locked`, typed materialization, and confirmation handlers.
- [Gmail source](../../packs/adapters/messaging/gmail/adapter.py), `_received_observation`, `_trusted_route_key`, `_extract_bodies`, and `_attachment_metadata`.
- [Runtime composition](../../adminme/runtime/application.py), direct intake subscription and startup recovery/publication reconciliation.
- [Acquisition restriction](../../adminme/adapters/disposable_test_acquisition.py), `restrict_adapter_config`.
- [Existing CoS contract](CHANGE-ORDER-023-SERVICE-OWNED-COS-SOURCES-AND-DIRECT-INTAKE.md) and [production-composition test](../../tests/acceptance/test_service_owned_cos_sources_direct_intake.py).

## Member workflow

1. A member forwards selected material to the CoS intake email or messaging endpoint, shares an event, or uses existing Add for review. A forwarding action requires no special prose when the attached evidence is sufficient. An optional comment can supply owner, intent, or destination.
2. The system identifies the submitting member from verified outer transport metadata and the reviewed channel binding. Quoted original senders, signatures, names, and instructions cannot select a member or grant access.
3. An immutable submission records the member, channel, source identity, evidence, receipt time, and processing status. Unknown or ambiguous routes stay service-private and do not enter a member queue.
4. A durable interpreter extracts one or more distinct proposals with exact evidence. For example, a forwarded appointment email may yield a calendar event and a separate preparation task. Missing dates or unclear meaning remain visible for clarification.
5. Triage shows the submitted source, proposed work, missing information, and exact destination. The member edits, rejects, links to existing work, or confirms each relevant action.
6. Existing domain and OpenClaw authorization govern delivery. The UI distinguishes accepted internally, awaiting approval, suppressed by observation, applied at provider, observed, reconciled, and failed.

Ordinary CoS conversation remains conversation unless the configured intake endpoint or an explicit submission gesture establishes the purpose. Bulk automatic forwarding from a personal mailbox must not silently reintroduce passive obligation discovery.

## Stop automatic obligation discovery at the source

Add one typed, versioned admission policy, conceptually `communication_obligation_intake = explicit_member_submission`. This policy is distinct from acquisition, display, context, and external-write permissions. Persist its revision/cutover event so restarts and event replay reproduce the decision. A source setting such as quiet/manual, or a hidden Triage card, is insufficient.

Resolve eligibility from event provenance before obligation interpretation and recheck it before publishing results. The same decision must govern ordinary ingest, historical replay, assessment recovery, requested revisions/currentness runs, and publication of saved model results. An in-flight result from the old policy cannot publish after the new policy takes effect. Complete those jobs with an audited retired/not-selected outcome so startup settlement does not keep retrying them as failures.

Relevant code paths include:

- [Noise filtering](../../packs/pipelines/noise_filtering/handler.py): `handle`, `_handle_source`, `_resume_source_publication`, and `_publish_source_drafts`. `_handle_source` currently resumes prior publication before looking up candidate policy.
- [Commitment extraction](../../packs/pipelines/commitment_extraction/handler.py) and [thank-you detection](../../packs/pipelines/thank_you_detection/handler.py), which independently emit `commitment.proposed`.
- [Pipeline runner](../../adminme/pipelines/runner.py) and runtime startup reconciliation of recoveries/publications.
- [Source assurance](../../adminme/source_assurance.py): intentional exclusion must appear as normal policy state, not missing extraction coverage.

Retain personal Comms observations, authorized search/recall, explicit replies, and canonical Calendar/Tasks/Reminders synchronization. Separate any retained non-obligation processing from the obligation interpreter. Do not disable all pipelines: financial observations, reviewed home rules, explicit Notes-to-Triage, lifecycle tracking, and People functions are outside this change. Reuse commitment/thank-you semantics on explicitly submitted evidence where appropriate.

## Preserve complete submitted evidence

Introduce a versioned submission contract rather than increasing the existing 2,000-character display field. Proposed fields are `submission_id`, submitting member, verified route evidence, service/channel/account binding, native receipt identity, source event IDs, member commentary, ordered evidence parts, original timestamps where supplied, receipt time, sensitivity, content digests, completeness status, parser version, and policy revision. Names here are a design, not existing schemas.

Store raw service ingress only in its authorized encrypted source scope. Create an explicitly authorized member-scoped submission snapshot for the forwarded material, so member review does not grant access to the whole CoS mailbox. Model input is bounded separately from storage and records exactly which parts were included. Large or unsupported material must create a visible incomplete/needs-information state, not a successful result based on a silently truncated prefix.

Keep member commentary separate from the forwarded original. Add a dedicated parser for submissions; preserve the conservative ordinary-message quote stripping in [message context](../../adminme/message_context.py). Treat all forwarded material as evidence, including text that looks like instructions to the assistant. Extraction receives no execution tools and cannot choose ownership, privacy, destinations, or permission.

Implement plain/HTML forwards, attached email, and calendar files as explicit parser paths. Calendar evidence needs UID, sequence, recurrence identity, timezone, start/end, and cancellation semantics. An invite is not an automatic RSVP; forwarding an existing event is not authority to duplicate or modify it. Preserve claimed original dates separately from receipt time, and flag ambiguous relative dates. Images/PDFs/other attachments need a declared decoder capability through the permitted skill seam or a visible unsupported state. The workflow is not complete for those formats until their process tests pass.

The Gmail route currently trusts a normalized From match. Harden it with evidence from the trusted receiving system plus the reviewed member/channel binding; domain authentication alone must not be mislabeled proof of an individual member. Unknown, conflicting, multiply addressed, or unverified submissions remain unresolved or require authenticated member claim. Never trust authentication headers copied inside a forwarded message. [RFC 8601](https://www.rfc-editor.org/rfc/rfc8601.html) defines the trust boundary for authentication results, and the [Gmail message resource](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages) exposes message headers and MIME parts rather than an authenticated application-member identity.

## Extract durably through the existing model seam

Move the reusable member-capture interpretation logic out of the HTTP handler into a production-composed event consumer. Manual capture and direct CoS submission feed that same service. Do not impersonate a member by calling an HTTP handler under a fabricated session.

Use the pinned OpenClaw `llm-task` seam with a versioned submission input/output schema. Reuse typed candidate fields, exact evidence grounding, candidate validation, and existing Triage events. Persist the interpretation before publishing child candidates; restart should reuse recorded results. Bound attempts and timeouts. A failed or ambiguous extraction leaves the submission available for retry or manual completion.

Identify transport retries using member/service/channel/account/native-receipt identity and revision. Use stable candidate keys within a recorded interpretation. A genuine repeated forward may suggest an existing item, but subject similarity, a model guess, or a cross-member content match must not automatically merge work. A member's dismissal or edit wins over later re-extraction. Existing exact canonical/provider-object matches should propose linking evidence instead of creating duplicate work.

The original pending-interpretation cue becomes the submission's processing envelope. Once candidates are published, replace its separate decision burden with the grouped children; preserve a parent clarification item when no candidate can be formed. Do not leave both “interpret this” and the extracted obligations as duplicate decisions.

## Retire the existing automatic backlog

Activate the admission policy before migrating the queue. Produce a private, content-minimized preview of exact counts selected, excluded, and preserved. Select by immutable source and producer provenance, not title, date, or a blanket queue filter. Include legacy `commitment.proposed` roots as well as `triage.item.proposed` roots.

Append retirement receipts for untouched, undecided automatic personal-communication obligation candidates. Preserve source facts, confirmed/materialized work, approved provider actions, manual submissions, CoS submissions, and member-enriched/managed/dismissed decisions. Race protection must compare the proposal/content revision actually inspected; a concurrent member action defeats the stale retirement attempt.

Reuse the compare-and-apply mechanism in `triage.item.superseded@v4` where suitable, with a dedicated retirement reason and policy generation guard. Do not reuse `eligibility_rule_replaced` without changing its meaning: current projection logic intentionally permits replacement proposals for that reason. A durable retirement marker must prevent later replay or late model publication from reopening the same passive source as new work.

Retired items leave active Triage while remaining auditable in history. Existing member-edited pending proposals remain as saved member work; removing those too would be an additional, explicit queue decision. Never delete the log, reset provider cursors, or clear the entire Triage projection as the migration.

Older releases may ignore new retirement events and resume old discovery. Rollback therefore needs a policy-aware supported predecessor or a tested compatibility mechanism that keeps obligation discovery off. Merely switching the release pointer is not proof that the new intake policy survives rollback.

## Reuse delivery with exact destination semantics

| Intended outcome | Existing path to retain | Required clarification or completion |
|---|---|---|
| Internal task, commitment, recurrence | Existing typed materializers | An internal task does not automatically become a Google Task. |
| Google or Apple calendar item | Triage `calendar_event` and exact writable calendar route | Honor calendar/timezone/visibility and bridge capability; do not create invitations implicitly. |
| Google Task or Apple Reminder | Triage reminder/list-item create path | Store the member's exact default account/list; Google Tasks due semantics are date-only. |
| New list | Current Triage list creation supports Apple Reminders | Do not advertise equivalent Google list creation through Triage until composed and tested. |
| CoS email receipt or completion message | Separate governed messaging action | CoS Gmail source enrollment is read-only; intake does not confer sending authority. |
| CoS iMessage receipt or proactive message | OpenClaw channel execution plus domain approval | Existing G11 member-account exact-chat reply is not general CoS send/new-chat support. |

Reuse the existing allowlist → governance → rate-limit order, both approval layers, final content/privacy guard, observation suppression, and terminal auditing. Candidate confidence must never authorize execution. Member approval and independent OpenClaw approval can be presented coherently while retaining their separate authority records.

Complete exact command/result/native-object/observed-state linkage per supported destination. Current specialized Google Tasks reconciliation handles `cos.google_tasks_execution.*`; it does not prove that every generic Triage provider write has the same reconciliation. Never label API acceptance or a provider create response as observed completion. Handle retries and ambiguous timeouts with existing idempotency/reconciliation safeguards rather than blind resend. Returned provider objects must not become fresh obligation candidates.

## CoS messaging channel

Use the pinned OpenClaw channel/conversation boundary for the CoS endpoint and emit a consented typed submission into AdministrateMe. Keep the CoS provider account, service principal, submitting member, and destination member account distinct. Reuse relevant Messages transport/normalization code only behind a proper service-owned channel contract; do not enroll the CoS as a fake member or label personal bridge history direct intent.

The central topology remains one AdministrateMe Python process plus the pinned Gateway. Any required Apple transport, edge process, permissions, account enrollment, or dependency installation must have its own reviewed operational activation. Email is the first demonstrable vertical slice because its service-owned source already exists; iMessage remains part of the requested design and is not complete merely because email works.

## Implementation sequence

Use one implementation branch from then-current protected main and one PR for this coherent change, with the following reviewable commits or work packages:

1. Add the admission policy, terminal exclusion receipts, source-assurance behavior, and revision-safe retirement migration. Prove no fresh or recovered passive source can emit an obligation candidate.
2. Add the submission/evidence contract, authenticated routing, forward parsers, and shared durable interpreter. Complete the email-to-Triage slice including multiple candidates, failures, and replay.
3. Compose the CoS messaging ingress through OpenClaw and expose truthful channel capability/state. Complete calendar/attachment parsing required by the supported forward formats.
4. Connect member destination defaults and complete each advertised output route through approval, execution, observation, and reconciliation. Add submission grouping and status wording to Triage.
5. Update governing contracts and run protected checks, full acceptance, replay/isolation/backup coverage, and the required disposable macOS rehearsal. Merge only after owner authorization and passing required checks.

After merge, a separate owner request can authorize deployment of the immutable merge commit. Use the guarded updater, verified encrypted backup, compatible rollback, observation mode, bounded settlement, Doctor, and authenticated read-only member acceptance. Provider enrollment, leaving the bounded acquisition setup, Apple permissions, and write enablement remain explicit operational changes. Do not modify an installed release or active environment to make activation pass.

## Acceptance criteria

The production composition root must demonstrate all of the following with credential-free/disposable fixtures before live activation:

1. Unsubmitted personal email/iMessage creates zero obligation-extraction invocations and zero obligation candidates during fresh ingest, history, restart, recovery, currentness revision, and delayed publication. Retained Comms, canonical provider mirrors, and non-obligation product functions still work.
2. One verified forward reaches durable capture, extraction, grounded candidate(s), the existing Triage decision, the exact member destination, both approvals, a fixture provider result, subsequent observation, and reconciliation.
3. Member A and B remain isolated. Unknown/spoofed/multiple senders, quoted identities, shared/group ambiguity, injected instructions, wrong accounts, and unrelated history cannot select ownership or authorize a write.
4. Long plain/HTML forwards, attached email, calendar files, timezone/relative-date ambiguity, duplicate calendar evidence, unsupported attachments, and multiple obligations yield truthful evidence/completeness states.
5. Duplicate transport delivery, crash after capture, timeout after interpretation, crash during publication, repeated forward, and member-edit races yield stable decisions without lost intake or reopened dismissed work.
6. Retirement removes only eligible untouched automatic candidates. Edited/snoozed/managed/approved/canonical/manual/direct-intake work survives; projection rebuild and rollback obey the same intake policy.
7. Each advertised destination passes its exact operation/capability/approval/observation/outcome test. No observation-mode test causes a live provider effect, and ambiguous provider failure cannot cause duplicate creation.
8. All six product functions, thirteen required projection capabilities, five adapter categories, six pack kinds, five profiles, four personas, and workbook round trips remain represented and tested. This proposal does not declare any existing gap complete.

Extend the existing direct-intake, Triage vertical-slice, source-assessment recovery/publication, currentness, delegated execution, provider reconciliation, and bridge acceptance tests. Source inspection is not a substitute for these new end-to-end proofs.

## Governing changes

- **SUPERSEDE:** automatic obligation discovery from personal communication streams as the normal Triage admission source, including historical/recovery paths. Amend the relevant portions of CO-011/012/013/017/018/021 and their acceptance requirements rather than leaving contradictory auto-mining mandates active.
- **MODERNIZE:** CO-023 direct intake from a bounded pending cue into verified submitted evidence and durable extraction; CO-022/UX Triage presentation; source assurance and replay/publication contracts; service-owned messaging support and exact output reconciliation.
- **PRESERVE:** ADR-0001 OpenClaw ownership and model seam; ADR-0008 deterministic context; ADR-0010 conservative passive-source handling; event truth, scope/privacy, all required product/domain capabilities, governance, observation, provider mirrors, and bootstrap/backup safety.
- **DEFER:** no required part of the requested software workflow. Live enrollment, permissions, deployment, and write enablement are separate authorization boundaries, not substitutes for missing implementation.

This is a design proposal. No production behavior, source configuration, queue item, credential, provider account, or service was changed by this assessment.
