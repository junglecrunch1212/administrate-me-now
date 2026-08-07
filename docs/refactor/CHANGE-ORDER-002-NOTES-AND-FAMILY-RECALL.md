# Change Order 002 — Notes and family recall

Status: **APPROVED; RELEASE CANDIDATE VERIFIED; LIVE DEPLOYMENT EVIDENCE PENDING**

Approved: 2026-08-06

Governing decisions: ADR-0000, ADR-0001, ADR-0003, D17, D26, CO-001

## Outcome

The member destination formerly labelled **Knowledge** is now **Notes**. Notes
is the private recall and source-evidence lens for member-owned notes, meeting
notes, transcripts, and provider summaries. It reduces working-memory load by
letting a member search authorized material without remembering which note app
captured it. It may propose evidence-backed Tasks, Calendar events, Reminders,
Lists, List items, or Recurrences to Triage. It never promotes or writes one
directly.

AdministrateMe is the family coordination hub. Notion, Obsidian, Apple Notes,
Evernote, Otter, Fathom, Zoom, Markdown folders, and later providers remain
source applications with independently negotiated capabilities. They are not
a second household truth store and no provider's share setting is treated as
AdministrateMe authorization.

## Constitutional classification

| Decision | Class | Result |
|---|---|---|
| Rename the adult surface from Knowledge to Notes | MODERNIZE | `/member/notes` and `administrateme.dashboard.notes` are canonical; old Knowledge routes are non-navigational compatibility aliases. |
| Keep Cat-D source applications authoritative for their note records | PRESERVE | Provider identity, account, folder, revision, timestamps, content hash, source URL, attachments, warnings, and correction provenance survive normalization. |
| Use Notion or Obsidian as the family system of record | SUPERSEDE | AdministrateMe's encrypted event log and typed projections remain canonical. Obsidian/Notion can be optional member sources or export targets only. |
| Permit authorized family recall | MODERNIZE | One owner may explicitly grant one enrolled adult `read` or `search`; search implies read. Shared-reference model extraction remains fail-closed. |
| Treat a provider-native share as household authorization | SUPERSEDE | Provider sharing and AdministrateMe grants are separate and independently revocable. |
| Restore quick capture or direct note-to-domain writes | SUPERSEDE | Capture mutations remain retired. Notes extraction creates only Triage candidates with exact source evidence. |
| Let an LLM mine the whole household corpus or choose scope/routing | SUPERSEDE | One bounded `llm-task` extracts schema-shaped language features from one authorized note. Deterministic code validates evidence, confidence, types, fields, member scope, and route. |

## Source capability model

The normalized note contract supports `apple_notes`, `apple_voice_memos`,
`obsidian`, `markdown`, `evernote`, `otter`, `fathom`, `zoom`, `notion`, and a
credential-free fixture. Support in the schema is not a claim that an adapter
is configured or live.

| Source class | Expected authority | Required truthful limits |
|---|---|---|
| Apple Notes | Member bridge mirror; provider remains authoritative | Local macOS Automation consent, account/folder identity, locked-note and attachment limitations, no central server API. |
| Evernote / Notion | OAuth/API mirror when a reviewed adapter exists | Exact account/workspace, API scopes, rate limits, rich-content loss, and webhook/poll freshness. |
| Obsidian / Markdown | Member-owned filesystem bridge | Vault/root allowlist, path identity, file hash/revision, symlink and traversal denial, optional adult-only enablement. |
| Otter / Fathom / Zoom | Meeting artifact/summary/transcript source | Artifact kind, participants when factual, source URL, generated-summary labeling, transcript availability, retention, and webhook/export limits. |
| Voice Memos / recordings | Sensitive member bridge source | Explicit member enablement, microphone/files consent, sensitivity default, transcript provenance, and no fabricated speaker identity. |

Adapters emit typed note observations. The event log remains append-only; the
`member_knowledge` projection is rebuildable; authorized FTS is deterministic;
`vector_search` is a derived index and never a source of truth.

## Privacy and authorization

- Notes is available only to an authenticated adult viewing themself. Principal
  view-as, child, device, ambient, generic OpenClaw memory, and operator scope
  do not grant note access.
- Owner-private is the default. Privileged notes remain owner-only and are
  excluded from full-text/vector indexes and all model processing.
- A grant identifies one note, owner, enrolled adult grantee, permission set,
  timestamp, optional expiry, and audit event. Revocation is event-sourced and
  must make the reference inaccessible without changing provider permissions.
- A shared reference does not copy or re-own the source. It retains the
  original owner's provenance and cannot be re-shared by the grantee.
- Search authorization is evaluated at query time. Removing or reclassifying a
  note revokes derived indexes; deleted or changed notes supersede pending
  candidates from the prior source version.
- Deletion or privileged reclassification also invalidates every active note
  grant in the rebuildable projection. Reusing an identifier or later returning
  the note to normal sensitivity cannot silently reactivate an old grant.

## Deterministic extraction contract

`skill:extract_note_semantics` is the only CO-002 language-extraction seam. It
receives one bounded note through pinned OpenClaw `llm-task`, treats note text
as untrusted data, and may return at most ten candidates of these distinct
types: Task, Calendar event, Reminder, List, List item, or Recurrence.

Deterministic code rejects a candidate unless:

1. the source is private to the same member;
2. the note is non-privileged and the current source version is eligible;
3. the target kind and every candidate field are allowlisted;
4. confidence is at least `0.72`;
5. a non-empty evidence quote appears exactly in the source body; and
6. the only initial route is the owning member.

The result is `triage.item.proposed`, never a domain record or provider command.
Triage enrichment resolves exact target type, member, provider, account, and
container. One human confirmation still precedes canonical materialization and
the independently governed provider-write path. Recurrence remains distinct
from a Task, Calendar event, Reminder, List item, and OpenClaw scheduler job.

## Member experience

Notes provides:

- source rail with exact provider/account state and visible capability limits;
- All, Recent, Shared, Meetings, and Needs Triage lenses;
- deterministic authorized full-text search over title, body, folders, and
  tags, with an optional source filter;
- note master/detail reading, source/folder/revision facts, tags, provenance,
  safe plain-text rendering, source link, and source-evidence dialog;
- explicit per-note adult read/search grants and
  independent revocation; and
- a deep link to exact Triage proposals without an inline approval shortcut.

The surface uses safe DOM text assignment, keyboard-native controls, responsive
master/detail collapse, honest empty/limited/freshness states, and an OpenClaw
chat drawer rather than a competing permanent rail on constrained widths.

## Cross-surface effects

Notes joins the executive-function loop as a new **Observe/Recall** lens. It
does not expand the set of action owners:

- Notes observes and recalls source evidence.
- Triage alone adjudicates extracted domain candidates.
- Calendar, Reminders, and their exact provider routes remain authoritative for
  calendar/reminder actions.
- Today may consume only confirmed projection state; a raw note or pending
  candidate cannot become the next action.
- Comms and Notes may link to the same meeting or interaction evidence without
  copying either source or merging their truth models.

## Acceptance and rollout

CO-002 is complete only when AC-29 proves the provider-neutral event path,
private surface, explicit grant/revoke, authorized search, privileged/deletion
index revocation, bounded source-evidence extraction, candidate supersession,
replay, exact OpenClaw binding, responsive UI, and compatibility alias. Live
deployment must then prove `/member/notes`, old-route redirect, asset coherence,
and central health on the reviewed Mac release while observation mode remains
enabled.

## Explicit non-goals

- No Notion, Obsidian, Pinecone, Chroma, LanceDB, Zapier, Make, n8n, provider
  SDK, or second model router becomes required platform infrastructure.
- No corpus-wide autonomous agent, provider-native permission inference,
  child-note sharing, principal view-as, automatic domain write, or silent
  provider mutation is introduced.
- CO-002 does not claim live Evernote, Otter, Fathom, Zoom, or Notion adapters;
  absent configuration remains truthfully not configured.
