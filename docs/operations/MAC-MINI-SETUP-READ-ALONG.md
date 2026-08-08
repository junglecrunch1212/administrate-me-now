# Mac mini setup read-along

Use this page beside the Terminal from the moment you obtain the reviewed
release through the first seven days of operation. It is written for an
operator who does not work with code. Do not skip the stop signs.

This runbook installs one AdministrateMe Python service and one exactly pinned
OpenClaw Gateway on a dedicated Apple-silicon Mac mini. Both listen only on the
Mac itself. Tailscale Serve is the only supported private web entry point.

## Release gate: use the exact reviewed `main` commit

This guide is for the reviewed AdministrateMe v2 release on GitHub `main`,
including the primary Google Calendar, Apple Calendar, Apple Reminders, and
Apple Notes work. The final release reviewer gives you one full 40-character
Git SHA, called `APPROVED_SHA` below, and confirms that GitHub checks passed for
that exact revision.

Do not copy a SHA from an old issue, this document, or a screenshot. Use the
SHA in the final release handoff that sent you to this page. Do not accept
“latest,” a branch name, a ZIP file, or a short seven-character SHA. Stage 4
will stop if the GitHub `main` checkout does not exactly match it.

## Decide what you are installing today

There are two different installations. Do not combine their identities:

1. **CoS Mac Mini central service — install this first.** It owns the encrypted
   event log, projections, member portal, OpenClaw, governance, Tailscale Serve,
   and central Google adapters. Use Stages 1–9 and 11–12 on the CoS Mac Mini.
2. **One Apple member bridge per member — install only on a suitable member
   Mac.** It reads and writes that member's iCloud Calendar, Reminders, and
   Notes through macOS. An iPhone is a primary surface but cannot run the bridge
   daemon. Use Stage 10 later on a Mac signed into that member's iCloud account.

For the first CoS Mac Mini install, it is safe to answer **No** when asked to
prepare member bridges if those member Macs are not sitting beside you and
ready. Do not sign the central service account into multiple people's iCloud
accounts. Do not duplicate Google-backed events through Apple Calendar:
AdministrateMe owns those calendars through the Google Calendar API even when
they appear inside Calendar on an iPhone.

The central wizard intentionally does not request Google OAuth credentials or
macOS privacy grants. First prove the credential-free central installation in
observation mode. Live Google authorization and Apple bridge consent are
separate provider gates after the base system is healthy.

## What you need before starting

Have these ready on paper or in a non-shared password manager:

- the approved 40-character Git SHA;
- the Mac administrator password (needed only for Apple, Homebrew, and
  Tailscale system installation prompts);
- a separate standard macOS account that will run AdministrateMe;
- each member’s exact Tailscale **user login**, normally their sign-in email;
- a household display name for the assistant;
- the number of principals, children, helpers, and ambient/context-only people;
- a model-provider account and its credential, to be entered later only in
  OpenClaw’s Terminal flow or the provider’s own page;
- at least 10 GiB free, plus an external location for encrypted backups;
- a keyboard and display for first setup and post-reboot recovery.

Never put an API key, password, OAuth code, Gateway token, recovery key, or
provider response in this guide, a GitHub issue, the member portal, an
AdministrateMe answer file, or a support bundle.

## Stage 1: prepare the Mac and its account

Do this while signed into a separate Mac administrator account.

1. Click **Apple menu → System Settings → General → Software Update**. Click
   **Update Now** or **Upgrade Now**, let it finish, and restart the Mac.
2. Return to **System Settings → Users & Groups → Add User…**. Authenticate as
   the Mac administrator. Set **New Account** to **Standard**, use a name such
   as `AdministrateMe Service`, choose a strong unique login password, and
   click **Create User**. Do not change it to Administrator.
3. Open **System Settings → Privacy & Security → FileVault**. Click **Turn On**.
   Store the recovery key somewhere other than this Mac and wait until System
   Settings says FileVault is on. If **Enable Users** appears, click it, select
   the new AdministrateMe service user, enter that user's login password, click
   **OK**, then click **Continue**. That user must be able to unlock the disk
   after a restart.
4. Open **System Settings → Energy**. Enable the option that prevents automatic
   sleeping while the display is off/on power. Also enable **Start up
   automatically after a power failure**. Display sleep is fine. In **Users &
   Groups → Login Options**, leave automatic login off.
5. Understand the reboot behavior: these are user LaunchAgents. After a reboot,
   FileVault must be unlocked and the dedicated user must log in before the two
   services can run. Locking the screen is fine; logging out stops availability.
6. Click **Apple menu → Log Out**. Log into the new dedicated standard account.
   Stay in this account for every central-install command below.

Open **Finder → Applications → Utilities → Terminal**. Keep this page open on
another device. Copy only one code block at a time into Terminal, press
**Return**, and wait for the `%` prompt to come back before continuing.

```bash
date
uname -m
sw_vers -productVersion
df -h "$HOME"
```

Expected: the time is correct, architecture is `arm64`, macOS major is 14, 15,
or 26, and available disk space is comfortably above 10 GiB. Stop if any of
those is false.

Install Apple’s Command Line Tools:

```bash
xcode-select --install
```

Accept Apple’s dialog and wait. If it says the tools are already installed,
that is okay. If a window opens, click **Install**, accept Apple's license, and
wait for it to finish. Verify:

```bash
xcode-select -p
git --version
```

## Stage 2: install Homebrew correctly

Open [brew.sh](https://brew.sh/) and compare its displayed install command with
the command below. They must match. Then paste it into Terminal:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

The script explains its changes and pauses. Read the summary, press **Return**
to continue, and enter the separate administrator password if macOS asks. The
password will not appear while you type. Homebrew must install to
`/opt/homebrew` on Apple silicon.

After Homebrew finishes, it prints a `brew shellenv` command. Run it. For the
standard Apple-silicon location, the command is:

```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> "$HOME/.zprofile"
eval "$(/opt/homebrew/bin/brew shellenv)"
```

Verify:

```bash
which brew
brew --version
brew doctor
```

Expected: `which brew` prints `/opt/homebrew/bin/brew`. Warnings from
`brew doctor` need review; do not continue past an error. The bootstrap requires
Homebrew `>=6,<7` because Homebrew 5 cannot consume the current Homebrew Core
formula metadata. New releases install Python 3.14 and Node 24 side by side.
An existing 3.11/22 service is not changed until a reviewed candidate passes
the ADR-0006 migration gate and you explicitly authorize the cutover.

## Stage 3: install Tailscale before AdministrateMe

Install the **Standalone** macOS app from
[Tailscale’s official package](https://tailscale.com/docs/install/mac). Do not
also install the App Store edition; the two variants conflict.

1. On the Tailscale page, choose the **Standalone** download. Open the
   downloaded installer, follow its prompts, then open **Applications →
   Tailscale**.
2. When macOS reports a blocked system extension, click **Open System
   Settings**, enable Tailscale under **Privacy & Security**, authenticate with
   the administrator password, and click **Allow** on the VPN-configuration
   dialog.
3. Click the Tailscale menu-bar icon, choose **Log in**, and finish sign-in in
   the browser. Join the intended household tailnet. The server is its own
   Tailscale device; do not copy a member bridge key or enrollment package to
   make it the server identity.
4. In **Tailscale → Settings → CLI integration**, select **Show me how → Install
   Now** and approve the bounded administrator prompt.
5. In the Tailscale admin console, restrict access to the future server to the
   intended household users. Device sharing also receives identity headers, so
   do not share this server node casually.
6. Leave automatic security updates enabled for the Standalone app. An update
   must not be confused with permission to change the separately pinned
   OpenClaw release.

Verify:

```bash
tailscale version
tailscale status
tailscale serve status
```

`tailscale status` must show the Mac in the expected tailnet. An empty Serve
configuration is normal. Do not enable Funnel. The member devices that will
open the portal must be signed in as users; requests originating from tagged
devices do not receive `Tailscale-User-Login`, which AdministrateMe requires.

Write down each member’s exact Tailscale login. A device name such as
`alice-iphone`, a `100.x.y.z` address, and the server’s `.ts.net` name are not
member logins.

## Stage 4: obtain the one approved GitHub version

Replace the example text below with the full SHA supplied by the reviewer. Do
not include angle brackets.

```bash
export APPROVED_SHA="paste-the-full-40-character-merged-sha-here"
mkdir -p "$HOME/AdministrateMe"
cd "$HOME/AdministrateMe"
git clone https://github.com/junglecrunch1212/administrate-me-now.git release
cd release
git switch main
git pull --ff-only
```

Now run the four release-boundary checks:

```bash
git status --short
git branch --show-current
git rev-parse HEAD
test "$(git rev-parse HEAD)" = "$APPROVED_SHA" && echo "APPROVED SHA MATCHES"
```

Expected:

- `git status --short` prints nothing;
- the branch is `main`;
- `git rev-parse HEAD` is exactly the reviewer’s SHA;
- the last command prints `APPROVED SHA MATCHES`.

Stop if any expectation fails. Do not run `git reset`, edit a file, or install
from the release-candidate branch on this Mac.

## Stage 5: credential-free lab rehearsal

The word “dry-run” is deliberately not used here. The old `--dry-run` option
was an internal test-harness switch and was not a non-mutating Mac rehearsal.
The installer now refuses it outside the test harness.

This lab instance uses `.invalid` identities and synthetic household data. It
still installs the locked software and creates temporary user services, so run
it only after the release check and stop it before the real install:

```bash
./bootstrap/install.sh \
  --fixture two-adult-one-child \
  --instance-root "$HOME/Library/Application Support/AdministrateMe Lab" \
  --expected-commit "$APPROVED_SHA"
```

This can take 15–30 minutes on the first run. OpenClaw `2026.7.2` is not
published as an npm release, so the installer downloads the reviewed GitHub
commit `ce67ffb70e83e5ebb56ea02e814e577153f84f61` as a roughly 95 MB source
archive, verifies its pinned SHA-256, installs its exact pnpm lock, and builds
the UI and CLI locally. It never falls back to npm `latest`, a beta, or a
floating branch. Normal output includes package downloads and TypeScript/UI
builds. Poetry runs from a separate instance-local tooling environment so its
`--sync` operation cannot delete the installer that is performing the sync.
The installer also explicitly acknowledges trust for the local plugin path;
that acknowledgment is bounded to the plugin inside the exact reviewed release
archive. Lab and production OpenClaw state are isolated beneath their respective
instance roots, so the rehearsal cannot leave a plugin/config path in the real
instance. The installer writes and validates the explicit local Gateway mode
required by the pinned build, enables and additively allows the pinned bundled
`llm-task` tool, and proves that tool registered before it starts services. It
preserves unrelated OpenClaw tool-policy entries. On a resume or upgrade,
it removes stale plugin registration metadata while preserving every immutable
release directory, then links only the current reviewed release. It must not ask
for a household or model-provider credential.

Check and stop the lab:

```bash
./bootstrap/doctor.sh \
  --instance-root "$HOME/Library/Application Support/AdministrateMe Lab"
./bootstrap/stop.sh \
  --instance-root "$HOME/Library/Application Support/AdministrateMe Lab"
```

Expected Doctor result: top-level `"status": "healthy"`, ten complete
sections, thirteen healthy projection stores/checkpoints, observation mode,
`plugin_compatibility.evidence.discovered: true`,
`plugin_compatibility.evidence.required_llm_tool.ok: true`, five reconciled
OpenClaw cron jobs, and no live-provider claim. Expected stop result: both
services `stopped`.

If this fails, stop. The family instance is still untouched. Use the
troubleshooting section at the end of this guide.

## Stage 6: prepare your wizard answers

The Terminal asks all questions first, then runs the ten implementation
sections. That is why the question order differs from the section numbers.
Nothing in the question phase should ask for a secret.

Use these role meanings:

| Role | Choose it for | Authority consequence |
|---|---|---|
| `principal` | An adult who administers household truth | Can use principal-only review and view-as controls |
| `child` | A restricted child member | Child-safe surfaces; chat requires a second confirmation |
| `helper` | A limited caregiver/helper | No principal authority and no interactive channel mapping |
| `ambient` | A person represented only as context | No interactive access |

Profiles are assigned automatically in this first-run wizard: principals and
helpers receive `adhd_executive`, children receive `kid_scoreboard`, and
ambient entries receive `ambient_entity`. The wizard does not offer all five
profiles interactively yet.

For OpenClaw channel actors, do not guess. A channel actor is an exact
`channel | account | sender` tuple from OpenClaw, not the Tailscale login. It is
safe to skip actor pairing on the first run as long as at least one principal
keeps private member-portal chat enabled. The Tailscale portal and OpenClaw
model chat can then be proven before channel messages receive household
authority. There is not yet a supported post-bootstrap actor-pairing command,
so treat skipped channels as off until that reconfiguration path is reviewed.

The “data-entry follow-up” questions do not create household facts. A Yes
records a visible task to enter reviewed data after bootstrap. A No records a
task to review or dismiss the category. Neither is falsely reported as seeded
data.

## Stage 7: run the real wizard with this page beside it

Start:

```bash
./bootstrap/install.sh --expected-commit "$APPROVED_SHA"
```

For a two-principal household in which both principals use Apple devices but are
installing only the CoS Mac Mini today, use this first-install pattern:

- answer **Yes** to Apple member devices;
- enter the detected `America/New_York` timezone if it is correct;
- enroll each real household person once; choose `principal` for both adults
  if you both administer household truth;
- enter each person's exact Tailscale login email when known; leave it blank
  only to defer both portal chat and member-bridge enrollment for that person;
- enable private member-portal chat for both principals;
- answer **No** to OpenClaw actor pairing unless you can see the exact tuple;
- answer **Yes** to the calendar/shared-list follow-ups you intend to complete;
- answer **No** to each member-bridge package unless a separate Mac signed into
  that person's iCloud and Tailscale accounts is ready within the next hour;
- confirm observation mode.

Answer for the actual household if there are children, helpers, or ambient
people; the pattern above is not permission to omit them.

Use this parallel prompt guide:

| Terminal prompt | Recommended first-run answer | What it actually does |
|---|---|---|
| What should the household call the assistant? | A display name only | Compiles the quiet-assistant persona; it cannot grant authority |
| Does this household use Apple member devices? | Yes only if Apple bridges are intended | Controls per-member bridge package preparation |
| How many household members? | The real count, 1–20 | Requires at least one principal |
| Member label | A recognizable display label | Stored in encrypted bootstrap state and runtime mapping |
| Role | One of the four exact role words above | Sets server-side authorization |
| Verified Tailscale login | Exact user login/email, or blank to defer access | Maps Serve’s identity header to this member; a blank identity keeps portal chat and bridge enrollment disabled until it is added later |
| Record principals as coparents? | Yes only when true | Creates explicit relationship records |
| Pair an OpenClaw actor? | No unless the exact tuple is already known | Unmapped channel senders receive no household authority |
| Enable member-portal chat? | Yes for each intended adult | Uses an isolated signed session; does not enable child chat automatically |
| Confirm child chat policy? | No until separately reviewed | A second child-specific gate |
| Prepare Plaid sandbox Link? | No unless testing sandbox now | Live banking is never enabled here |
| Create after-bootstrap data-entry follow-up? | Yes for categories you intend to enter | Creates follow-ups, not facts |
| Prepare integration pairing? | Yes for calendars/shared lists you intend to connect | Records a follow-up only; it does not grant macOS/OAuth access or activate Google Calendar |
| Prepare a separate member-bridge package? | Yes only for a member who has their own Mac ready now | Stages a member-bound, 30-minute enrollment; Doctor remains pending until it is accepted or revoked |
| Allow Obsidian for adult bridges? | Only if adult vault ingestion is intended | Child packages still deny Obsidian |
| Confirm observation mode? | Yes, after reading the prompt | Installation stops unless the minimum trust workflow is accepted |

After the answers, the implementation sections run. If you selected no member
bridges, the final JSON should say `"status": "healthy"`. If you selected one
or more, it should say `"status": "bridge_enrollment_required"`; that is an
expected handoff, not a failed install. Both central services are running, but
Doctor intentionally stays red until every selected bridge is finalized or
explicitly revoked. Any plain `"status": "failed"` is a real stop condition.
A model provider and live adapters can still truthfully say
`optional_unconfigured`.

If Terminal closes or a section fails, do not delete anything. Open a new
Terminal, return to the checkout, and rerun the same command:

```bash
cd "$HOME/AdministrateMe/release"
./bootstrap/install.sh --expected-commit "$APPROVED_SHA"
```

The resumed installer now reuses the original encrypted answer set and says so
explicitly; it must not ask you to recreate the household with new random IDs.
Supplying a different answer file to an existing instance is refused.

## Stage 8: activate OpenClaw on the server

Run this only in the dedicated Mac’s Terminal:

```bash
./bootstrap/activate-openclaw.sh
```

The flow may open provider-owned sign-in. Enter a model credential only in the
OpenClaw prompt or provider page. Select a local Gateway and only the channels
you intend to test. The activation command then:

- pins and verifies OpenClaw `2026.7.2`;
- points OpenClaw at the compiled per-instance AdministrateMe workspace;
- enables the pinned bundled `llm-task`, adds it without removing unrelated
  tool-policy entries, and verifies runtime registration;
- validates OpenClaw configuration;
- runs `openclaw models status --probe`;
- reconciles exactly five owned `administrateme-*` cron jobs in the household
  IANA timezone, leaving unrelated OpenClaw jobs untouched;
- writes only a non-secret “probe passed” marker;
- restarts both user services.

Run:

```bash
./bootstrap/status.sh
./bootstrap/doctor.sh
openclaw gateway status --require-rpc
openclaw channels status --probe
```

Do not run `openclaw update`; the tested pin is intentional. Do not also run
`openclaw onboard --install-daemon` or `openclaw gateway install`; AdministrateMe
already owns the one OpenClaw user LaunchAgent.

Do not manually add or rename these jobs. Bootstrap and every safe `start`
reconcile the two versioned declarations, reject declaration drift or duplicate
AdministrateMe ownership, update changed jobs, remove only previously recorded
stale AdministrateMe jobs, verify the result, and roll back a partial failure.
The five jobs use isolated sessions and `no-deliver`; channel delivery stays an
OpenClaw configuration choice rather than a bootstrap side effect.

## Stage 9: expose only the portal with Tailscale Serve

With both services running (Doctor may still show only the expected bridge
enrollment follow-up):

```bash
tailscale serve --bg http://127.0.0.1:3330
tailscale serve status --json
```

This syntax was revalidated against the current official
[Tailscale Serve CLI reference](https://tailscale.com/docs/reference/tailscale-cli/serve).

Tailscale may print a one-time HTTPS consent URL. Open it, approve HTTPS for the
tailnet, then repeat the command. In the resulting JSON, confirm the backend is
exactly `http://127.0.0.1:3330` and there is no Funnel configuration.

Open the printed private `https://…ts.net` address from one enrolled member’s
Tailscale-connected device. Never browse directly to the Mac’s LAN IP or port
3330. Tailscale Serve removes client-supplied identity headers, adds the real
`Tailscale-User-Login`, and proxies to the loopback-only runtime.

Verify separately for every member:

1. An enrolled principal sees only their authorized portal.
2. A second adult cannot see the first adult’s chat history.
3. A child sees child-safe surfaces and no adult-only data.
4. A disabled or unknown login receives no household view.
5. Ordinary chat creates no task. An explicit proposal still requires the
   review/approval path and is suppressed by observation mode.
6. Stop and start preserve isolation:

```bash
./bootstrap/stop.sh
./bootstrap/start.sh
./bootstrap/doctor.sh
```

Then perform the disposable-Mac accessibility checkpoint in Safari. Use only
the keyboard to reach every control and confirm the visible focus follows the
page order. Turn on VoiceOver with Command-F5 and confirm controls have useful
names, status/error changes are announced, headings provide navigation, and no
member's private content is read after switching identities. Repeat at 200%
browser zoom and with Reduce Motion enabled. Record the macOS/Safari versions,
each result, and any failure; a failed checkpoint blocks family rollout.

Emergency private-edge removal:

```bash
tailscale serve reset
./bootstrap/stop.sh
```

At this point the CoS Mac Mini central installation is complete and still in
observation mode. If no separate member Mac is ready, skip Stage 10 for now and
continue with Stage 11. A staged bridge is not required for the central portal.

## Stage 10: enroll each selected member bridge

Do this later on one suitable member Mac at a time while central observation
mode remains on. Do **not** deliberately deny the first privacy prompt; a
denial must be repaired manually in System Settings and adds no useful safety
to the installation.

The central portal does not require a member bridge. For every bridge you chose
in Stage 7, complete this one-member-at-a-time flow. Never reuse a package,
code, macOS login, Mac, or Tailscale identity for a different member. One
macOS login may host only one member bridge.

On the central Mac, display the selected member IDs:

```bash
./bootstrap/bridge-status.sh
export CENTRAL_URL="https://paste-the-private-server-name.ts.net"
```

`CENTRAL_URL` must be the private HTTPS name printed by Tailscale Serve, with
no path or port. Export one package, replacing the member ID and filename:

```bash
./bootstrap/bridge-export.sh \
  member_replace_me \
  "$HOME/Desktop/member-replace-me.adminmebridge" \
  --central-url "$CENTRAL_URL"
```

The command prints a one-time transfer code that is not saved. The package and
code expire after 30 minutes. Send the `.adminmebridge` file with AirDrop or an
equivalent private file transfer. Give the code separately, for example by
voice or a shared password-manager item. Do not paste either into chat.

On that member's Apple-silicon Mac:

1. Log into the macOS account that is signed into **that member's** iCloud
   account. Open Calendar, Reminders, and Notes once and verify the intended
   iCloud data is visible.
2. In **Calendar → Settings → General → Default Calendar**, select an iCloud
   calendar that AdministrateMe may organize. Do not select a Google calendar;
   Google-backed calendars are owned by the central Google adapter.
3. In Reminders, verify the default list is an iCloud list suitable for new
   AdministrateMe reminders. Existing accessible lists will still be observed.
4. Turn on FileVault, install Homebrew, install the Standalone Tailscale app
   plus CLI integration, and sign in as the exact Tailscale user printed by
   central `bridge-status`.
5. Repeat Stage 4 on this Mac to clone the exact approved `main` SHA.

In that clean checkout run:

```bash
export APPROVED_SHA="paste-the-same-full-40-character-sha"
bash bootstrap/install-member-bridge.sh \
  --expected-commit "$APPROVED_SHA" \
  "$HOME/Downloads/member-replace-me.adminmebridge"
```

Paste the one-time code only into the hidden Terminal prompt. The installer
checks the release SHA and Tailscale self identity before writing anything,
creates a separate release/venv/config, installs a per-user LaunchAgent,
submits an owner-scoped `bridge.enrolled` proof through Tailscale Serve, and
deletes the transferred package after success. Check it locally:

The first start can display three macOS consent dialogs. Click these exact
choices:

1. For Calendar, click **Allow Full Access**.
2. For Reminders, click **Allow Full Access**.
3. When AdministrateMe/Python/osascript asks to control Notes, click **Allow**.

If a dialog does not appear within two minutes, open **Apple menu → System
Settings → Privacy & Security**. Check **Calendars**, **Reminders**, and
**Automation**. Enable the entry associated with the AdministrateMe bridge,
Python, Terminal, or `osascript`; under Automation, enable **Notes**. Then run:

```bash
launchctl kickstart -k "gui/$(id -u)/com.administrateme.member-bridge"
```

Do not grant Contacts, Photos, microphone, Full Disk Access, or Accessibility
unless a later separately reviewed connector explicitly requires it. Now check
the bridge:

```bash
bash bootstrap/member-bridge.sh bridge-local-status
launchctl print "gui/$(id -u)/com.administrateme.member-bridge" >/dev/null \
  && echo "MEMBER BRIDGE IS RUNNING"
```

Expected: JSON top-level status `healthy`, service `running`, the correct
member ID, all three primary Apple packs under `enabled_packs`, and the final
line `MEMBER BRIDGE IS RUNNING`. Leave the member logged in; screen lock is
fine, logout stops the LaunchAgent.

Back on the central Mac, finalize only after the local status says `healthy`:

```bash
./bootstrap/bridge-finalize.sh member_replace_me
./bootstrap/bridge-status.sh
```

Finalization finds the real encrypted event-log proof, marks that member
active, deletes the central transfer copy and one-time Keychain material, and
restarts the central services. Repeat for each selected member. If a selected
bridge will not be installed, explicitly close it instead of leaving it
pending:

```bash
./bootstrap/bridge-revoke.sh member_replace_me
```

To remove an installed bridge, disable it on the member Mac first, then revoke
it centrally. Central revocation alone still blocks future bridge ingest if
the member Mac is lost:

```bash
bash bootstrap/member-bridge.sh bridge-disable
# Then, on the central Mac:
./bootstrap/bridge-revoke.sh member_replace_me
```

Run `./bootstrap/doctor.sh` on the central Mac after all selected records show
`active` or `revoked`. It must now be healthy. A successful enrollment proves
the private transport and member binding and enables that member's Apple
Calendar, Apple Reminders, and Apple Notes packs. Keep observation mode enabled
until the initial counts, account ownership, recurrence, and location alarms
have been reviewed. Do not hand-edit bridge YAML to bypass enrollment.

Gmail, Plaid, Home Assistant, Google Calendar/Tasks, and Apple permissions are
also live-provider gates, not part of credential-free Doctor proof. Plaid must
remain sandbox-only during bootstrap.

### Google calendars are paired centrally, not through the Apple bridge

The first-run wizard records a calendar-pairing follow-up but does not ask for
or create personal Google OAuth credentials. Therefore a healthy first install
may truthfully report Google Calendar as `optional_unconfigured`. Do not repair
that by selecting a Google calendar as the Apple bridge's default calendar, by
pasting OAuth JSON into `adapters.json`, or by putting a token in `.env`.

The production runtime now loads configured Google Calendar adapter instances,
one member/account/calendar at a time, but live OAuth enrollment remains a
separate provider-consent operation after the credential-free base and backup
are proven. Keep using the iPhone Google Calendar account normally during the
initial observation period; AdministrateMe will not create a duplicate
EventKit copy.

## Stage 11: backup, diagnostic, and recovery proof

Create the first encrypted backup:

```bash
./bootstrap/backup.sh
```

Copy the printed `.ambackup` path to your operator notes. The backup does not
contain the Keychain values, so a second recovery plan must protect the Mac and
its FileVault/Keychain access.

Create the safe diagnostic:

```bash
./bootstrap/doctor.sh \
  --export "$HOME/Desktop/administrateme-diagnostic.json"
```

Open it in TextEdit. It should say `"safe_to_attach": true`. It contains
operational names and times, so review it anyway. Do not attach raw logs,
Keychain exports, browser storage, `.openclaw` state, bridge YAML, event stores,
or workbooks.

Restore rehearsal uses only the exact path printed by backup:

```bash
./bootstrap/restore.sh "/absolute/path/to/the/printed-file.ambackup"
./bootstrap/doctor.sh
```

Restore stages and authenticates the archive, checks SQLCipher, rebuilds all
fourteen registered projections (thirteen domain truth plus Triage), preserves rollback state, and returns the old state if
post-restore Doctor fails. Never unzip or edit a backup manually.

## Stage 12: the five-to-seven-day trust period

Keep observation mode on. Every day:

```bash
cd "$HOME/AdministrateMe/release"
./bootstrap/status.sh
./bootstrap/doctor.sh --no-synthetic-proof
```

Also check Activity Monitor for persistent CPU/memory growth, available disk
space, Tailscale access, member isolation, suppressed external actions, and the
latest encrypted backup. Use each of the eight deterministic portal surfaces.

Five elapsed days are only a minimum clock. Do not enable side effects until
the seven-day review is complete, every identity/privacy finding is resolved,
backup and Doctor pass, connector watermarks are understood, and a principal
explicitly approves trust. There is currently no operator shortcut in this
guide for changing trust state.

## Live troubleshooting: use this exact loop

When a command fails:

1. Stop typing. Do not rerun it more than once.
2. Record the command, the first error line, and whether the Terminal prompt
   returned.
3. If any service may be running, use:

```bash
./bootstrap/status.sh
./bootstrap/doctor.sh \
  --export "$HOME/Desktop/administrateme-diagnostic.json"
```

4. If the diagnostic says `safe_to_attach: true`, attach that JSON in this
   Codex task and say which numbered stage failed. Do not attach anything else.
5. If Doctor cannot export, share only the error **type and message** shown by
   its JSON. Do not paste raw logs until a reviewer gives a narrow redaction
   command.
6. For immediate containment:

```bash
tailscale serve reset
./bootstrap/stop.sh
```

These commands preserve data, backups, Keychain items, and enrollment state.

### Fast symptom guide

| Symptom | Likely cause | Safe response |
|---|---|---|
| `brew: command not found` | Homebrew shell environment was not loaded | Run `eval "$(/opt/homebrew/bin/brew shellenv)"`, verify `which brew`, then retry once |
| Unsupported Homebrew/Python/Node/npm | A floating system tool is being selected | Do not upgrade OpenClaw; rerun the reviewed installer so its exact formula paths are used |
| Homebrew reports `unknown install step` | Homebrew 5 is trying to read current Homebrew Core metadata | Update Homebrew to the supported `>=6,<7` release, verify `brew --version`, then rerun the reviewed installer |
| `openclaw: command not found` | The verified source build or instance-local wrapper is missing | Rerun the reviewed installer once. Do not run `npm install -g openclaw` or substitute `latest`; attach Doctor output if it persists |
| OpenClaw archive checksum/source provenance failure | Download was incomplete, changed, or an unreviewed executable is first on PATH | Stop. Keep the diagnostic, verify network/disk, and rerun the exact installer; never bypass the SHA-256 check |
| Checkout has local changes | File edit, generated file, or wrong clone | Do not clean it manually on the family Mac. Rename the checkout and clone the approved `main` again after review |
| Expected commit mismatch | `main` is not at the reviewed release | Stop. Ask the reviewer for the full merged SHA and GitHub check status |
| FileVault check fails | FileVault is off or encryption is not complete | Finish FileVault setup from the administrator account, reboot, then retry |
| Keychain item unavailable | Dedicated login keychain is locked or wrong user is running | Log into the dedicated account locally and unlock its login Keychain; never recreate values in `.env` |
| Services stop after reboot | User LaunchAgents have no logged-in GUI session | Unlock FileVault and log into the dedicated account; do not enable automatic login |
| Port 3330 or 18789 is busy | Another runtime/Gateway is installed | Stop. Do not kill an unknown process. Attach Doctor output and identify the process with `lsof -nP -iTCP:3330 -sTCP:LISTEN` and the same command for 18789 |
| `no_model`, `missing_credential`, auth, billing, or rate-limit probe | Provider setup is incomplete or unavailable | Return to `./bootstrap/activate-openclaw.sh`; fix only in OpenClaw/provider UI, then probe again |
| Plugin compatibility mismatch | OpenClaw was upgraded or a wrong plugin is linked | Do not use `latest`. Restore the reviewed pin through the installer or rollback |
| Doctor says plugin `discovered: false` or cron reports non-JSON output | The plugin did not register its declared dashboard RPCs during pinned-runtime discovery | Stop services, preserve the diagnostic, and reinstall the reviewed release containing the discovery-registration repair; do not hand-edit `.openclaw` |
| Doctor says required `llm-task` is disabled, not allowed, or not registered | The pinned bundled language tool was not reconciled, so message NLP cannot run even if the Gateway is healthy | Preserve the diagnostic and rerun only the reviewed installer/updater; never hand-edit OpenClaw JSON or broaden the global tool allowlist |
| Tailscale URL gives 502/unavailable | Runtime is stopped or Serve points to the wrong backend | Run status/Doctor; verify Serve targets only `127.0.0.1:3330` |
| Portal says missing/unknown Tailscale identity | Wrong login was entered, direct LAN access, or requesting device is tagged | Use the private Serve URL from a user-owned Tailscale device and compare the exact login; do not spoof the header |
| One member sees another member’s data | Identity/scope failure | Immediately reset Serve and stop services. Preserve the diagnostic and treat it as a release-blocking security incident |
| Bootstrap reports resumable | A section was interrupted safely | Rerun the exact installer command; it must announce reuse of encrypted answers |
| Bootstrap reports `bridge_enrollment_required` | One or more intentionally selected member packages are staged | Continue Stages 8–10; export, accept, and finalize or explicitly revoke each listed member |
| Bridge package expired or code fails | More than 30 minutes elapsed, the wrong code was used, or the file/code do not match | Delete only the received transfer copy and export a new package centrally; never reuse a code |
| Bridge says different Tailscale identity | The member Mac is signed into another user | Stop. Sign into the exact intended Tailscale user or revoke the selection; do not edit the package or mapping |
| Bridge enrollment proof not found | The target could not reach the private Serve URL or ingest was denied | Keep the package if still present, run local bridge status and central Doctor, verify Serve/Tailscale identity, then retry once before expiry |
| Backup or restore fails | Keychain, path, disk, or archive authentication problem | Keep services stopped if the command did so; do not move/unpack the archive; attach the safe diagnostic |

## Before you leave the CoS Mac Mini

Read each line and check it only after you have seen the stated evidence:

- [ ] `git rev-parse HEAD` exactly equals the 40-character `APPROVED_SHA` from
  the release handoff, on branch `main`, with an empty `git status --short`.
- [ ] FileVault is on, automatic login is off, and the dedicated standard user
  is the logged-in service account.
- [ ] `./bootstrap/status.sh` reports both central user services running.
- [ ] `./bootstrap/doctor.sh` is healthy, or its only expected incomplete state
  is a member bridge you deliberately staged and will finalize/revoke.
- [ ] OpenClaw's model probe passed, Doctor reports the required `llm-task`
  enabled/allowed/runtime-registered, and no floating/latest OpenClaw install
  was added.
- [ ] `tailscale serve status --json` points only to
  `http://127.0.0.1:3330`, and Funnel is absent.
- [ ] Both principals tested the private portal from their own
  Tailscale-connected device and saw only your authorized view.
- [ ] The encrypted backup and safe diagnostic were created, and the backup was
  copied to its separate protected location.
- [ ] Observation mode is still active. No calendar, reminder, note, message,
  banking, or home-automation side effect was enabled merely to make Doctor
  look green.

## What “finished” means for this release

The Mac mini bootstrap is finished only when the approved merged SHA is
installed, Doctor and backup/restore pass, OpenClaw’s live model probe passes,
the workspace is active, Tailscale Serve is private, member isolation is
manually proven, and the trust period is complete.

The five cron standing orders and selected member-bridge LaunchAgents are part
of the verified bootstrap outcome. Individual live connector packs are
separate opt-in grants and are not made healthy merely by enrollment.
