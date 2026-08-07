# Change Order 003 — People and relationship memory

Status: **APPROVED — IMPLEMENTED; AC-30 PASS; OBSERVATION DEPLOYMENT PENDING**

Proposed: 2026-08-06

Approved: 2026-08-06

Governing decisions: ADR-0000, ADR-0001, D17, D26, D28, D29, CO-001,
CO-002

Governing decision: ADR-0004 — Party, endpoint, provider-record, and
member-relationship boundaries

## Outcome

**People** becomes AdministrateMe's deterministic relationship-memory surface.
It helps an adult remember who someone is, why the relationship matters, what
has happened, what is owed, and what the member explicitly wanted to remember
or do next. It is not a sales CRM, a copy of one address book, a list of every
message sender, a social graph inferred by a model, or another communications
inbox.

The surface composes authorized Parties, contact-source records, explicit
relationships, Interactions, Commitments, Calendar participation, Reminders,
Notes evidence, and Artifacts without collapsing those domains. Provider
contacts remain provider-authoritative. AdministrateMe owns household identity,
member-specific relationship context, authorization, corrections, provenance,
and deterministic relationship cues.

The product name remains **People**. “CRM” remains an architectural descriptor,
not member-facing language.

## Why the current implementation must be replaced

The live and code audits found the following root causes:

| Failure | Current cause | Required correction |
|---|---|---|
| Retailers, newsletters, and automated senders appear as people | `identity_resolution` mints every unresolved inbound identifier below its merge threshold as `kind="person"` | Preserve an unresolved Endpoint; promote only under deterministic, reviewable rules |
| Rows show raw sender strings and an unexplained “Ready” state | People uses the generic card renderer over uncomposed projection rows | Add a People-specific composition contract and purpose-built workspace |
| “Recent contact” renders as repeated “Household item” | Generic rendering receives Interactions without a member-facing label or linked Party context | Compose authorized interaction direction, channel, participant, and time |
| No useful profile experience | A detail API exists, but the People surface never calls it | Build searchable master/detail People UI with overview, timeline, open loops, details, and provenance |
| Identity corrections are incomplete | `party.merged` is registered but not consumed; update, split, archive, identifier revocation, and relationship ending are absent | Implement reversible event-sourced lifecycle and replay behavior |
| Member isolation is too coarse | Structural identifiers and relationships default to shared/normal, and identity candidates are loaded tenant-wide | Give every record/fact/link an explicit scope and filter before lookup or aggregation |
| Contact providers are not actually connected | The bridge contract names `contacts`, but no Apple Contacts, Google People, Microsoft Graph Contacts, or CardDAV adapter exists | Add capability-negotiated contact adapters without claiming absent configuration |
| Relationship intelligence is mostly prompt-era prose | Closeness, summaries, graph mining, birthdays, and hosting cues are either absent or weakly shaped | Replace moralized/model-decided scoring with explicit policy and deterministic measures |
| Mobile layout overflows | Raw identifiers have no wrapping boundary, and People does not use the responsive workspace/chat rules | Add a 390px-safe single-column mode and bounded text/actions |
| Acceptance proves only route presence | Existing browser checks prove the title/member context, not a closed People loop | Add AC-30 process, privacy, replay, provider, cross-surface, and visual proof |

The implementation must not erase the append-only log or hard-code tenant data.
Legacy pipeline-created Parties are retained as evidence, deterministically
qualified as unconfirmed endpoints, and removed from the main People directory
unless later evidence or a member decision promotes them.

## Constitutional classification

| Decision | Class | Result |
|---|---|---|
| People is relationship memory, not a sender database | MODERNIZE | Only relationship-bearing, authorized Parties appear in the primary directory |
| Separate Endpoint, provider Contact Record, and canonical Party | MODERNIZE | Observing an address never asserts a human identity; provider records keep their native authority |
| Keep Interactions, Parties, Commitments, Notes, Reminders, Calendar events, and Artifacts distinct | PRESERVE | People composes links; it does not become a universal work or content object |
| Treat all inbound identifiers as people | SUPERSEDE | Unknown, automated, marketing, transactional, and service endpoints remain classified endpoints until promoted |
| Use provider sharing as AdministrateMe authorization | SUPERSEDE | Provider permissions and AdministrateMe grants are independent and revocable |
| Give every member one household-wide relationship score | SUPERSEDE | Relationship context, cadence, suppression, and cues are member-specific; counts are descriptive, never moral scores |
| Let an LLM merge identities, infer relationships, rank people, or choose outreach | SUPERSEDE | Deterministic code owns eligibility and routing; bounded language extraction can create only evidence-backed Triage candidates |
| Use generic OpenClaw memory as CRM truth | SUPERSEDE | OpenClaw retains channel/session/model responsibilities; AdministrateMe owns typed People truth |
| Add one OpenClaw agent per member, account, or contact | SUPERSEDE | One pinned Gateway uses member-bound sessions and exact provider-account routes; member isolation is enforced in AdministrateMe |

## Semantic boundaries

1. **Endpoint** — an observed addressable identifier such as an email address,
   phone number, iMessage handle, WhatsApp peer, or provider directory id. It
   may be personal, organizational, automated, service, or unresolved. An
   Endpoint is not evidence that a Party exists.
2. **Provider Contact Record** — one exact record in one member/provider/account/
   container with provider id, revision, groups, native fields, tombstone, and
   capability state. The provider remains authoritative for mirrored fields.
3. **Party** — a stable person, organization, or household identity. A Party is
   created by explicit member entry, an eligible curated provider contact, a
   household enrollment fact, or an approved Triage candidate.
4. **Identifier Link** — a scoped, provenance-bearing link between an Endpoint
   and a Party. Shared mailboxes, family phones, aliases, and organizational
   endpoints may have non-exclusive or role-qualified links; the current
   tenant-wide one-identifier/one-party assumption is removed.
5. **Membership** — a Party's explicit role in a household or organization.
   Household membership is an authorization fact; an external relationship is
   not membership and never grants a portal identity.
6. **Relationship** — a typed, directional or mutual, time-bounded connection
   between Parties with source, scope, sensitivity, and correction history.
7. **Member Relationship Profile** — one adult's private, explicit context for a
   Party: circle, desired cadence, favorite, suppression/no-contact state,
   personal tags, and cue budget. It is not shared merely because the Party is.
8. **Relationship Fact** — a typed fact such as birthday, employer, role,
   preference, important date, address, or contextual detail, carrying exact
   provenance, scope, sensitivity, validity, and correction state.
9. **Interaction** — an independently scoped communication or touchpoint. It is
   linked to a Party only through authorized Identifier Links and never copied
   into the Party record.

## Deterministic identity and promotion rules

- An existing provider record always reconciles by its provider-native record
  id and account/container coordinates before any identity comparison.
- An exact verified identifier may link automatically only within the current
  member's allowed scopes and only when it resolves unambiguously.
- An eligible curated contact record may create a Party. Google “Other
  Contacts,” provider directories, marketing senders, and message-history
  participants are not curated contacts and cannot take this path.
- Unknown communication and calendar participants create or update Endpoints,
  not Parties.
- Deterministic two-way/repeated-contact thresholds may raise a “save or link
  this person?” Triage candidate. They never promote automatically.
- Name similarity, model output, shared domains, contact photos, and graph
  proximity can suggest review but can never merge or create canonical truth.
- Merge, split, relink, archive, restore, identifier verify/revoke, and
  relationship end/correct are explicit, idempotent events. Destructive
  provider deletion is a separate governed action.
- Source updates and deletions supersede pending candidates derived from an old
  source version.

## Event, projection, and API scope

CO-003 introduces or completes typed lifecycle contracts for:

- provider contact and group observations, updates, tombstones, source
  revisions, and reconciliation outcomes;
- Party create/update/archive/restore/merge/split and reversible corrections;
- Endpoint observation and deterministic endpoint classification;
- Identifier link/add/verify/revoke/primary/relink lifecycle;
- membership add/update/end and relationship add/update/end;
- member relationship profile set/correct/suppress and explicit cue policy;
- typed relationship fact propose/confirm/correct/revoke;
- explicit People grants and revocations where a private Party or field group
  is shared with another enrolled adult; and
- existing guarded external-sync requested/completed/failed/reconciled events
  for provider contact writes.

The `parties` projection advances to a replayable v2 read model containing
scoped Parties, Endpoints, provider records, source links, identifiers,
memberships, relationships, member profiles, typed facts, grants, provider
groups, deterministic authorized FTS, interaction measures, and cue state.
Structural rows no longer acquire implicit `shared:household` visibility.

Every query must apply scope in SQL before identifier resolution, search,
counts, cue computation, or timeline aggregation. Hidden records must not leak
through duplicate suggestions, counts, search results, source health, birthday
cues, or response-time measures.

The member API gains People-specific list/search/detail/source/cue contracts and
guarded commands. Read contracts are shared by the Python portal and pinned
`administrateme.dashboard.people` OpenClaw binding. Mutations always use
`authMember`; `viewMember` never authorizes a write.

Triage gains distinct target kinds for `party_candidate`, `party_identifier`,
`party_relationship`, and `relationship_fact`. These are not Tasks,
Commitments, Reminders, Calendar events, Lists, or Recurrences.

## Contact-source capability model

Schema support is not a claim that a source is configured or healthy. Every
source reports read/write/delete/group/photo/note/incremental-sync capability,
account/container identity, permission state, freshness, and degraded reason.

| Source | CO-003 contract | Truthful constraints |
|---|---|---|
| Apple Contacts | Per-member bridge using `CNContactStore`, containers/groups, `CNChangeHistoryFetchRequest`, and `CNSaveRequest` | Runs only where the member granted Contacts access; notes require Apple's separate entitlement; unified contacts and backing containers must not be conflated; change history can require a full rebuild |
| Google People | Per-account Cat-B mirror of `people.connections`, contact groups, sync tokens, field masks, source etags, and sequential mutations | “Other Contacts” and Workspace directory people remain separate sources; sync tokens expire; writes require current source etags and replace named field masks |
| Microsoft Graph Contacts | Per-account contact/contact-folder mirror with delta tokens and `Contacts.Read`/`Contacts.ReadWrite` capability | Mailbox contacts, shared folders, and organizational directory contacts are distinct; least-privilege delegated access is the default |
| CardDAV | Account/address-book mirror using vCard, ETags, discovery, query/multiget, and advertised methods | Servers vary; address-book privacy/ACL and capability negotiation remain visible; no assumption of create/delete/group support |
| Fixture | Credential-free, loss-aware full loop | Test-only; never presented as a connected provider |

Apple's Contacts framework supports authorized fetch/save, containers/groups,
change notifications, and change-history tokens: [CNContactStore](https://developer.apple.com/documentation/contacts/cncontactstore),
[contact-store authorization](https://developer.apple.com/documentation/contacts/accessing-the-contact-store), and
[CNChangeHistoryFetchRequest](https://developer.apple.com/documentation/contacts/cnchangehistoryfetchrequest).
Google documents incremental `people.connections` sync, expiring sync tokens,
source etags/field-mask writes, and contact-group mutation:
[connections.list](https://developers.google.com/people/api/rest/v1/people.connections/list),
[updateContact](https://developers.google.com/people/api/rest/v1/people/updateContact), and
[contactGroups.members.modify](https://developers.google.com/people/api/rest/v1/contactGroups.members/modify).
Microsoft documents contact folders, least-privilege scopes, update semantics,
and delta support: [list contacts](https://learn.microsoft.com/en-us/graph/api/user-list-contacts?view=graph-rest-1.0),
[update contact](https://learn.microsoft.com/en-us/graph/api/contact-update?view=graph-rest-1.0), and
[contact resource](https://learn.microsoft.com/en-us/graph/api/resources/contact?view=graph-rest-1.0).
CardDAV defines provider-authoritative address-book collections, vCard records,
ETag-aware synchronization, reports, and ACL behavior in
[RFC 6352](https://www.rfc-editor.org/rfc/rfc6352.html).

The release implementation includes reviewed adapter packs for all four source
families. Live activation requires only the sources actually enrolled by the
household; absent Microsoft or CardDAV credentials remain disabled and do not
block Apple/Google release evidence.

## Provider field and write behavior

The provider-neutral snapshot preserves, without pretending parity:

- structured and formatted names, nicknames, phonetics, pronouns where
  supported, organization/title/department, relations, dates, birthdays,
  addresses, phones, emails, URLs, messaging handles, labels, groups,
  containers/folders, photos by encrypted artifact reference, and provider
  notes when separately authorized;
- provider/account/container/external record id, source kind, revision/etag,
  update time, source URL, tombstone, native labels, and capability warnings;
- field-level source and primary/preferred metadata so a provider round trip
  does not overwrite unsupported or unrelated fields.

Provider contact fields and AdministrateMe relationship context remain
separate. An “Edit contact” command names an exact provider/account/container,
expected revision, field mask, preview, and capability. A “Remember privately”
command writes only typed AdministrateMe context. The UI never silently syncs
one into the other.

External create/update/delete/group/photo actions pass allowlist → governance →
rate limit, independent OpenClaw and domain approval where required, final
content/privacy guard, revision conflict check, and observation handling.
Requested, suppressed, completed, failed, conflicted, and reconciled are
distinct terminal evidence. Provider delete requires a second explicit review;
local archive is the default reversible action.

## Privacy and authorization

- People remains an adult surface. Child, device, ambient, operator, and
  generic OpenClaw memory access are denied server-side.
- Provider contact records, identifiers, timeline rows, member profiles, and
  relationship facts are owner-private by default. Work and personal accounts
  never union without an authorized combined view.
- Principal view-as may see only explicitly shared People material. It cannot
  reveal another adult's private contact source, identifiers, relationship
  notes, cadence, suppressed contacts, or interaction history.
- A provider-native share does not grant AdministrateMe read access. Sharing a
  Party uses an explicit adult grant and field-group permissions; the grantee
  cannot re-share the source owner's private record.
- Household membership and portal authorization are separate from external
  relationship labels. A contact, coparent, caregiver, vendor, or emergency
  person never becomes a household member or helper implicitly.
- Sensitive or privileged identifiers, addresses, health/legal context,
  relationship states, and provider notes retain field-level scope and may be
  excluded from FTS/model use. Privileged facts are owner-only.
- Suppressed, no-contact, estranged, memorialized, blocked, and inappropriate
  cue states prevent relationship nudges without deleting history.
- Contact photos never enable facial recognition. Addresses are not sent to a
  geocoder or converted into location cues without a separately authorized
  adapter and explicit member policy.

## Deterministic relationship cognition

People replaces “closeness scoring” with two separate concepts:

1. **Descriptive measures:** last inbound/outbound interaction, interaction
   counts by time window/channel, open Commitments, upcoming explicit dates,
   and hosting/favor events when typed facts exist.
2. **Member intent:** explicit circle, desired cadence, cue enablement, quiet
   periods, suppression, and maximum cue budget.

A reconnect cue is eligible only when the member opted in for that Party or
circle, the configured window has arrived, no suppression applies, the source
data is fresh enough, and the weekly attention budget permits it. Copy is
neutral: “You wanted to reconnect around now,” never “You neglected” or “This
relationship is overdue.” Birthdays and important dates must be explicit
source facts; no date is inferred from prose without Triage confirmation.

Existing `crm_surface` becomes a deterministic relationship-cue pipeline. It
selects no more than the configured number of authorized cues and uses typed
templates. A model does not choose who matters, when to contact them, or how to
rank them.

If natural-language extraction is useful, one bounded pinned-OpenClaw skill may
process one authorized Interaction or Note source version and return at most a
small schema-shaped set of candidate relationship facts. Deterministic code
requires exact evidence, allowlisted fact kinds, confidence threshold, matching
owner scope, non-privileged sensitivity, and an eligible known Party. Results
go only to Triage. The skill cannot create a Party, relationship, cadence,
share, merge, reminder, outreach, or provider write.

## Member experience

### Information architecture

People uses a responsive source/lens → person list → detail workspace:

- **Overview** — only due explicit dates, member-requested reconnect windows,
  open promises, and identity reviews; no generic engagement feed;
- **All People** — stable alphabetic directory of authorized, qualified
  Parties with deterministic search;
- **Household & family** — explicit membership and family relationships;
- **Circles** — member-created circles and provider groups, clearly labeled by
  source and never treated as authorization;
- **Organizations** — schools, care teams, employers, vendors, and services
  intentionally promoted into relationship memory;
- **Recently in touch** — authorized interaction chronology; and
- **Needs review** — duplicate/link/promotion/correction candidates with exact
  evidence and a deep link to Triage.

Search covers authorized name, nickname, organization, explicit relationship,
circle, and identifiers. It does not search message bodies or hidden provider
records and does not rank by a model.

### Person detail

The detail workspace provides:

- calm identity header with display name, explicit relationship, source and
  privacy badges, preferred authorized methods, and no raw-address headline;
- deterministic actions: open the exact Comms conversation/account, start a
  governed message, open Calendar Find Time with the authorized attendee,
  create or link an explicit Reminder/Commitment, record an offline
  interaction, edit local context, edit an exact provider contact, or archive;
- **Overview** of typed relationship facts, important dates, contact methods,
  member intent, and clear provenance;
- **Timeline** of authorized Interactions, calendar participation, confirmed
  facts, manual touchpoints, and corrections, with channel/source filters;
- **Open loops** for Commitments, waiting items, Reminders, and scheduled plans,
  each deep-linked to its owning surface;
- **Notes & artifacts** as links to separately authorized source records, not
  copied text; and
- **Details & sources** for provider records, groups, revisions, merge/link
  state, sharing, correction, and evidence.

### ADHD behavior requirements

- Recognition replaces recall: global People search, stable recent people, and
  context-preserving deep links are always easy to reach.
- Progressive disclosure keeps one person and one relevant next step visible;
  provider/schema detail stays behind a deliberate disclosure.
- The default is bounded and quiet. It cannot become another infinite inbox or
  guilt-producing backlog.
- Relationship cues are opt-in, explainable, snoozable, suppressible, and
  capped. Silence is a valid state.
- Labels distinguish “waiting on them,” “I promised,” “important date,” and
  “reconnect window”; they never collapse into generic priority.
- Mobile uses a single-column list/detail transition, 44px targets, wrapping or
  truncation with explicit copy actions, no horizontal page overflow at 390px,
  reduced-motion support, and a chat drawer rather than a permanent rail.

## Cross-surface alignment

- **Today:** may surface only confirmed, explicit People cues—an important date,
  an active Commitment, or an opted-in reconnect window—and deep-links to the
  Party. Unknown senders and pending relationship candidates never enter Today.
- **Triage:** is the sole gate for derived Party promotion, identity links,
  relationship facts, and relationships. Merge/split previews show every
  affected source/link before confirmation.
- **Comms:** preserves member/account/workspace isolation and exact thread
  truth. Known Parties provide a contact lens; unknown senders remain Endpoints.
  “Message” returns to the correct provider/account/thread and still passes
  external-write governance.
- **Reminders:** may link an explicit Party and authorized structured address,
  but never converts the Party or relationship into a Reminder automatically.
- **Calendar:** resolves known attendees without creating unknown Parties;
  People can open Find Time with an authorized attendee. Relationship edges do
  not grant calendar visibility, override calendar rules, or imply availability.
- **Notes:** exact-evidence relationship facts may be proposed for a known Party
  through Triage. People links to the Note only if the current member may read
  it; it never copies private note text.
- **Finance:** merchants and counterparties remain financial entities or
  Endpoints unless intentionally promoted to an organization Party. People
  never exposes privileged transaction detail.
- **Scoreboard:** receives no People directory. Any future child-safe emergency
  contact experience requires a separate guardian-curated contract.
- **Settings:** owns contact-source enrollment/health, write capabilities,
  sharing defaults, relationship-cue budgets, privacy/suppression policy, and
  observation evidence.
- **OpenClaw:** continues to own channels, sessions, generic conversation, and
  model routing. AdministrateMe's pinned People binding is a scope-filtered
  domain read/action seam, never a second agent or generic-memory export.

## Implementation sequence

1. **ADR and migration contract** — approve ADR-0004; define semantic changes,
   legacy qualification, correction events, projection migration, replay, and
   rollback without changing published history.
2. **Domain and privacy spine** — add schemas, parties v2, scoped SQL queries,
   FTS, reversible lifecycle, grants, provider records, endpoints, member
   profiles, facts, and deterministic cue composition.
3. **Identity and Triage** — replace identity resolution, cover outbound and
   calendar participants, add People target kinds, supersession, merge/split
   preview, and process-level guarded commands.
4. **Contact adapters** — implement fixture, Apple Contacts bridge, Google
   People, Microsoft Graph Contacts, and CardDAV packs with truthful capability,
   cursor, reconciliation, permission, conflict, and terminal-write behavior.
5. **Member/OpenClaw experience** — build the responsive People workspace,
   detail routes/actions, source health, provenance/correction, shared Python
   composition, and pinned OpenClaw binding.
6. **Executive-loop integration** — wire typed, non-authorizing reads and deep
   links across Today, Triage, Comms, Reminders, Calendar, Notes, Finance, and
   Settings; update workbooks without making them a write authority.
7. **Constitutional reconciliation and release** — amend thesis, architecture,
   UX, acceptance, crosswalk, migration map, deferral register, operations, and
   handoff; run the release candidate; deploy one immutable commit in
   observation mode; verify live desktop/tablet/phone behavior and rollback.

## Acceptance and rollout

CO-003 is complete only when **AC-30** proves through the production
composition root that:

1. provider-neutral contact snapshots create/update/delete exact source records
   without losing provider/account/container/revision identity;
2. an eligible curated contact produces a qualified Party, while marketing,
   automated, Google Other Contact, directory-only, and unknown message
   endpoints do not enter the primary People directory;
3. exact authorized identifiers resolve Interactions, ambiguous/hidden matches
   fail closed, and repeated genuine contact can only create a Triage candidate;
4. Triage approval can promote/link/correct, and merge/split/revoke/archive/
   restore remain idempotent, reversible, provenance-bearing, and replayable;
5. two adults with private personal/work sources cannot see one another's
   contacts, identifiers, timelines, cues, counts, search hits, or provider
   health without an explicit authorized share; view-as, child, device,
   ambient, and operator paths fail closed;
6. explicit shared household relationships remain visible without sharing the
   owners' private provider records or Interactions;
7. deterministic birthday, commitment, reconnect, suppression, freshness, and
   attention-budget rules produce the same People/Today result on replay and no
   model selects or ranks a person;
8. one bounded evidence-backed language candidate can reach Triage but cannot
   mutate a Party, relationship, outreach, or provider;
9. Apple, Google, Microsoft, and CardDAV capability matrices are truthful;
   fixture write requests prove allowlist/governance/rate-limit/privacy/
   observation/revision ordering and terminal reconciliation;
10. People, Comms, Calendar, Reminders, Notes, Triage, Today, Settings, the
    workbook, and `administrateme.dashboard.people` agree on identity and links
    without copying source truth; and
11. the purpose-built UI passes keyboard/accessibility, safe-rendering,
    evidence, empty/degraded/conflict, reduced-motion, URL-state, console, and
    visual checks at 1440×1000, 1280×900, 1024×768, 820×1180, and 390×844 with
    no horizontal page overflow.

Release remains in observation mode. Real Apple/Google enrollment and a
read-only reconciliation window precede any live contact mutation. Provider
write activation is per member, account, source, and operation; read success
never silently enables writes.

## Explicit non-goals

- No sales pipeline, lead scoring, contact scraping, social-media enrichment,
  facial recognition, people-search broker, or cloud CRM becomes infrastructure.
- No model-computed closeness, relationship importance, sentiment, outreach
  priority, contact merge, household role, privacy, or authorization.
- No autonomous message, birthday greeting, follow-up, contact creation,
  provider edit, deletion, or sharing change.
- No provider contact, interaction body, or relationship note is copied to
  generic OpenClaw memory or another member's source.
- No Apple ID is added to the central service account for this feature; Apple
  Contacts runs through a separately enrolled member bridge with OS consent.
- No contact-provider capability is fabricated. Unsupported fields remain
  provider-specific, visible as limitations, and protected from destructive
  round trips.
- No child People route is introduced by this change order.

## Approval effect

Approval authorizes implementation of the seven phases above, ADR-0004, AC-30,
the listed constitutional amendments, reviewed code/tests/docs, a new commit,
draft PR update, and observation-mode deployment. It does not authorize live
provider contact mutation outside the existing guarded-write and observation
contracts.
