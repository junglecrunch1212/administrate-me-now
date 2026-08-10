# CO-012 — Privacy-scoped message context and explainable multi-candidate Triage

Status: **IMPLEMENTED IN REVIEWED CANDIDATE; RELEASE EVIDENCE PENDING**

Date: 2026-08-10

Affected architecture: Cat-A message facts, the encrypted append-only log,
`noise_filtering`, the pinned OpenClaw language seam, Triage events/projection,
member Triage UX, replay/supersession, privacy, and release evidence.

## 1. Failure and engineered objective

A multi-participant legal-authorization conversation demonstrated that
single-message extraction is not an adequate event-sourcing unit. One matter
can contain several documents to prepare, records to return, a member promise
to transfer fees, questions requiring a decision, execution/collection steps,
and related signing-service notices. Treating each body independently loses
pronouns, ownership, completion state, and related evidence; merging same-kind
items by body-local character offsets can collapse distinct work.

CO-012 replaces that behavior with one testable goal:

1. Assemble a bounded context pack from immutable source facts under the exact
   tenant, owner, visibility, sensitivity, provider-account, and workspace
   boundary of the triggering message.
2. Relate messages only through code-owned provider thread IDs, participant
   overlap, normalized subject tokens, or attachment filename tokens. Strip
   duplicated quoted history and assign stable aliases.
3. Let the pinned OpenClaw `llm-task` seam interpret language across the pack
   and return zero or more distinct typed candidates, each with an exact source
   alias/quote, apparent action owner, linguistic lifecycle state, compact and
   expanded explanation, next step, unresolved questions, and related aliases.
4. Admit candidates only in fixed code. Evidence must be an exact substring of
   a primary-thread source. Other-owned/completed/answered/superseded items and
   related-only evidence are rejected. Unknown ownership or lifecycle remains
   visibly human-gated rather than silently asserted.
5. Render compact cards that say what the decision is and expanded cards that
   separate “What this is,” “Why it may need you,” “Smallest next step,” thread
   state, unresolved questions, deterministic source edges, and literal source
   evidence. Generated explanation is always labelled review aid, never fact.
6. Reconcile a thread as a versioned candidate set. A later message may retire
   untouched older interpretations; any member enrichment or decision wins and
   is never silently reopened.

No candidate becomes a Task, Commitment, Calendar event, Reminder, List,
Recurrence, Party, fact, or provider write without the existing member gate.

## 2. Deterministic and language ownership

| Concern | Fixed code | Pinned language seam | Member |
|---|---|---|---|
| Source inclusion and scope | Owns exact boundary and bounded selection | Cannot add a source | Cannot widen privacy through Triage |
| Conversation edges | Owns thread/participant/subject/attachment rules and reason codes | May cite supplied related aliases only | Reviews displayed edge evidence |
| Quoted history | Removes deterministic reply-history boundaries | Interprets only retained authored portions | Can open exact source evidence |
| Ask/meaning interpretation | Validates schema, aliases, quotes, owner/status enum, kind and fields | Identifies linguistic asks, owner/status, summaries and unresolved questions | Corrects title/type/details/route |
| Candidate admission | Rejects ungrounded, other-owned, closed, related-only, disallowed or duplicate items | Has no admission/ranking authority | Confirms or dismisses each item |
| Thread lifecycle | Latest thread source owns candidate set; code supersedes untouched older set | Describes supplied linguistic state only | Prior enrichment/decision is permanent |
| Canonical/provider effect | Existing allowlist, governance, rate limit, privacy, observation and approval | None | Explicit final confirmation required |

Confidence remains display provenance and never controls eligibility. Message
content is untrusted prompt data: the skill prompt forbids following embedded
instructions, tool calls, scope widening, or non-JSON output.

## 3. Context contract

`message-context-pack-v1` contains at most 12 primary-thread messages, six
related messages, 2,400 authored characters per message, and 24,000 characters
overall. Stable `mNN` aliases carry direction, time, channel, sender,
recipients, subject, attachment names, and explicit relationship reason codes.
The pack contains no projection state and is derived only from the encrypted
append-only source log.

Exact scope equality is mandatory for tenant, owner, visibility, sensitivity,
provider account, and workspace. A candidate event whose payload member
conflicts with the current member is excluded. Privileged or cross-member data
cannot enter the pack. A single shared mailbox address cannot relate two
threads without a second subject/attachment signal or a second shared party.

`triage.item.proposed@v3` adds `triage-context-interpretation-v1`. It is a
private derived proposal, not household truth. It retains the context
fingerprint, thread key, source edge receipts, exact evidence refs, bounded
review explanation, owner/status signals, and unresolved questions. Triage
projection v6 adds only nullable `interpretation_json`; old events replay with
`NULL` and preserve prior behavior.

## 4. Candidate-set lifecycle and replay

- Older messages in a retained provider thread receive an explicit terminal
  `deferred_to_latest_thread_message` assessment. Classification still occurs;
  duplicate candidate work does not.
- The latest message evaluates the primary thread with related context. A
  model result may contain up to 20 distinct candidates.
- Candidate identity includes thread, target kind, and exact quote, not a
  body-local offset. Same-kind asks in different messages remain separate.
- A new anchor supersedes only untouched older proposals from that primary
  thread with `eligibility_rule_replaced`. Enriched, confirmed, materialized,
  or dismissed proposals remain immutable member decisions and are not
  reopened.
- Replay generation 12 and context/content fingerprints reuse exact prior
  results and deduplicate provider-observation duplicates. No source fact is
  rewritten and rollback can ignore the additive v3 field/column.

## 5. Acceptance boundary

AC-37 requires:

1. a fictitious six-message, multi-participant, bilingual-capable legal matter
   plus a related third-party signing notice;
2. at least four distinct open candidates across Task and outbound Commitment,
   with other-owned and completed items rejected;
3. exact source aliases/quotes and deterministic relationship reason codes;
4. quoted-history deduplication, character bounds, prompt-injection defense,
   cross-member/workspace/sensitivity exclusion, replay idempotency, and
   untouched-candidate supersession with member-decision preservation;
5. process proof through the production composition root and the real Triage
   API/projection, with compact/expanded UI review and no canonical or external
   effect;
6. full release gates, reviewed merge, guarded observation deployment, replay
   settlement, Doctor, and authenticated live browser proof before this change
   is called deployed.

Household source content is evaluation evidence only. Repository fixtures use
fictional identities and `.invalid` addresses.
