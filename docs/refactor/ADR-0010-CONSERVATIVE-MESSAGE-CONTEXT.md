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

Participant, address, subject, household/business-name, and filename overlap
may explain an already-admitted edge but never admit one. The language model
receives the bounded set and cannot add sources.

One `authored-message-text-v2` function normalizes line endings, removes
recognized plain-text or HTML reply/forward containers including a boundary at
offset zero, and supplies fallback extraction, pinned-skill input, exact-quote
validation, and displayed evidence.

Member corrections append exact relationship-rejected or source-text-corrected
events. Explicit relationship-confirmed and source-text-reinstated events
reverse them. The immutable provider facts remain unchanged.

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
