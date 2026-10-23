# CoS service-private continuity

## Engineered working prompt

Reuse the existing CoS service principal, Party, encrypted append-only log,
delegation kernel and Tasks projection. Treat the CoS and her computer as a
trusted household employee and working environment serving both household heads.
Deliver one complete path: authenticated native tool invocation → private internal work → typed
lifecycle → bounded authorized history → rebuild/restart/restore. Combine
currently authorized canonical facts across household contexts and retain useful
notes and scratchpads with provenance. Preserve specific confidentiality and
separate internal processing from disclosure to people or destinations.
Retain the full provider/workflow ambition as the sequence below; do not infer
provider enrollment, account replacement, sending or deployment permission.

The supplied prompt's implementation list was truncated after item 5. This
slice implements its explicit identity, private ownership, work, event and
read/audit requirements and carries forward the stated setup, capability,
risk and recovery deliverables here. Current fast-delivery policy supersedes
the old checkout's branch/review/reporting ceremony.

## Current-state and gap matrix

| Area | Existing source and evidence | Boundary addressed or remaining |
| --- | --- | --- |
| Principal and Party | `adminme/service_identities.py`; `AdministrateMeRuntime._reconcile_household_enrollment` in `adminme/runtime/application.py`; `tests/acceptance/test_cos_identity_delegation_kernel.py` | Stable nonhuman principal and organization Party already exist. No synthetic member or duplicate identity registry is needed. |
| Member and service sessions | `adminme/lib/session.py`; `adminme/products/openclaw/main.py::_session`; `adminme/projections/parties/queries.py::resolve_service_delegation` | Existing member route requires exact member mapping and delegation. New `adminme/lib/principal.py` keeps service sessions free of member identity aliases. |
| Ownership/privacy | `ServiceRequestContext.allowed_scopes`; `adminme/lib/scope.py`; projection scope canaries | Previously delegated contexts also included service-private scope. They now receive only their authorized member/shared/external scope. Independent service records remain in the exact service scope and tenant. The new context tool separately resolves each standing source grant, so private storage does not isolate the CoS from authorized household facts. |
| Work and lifecycle | `task.created@3`, `cos.work.state_changed@1` in `adminme/events/schemas/domain.py`; Tasks handlers and CO-024 process acceptance | Existing work required a member/shared delegation even when its Party owner was the CoS. Additive v4/v2 events express service-self authority with a null delegation and no member/provider ownership. |
| Event history | `adminme/events/envelope.py`, `adminme/events/log.py`, `adminme/events/writer.py`; SQLCipher/idempotency/replay tests | Existing store remains authoritative. Actor, operator, request fingerprint, correlation, causation and typed results remain distinct. No new database or event store. |
| Service work API | `adminme/products/openclaw/service_work.py`; `tests/acceptance/test_cos_service_private_work.py` | New create/lifecycle/read paths include completed work, bounded paginated audit and revision/idempotency checks. Source-linked records preserve grant, sensitivity and processing-policy coordinates, and recheck them on reuse. Member-shaped state APIs are not used for service-private reads. |
| Native runtime | `openclaw/administrateme/src/service-private.ts`, `hooks.ts`, `provenance.ts`; `adminme/openclaw_topology.py`; `bootstrap/openclaw_activation.py` | Exact native service agent/session binding, separate immutable instructions, four internal tools and a sealed operator Gateway entry. Member requester/delivery context and generic member tools are rejected in the service session. |
| Provider identity | `adminme/provider_bindings.py`; adapter manifests; `tests/acceptance/test_service_owned_cos_sources_direct_intake.py` | Service ownership is supported for Gmail, Google Calendar and Google Tasks. This does not prove that every service account is configured, authorized, acquiring or writable. Personal bindings remain distinct. |
| Apple/Drive/Contacts | `adminme/apple_service_permissions.py`; reviewed adapter/bridge manifests | Existing Apple permission records are member-owned. Exact service-owned Apple destinations, Drive and Contacts require separate work and account/device consent where applicable. |
| Scheduling/delivery | `adminme/products/openclaw/cos_reminders.py`, `adminme/cos_reminders.py`, native reminder hook; `tests/acceptance/test_cos_self_reminders.py` | Existing self-reminders use native OpenClaw scheduling and terminal outcomes. This internal-work slice registers no timer and makes no delivery claim. General workflow composition is later work. |

## Authority and operation

`PrincipalIdentity` distinguishes human members, service principals and system/
device actors. Existing member APIs retain their contracts. The new
`ServiceSession` has no `auth_member_id` or `view_member_id`; it admits only
read/create/manage internal work in `service-private:<principal>`. Source context
is resolved separately under each current standing grant; internal aggregation
does not turn the CoS into either member or expand a disclosure audience.

The native agent ID uses a separate namespace from member agents. Its session
coordinate binds the principal, fresh run nonce and existing transport secret
through HMAC. Each new operator request starts a fresh model session, then
loads durable records through current authority checks. This prevents old
transcripts from becoming an unchecked source after a grant is revoked.
The plugin derives provenance from trusted native context, never tool
parameters. The backend independently verifies loopback authentication, exact
configured CoS principal/Party, agent, session and absence of member routing.
The signed session coordinate is omitted from durable audit and responses.

The service agent exposes exactly:

- `administrateme_service_private_work`: create typed private internal work.
- `administrateme_service_private_work_state`: start, hold, follow up, resume,
  complete or cancel one expected revision.
- `administrateme_service_private_state`: read work and paginated typed history.
- `administrateme_service_context`: read bounded task/commitment facts,
  current calendar/contact facts, and mail/note/file metadata across current
  member/shared/external standing grants, plus service-owned acquired Gmail
  headers and policy-permitted bounded plaintext snippets.

These have standing internal authority and zero provider effects. Existing
member/private/shared grants and external approval gates remain separate.
Member work is not moved into this store. Concise work records, notes and
scratchpads can use multiple authorized source facts; source references, exact
grants, audience restrictions and the highest sensitivity are retained. Revoked
grants or a source policy that disallows processing block further use without
erasing history. Client information stays subject to those restrictions.
No unrestricted private transcripts or model chain-of-thought are copied into
canonical service memory.
Descriptions and reason codes are concise operational records, not hidden
reasoning. Due/follow-up dates explicitly return `timer_registered: false`.

The supported installer prepares the isolated service workspace and exact
tool allowlist. Its declared `AGENTS.md` and established CoS `IDENTITY.md` form
one immutable content-addressed profile. Native OpenClaw recognizes the existing
identity instead of creating a first-run `BOOTSTRAP.md` birth sequence. Missing,
symlinked or changed declared files fail preparation; legacy artifacts still
select their original instruction-only workspace. Prior directories and private
records remain intact. This **MODERNIZE** repair leaves member profiles and
global onboarding settings unchanged. The native Gateway check verifies the
actual model prompt has the established identity and no onboarding instructions
on the first run and after restart. Browser tools remain outside this service
allowlist; future browser provisioning must carry the standing identity guidance
and private intended-identity inventory before use.

The supported operator entry is:

```sh
openclaw gateway call administrateme.service.run --params '{"message":"Review authorized work and retain a coordination note","request_id":"coordination-review-001"}' --json
```

Use the managed runtime environment. The plugin seals the service session and
calls pinned native `subagent.run` with `deliver:false`; the public command
contains no transport credential or signed session. The returned run ID means
accepted execution, not completed work. Read back the work/history through the
service state tool to establish the actual result. The internal `webchat`
channel sentinel is accepted only without recipient, sender or account routing.
A member cannot select this authority by adding a field to a chat request.
Automatic intake and progress receipts from member chats remain a follow-up.

## Effective capability matrix

| Capability | Implemented in this slice | Configuration/authority | Operational proof |
| --- | --- | --- | --- |
| Service-private create/read/lifecycle | Yes, native plugin and application routes | Exact configured CoS identity and native service session; internal authority only | New production-composition tests and native plugin tests; live activation is separate |
| Cross-source working context and notes | Current standing grants resolved per source; typed task/commitment facts, notes and scratchpads | No repeat permission for covered routine internal work; source restrictions retained | New two-head coordination, privileged-source retention, revocation, restart and encrypted-restore tests |
| Household-shared context | Existing separate shared delegation | Existing adult quorum remains required | Existing shared coordination/operating agreement tests |
| Service provider accounts | Existing Gmail/Calendar/Tasks binding contracts | Exact account/resource/verb authorization still required | Existing source/fixture evidence is not a new provider-write verification |
| Service-owned Apple/Drive/Contacts | Not completed by this slice | Needs exact supported provider/destination setup | Not claimed operational |
| Autonomous waits and provider effects | Existing bounded reminder path only | Native timer plus exact send authority and terminal callback required | No new provider action, message or timed-delivery test in this slice |

## Migration, checks and recovery

This is **MODERNIZE** for independent service authority and usable private
continuity, **PRESERVE** for member APIs, old event versions, fifteen existing
projections, encryption, append-only truth and provider approvals, and
**SUPERSEDE** for service-private access inherited by delegated member sessions.
The later deliverables below are **DEFER** for this slice, not a claim that the
whole remote-human-CoS ambition is complete.

No destructive storage migration is introduced. Tasks projection v10 retains record kind/source metadata and adds an indexed
metadata-only source-read receipt table, reconstructed from retained audit events.
Additive task.created v5 and openclaw.tool_call.recorded v3 preserve older meanings. The log itself is not rewritten.
Old v3/v1 delegation events remain registered and replayable. Focused hosted
Linux/macOS checks cover principal/scope/schema contracts, process lifecycle,
pagination, retry conflicts, member canaries, two-head working context, source
revocation, rebuild/restart/encrypted restore, native Gateway invocation and
tool boundaries, topology selection and maintenance recovery. CI retains its protected
revision check and falls back for changes outside the reviewed scope.

Before new events exist, an immutable predecessor may be selected through the
supported updater. Once v4/v5 task or v2/v3 audit history exists, keep a schema-aware runtime and
disable the feature through the supported managed Gateway configuration:

```sh
openclaw config set plugins.entries.administrateme.config.serviceWorkEnabled false --strict-json
openclaw gateway restart
```

The plugin denies both the operator entry and service tools when disabled;
normal installer reconciliation preserves this explicit setting. Verify the
entry refuses new runs and existing runtime health remains good. Keep the
schema-aware runtime; do not erase history or assume an older registry can read new events. A later binary
rollback requires demonstrated compatibility with the retained history.
Workspace selection/removal retains prior immutable instruction directories
and private data. Maintenance must match the exact planned target agent,
workspace and tool allowlist while retaining all prior member grants.

Material risks are service/member scope confusion, forged native provenance,
duplicate or stale transitions, credential-bearing audit, older schema
readers, and mistaking work state for delivery. The tests target those
boundaries. A live deployment still needs the owner's deployment request and
fresh applicable persistence/recovery verification; no live account, grant,
permission, credential or provider destination is changed by implementation.

## Remaining deliverables

1. Full-body/attachment access with trusted explicit-demand evidence and
   provider acquisition/current-state indexing not already supported. Own Gmail
   metadata and policy-permitted plaintext snippets are implemented below; Apple/Drive and wider provider content remain later slices; own Calendar/Tasks
   current facts are implemented below.
2. Authorized member instruction → bounded CoS work with explicit provenance
   and audience-aware progress receipts; no automatic private-history copy.
3. One exact service-owned provider workspace at a time, with supported verbs,
   read-back, consent and reconciliation; retain all personal integrations.
4. One useful durable workflow using existing OpenClaw trigger/wait facilities:
   intent → authorized context → policy → provider attempt → terminal result →
   reconciliation → correctly scoped member receipt. Ambiguous effects must
   not be blindly retried.
5. Broader matters, callbacks and service operating context only as those
   concrete workflows require them. No second scheduler or generic framework
   is needed for the current internal-work outcome.


## Authorized source context extension

**MODERNIZE:** the existing service-context tool now includes current complete
calendar observations (v2/v3), contacts and bounded mail, note and artifact
metadata. It uses existing standing grants; no provider calls, credentials or
new permissions are introduced. Calendar cancellation remains an explicit
status. Superseded/deleted snapshots, locked notes and retracted messages are
excluded, including explicit old event references. The original encrypted event
supplies each fact; its projection only identifies the current revision.
Metadata fields are bounded and truncation is reported. Provider bodies, HTML,
attachments, arbitrary contact fields and storage references are not exposed.

**PRESERVE:** source ownership, sensitivity, exact authority and
internal-processing-only restrictions accompany retained notes. Effective
source policy includes the existing parent/container inheritance resolver.
Delegated sources still admit typed-fact modes only, without provider bodies;
metadata-only, no-context and explicit-demand-only delegated sources are withheld.
Retained notes re-read their original event's coordinates and current policy on
every reuse. They remain historical notes after an observation changes; they
never become current source facts. Revocation blocks reuse without erasing history.

## Service-owned Gmail and private follow-up

**MODERNIZE:** the same native tool reads the CoS's already-acquired Gmail,
including ordinary unrouted mail, using exact service ownership rather than a
member grant. Tenant, service principal, envelope owner/visibility, provider,
account and workspace must agree. The existing native-message index selects the
current original event; older revisions cannot be requested as current content.
No provider call or enrollment is added. Balanced/default policy is `typed_facts`:
it permits bounded headers, not a body. `metadata_only` returns identifiers/dates;
`typed_facts_safe_snippet` additionally permits at most 2,000 plaintext characters
with explicit truncation. `none` and explicit-demand-only modes remain withheld.
No policy is elevated, no HTML interpreted and no attachment fetched.

New `task.created@5` preserves truthful service-self source authority alongside
existing exact member/shared/external provenance. The private note inherits the
highest source sensitivity and the strongest consumed read mode. Typed audit v3
records source authorities and a one-way digest of the authenticated native run;
it contains no body or signed session secret. The Tasks projection indexes these
receipts. A new note inherits every source read in its run (also sources from
retained notes/history), so omitting IDs or changing policy between read and save
cannot discard restrictions. A note is bounded to 20 sources; a broader run must
start a fresh narrowly scoped session before saving. Each new native run already
has a fresh authenticated nonce. Receipt replay/restart/restore preserves the
same floor. Narrowed policy or a revoked member grant blocks saved-note reuse;
restoring sufficient authority allows it again without rewriting history.

Focused production-composition coverage includes own/unrouted mail, wrong owner
and member identity, mixed privileged provenance, plaintext bounds, policy modes,
in-run omission/downgrade and derived-note laundering, exact currentness, replay,
restart and encrypted restore with zero provider effects. The existing focused
`cos-service-work` checks now also bind the typed audit schema; an unreviewed
schema still falls back to broader validation. Recovery uses the feature-disable
command above on a runtime that understands all retained event versions.

This slice does not complete full-body/attachment access, explicit-demand content,
Apple/Drive/Contacts setup or provider writes. Those remain separate usable deliverables under the full prompt.


## Service-owned Calendar and Tasks current context

**MODERNIZE:** the same private context tool reads existing Google Calendar v3
observations and Google Tasks list v2/item v4 snapshots under exact service-self
ownership. It returns event titles/times, list names, task titles, due components,
ordering, completion and source revisions. Google Tasks due dates remain floating
all-day dates, not a scheduled timestamp or timer. Calendar descriptions, arbitrary
provider data, task note bodies and attachment contents are not exposed.

Calendars v4 and Lists v5 add separate private current-source pointer tables and
replay retained events. Original encrypted events still supply content. Native
keys include tenant, principal, provider, account, container and object. A source
update supersedes only that exact copy. Explicit tombstones hide deleted events
and items; list deletion also hides its old children, and recreating a list does
not resurrect old item snapshots. Partial snapshots never imply deletion. Member
calendar capacity, lists and canonical tasks remain separate.

Existing source policy and v5 note provenance apply without new grants or model
flags. Metadata mode excludes titles; typed planning facts do not claim snippet
access. Acquisition pause and retained-context policy remain distinct. Replay,
restart and encrypted restore rebuild the private pointers and source receipts.
The focused hosted check adds the affected calendar/list contracts and real
producer → production service-route → private follow-up tests. Live usability
still requires authorized acquisition and native service-tool read-back after
activation. Recover through the existing service-work feature disable on the
schema-aware runtime; no provider effect or enrollment is introduced here.
