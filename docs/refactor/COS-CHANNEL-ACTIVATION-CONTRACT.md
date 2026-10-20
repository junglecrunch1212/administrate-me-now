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

The hosted exact-source installation gate loads the actual CoS approval relay
as a service in a fresh, credential-free Gateway. Independent loopback clients
exercise the public native approval fallback, both decisions, session/revision
and expiry refusals, terminal finalization and replay refusal against the real
registry. Another ordinary approvals client cannot see or resolve the request;
the process-local runtime authority remains inside Gateway. Startup and child
cleanup are bounded, channels and background work are disabled, and diagnostics
contain synthetic data only. This MODERNIZE validation follows the independent
approval requirement in `ACCEPTANCE-CONTRACT.md`; no production test API is added.

## No-send macOS permission preflight

MODERNIZE: provider enrollment gains a narrow operator preflight through the
existing Gateway, preserving the permission and provider-effect rules in
`BOOTSTRAP-AND-TOPOLOGY-CONTRACT.md` and `ACCEPTANCE-CONTRACT.md`. From the selected
release's Python environment, `python -m bootstrap.cos_permissions
--instance-root <deployment-root>` checks permission without prompting. The
separate `--request` action may display macOS consent; it never accepts consent,
opens settings, launches Messages, sends AppleScript or sends a message. Request
is an explicit operator action. Any native grant still requires the owner's
action-time approval.

The loopback protocol-v4 RPC requires `operator.admin` plus a short, single-use,
operation-bound HMAC ticket. The CLI loads the actual Gateway secret reference
from the configured secret store; no token appears in arguments or output.
Member dashboard scopes are unchanged. Trusted Gateway startup pins the selected
release's helper paths and interpreter hashes. The RPC accepts no paths, target,
environment, command or arbitrary arguments. Helpers inherit only basic OS
environment values, and run as children of the actual Gateway. Checks are
bounded; request waits at most 180 seconds before terminating its own child.
There are no retries or automatic permission grants.

The Apple Events helper calls only public CoreServices descriptor creation,
`AEDeterminePermissionToAutomateTarget`, and descriptor disposal, off the main
thread, targeting fixed `com.apple.MobileSMS`. It preserves exact OSStatus:
`0` authorized, `-1744` needs consent, `-1743` denied, `-600` target not running,
and `-1742` target inaccessible. Unsupported platform, other OS errors, helper
failure and timeout remain distinct from authorization. The signed result binds
operation, nonce, timestamps, helper/interpreter hashes and Gateway PID.

Check also opens only the service account's fixed Messages SQLite database in
read-only mode and inspects schema metadata; it never enumerates chats, messages
or contacts. `imsg initialize/status` is intentionally not used because that
provider starts a Contacts cache even for status. Database readiness means this
Gateway child can open the expected schema; it does not establish a successful
imsg send. Request does not run the database check.

macOS chooses the responsible identity across launchd, Node, Python and imsg
processes. Its actual consent UI and a subsequent deployed-service check must
confirm attribution. A Terminal, Codex or SSH result is not a service permission
proof. Bare interpreters may be denied because of usage-description, entitlement
or attribution restrictions; an authorized result does not prove the transport
can send, the CoS account is correct, or all provider permissions are ready.
No private API, SIP change or Full Disk Access bypass is used.

Hosted synthetic tests exercise refusal, pin drift, replay, environment isolation
and timeout. The exact-source macOS Gateway gate also calls the real helper only
with `askUserIfNeeded=false`, verifies its signed status and admin-scope refusal,
and performs the read-only schema check on the disposable runner. CI never
requests consent or accesses the family instance.

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

Ordinary app upgrades and rollbacks preserve an enrolled transport through the
existing stopped-service activation transaction. Preflight validates the old
guard against the current immutable release and prepares only the selected
release's wrapper path/hash and the matching account `cliPath`. The journal
holds exact original and selected guard/gateway bytes and rejects wider deltas.
Provider binaries, provider accounts, recipient policy and operational approval
are unchanged. Drift after preflight aborts selection. Partial writes, failed
Doctor and interrupted selection recover the original bytes before services
restart; explicit rollback prepares the same narrow reverse transition.
Replies remain held while either the channel-operation journal or ordinary
release journal is active, including the post-start verification interval.
Runtime verification continues to require the selected immutable release's exact
wrapper; retained-release paths and caller-supplied replacement hashes are not
accepted as substitutes.

### Upgrading an already live CoS

This MODERNIZE preserves the AGENTS.md observation-on deployment rule. Before
collecting candidate readiness, use the supported `--enter-observation`
operation from the clean newly merged protected-main checkout with the selected
predecessor's installed Python. Refresh the checkout's `origin/main` before
invocation: the controller requires its exact HEAD to equal that protected-main
reference, proves the selected predecessor's immutable source and ancestry, and
refuses a different interpreter or deployment binding. It never edits an
installed release or starts the candidate to perform containment.

The private `--request` JSON contains only `owner_member_id`, a distinct
`operator_principal_id`, `owner_authorization_sha256`, `expected_release_commit`,
`expected_activation_id`, and `expected_configuration_fingerprint`. These bind
the owner's existing deployment/write authorization to the actual current live
receipt and configuration; they do not require another approval ceremony or
accept fabricated enrollment evidence. The command is:

```sh
SELECTED_PYTHON -m bootstrap.cos_channel_activation \
  --instance-root PRIVATE_INSTANCE_ROOT --request PRIVATE_OBSERVATION_REQUEST \
  --enter-observation
```

The existing operation lock and journal hold replies before stopping services.
The stopped encrypted log establishes the authoritative, complete provider-effect
baseline; a legitimate live reply finishing before this boundary is retained.
An authenticated encrypted backup and isolated restore rehearsal precede the
observation mutation. The controller revalidates source/configuration and policy,
appends an idempotent deactivation of the previous live authority and an audited
observation enable, archives the previous closed interval unchanged, and starts a
new `cos_enter_observation` interval. Enrollment, routing, credentials, recipient
policy, the old receipt and all activation/source history remain unchanged.
The retained receipt is historical: its deactivation prevents live dispatch.
Healthy selected-release Doctor, current observation-on readiness and unchanged
authoritative provider-effect counts are required before the journal is closed.

On interruption or failed verification, the journal continues to deny replies
and services remain stopped. Run the same merged controller and installed Python
with `--instance-root PRIVATE_INSTANCE_ROOT --recover`; recovery completes that
same observation boundary and never restores live writes. A repeated recovery
does not duplicate the deactivation, observation-enable fact or archived interval.
Configuration, policy or unknown-effect drift remains a blocking failure.

After successful containment, collect normal HTR-10 and candidate-pending
evidence and use the guarded updater. Its encrypted backup, rollback, full Doctor
and member-browser gates remain mandatory. Current and archived effect intervals
must survive promotion and rollback without reinterpretation of authorized live
effects as observation effects. After successful deployment, run the existing
`--request PRIVATE_ACTIVATION_REQUEST --enable-live` operation from the selected
merged release with fresh actual sender/transport evidence. Omit
`recipient_entries` to retain the existing policy without reseeding. This appends
a fresh activation boundary; suppressed and pre-boundary requests are never
released or retried automatically.

The retained dataset remains an accepted bounded predecessor. This operation
does not relabel the discarded pre-reset history as a proven production
rollback. Physical browser acceptance and external sign-in coverage are
reported separately and must not be inferred from these process tests.
