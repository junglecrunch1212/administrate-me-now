# ADR-0003: Notes is authorized family recall, not a second brain SaaS

Status: **ACCEPTED**

Date: 2026-08-06

Scope: CO-002

## Context

Household planning signals frequently begin in personal notes, shared Apple
Notes, meeting transcripts, provider summaries, and Markdown vaults. A family
member with ADHD benefits from writing information once and recalling it
without remembering its source application. The draft second-brain proposal
suggested Notion as a central shared hub and an external agent/vector stack.
That is usable as a standalone productivity design, but it conflicts with
AdministrateMe's local-first encrypted event source, per-member privacy,
domain-type separation, OpenClaw model boundary, deterministic authority, and
single-runtime topology.

## Decision

AdministrateMe is the coordination and authorization hub. Source applications
remain authoritative for note content. The event log stores typed observations;
`member_knowledge` projects authorized note state; deterministic FTS and the
scope-filtered derived vector projection support recall. Neither Notion nor
Obsidian is required or canonical. Either can be a capability-negotiated Cat-D
source or export target later.

Notes is owner-private by default. Provider-native shares do not cross the
AdministrateMe boundary. An owner may explicitly grant one non-privileged note
to one enrolled adult for read/search, with expiry and independent revocation.
Extraction remains owner-private; a grantee cannot use the reference for model
processing or create owner/grantee Triage candidates. View-as and operator
privilege do not substitute for a grant.

An LLM may perform only bounded natural-language extraction through pinned
OpenClaw `llm-task`. It cannot search an unauthorized corpus, decide attention,
infer permission, select a member/provider/container, create a domain object,
or write a provider. Exact evidence and deterministic validation produce only
Triage proposals. Changed, deleted, or privileged source versions revoke
derived indexes and supersede their pending candidates. Deletion or privileged
reclassification also durably invalidates active note grants; restoring a
source later requires a new explicit owner grant.

## Consequences

- The member label and canonical route become Notes and `/member/notes`.
- `member_knowledge` remains the constitutional capability name for code and
  replay compatibility; old Knowledge HTTP/OpenClaw routes remain deprecated
  aliases, not navigation.
- The note envelope must be provider-neutral and loss-aware.
- Search must authorize owned and expressly shared material at query time.
- Meeting transcripts remain notes/artifacts until Triage confirms a distinct
  Task, Calendar event, Reminder, List, List item, or Recurrence.
- Optional provider adapters may ship incrementally without changing the
  central model or fabricating parity.

## Rejected alternatives

1. **Notion as the central family database.** Rejected because it creates a
   second authority, expands cloud disclosure, and collapses typed domain facts
   into app databases.
2. **One shared Obsidian vault.** Rejected as the authorization boundary; vault
   sync and folder conventions are not member/domain access control. Obsidian
   remains a good optional source and durable Markdown export.
3. **Autonomous whole-corpus RAG agent.** Rejected because model retrieval and
   decisions would own scope and routing. Deterministic authorization plus
   bounded single-note extraction is retained.
4. **Provider share equals AdministrateMe share.** Rejected because provider
   permissions are heterogeneous, difficult to revoke coherently, and cannot
   authorize other household domains.
