# Native account apps in the household runtime

An ordinary CoS Codex session can inherit connected account apps independently of
OpenClaw's dynamic-tool allowlist. Neither the Mac user nor the native model login
establishes a provider account suitable for household work.

The reviewed release declares `contracts.nativeAccountApps = "disabled"`.
Supported topology preparation and plugin reconciliation set only the explicitly
installed Codex entry's `config.codexPlugins.enabled` to `false`. This uses the
pinned native plugin policy. It preserves model authentication, native shell/file
policy, OpenClaw tools, member bridges, provider credentials and existing grants.
Preparation is read-only; activation records the full expected configuration.
Failed reconciliation restores its exact predecessor bytes. A legacy target does
not silently re-enable ambient accounts merely because it lacks the declaration.

The pin produces an explicit `apps._default` denial for ordinary new and resumed
Codex threads. Isolated checks exercise that actual native configuration path,
including unchanged native code mode and reviewed dynamic tools. They make no
account, model or provider calls and do not prove a household account's effective
runtime catalog.

After supported deployment, inspect a fresh ordinary CoS session's effective
native tool exposure without calling a provider. Verify ambient account apps are
absent or rejected, the intended native file route remains available, and the
reviewed inventory/domain tools still work. Account access still requires the
private current identity map and destination verification.

Arbitrary inherited `mcp_servers` are a separate native configuration surface.
This setting does not remove them, including servers generated for an individual
thread. Inspect their effective runtime names and bindings separately; do not
claim that disabling account apps proves all MCP access or general shell work is
bounded. Do not change the user's ChatGPT/Codex settings on other devices.
