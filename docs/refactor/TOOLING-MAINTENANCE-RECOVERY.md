# Homebrew tooling maintenance recovery

The current tooling predicate accepts Homebrew 6 and 7 with the existing
Python 3.14, Node 24, npm 11 and pinned OpenClaw bounds. Maintenance preparation
and pause run only the selected tooling probe before stopping services.

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
