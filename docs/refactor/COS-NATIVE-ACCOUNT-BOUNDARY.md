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
Codex threads. Existing legacy or changed app-policy bindings rotate through the
pinned thread lifecycle; an already loaded thread must not be trusted to apply
resume overrides alone. Isolated checks exercise that native configuration path,
including unchanged native code mode and reviewed dynamic tools. They make no
account, model or provider calls and do not prove a household account's effective
runtime catalog.

After supported deployment, verify the installed setting and a fresh ordinary
CoS native binding's `pluginAppsFingerprint`, `pluginAppsInputFingerprint`, and
empty `pluginAppPolicyContext.apps` without calling a provider. Retain native
thread IDs when verifying legacy binding rotation. Verify the intended native file route remains available and
the reviewed inventory/domain tools still work. Current supported operator
surfaces do not expose a complete per-thread native tool catalog: `tools.effective`
projects only configured MCP servers and `/codex mcp` omits the thread binding.
Do not substitute model narration for that missing evidence. Account access still
requires the private current identity map and destination verification.

Arbitrary inherited `mcp_servers` are a separate native configuration surface.
This setting does not remove them, including servers generated for an individual
thread. Inspect configured Gateway and agent-home MCP inventories separately;
those files do not prove the absence of generated per-thread servers. A complete
effective native catalog remains unresolved through supported operator surfaces.
Do not claim that disabling account apps proves all MCP access or general shell
work is bounded. Do not change the user's ChatGPT/Codex settings on other devices.
