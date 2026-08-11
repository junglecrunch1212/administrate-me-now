# Gate 7 household experience architecture

## Responsibility split

```mermaid
flowchart LR
    P["Principal"] --> OC["Pinned OpenClaw Control UI"]
    A["Authenticated adult"] --> MP["Narrow member portal"]
    C["Child or shared screen"] --> MP
    OC --> B["Ten typed dashboard bindings"]
    MP --> V["Authorized domain views"]
    MP --> R["Protocol-v4 text relay"]
    R --> G["Loopback OpenClaw Gateway"]
    G --> M["OpenClaw sessions, transcripts, routing, providers"]
    B --> API["AdministrateMe domain APIs"]
    V --> API
    G --> T["Signed, typed domain tool call"]
    T --> API
    API --> E["Encrypted event log and 13 domain projections + Triage"]
    MP --> Q["Atomic data-only temporary view specs"]
    Q --> API
```

OpenClaw owns generic conversation, transcript/history, conversational memory,
provider/model routing, fallback/retry, token accounting, runtime
administration, and provider credentials. AdministrateMe owns household
identity, role/scope authorization, domain facts, proposals, guarded writes,
observation, projections, evidence, workbooks, and disposable saved-view
configuration.

## Member chat boundary

The browser calls only `/api/member/v1/chat/{status,history,stream,abort}`. The
server authenticates the Tailscale login at trusted ingress, refuses view-as
chat, applies member/guardian policy and a sliding-window limit, and derives an
opaque actor plus signed OpenClaw session key. The Gateway connection is fixed
to loopback port 18789, protocol version 4, and OpenClaw `2026.7.2`.

Only safe status phases, rendered assistant deltas, terminal state, safe error
code, retryability, and the request correlation id are relayed. Bounded
WebSocket and HTTP streams abort on disconnect/cancellation. Tool XML,
reply/audio directives, control tokens, provider metadata, hidden content, and
raw envelopes are dropped. The portal never accepts a provider/model choice.

For a domain tool call, the plugin validates the session HMAC before creating
portal provenance. The Python tool endpoint independently recomputes the
member binding and compares actor/session in constant time. An operator token
authenticates only the loopback transport. Portal session identifiers are
validated in memory and redacted from persistent tool/proposal audit payloads.

## Temporary surfaces

`TemporarySurfaceStore` writes one schema-versioned JSON file atomically under
the instance configuration directory. A create request can supply title,
purpose, allowlisted query declarations, allowlisted task verbs, allowlisted
required capabilities, compatible modes, and optional expiry. Stable id,
owner, audience, lifecycle status, and versions are server generated.

The only query names are `what_now`, `tasks.open`, `lists.open`,
`calendar.week`, and `finance.recent`. Evaluation ignores cross-scope ids and
calls existing scope-filtered projection query functions under a fresh
request `Session`. Raw event log, SQL, code, script, and arbitrary template
declarations are rejected. Save/delete never appends a task, commitment, list,
calendar, finance, or fabricated project fact.

An active view is private to its owner. Explicit promotion names known adults;
device/ambient audiences are rejected. A child audience additionally requires
`child` compatibility plus the child query/action allowlists. Every read and
action repeats visibility, role, capability, and domain authorization. Expiry
or archive removes navigation; bookmarks show a calm unavailable shell with no
spec or domain payload. The underlying log and all fifteen registered
projections remain unchanged.

## Routes and experience modes

- Adults: Today, Triage, Comms, Reminders, Calendar, People, Notes, Finance,
  Scoreboard, Settings.
- Children: Today and Scoreboard.
- Shared screens/devices: Scoreboard.
- Carousel: `adhd_executive`/fog-aware, one ranked action, micro-step, endowed
  progress, bounded decisions, immediate outcome-to-recalculated-next action.
- Compressed: minimalist/dense queue, restrained animation, strict proactive
  cap.
- Child: warm language/rewards, no Comms/adult communications, Reminders, People, finance,
  Notes, settings, or operator control.

People is an adult-only relationship-memory workspace, not a sender list or
sales CRM. Its source/lens rail and authorized search lead to a qualified
Party list and one progressively disclosed detail view. Provider Contact
Records, Endpoints, Identifier Links, Relationships, member-specific profiles,
typed facts, Interactions, Commitments, and Artifacts keep their own source and
privacy semantics. Unknown endpoints stay out of the directory; provider
records and private relationship context do not cross adult boundaries without
an explicit field-group grant. Cues use only explicit cadence and typed facts,
never model closeness or guilt. URL-backed search and selection change only the
view. The pinned OpenClaw People binding consumes the same deterministic
cockpit rather than generic memory or a separate agent.

Notes is a private source/search/master-detail workspace with All, Recent,
Shared, Meetings, and Needs Triage lenses. Provider sharing is explanatory
evidence only; one explicit AdministrateMe grant controls each shared
reference. Notes renders untrusted content as text, exposes provenance and
source limits, and links candidates to Triage without an inline approval path.

Calendar is the deterministic time-capacity cockpit, not a raw provider list.
Calendar-list observations record capabilities; principal-approved per-member
rules separately classify hard blocks, soft load, context, quarantine,
caregiving markers, buffers, write destinations, and recurring household
guardrails. Duplicate copies retain source provenance and render once. The same
effective-event contract drives Calendar, Today, and Triage route validation;
no model chooses availability or a write destination.

The member Calendar uses anchored Day and Week time grids, a six-week Month,
a fourteen-day Agenda, and duration-aware Find time. Calendars can be hidden
or recolored by source for visual focus, but those controls are explicitly
presentation-only: every authorized fact continues to participate in the
approved capacity rules. Household guardrails sit behind event blocks,
all-day facts have a separate lane, and event detail progressively discloses
buffers, provider provenance, duplicate copies, and evidence. Rules and
recurring policy are separated from daily browsing and require an explicit
settings disclosure.

The page route and data route each enforce the role allowlist. Navigation
hiding is presentation only. All untrusted values are assigned with
`textContent`; the production portal contains no `innerHTML` rendering seam.

## Five-surface executive loop

CO-001 **MODERNIZES** Today, Triage, Comms, Reminders, and Calendar as one
closed member workflow while preserving their independent domain types and
sources of truth. Exact, URL-backed surface links carry the member from a
Today card to the originating thread, proposal, provider reminder, or calendar
event. These links and shared freshness/attention DTOs are presentation
contracts only; they cannot authorize or materialize state.

Comms Focus v2 treats unread as visual state and requires an explicit typed
action signal or linked Triage candidate. Today passes the same inputs through
the same pure predicate. Triage uses structured authorized directories and
shows candidate readiness, source evidence, recent outcomes, and the existing
Calendar reasoning preview before confirmation. Reminders and Today consume
one effective-reminder composer; strict possible-copy clusters group only the
read view and retain every provider item. Today separates timed events from
all-day/context facts so context cannot become “next on the clock.” The five
action-capable surfaces persistently explain observation and freshness, and
the OpenClaw chat rail becomes an explicit drawer below 1600 px.

The existing `classify_message_nature` skill is schema-bound through pinned
OpenClaw and may report bounded language features/candidates only. Fixed
pipeline code validates confidence, scope, sensitivity, supported target kind,
and closed candidate fields before emitting a private Triage proposal. No
model ranks, routes, authorizes, schedules, sends, completes, suppresses, or
mutates.

## Degraded and diagnostic truth

Domain projections remain usable if OpenClaw/model chat is unavailable. Chat
states distinguish member-disabled, guardian-disabled, unauthorized,
provider-unconfigured, transport-unconfigured, OpenClaw-degraded, and healthy.
Settings distinguishes disabled, blocked, degraded, healthy, and workbook
not-built states with explicit reasons. Doctor reports portal, deterministic
domain views, chat transport, and model-provider state separately and scans
the final report for referenced secret values.
