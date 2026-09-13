# ADR-0010 — Conservative message context and exact correction

Status: **Accepted for candidate implementation**

Date: 2026-08-12

## Context

Provider thread IDs are useful source facts but can be reused for a later
subject. Participants, addresses, surnames, account names, subject tokens, and
attachment filenames recur naturally across unrelated household and business
work. Those observations are not relationship authority. Separately, message
ingestion time is not message occurrence time, and quoted history is not newly
authored evidence.

## Decision

AdministrateMe uses provider message time for message event `occurred_at` and
retains append time as `recorded_at`. A provider thread is segmented into
source-time episodes no larger than 30 elapsed days or 100 source events.

Cross-thread context is admitted only through:

- an exact RFC `Message-ID`/`References`/`In-Reply-To` edge;
- an exact attachment digest or provider attachment identity; or
- one explicit member-confirmed exact source pair.

The context contract records the exact primary source event(s) that supplied
each strong edge. A rejection targets those pairs, not whichever message later
became the provider-thread anchor. `provider_attachment_id` from Gmail is
message-local and is therefore not a provider attachment identity. Until the
Gmail adapter can supply a content digest or genuinely stable file identity,
attachment-based cross-thread admission is unavailable for Gmail; filenames
remain corroboration only.

Participant, address, subject, household/business-name, and filename overlap
may explain an already-admitted edge but never admit one. The language model
receives the bounded set and cannot add sources.

**PRESERVE relationship authority; MODERNIZE exact completion handling:** a later
primary-thread message may supply a grounded completion interpretation for an
exact action quoted from an already admitted related source. Code matches the
original source event, target kind and original action quote before retiring
only an untouched system proposal. The completion must be later in source time
and cite the primary source that supplies the admitted relationship edge.
Missing, foreign, unreliable or unlinked evidence has no effect. This cannot
create related-only work, retire sibling actions, change a member decision,
complete a canonical object, broaden acquisition, or request global replay.
Immutable interpretation receipts and compare-and-apply supersession remain the
audit trail. Original relationship restrictions above continue to govern.

One `authored-message-text-v2` function normalizes line endings, removes
recognized plain-text or HTML reply/forward containers including a boundary at
offset zero, and supplies fallback extraction, pinned-skill input, exact-quote
validation, and displayed evidence.
An HTML-only body is canonicalized before the pipeline checks for empty text.
**MODERNIZE HTML canonicalization; PRESERVE exact evidence and authored scope:**
HTML content in either MIME body field is reduced before applying the message
character limit. Non-content head, title, style, script and template blocks, and comments,
cannot consume the source-text budget or supply candidate quotes. Body-level
noscript fallback remains source text.
Plain text with literal angle brackets remains plain text. The normalized text
is shared by model input, candidate grounding and displayed evidence; a raw
provider payload is never rewritten. Existing quote-container restrictions
still apply before extraction. This correction does not change a consumer
version, clear checkpoints, renew model attempts or request historical replay.
Previously committed assessments remain evidence of the input actually used;
synthetic fixed-response process proofs establish input and evidence mechanics,
not actual-model accuracy or repair of earlier missed obligations.
When a provider supplies both plain and HTML MIME alternatives, an explicit
HTML reply/forward boundary constrains the plain alternative; only an agreeing
authored prefix is usable, and disagreement is unreliable rather than evidence.
Retained pre-v4 Gmail Sent facts lack that HTML provenance entirely. They
remain immutable source facts, but their bodies are marked
`legacy_gmail_sent_quote_boundary_unavailable` and excluded from authored
evidence during v21 replay and later context assembly. Replay appends an
eligibility-replacement receipt for each untouched system proposal previously
rooted in that unreliable body; member-enriched or decided proposals remain
permanent. `triage.item.superseded@v3` binds that system receipt to the exact
proposal `last_event_id` inspected by replay, and the deterministic Triage
projection ignores it if a member revision won the append order.
An untouched context-free deterministic fallback is not a stale
interpretation merely because the replay generation changed. When v21 derives
the exact same source, target kind, candidate key, title, and current
high-signal fallback rule, it retains the existing pending proposal and records
the new assessment without superseding or duplicating the review item.
If that same deterministic core is attached to an older contextual
interpretation whose fingerprint is stale, replay retires the contextualized
proposal but does not treat its historical candidate key as active. The
current deterministic candidate receives a distinct, replay-versioned proposal
identity, so the stale context leaves the queue while the still-valid ask
remains available for human review. Projection applies that replacement only
after the revision-bound supersession actually retired the predecessor. If a
member revision won the append order, its enriched, dismissed, confirmed, or
later terminal state remains authoritative and the replacement event cannot
create duplicate or reopened review work beside it.
An unrecognized quote-header/container signal produces an explicit unreliable
result and a terminal no-candidate assessment instead of language inference.

Member corrections append exact relationship-rejected or source-text-corrected
events. A source-text correction is bound to the proposal/candidate, authored
text digest, exact evidence digest, and immutable character span; it removes
only that evidence span, not every other valid ask in the source. Explicit
relationship-confirmed and source-text-reinstated events reverse the exact
decision. The immutable provider facts remain unchanged.
Triage exposes a correction control only when the read-side server declares
that the exact relationship provenance or uniquely grounded authored-text
evidence required by the command is present. Relationship-edge sources are
authorized and hydrated even when they fall outside the displayed model window.

The bounded model payload always retains both the source currently being
assessed and the latest episode anchor, reserving character budget for the
anchor before older bodies. Candidate reconciliation uses the complete bounded
episode identity (up to 100 sources), not only the 12 messages sent to the
language seam.

Missing, invalid, non-positive, or out-of-range Gmail `internalDate` values are
terminally quarantined. They never fall back to ingestion wall-clock time.

## Rejected alternatives

- Participant count, shared mailbox, exact email mention, surname, subject, or
  filename overlap alone. These are common and create high-confidence-looking
  false associations.
- Model semantic similarity or model-selected context. It is not deterministic
  authority and cannot safely enforce scope.
- Mutating/deleting the provider source or Triage history. This breaks replay,
  provenance, and correction auditability.
- Sender/account-wide suppression after one wrong-context dismissal. A wrong
  relationship says nothing general about the sender or account.
- Treating ingestion time as the source date. It produces false chronology and
  incorrect matter episodes during backfill or replay.

## Consequences

Some real relationships will remain split until exact provider continuity or a
member confirmation exists. That is the intended false-negative bias. New
event/schema versions and two additive Context Fabric decision tables increase
replay work slightly. The benefit is deterministic chronology, explainable
strong edges, exact reversible corrections, and no name-based suppression or
model-owned context authority.
