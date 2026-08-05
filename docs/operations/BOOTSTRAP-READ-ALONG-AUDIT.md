# Bootstrap read-along code review

Date: 2026-08-04
Reviewed branch: `codex/administrateme-v2-runtime`
Pull request: [#248](https://github.com/junglecrunch1212/administrate-me-now/pull/248)

## Outcome

The operator audit originally reopened the candidate for two production gaps:
OpenClaw cron declarations were never installed, and member bridge packages had
no safe consumer. Both gaps are now implemented and process-tested in the
working release candidate. The audit also found that `openclaw@2026.7.2` is not
published on npm; bootstrap now installs the exact reviewed GitHub source
commit from a pinned SHA-256 archive instead of failing or floating.

This is code readiness, not deployment authorization. PR #248 still requires
independent review, a green merged SHA, and the disposable Apple-silicon live
gate. The family Mac remains off limits until those steps complete.

## Review ratings

| Dimension | Rating | Evidence-based summary |
|---|---|---|
| Security | Ready for independent review | Keychain references, loopback listeners, verified source provenance, Tailscale identity binding, short-lived encrypted bridge transfer, separate one-time code, owner-scoped proof, and independent bridge revocation all fail closed. |
| Correctness | Locally ready; macOS live proof pending | Cron reconciliation is idempotent/owned/verified/rollback-capable. Bridge export, target acceptance, launchd configuration, real ingest proof, finalization, expiry, and revocation are process-tested. |
| Performance | Bounded bootstrap; live soak outstanding | Exact-source OpenClaw build is intentionally slower than npm install. Long-duration Mac mini CPU, memory, and disk behavior remains a live gate. |
| Maintainability | Good | Pins and source receipts are centralized, wrappers are stable, section journals resume with original encrypted answers, and Doctor identifies expected bridge follow-up separately from faults. |

## Original P0 findings and resolution

### Final GitHub version

The release boundary is still a full independently reviewed merged SHA. The
read-along refuses a dirty checkout, short SHA, unmerged branch, ZIP, or
floating `latest`. This remains a human/repository gate, not a code defect.

### OpenClaw schedules

`bootstrap/openclaw_cron.py` cross-checks
`openclaw/administrateme/automations.json` against
`bootstrap/openclaw/cron.yaml`, requires a valid IANA household timezone, and
owns only names beginning `administrateme-`. It lists current jobs as JSON,
rejects duplicate owned names, adds or edits drifted jobs, removes only stale
IDs recorded by the prior reconciliation, leaves foreign jobs untouched,
verifies the final state, rolls back partial mutations, and writes a
secret-free Doctor marker. Bootstrap, activation, and safe service start all
use it.

### Member bridge enrollment

The central export command rotates a short-lived transfer key into Keychain,
creates a member/release/identity-bound AES-GCM transfer, and prints the
one-time code separately. The member installer verifies Apple silicon,
FileVault, disk, Tailscale self identity, a clean exact-SHA checkout, package
expiry, child restrictions, and pack allowlists before mutation. It installs a
separate release and LaunchAgent, emits a real owner-scoped `bridge.enrolled`
event through the private Serve origin, consumes the transfer file, and rolls
back a failed install. Central finalization requires that encrypted event-log
proof before activating and revoking bootstrap material. Central revocation is
independent of portal identity and remains fail closed if the member Mac is
lost.

### OpenClaw distribution mismatch

The previous `npm install --global openclaw@2026.7.2` would fail because that
version is not in the npm registry. `bootstrap/install-openclaw-source.sh` now
downloads only the codeload archive for commit
`944cf08f94c334cff2b0fdf7e01187a9efa42715`, verifies SHA-256
`a0f1800a64ef495062da6c65e0e9a2b9a6ca0e51c3bc6197918db4eddc7292b8`,
uses `pnpm@11.15.1` with the source lock, builds UI/CLI, installs an
instance-local executable, and writes a Doctor-validated provenance receipt.
There is no npm or branch fallback.

## Other high-impact fixes retained

- Production `--dry-run` is refused; fixture rehearsal uses a separate Lab
  root.
- Stable wrappers reconstruct Homebrew Node and the instance-local OpenClaw
  path in a fresh Terminal and in LaunchAgents.
- Resume reuses the original encrypted answer set and rejects changed answers.
- The exact release SHA and clean worktree are checked before instance mutation.
- Central and member release archives are extracted atomically; commit receipts
  make interrupted environment installs retry instead of trusting partial state.
- Stable member helpers change into the archived release before invoking
  Python, so status/disable work from any Terminal directory.
- Tailscale login matching tolerates harmless casing differences while the
  wizard still rejects case-insensitive duplicate household identities.
- The compiled OpenClaw workspace is selected before model/Gateway probing.
- Household timezone is explicit and validated with IANA data.
- Seed questions create truthful follow-ups, not invented household facts.
- Observation briefing requires explicit confirmation.
- Doctor distinguishes intentional pending bridge enrollment from an actual
  bootstrap failure.

## Remaining live-only gates

- independent PR review, merge, and green checks at the exact merged SHA;
- disposable Apple-silicon source build, launchd, cron RPC, reboot, and bridge
  rehearsal;
- real OpenClaw model/channel sign-in and deep RPC probe;
- private Tailscale Serve identity checks for each household user;
- minimum Apple/provider grants, Safari/VoiceOver, backup/restore, and the
  five-to-seven-day observation soak.

The executable commands and operator responses for those gates are in
`docs/operations/MAC-MINI-SETUP-READ-ALONG.md`.
