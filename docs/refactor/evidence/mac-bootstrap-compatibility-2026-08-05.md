# Mac bootstrap compatibility evidence — 2026-08-05

Classification: **MODERNIZE** packaging/runtime integration; **PRESERVE** the
reviewed-source, privacy, topology, scheduler-ownership, and fail-closed
bootstrap contracts.

The Apple-silicon rehearsal reproduced five failures: Homebrew 5 rejected
current Core metadata; Poetry deleted its own target-environment dependencies
during `install --sync`; the source installer created `bin` and `state` before
the wizard's former app-only root check; pinned OpenClaw required explicit
trust for a local path; and dashboard ownership validation ran during plugin
discovery although the plugin registered only in full activation. The last
failure made Doctor report `discovered: false` and polluted the cron command's
expected JSON result with plugin registration diagnostics.

The rehearsal also found that OpenClaw's default `~/.openclaw` state allowed a
lab plugin path to survive into another instance. All installer, wrapper,
activation, cron, and launchd entry points now bind OpenClaw state and config to
the selected AdministrateMe instance root. The clean isolated state also made
the pinned build's explicit `gateway.mode` prerequisite visible; bootstrap now
sets it to `local` and validates the config before service startup.
Reruns remove stale AdministrateMe plugin registration metadata with
`--keep-files` before linking the current reviewed release, preventing duplicate
plugin IDs without deleting any immutable release evidence. Removal is bounded
and repeated because older failed resumes may have accumulated more than one
stale load path.

Finally, the live process gate showed that optional unconfigured member chat
was correctly reported as a blocked capability but incorrectly made every
deterministic product endpoint fail global readiness. It remains visibly
blocked while the runtime is ready/degraded until OpenClaw model activation.
Service start now reloads already-loaded user LaunchAgents before kickstart so a
resumed install or upgrade cannot keep executing the prior immutable release.

The repaired plugin was built and installed with `--link --force` into an
isolated OpenClaw state directory using the exact pinned OpenClaw `2026.7.2`
source build at commit `944cf08f94c334cff2b0fdf7e01187a9efa42715`.
`plugins inspect administrateme --runtime --json` returned `status: loaded`, all
eleven gateway methods, and an empty diagnostics array. Discovery started no
plugin services or hooks.

Focused evidence after repair:

- native plugin contract/check: passed;
- plugin tests: 14 passed, including discovery and tool-discovery regressions;
- bootstrap unit plus process acceptance tests: 24 passed;
- Ruff on changed Python/tests: passed;
- the only Python warning was the pre-existing Starlette `httpx` test-client
  deprecation warning.

The live Apple-silicon lab then completed release installation and service
startup through the production composition root. Doctor reported Homebrew
`6.0.15`, reviewed OpenClaw source provenance, `discovered: true`, thirteen
healthy projections/checkpoints, and `5/5` live managed cron jobs. The selected
three-bridge fixture remained intentionally incomplete with
`package-created-not-live`; no live provider was claimed. Final root/bridge and
strict acceptance gates cover the readiness and LaunchAgent resume repairs.
