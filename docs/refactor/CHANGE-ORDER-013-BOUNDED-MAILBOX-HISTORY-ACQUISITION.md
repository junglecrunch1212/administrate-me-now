# CO-013 — Bounded, durable mailbox history acquisition

Status: **IMPLEMENTED IN REVIEWED CANDIDATE; LIVE EVIDENCE PENDING**

Date: 2026-08-10

Affected architecture: Gmail Cat-A source acquisition, durable adapter cursors,
Comms source completeness, downstream contextual reasoning, Triage source
assurance, bootstrap/upgrade evidence, and member trust.

## 1. Failure and objective

The live Gmail adapter's first poll recorded the provider's current
`historyId` and intentionally emitted no pre-existing messages. Incremental
`users.history.list` therefore observed only changes after installation. The
runtime, pipeline, source-assurance proof, and Triage projection could all be
healthy while an existing mailbox conversation had never entered the event
log. Better extraction cannot identify an obligation from a source fact that
does not exist.

CO-013 closes that acquisition gap without treating a provider mailbox or a
model as household truth:

1. On first use of each acquisition policy, snapshot the current Gmail history
   cursor before listing prior messages. This cursor is the floor for the next
   incremental poll, so messages arriving during acquisition remain observable.
2. List a bounded recent window with `users.messages.list`, excluding drafts
   and spam/trash. Defaults are 730 days and 1,000 messages; explicit integers
   may narrow, disable, or increase the bounds only within 0–3,650 days and
   0–5,000 messages.
3. Fetch and append each listed message through the existing account-bound,
   direction-preserving `messaging.received@v3` / `messaging.sent@v2` path.
   Existing content fingerprints and native message IDs provide per-message
   replay deduplication and quarantine semantics.
4. Record a content-free durable completion cursor keyed by an irreversible
   digest of the adapter ID and acquisition policy. A restart with the same
   policy does not relist or re-emit history. A reviewed policy change creates
   a distinct bounded acquisition obligation rather than silently mutating the
   old receipt.
5. Expose only counts, bounds, completion state, and truncation in adapter
   status. No subject, sender, body, query result, credential, or household
   identifier enters operational evidence.

The backfill emits source observations only. It does not invoke a provider
write, confirm a candidate, or create a Task, Commitment, Calendar event,
Reminder, List item, Recurrence, Party, or fact. Contextual interpretation and
the human gate remain downstream under CO-012.

## 2. Deterministic ownership

| Concern | Fixed code | Pinned language seam | Member/operator |
|---|---|---|---|
| Provider cursor snapshot | Owns ordering and durable watermark | No role | May inspect content-free health |
| History query and cap | Owns validated bounds, exclusions, paging, and truncation | No role | May choose a narrower reviewed policy |
| Message identity/direction | Existing native ID, label, account, workspace, owner, scope, and fingerprint rules | No role | Can inspect the exact authorized source in Comms |
| Candidate meaning | Admits only grounded typed output under existing rules | Interprets only appended authorized facts | Reviews each proposal in Triage |
| Canonical/provider effect | Existing confirmation, allowlist, governance, approval, privacy, and observation controls | No authority | Explicit later authorization remains required |

## 3. Durability and rollback

The ordinary adapter cursor and the policy completion cursor contain metadata
only. Source observations remain immutable in the encrypted event log. The
policy marker is written only after every discovered message is either
accepted/deduplicated or explicitly quarantined; transient listing or fetch
failure leaves the acquisition incomplete and retryable. Rollback may ignore
the additive marker and source observations, but must never delete either.

The acquisition is finite. It stops at the configured message cap, after 100
provider pages, or on a repeated page token. A response that advertises more
history at any bound records `truncated: true`; it does not silently claim
whole-mailbox coverage. Continuous changes remain owned by Gmail history polling.
Pub/Sub is still an optional latency improvement, not a completeness
substitute.

## 4. Acceptance boundary

AC-38 requires:

1. adapter-level proof of cursor-before-list ordering, paging, exact query,
   hard caps, validation, normal typed emission, and content-free status;
2. production-supervisor proof that two pre-existing messages are emitted on
   first composition and never relisted after a fresh adapter instance reuses
   the durable cursor store;
3. preservation of existing Gmail direction, scope, quarantine, incremental
   cursor, and message-level dedupe tests;
4. full release gates, reviewed merge, guarded observation deployment, source
   settlement, Doctor, and authenticated Comms/Triage proof;
5. live proof that a previously absent multi-message obligation is now present
   as source context and produces separate human-gated candidates without a
   canonical or provider effect.

Repository fixtures use fictional identities and `.test` addresses. Live
household content may be inspected only through authorized private surfaces and
must not enter commits, logs, screenshots, or release evidence.

The macOS rehearsal path filter includes adapter packs and the Gmail supervisor
composition test. An adapter-only acquisition change therefore cannot evade the
Apple-silicon bootstrap/recovery gate merely because it does not touch central
runtime files.
