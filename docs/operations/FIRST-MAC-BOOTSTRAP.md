# First reviewed Mac bootstrap

This is the exact first-install path for a non-programmer operator. Use it only
after the release-candidate pull request is independently reviewed and merged.
The family Mac receives merged, reviewed code only and is never used for
experimentation, development, or trying an unreviewed provider.

## 1. Record the review boundary

Ask the reviewer for the merged commit SHA. Write it down outside the Mac.
Do not continue with a branch name, draft pull request, or “latest” download.
The rollback boundary is the previous reviewed commit plus an encrypted backup.

On the Mac, open **Applications → Utilities → Terminal**, then later verify the
checkout with:

```bash
git status --short
git branch --show-current
git rev-parse HEAD
```

Expected: no status output, branch `main`, and the exact SHA supplied by the
reviewer.

## 2. Prepare the dedicated Mac account

1. Open **System Settings → Users & Groups → Add User**.
2. Create a dedicated **Standard** account named for AdministrateMe. Do not run
   the services as an administrator or root.
3. Open **System Settings → Privacy & Security → FileVault** and turn FileVault
   on. Store the recovery key outside the Mac.
4. Sign into the dedicated account and install all offered macOS updates.
5. Confirm at least 10 GiB is free in **System Settings → General → Storage**.
6. In Terminal, run `xcode-select --install`; accept Apple's Command Line Tools
   dialog and wait for it to finish.
7. Install Homebrew from its official signed installer, then run `brew --version`.
   The installer accepts Homebrew `>=4.6,<6` only.

Do not clone the repository or enter a household credential while another user
is screen-sharing or recording this account.

## 3. Install and verify private network access

1. Install the current stable standalone Tailscale macOS client from
   [Tailscale's official macOS instructions](https://tailscale.com/docs/install/mac).
2. Open Tailscale, approve the VPN configuration, and sign in with the dedicated
   server identity—not a household member's bridge identity.
3. In **Tailscale → Settings → CLI integration**, choose **Show me how → Install
   Now** and approve that bounded installation.
4. Run `tailscale status`. Confirm this Mac appears under the expected tailnet.
5. Do not enable Funnel. Funnel is public; AdministrateMe permits only private
   Tailscale Serve as its edge.

The current Serve syntax is documented by
[Tailscale](https://tailscale.com/docs/reference/tailscale-cli/serve). Access
rules still apply; review them before exposing the portal.

## 4. Obtain only the merged release

In Terminal:

```bash
mkdir -p "$HOME/AdministrateMe"
cd "$HOME/AdministrateMe"
git clone https://github.com/junglecrunch1212/administrate-me-now.git release
cd release
git switch main
git pull --ff-only
git status --short
git rev-parse HEAD
```

Stop if the SHA differs from the reviewer's SHA or `git status --short` prints
anything. Do not “fix” it on this Mac.

## 5. Rehearse without credentials

Run the disposable fixture first. This installs the locked dependencies and
pinned OpenClaw but uses synthetic household data and no provider credential:

```bash
./bootstrap/install.sh --fixture two-adult-one-child \
  --instance-root "$HOME/Library/Application Support/AdministrateMe Lab"
./bootstrap/doctor.sh \
  --instance-root "$HOME/Library/Application Support/AdministrateMe Lab"
./bootstrap/stop.sh \
  --instance-root "$HOME/Library/Application Support/AdministrateMe Lab"
```

Expected: final JSON contains `"status": "healthy"`, `"mode":
"observation"`, thirteen projections, all ten sections complete, and no live
provider claim. The final command must report both services stopped while
preserving the fixture instance. Stop if any command exits nonzero.

## 6. Run the real ten-section wizard

Start the reviewed installer:

```bash
./bootstrap/install.sh
```

It prints a read-only preflight result before mutating the instance. It installs
the locked Python environment and pinned OpenClaw/plugin, then journals these
ten sections in encrypted state:

1. **Environment preflight.** Expect native `arm64`, supported macOS, FileVault,
   non-root account, disk, Homebrew, Python 3.11, and Node checks to pass.
2. **Assistant persona.** Enter only the household's display name. Persona never
   changes member authority or model routing.
3. **Household composition.** Enter each member label and role. For every Apple
   bridge member, enter that member's verified Tailscale login. At least one
   principal is required.
4. **Profile assignment.** The wizard previews one of five behavior profiles per
   member. A profile changes presentation/assistance style, never permissions.
5. **Credential references.** The installer creates encryption, backup, plugin,
   and Gateway secrets in macOS Keychain. It records references only.
6. **Plaid sandbox.** Choose only sandbox preparation or disabled. Live banking
   is not permitted during bootstrap.
7. **Household seed data.** Choose which non-secret categories to seed. A skip
   becomes a follow-up, not a false completion.
8. **Channels and adapters.** Map every OpenClaw channel/account/actor to exactly
   one household member. Then answer the separate member-portal chat question
   for each adult. Child chat stays off unless it is enabled and independently
   confirmed for that child. Disabled members have no chat route.
9. **Observation briefing.** Confirm observation is on. The earliest trust date
   is five full days; seven is recommended. Time alone never turns it off.
10. **Member bridge enrollment.** Create one member-bound package per selected
    Apple member. Child packages exclude Obsidian and all packages deny
    cross-owner claims.

The wizard explicitly says not to enter secrets. **Never paste an API key,
Gateway token, password, OAuth code, hidden prompt, or provider response into
the member portal or any browser.** If a secret is requested anywhere except
the server-side OpenClaw configuration flow or the provider's own consent page,
cancel and stop.

Expected final output: `"status": "healthy"`, both named services running,
`"observation_mode": true`, and Doctor evidence. Provider/chat capability may
truthfully say unconfigured until the next step.

## 7. Configure OpenClaw only on the server

Run:

```bash
./bootstrap/activate-openclaw.sh
```

This opens OpenClaw's own model/channel configuration in Terminal, validates
the exact pinned configuration, runs `openclaw models status --probe`, writes a
non-secret “probe passed” marker, and restarts the two user services. Enter a
provider credential only into that OpenClaw terminal prompt or the provider's
own OAuth page. AdministrateMe never receives it.

Then run:

```bash
./bootstrap/doctor.sh
./bootstrap/status.sh
```

Expected: healthy services, pinned OpenClaw/plugin, chat transport configured,
model provider configured, loopback listeners only, and observation still on.

## 8. Expose only the member portal through Tailscale Serve

With Doctor healthy:

```bash
tailscale serve --bg http://127.0.0.1:3330
tailscale serve status --json
```

Follow the one-time Tailscale HTTPS enablement link if shown. Confirm the target
is exactly `http://127.0.0.1:3330`, the URL ends in your private `.ts.net`
tailnet name, and Funnel is absent. A principal should open that HTTPS URL from
an enrolled device. An unrecognized Tailscale login must receive no household
view.

To remove the edge immediately:

```bash
tailscale serve reset
./bootstrap/stop.sh
```

## 9. Verify member chat isolation

For each chat-enabled adult, separately:

1. Open the member portal through Tailscale and choose **Open household CoS**.
2. Send an ordinary message such as “Help me think through this afternoon.”
3. Confirm a reply appears and no task/commitment is added.
4. From a second adult's device, confirm the first adult's transcript and tool
   result are absent.
5. Ask for an explicit task proposal. Confirm it is private and requires the
   existing guarded proposal/approval path before any external effect.
6. Choose **Stop** during a response and confirm streaming cancels cleanly.
7. Run `./bootstrap/stop.sh`; confirm the portal is unavailable. Run
   `./bootstrap/start.sh`; confirm each adult returns only to their own session.

For a disabled adult or child, the chat control must be absent or state that it
is disabled. A shared-screen/device role receives no chat route. Never test
isolation using another person's credential.

## 10. Understand temporary quest privacy

Temporary quest/experiment surfaces are private saved lenses over existing
authorized reads. Creating or changing a lens does not create a Project,
workflow, scheduler, copied household fact, SQL query, code, template, or new
projection. The standard portal currently displays already-created authorized
quests; authoring/promotion remains a reviewed API/operator action.

Required behavior before using one with family data:

- Creation defaults to the creating member only.
- Another member cannot discover or evaluate it.
- Promotion requires explicit review and a named audience; child exposure has
  a second child-safe query/action check.
- Expiry removes only the lens. Archive removes it from active navigation.
- Replay/restart retains the underlying facts, history, thirteen projections,
  and eight standard surfaces.

If a request mentions raw logs, event logs, SQL, script, executable code, or a
template, it must fail closed.

## 11. Grant macOS permissions one integration at a time

On each member's own bridge Mac—not by sharing the server identity—enable only
the selected source. macOS may show **System Settings → Privacy & Security →
Calendars, Reminders, Contacts, Notes, or Microphone**. First deny the prompt
and confirm degraded status is honest; then grant the minimum required access.

Bridge activation is not an invitation to share accounts. Use the member-bound
enrollment package, copy `bridge/config.example.yaml` to
`~/.adminme-bridge/config.yaml`, fill only that member's generated IDs,
Tailscale login, server `.ts.net` endpoint, and selected roots, then set mode
`600`. Run the first check in the foreground:

```bash
poetry -C bridge install --only main --sync
poetry -C bridge run adminme-bridge
```

Confirm the central activity view records that member and never another. Stop
the foreground daemon after the check; unattended bridge service installation
remains the documented limitation in `KNOWN-LIMITATIONS.md`.

## 12. Backup, restore rehearsal, and rollback

Create a baseline backup:

```bash
./bootstrap/backup.sh
```

Record the `.ambackup` path printed by the command. Make one harmless fixture
change during the observation period, then restore only through:

```bash
./bootstrap/restore.sh "/absolute/path/printed-by-backup.ambackup"
./bootstrap/doctor.sh
```

Restore authenticates into staging, checks hashes and SQLCipher, rebuilds all
thirteen projections/workbooks, and keeps a rollback copy before replacement.
Never unpack or edit an `.ambackup` by hand.

For an emergency, run `./bootstrap/stop.sh`; this preserves data, Keychain
items, backups, and enrollments. For a reviewed software rollback, use the
previous release pointer and `python -m bootstrap.cli rollback`; do not delete
the previous release until the trust period and a fresh backup both pass.

## 13. Create a safe support bundle

Run:

```bash
./bootstrap/doctor.sh --export "$HOME/Desktop/administrateme-diagnostic.json"
```

Expected: `"safe_to_attach": true`. Open the JSON in TextEdit and review it
before sharing; it contains operational names/timestamps but no credential
values or instance path. Never attach logs, Keychain exports, browser storage,
bridge config, provider pages, or raw household exports.

## 14. Five-to-seven-day trust period

Keep observation on. Each day, run status and Doctor, review suppressed actions,
member isolation, connector watermarks, Activity Monitor idle CPU/memory, disk
growth, and backup health. Use all eight surfaces and completion/deferral once;
confirm What Now recalculates immediately. Do not enable external side effects
until five full days have elapsed, the seven-day review is complete, every
identity/privacy finding is resolved, Doctor and backup pass, and a principal
explicitly approves trust.

If anything is ambiguous, stop services and return to the merged-review
boundary. Do not repair production by editing Python, JSON, launchd plists, the
OpenClaw workspace, or browser storage on this Mac.
