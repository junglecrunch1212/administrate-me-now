# Native CoS private drafting from conversation

MODERNIZE internal work intake: `administrateme_workstation_work` now connects a
current authenticated request to OpenClaw's native CoS worker, an encrypted
scratchpad and actual read-back. This is a complete private drafting route. Native
files, browser work, provider writes and timed continuation remain later outcomes.

The member-facing tool consumes the existing trusted current-input binding for
private iMessage, receiver-authenticated Gmail or member dashboard chat. A model
cannot supply the requester, worker identity, service session or return audience.
The domain reuses the retained follow-up instruction and service-self work model;
the worker receives the full original authored instruction, including constraints.
An exact excerpt identifies the requested draft but cannot discard other text.

OpenClaw owns the subagent run and its transcript. The plugin starts a fresh signed
service session on the supported subagent lane with delivery disabled. The caller
waits at most 45 seconds; a timeout is pending/unknown, never success. A later
current status request can read the same artifact in a new worker session, through
any supported conversation belonging to that same member. It does not create a
second note or require recreating the request in a form.

Existing encrypted work/source-read/audit events provide durable binding. Before
execution, the host seeds the source-read ledger with the original instruction.
The native tool hook checks current authority for every call and permits only the
exact note creation and exact note read. Provider tools, other notes, arbitrary
sources and execution are unavailable through this handoff. The worker remains
CoS; its service session never impersonates the requesting member.

Only a successful native read-tool result produces a read-back proof. The backend
compares its digest and event revision with the canonical retained note. Before
returning content it separately rechecks the current member, original request,
source authority and proof. This first slice permits only the original member's
instruction as a content source. Other consumed sources keep their restrictions
and prevent disclosure through this narrow result route. Raw worker transcripts
are never returned. The existing channel response mechanism returns the verified
result in the authenticated conversation, with its existing sending checks.

No new event schema or plaintext artifact format is introduced. Recovery can use
the supported updater to the prior schema-compatible release, retaining the notes
and source/audit history. The bounded release check is an actual conversation
request producing one note, a native read-back and same-origin verified content;
wrong identity, revoked source access, missing proof, unrelated native tools,
changed content and duplicate creation are failure signals. No live member input
or external provider effect is fabricated for acceptance.
