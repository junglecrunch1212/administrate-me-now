# CoS browser read workstation

A genuine private member conversation can request an authorized CoS browser read
through `administrateme_workstation_work(action="browser_read")`. OpenClaw runs a
CoS worker with only its native browser tool. The installed guard binds the actual
shared tab, verifies the current destination account, workspace and role before
reading visible text, then retains an encrypted `cos.workstation.source_observed`
event. Only the originating member, with current source/audience authority, can
receive the retained result. `browser_status` recovers a lost acknowledgement from
its `browser_request_event_id` without another browser read.

This usable slice reads at most 2,000 UTF-8 bytes and explicitly reports truncation.
It does not navigate, click, edit, create files, schedule or send independently.
General authorized browser work and native file/document work remain later outcomes;
the browser route is a shared native tool boundary, not a separate provider SDK.

## Supported private preparation

The protected release must contain both the native hook and result middleware.
Supported activation enables `allowPromptInjection` only for the reviewed
AdministrateMe plugin because this pin places finite `toolsAllow` policy inside
`before_prompt_build`. These hooks return no prompt prose. Browser admission
requires this setting; ordinary unbound service work keeps its existing surface.
All account/device/tab inputs and proof remain private, outside Git. Root is the
sole live operator; candidate tests use disposable hosted state.

1. Obtain a fresh native `config.get` snapshot into a private file. Run the deployed
   `python -m bootstrap.workstation_browser prepare-native-patch --instance-root
   <root> --snapshot <private-snapshot> --output <private-patch>`. Apply the produced
   `baseHash`/`raw` parameters through native `config.patch`, which checks the hash
   and performs its supported restart. Do not edit active configuration. Preparation
   enables the bundled browser plugin and exactly one explicit extension profile,
   named `chrome`, with `allowSystemProfileImport: false`. A second extension
   profile or another driver under `chrome` is rejected. Existing credentials,
   member tools and other gateway policy are preserved.
2. In the verified intended CoS Chrome profile, use `openclaw browser extension path`
   to locate the pinned extension, enable Developer mode in `chrome://extensions`,
   and load that directory. Use `openclaw browser extension pair --json` and transfer
   its host-local token directly to the extension without transcript/log disclosure.
   Complete any required human browser consent. Pairing selects the first extension
   profile in this pin; one explicit `chrome` profile prevents ambiguity.
3. Share the intended CoS tab through the extension. Verify the actual destination
   account/workspace/role independently. Obtain its actual native target ID using
   `openclaw browser --browser-profile chrome tabs --json`. Do not invent a target,
   infer account from a label or /u/0, copy cookies, or attach a member profile.
4. Use `python -m bootstrap.workstation_browser schema` for the strict policy shape.
   Provision a private document through `provision-policy --instance-root <root>
   --input <private-policy> --expected-revision <current>`. Each resource references
   the existing canonical `workstation-identities.json` binding/revision, exact URL,
   authorized HTTPS path prefix, observed native target/evidence, independent DOM
   identity probes, allowed member audience and inherited source restrictions.
   Revision checks and private file validation prevent stale/ambiguous replacement.
5. Read `/api/openclaw/v1/tools/workstation-browser/source-policy` through the existing
   authenticated service context. If the default source policy lacks requested
   content access, prepare one exact `BrowserSourceProvision` document with its
   policy key, binding/source revisions, actual operator and owner-authorization
   digest, nonce and expiry within one hour. Stage it with `stage-source-policy`;
   `/provision-source-policy` consumes only that staged nonce and appends the existing
   `settings.source_policy_v2_changed` event. It permits only
   `content_on_explicit_authorized_demand`, preserves other policy dimensions and
   cannot override explicit none/metadata/paused restrictions. The source policy
   itself never admits a read without a current authenticated instruction.
6. Prepare/apply a fresh native patch after provisioning. This adds `browser` only
   to the existing CoS worker when the reviewed guard, canonical identities and
   complete private policy are available. Supported activation/reconciliation
   carries that setting and the current workstation instructions forward.
   Native patch preparation accepts either legacy `agents.list` or current keyed
   `agents.entries`, rejects an ambiguous mixture, and adds only `browser` to the
   existing CoS worker's explicit allowlist. Other agent settings are preserved.

### Gmail self-mailbox identity

The default `dom_fields_v1` verifier keeps the generic independent DOM-field
contract. For an operator-verified Gmail inbox, `identity_verifier:
gmail_self_mailbox_v1` uses a fixed provider verifier and an empty
`identity_probes` object. It requires an exact
`https://mail.google.com/mail/u/<index>/#inbox` URL and all three existing canonical
identity fields. The URL index is a routing coordinate, never account identity.

Before any mailbox text is read, the same synchronous callback checks visible
Google account chrome under the unique `#gb` container. The account-control email
and mailbox email in the actual title must equal the canonical account. The
supported title shape is
`Inbox (22) - cos@example.invalid - Synthetic household Mail` (the count is
optional). The title's workspace label must equal the canonical workspace.
It requires one visible, enabled Chat control in the observed Gmail app-navigation
ancestry. Hidden, disabled, inert, ambiguous and message-content controls reject.
Google's
[delegation documentation](https://support.google.com/mail/answer/138350)
states that delegated accounts cannot use Chat in Gmail; its
[navigation documentation](https://support.google.com/mail/answer/11555490)
identifies the main menu as the entry point. Matching mailbox labels or the
absence of a `Delegated` label alone do not establish self access.

The uniquely anchored Chat roster iframe corroborates the provider integration:
its actual `src` must have the exact `https://chat.google.com` origin, matching
`/u/<index>/frame` path and Gmail origin parameter, with no credentials or
`srcdoc`. The frame can have zero height while the inbox is selected. Its presence
and dimensions do not prove role; the visible, enabled Chat navigation does.
No iframe content is read. The Google account-management menu lives in a separate
cross-origin frame and is not part of this verifier.

Only the positive self-only Chat feature derives canonical role `account
user`; the role is not claimed to be literal page text. Workspace comes from the
real title, not an email-domain inference or operator-attestation fallback. Nested
accessible controls are accepted only when they form one containing control with
identical labels; duplicate siblings or contradictory labels reject. Message
content cannot supply account indicators. Missing or changed app navigation,
untrusted frame metadata, delegated/unknown identity, different tenant and
unsupported title formats all withhold the page body. Chat-disabled accounts
remain unverified by this narrow mode. It does not click, fetch another identity
page or evaluate across origins, and needs no open account menu.

The verifier mode participates in existing route and source-authority hashes;
retained observations already carry these hashes, canonical binding revision,
actual identity tuple and native target. The fixed method distinguishes the
title-derived workspace and Chat-feature-derived role without changing old
event schemas or generic historical hashes. Hosted checks cover synthetic DOM,
the existing process capture/replay boundary and a disposable native Chrome run
(`check-workstation-work-gateway.mjs <pinned-source> --browser-gmail`). Synthetic
markup does not prove live Gmail selectors: activation still requires the sole
operator to verify the actual intended provider chrome and installed guard produce
a terminal observation. Existing policy disablement remains recovery.

The source coordinate is CoS ownership plus canonical binding, actual account and
workspace, resource ID and authorized path-prefix hash. Exact URL/tab are separate
route evidence. A verified replacement tab or URL inside the same account/resource
scope is refreshed through the same revision-checked provisioning command; it does
not require another source grant or erase historical observation provenance. This
first slice still requires operator-verified tab refresh; automatic verified tab
creation/navigation is not yet implemented. Account/resource/audience changes
invalidate the corresponding authority. Keep explicit client restrictions.

## Runtime evidence and recovery

The inventory's tool rows describe OpenClaw dynamic tools. Codex owns its base
shell/file surface separately; agent dynamic allowlists cannot prove that surface
absent. Finite per-run `toolsAllow`, native hooks, sandbox/root/network controls
and actual invocation evidence govern it. Do not replace these controls with a
duplicate exec wrapper or use shell access to bypass source/account restrictions.

Acceptance covers authenticated intake and encrypted replay/audience checks in the
real application, plus the installed native tool path through a disposable Chrome
DOM and synthetic extension transport in hosted CI. That proves composition without
claiming a live household profile, grant or outcome. The live affected-workflow
check requires a genuine member read request, actual matching destination identity,
a terminal retained observation, bounded complete native result and authorized
same-origin reply. Health and a configured extension alone are insufficient.

On regression, provision the next policy revision with `enabled: false` and prepare
and apply a fresh native patch with `--disable`. Disabled policy immediately denies
new reads and disclosure, even when canonical evidence is stale. Preserve the
schema-aware runtime once browser events have been appended; revoke this feature
instead of rolling history back into an older reader lacking these event schemas.
Record the release, expected/observed behavior, redacted error and restored health.


Current member instructions are compiled in authority/account/work priority order.
The six background programs remain byte-for-byte in the installed household skill's
`programs/` references and are loaded only for their matching trigger. The supported
updater measures the entire composed AGENTS file, including retained private layers,
against a conservative 20,000-byte ceiling before maintenance; it never truncates
or deletes private overlays. A release-local layout marker keeps legacy compilation
and exact byte recovery compatible. Dynamic-tool policy describes neither Codex's
native shell/files nor ambient MCP/apps connectors; those require separate runtime
and operation evidence and actual account verification before private operations.
