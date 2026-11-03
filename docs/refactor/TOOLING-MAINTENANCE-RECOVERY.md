# Homebrew tooling maintenance recovery

The current tooling predicate accepts Homebrew 6 and 7 with the existing
Python 3.14, Node 24, npm 11 and pinned OpenClaw bounds. Maintenance preparation
and pause run only the selected tooling probe before stopping services.
The bootstrap guard and version manifest use the same reviewed Homebrew range,
`>=6,<8`; boundary regressions exercise the actual shell guard against Doctor.
Current Doctor also uses the selected manifest's Node compatibility floor.
The separate retained-report correction keeps its reviewed historical Node
range; it does not reinterpret immutable predecessor manifests.

## Update ownership and reproducibility

One protected AdministrateMe release owns the selected OpenClaw commit, archive
digest, plugin API, runtime declarations and dependency locks. Update discovery
and notices do not authorize installation. The managed Gateway startup disables
independent upstream auto-installation; a check-only release report creates no
schedule or update worker. An unattended delivery path must explicitly connect
discovery to protected candidate qualification and the guarded updater before
it can be described as automatic updating.

Production selects reviewed Python and Node executables and locked Python/npm
packages. A compatible range allows qualification of a newer patch; it does not
silently move the selected executable. Homebrew formula refreshes and desktop
application updates have separate ownership and may drift independently.
Do not run broad package upgrades against serving virtual environments or remove
retained runtimes before compatible recovery is established.

CI keeps deterministic runtime pins and separately exercises rolling supported
Homebrew patches. The rolling case must prove the actual formula/executable
binding. Apple SDK fixtures resolve the runner's selected SDK within the reviewed
major and retain real compilation/linking assertions. New major versions, changed
native APIs, database schemas and incompatible toolchains require qualification;
accepting arbitrary newest versions would remove that proof.

Keep the last known-working generation and required verified backups. Recovery
must respect accepted history: after new-schema work is accepted, revoke the
affected feature or roll forward on a compatible runtime rather than launch an
older binary against newer data. Standing automation must preserve transaction
locks, a bounded maintenance window, recorded outcomes and compatible recovery.

Two source-verified retained releases have the obsolete Homebrew-6 ceiling:
`471d20ec71d40d59490ff763be524d73a3c3cb7d` and
`02c2957acc4e659a19720db5bfb296ebba710bbd`. The protected controller can qualify
their complete reports only when that ceiling is the sole failed check and
the actual observed tuple satisfies the current rule. The original Doctor
still failed: its complete unchanged report, digest and source identity are
retained in a separate `retained_doctor_tooling_compatibility` decision.
Missing checks, unknown sources, another failed check or an incompatible tuple
remain failures. Full settlement still requires the original synthetic proof.

From the protected repair checkout, using its supported Python environment:

```sh
python -m bootstrap.cos_channel_activation --instance-root INSTANCE \
  --recover-tooling-maintenance --request ORIGINAL_MAINTENANCE_PLAN
python -m bootstrap.cos_channel_activation --instance-root INSTANCE \
  --restore-tooling-maintenance --request ORIGINAL_MAINTENANCE_PLAN
```

The first command completes the already-started pause. The second restores the
unchanged selected predecessor to normal operation through guarded activation.
Neither command installs a release, changes native preferences, replaces the
original plan/target/expiry, refreshes sender evidence, or grants new authority.
Original configuration, recipient policy, sender identities, reply/reminder
grants, observation boundary and provider-effect count must still match.
The completed pause history and original failed report remain retained; the
restoration is a separately linked operation. Subsequent feature deployment
requires a fresh ordinary maintenance plan against the then-current state.

After successful restoration, verify the same selected release, normal readiness,
original sources/grants/native configuration and absence of active journals.
An interrupted admitted operation uses the existing `--recover` command from
that same protected controller. It either completes the original pause with a
fresh full Doctor or compensates an interrupted restoration into observation
with polling paused. Missing diagnostics are never health evidence. A failed
restoration does not establish normal operation, and this recovery does not
restore an old native configuration that discards current preferences.

## Cancel a completed pause before release selection

If an update fails before selecting its target and leaves no active updater
journal, restore the unchanged predecessor with the original unexpired plan:

```sh
python -m bootstrap.cos_channel_activation --instance-root INSTANCE \
  --cancel-maintenance --request ORIGINAL_MAINTENANCE_PLAN
```

This explicit path accepts either the original protected target controller or
its protected successor. It is limited to the source-verified retained releases
above, with the current qualified Doctor decision and all the original authority,
configuration, sender evidence and zero-contact observation boundaries intact.
It does not select a release, migrate credentials, modify Keychain aliases or
retarget the plan. The original committed pause is retained byte-for-byte; a
separate cancellation journal binds its hash and original plan. Normal activation
closes that same observation window at the new activation timestamp and preserves
all prior windows. Successful cancellation consumes the prior activation receipt,
so replay cannot create a second normal transition.

If cancellation is interrupted, run the existing `--recover` from the exact
controller recorded in its journal. Recovery validates the original pause hash
and selection, then compensates into observation with polling paused; this is
not a claim of normal restoration. An active update, changed selection, expired
plan, changed authority/configuration or another Doctor failure remains blocked.
The Node-only updater credential guard is a separate repair; do not retry the
unchanged failing updater merely because cancellation restored normal operation.
