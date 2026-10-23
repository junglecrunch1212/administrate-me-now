# Member Google connections

Settings → Connections → Add Google account connects an adult member's verified
Google account to Gmail, Calendar and Tasks. The member enters the intended email,
chooses personal/work provenance and separate Read/Write permissions, reviews them,
then authenticates with Google. The account returned by Google must match that
email and grant the exact selected scopes. The console distinguishes saved
permissions from each adapter's actual running/sync state. Contacts and Drive are
not offered because their production connection path is not implemented.

This MODERNIZES operator-only member enrollment while PRESERVING the ownership,
source policy, write governance and manual-forward intake contracts. Gmail reads
begin with new mail; this flow does not enable historical mailbox mining. Calendar
and Tasks use their existing discovery and sync. Write requires Read and permits
only existing governed operations; it is not approval for a send, disclosure or
provider action. Read off stops acquisition and CoS source context, while retaining
history and account ownership. Write off retains Read if selected. Broader access
always returns the owner to Google consent; reductions take effect without it.

Google account access does not grant the CoS private delegation or household
sharing. Settings links to How my CoS works for the member's own permission review.
Other adults cannot grant that authority or sign in on their behalf. Schedule
coordination continues through existing private/household scopes and operating
agreements; a private calendar does not automatically become another adult's data.

## Server setup

Use a Google **Web application** client, with the exact callback registered:
`https://<canonical-tailnet-dashboard>/api/member/v1/settings/google-connections/callback`.
An installed-app/localhost client cannot be substituted. Read the client from an
owner-only private JSON file and run the protected release's supported operation:

```sh
python -m bootstrap.member_google_oauth --client-file <private-web-client.json> \
  --dashboard-origin https://<canonical-tailnet-dashboard>
```

This stores the client in the instance's Keychain and a private reference/callback
in `config/member-google-oauth.json`. Use the supported runtime restart/activation
procedure to load it. Google project/API availability, consent-screen testing
users and verification requirements remain provider prerequisites. No member
refresh credential is obtained until that member consents. The client and each
new grant use separate Keychain entries; token/code/verifier data never enter
Settings storage, URLs returned after callback, Git or logs. OAuth state and PKCE
are instance-encrypted, member-bound, short-lived and single-use.

## Persistence and recovery

Owner-authenticated exact authorization is recorded in the encrypted event log
before publication. The member Google transition commits only that owner's Google
adapter rows/selections and the corresponding readiness binding, under the updater
lock and an authenticated crash-recovery journal. Disabled rows and original
attempts remain for attribution. Startup resolves an interrupted publication before
loading configuration. Policy and ownership caches update before new adapter
events. Unrelated adapters keep their running instances. Selected old instances
leave write routing before shutdown; an already in-flight provider action retains
its existing result/reconciliation rules and is never blindly retried.

An interrupted callback is reconciled by immutable request ID. An interrupted code
exchange requires fresh consent; it is not redeemed again. A committed permission
change is recovered from its durable configuration, never silently rolled back to
broader access. Settings reports provider startup failure separately from saved
permissions. On a connection regression, turn off the affected service in its
owner's Settings. Keep the schema-aware release for retained authorization history;
never edit installed configuration or copy someone else's credentials to repair it.

## Acceptance

The focused hosted suite is `scripts/acceptance/member-google-settings.sh` on
Linux and macOS. It covers real application composition with synthetic providers,
account/role isolation, exact OAuth grants, credential storage, stale/unknown
requests, permission reductions, source-context privacy, manual intake, recovery,
unrelated-instance preservation and rendered desktop/narrow Settings. These are
not actual member Google consent or physical-device proofs. Final live acceptance
uses the intended member's authenticated HTTPS console; each new account still
requires its owner's Google sign-in and permission review.
