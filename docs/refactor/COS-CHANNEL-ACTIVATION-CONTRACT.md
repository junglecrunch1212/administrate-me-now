# CoS channel activation

This MODERNIZE implements the explicit member submission architecture in
CHANGE-ORDER-032 and the deployment safety rules in AGENTS.md. The immutable
release is deployed with observation on. Provider enrollment and live replies
are a separate owner-authorized operational transition.

The `cos-forwarded-acquisition-v1` overlay SUPERSEDES the bounded sample's
selection of an actively polled mailbox. It PRESERVES the exact
`disposable-test-acquisition-v1` object, dataset marker, source facts, durable
personal-mail lifetime ledger, and old decisions. The overlay admits one exact
enrolled CoS Gmail account, pauses every member adapter, disables historical
mailbox expansion, and optionally enrolls one dedicated OpenClaw Messages
channel. Member bridge ingestion and workbook reverse remain paused under the
retained dataset boundary. Other product functions and their implementations
remain present. Settings connection resumes cannot widen the acquisition policy.

Accounts, credentials and member contacts are private runtime configuration.
The operation does not create accounts or manufacture proof of a sign-in.
An email channel can become ready independently of Messages. Messages requires
the pinned, verified guarded transport and an exact sender identity; signing in
to Messages alone is insufficient. No activation test sends a provider message.

## Supported operation

Run `python -m bootstrap.cos_channel_activation` from the clean protected-main
checkout matching the selected release, with that release's Python. Pass
`--instance-root` and `--request` naming an owner-only private JSON document.
Without `--enable-live`, acquisition remains in observation. `--enable-live`
also requests the separately authorized reply boundary.

The request contains the reviewed `adapter_config`, exact `owner_member_id`,
distinct `operator_principal_id`, `owner_authorization_sha256`, and
`sender_proofs`. Each proof names `channel` (`email` or `imessage`), the SHA-256
of the exact provider account, separate sender and transport evidence SHA-256
values, and an actual timezone-aware `observed_at` no older than one day.
Evidence comes from authenticated account/transport checks, never an invented
successful send. Contacts are not proof of provider ownership.

Optional `recipient_entries` seeds the owner's explicit Settings allowlist.
The operator identity remains the audit actor; the independently named
authorizing principal must exist in the member mapping. Provisioning does not
impersonate a browser session or grant per-action authority.

Messages requests additionally contain `prepared_openclaw_config` and
`cos_imsg_guard_config`. The pure channel validator and file verifier must accept
both. Their fixed runtime coordinates are covered by the same journal,
encrypted backup, restart and recovery boundary as the adapter configuration.
Before stopping services, preparation reconciles the exact member peer routes,
agent list and binding fingerprints against existing private member workspaces.
Missing or moved workspaces and unknown members are refused. The reconciled
request must equal the narrow channel preparation of the current gateway
configuration; unrelated model, plugin, agent and provider changes are refused.

The controller serializes with the existing updater lock, proves the selected
immutable source, requires current healthy observation-mode readiness, stops
the services, creates and authenticates a fresh encrypted backup and isolated
restore rehearsal, and journals original control-file bytes before changing
them. The journal retains only the initial recipient-policy revision and event
reference; compensation reads its contacts from encrypted history. It never
deletes or edits the event history or cursor ledger.

For live mode, `cos.channels.activated` and `observation.disabled` record the
exact activation boundary. The old observation audit retains its original
start and gains this end; global terminal-effect integrity continues for all
later effects. The content-free activation receipt is bound to deployment
configuration and replayed against the encrypted log. The final response guard
holds every send while the operation journal exists. Full bounded Doctor
settlement and unchanged provider-call counts must pass before commit releases
that hold.

CoS effect accounting verifies each private member approval, its exact recipient
policy, the consumed independent OpenClaw approval, contact intent, terminal
result and correlated outbound fact. Duplicate or orphan facts, missing links,
identity drift and unknown delivery degrade global integrity. Confirmed effects
remain counted even when their terminal chain is incomplete. A refusal before
contact intent is an accounted zero-effect request; unexecuted draft approvals
are not provider activity. Closed observation windows preserve the prior proof
without hiding later live effects.

## Recovery and live safety

Use `--recover` after an interrupted operation. Failure recovery stops the
services, restores the original control files, pauses all polling, reenables
observation, appends a deactivation, compensates an unchanged seeded recipient
policy, refreshes the exact configuration binding, and requires full Doctor
before archiving the journal. A subsequent human policy edit is never overwritten.
Source facts acquired during the attempted operation remain in the append-only
history. An interrupted journal continues to deny sends until recovery or commit.

Live mode requires a nonempty current recipient policy and its durable
activation. Every new reply still needs its independent domain and OpenClaw
approvals, recipient/privacy checks, and rate limits. Old suppressed commands
are not released: the initiating request timestamp must be at or after the
activation boundary. Clearing the allowlist revokes live authorization;
ordinary edits apply immediately without broadening any previously approved
reply. Unsupported release downgrades are refused while the overlay is present.

The retained dataset remains an accepted bounded predecessor. This operation
does not relabel the discarded pre-reset history as a proven production
rollback. Physical browser acceptance and external sign-in coverage are
reported separately and must not be inferred from these process tests.
