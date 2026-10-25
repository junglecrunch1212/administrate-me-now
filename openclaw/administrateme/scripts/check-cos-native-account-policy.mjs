/** Exact pinned ordinary Codex thread policy; no account/model/provider calls. */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

assert.equal(process.env.GITHUB_ACTIONS, "true", "Use isolated hosted CI");
const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const source = path.resolve(process.argv[2]);
const versions = JSON.parse(await readFile(path.join(repository, "bootstrap/versions.json"), "utf8"));
assert.equal(path.basename(source), versions.openclaw.commit);
assert.equal(JSON.parse(await readFile(path.join(source, "package.json"), "utf8")).version, versions.openclaw.version);
const requirePinned = createRequire(path.join(source, "package.json"));
const { createJiti } = requirePinned("jiti");
const jiti = createJiti(path.join(source, "package.json"), { tsconfigPaths: path.join(source, "tsconfig.json") });
const load = (relative) => jiti.import(path.join(source, relative));
const { resolveCodexPluginThreadConfigStartupPolicy, createCodexPluginThreadConfigStartupProvider } = await load("extensions/codex/src/app-server/plugin-thread-config-deadline.ts");
const { buildCodexPluginThreadConfig, isCodexPluginThreadBindingStale } = await load("extensions/codex/src/app-server/plugin-thread-config.ts");
const { shouldEnableCodexAppServerNativeToolSurface } = await load("extensions/codex/src/app-server/dynamic-tool-build.ts");
const { buildThreadStartParams, buildThreadResumeParams } = await load("extensions/codex/src/app-server/thread-requests.ts");
const prepared = JSON.parse(execFileSync(process.argv[3], ["-c", `import json; from pathlib import Path; from bootstrap.native_account_policy import prepare_native_account_policy; print(json.dumps(prepare_native_account_policy({"plugins":{"entries":{"codex":{"enabled":True}}}}, Path.cwd())))`], {
  cwd: repository, env: { ...process.env, PYTHONPATH: `${repository}:${path.join(repository, "bridge")}` }, encoding: "utf8",
}));
assert.deepEqual(prepared, { plugins: { entries: { codex: { enabled: true, config: { codexPlugins: { enabled: false } } } } } });
const pluginConfig = prepared.plugins.entries.codex.config;
assert.equal(resolveCodexPluginThreadConfigStartupPolicy({ pluginConfig: {}, nativeToolSurfaceEnabled: true }).pluginThreadConfigRequired, false, "unconfigured ordinary run did not request app suppression");
const startupPolicy = resolveCodexPluginThreadConfigStartupPolicy({ pluginConfig, nativeToolSurfaceEnabled: true });
assert.equal(startupPolicy.pluginThreadConfigRequired, true);
const selected = await buildCodexPluginThreadConfig({ pluginConfig, appCacheKey: "synthetic-ordinary-service", request: async (method) => { throw new Error(`Unexpected account or provider call: ${method}`); } });
assert.deepEqual(selected.configPatch, { apps: { _default: { enabled: false, destructive_enabled: false, open_world_enabled: false } } });
assert.deepEqual(selected.policyContext.apps, {});
const startupProvider = createCodexPluginThreadConfigStartupProvider({
  pluginConfig, inputFingerprint: selected.inputFingerprint,
  policy: startupPolicy.resolvedPluginPolicy, enabledPluginConfigKeys: startupPolicy.enabledPluginConfigKeys,
  appCacheKey: "synthetic-ordinary-service", requestTimeoutMs: 1000, signal: new AbortController().signal,
  client: { request: async () => { throw new Error("App exclusion must not inspect account inventory"); } },
});
assert.equal(startupProvider.enabled, true, "explicit app denial must enter native thread policy reconciliation");
assert.deepEqual((await startupProvider.build()).configPatch, selected.configPatch);
// A loaded legacy thread can ignore resume overrides; the pin rotates its stale binding.
assert.equal(isCodexPluginThreadBindingStale({ codexPluginsEnabled: startupProvider.enabled, currentInputFingerprint: selected.inputFingerprint }), true);
assert.equal(isCodexPluginThreadBindingStale({ codexPluginsEnabled: startupProvider.enabled, bindingFingerprint: "previous-app-policy", bindingInputFingerprint: "previous-app-input", currentInputFingerprint: selected.inputFingerprint, hasBindingPolicyContext: true }), true);
assert.equal(isCodexPluginThreadBindingStale({ codexPluginsEnabled: startupProvider.enabled, bindingFingerprint: selected.fingerprint, bindingInputFingerprint: selected.inputFingerprint, currentInputFingerprint: selected.inputFingerprint, hasBindingPolicyContext: true }), false);
const params = { sessionId: "fixture-ordinary-service", sessionKey: "agent:fixture-service:ordinary", runId: "fixture-run", provider: "openai", modelId: "gpt-5.4", config: prepared, authProfileStore: { version: 1, profiles: {} }, prompt: "Retain a private working note" };
assert.equal(shouldEnableCodexAppServerNativeToolSurface(params), true, "app exclusion must not remove native shell/file capability");
const dynamicTools = [{ type: "function", name: "administrateme_workstation_inventory", description: "Reviewed fixture tool", inputSchema: { type: "object", properties: {} } }, { type: "function", name: "browser", description: "Reviewed native browser route", inputSchema: { type: "object", properties: {} } }];
const options = { cwd: repository, dynamicTools, developerInstructions: "Synthetic ordinary service policy proof", appServer: { approvalPolicy: "never", approvalsReviewer: "user", sandbox: "workspace-write" }, nativeCodeModeEnabled: true, nativeCodeModeOnlyEnabled: false, webSearchAllowed: false,
  config: { ...selected.configPatch, mcp_servers: { "fixture-separate-mcp": { enabled: true, command: "never-executed" } } } };
const started = buildThreadStartParams(params, options);
assert.deepEqual(started.dynamicTools, dynamicTools);
for (const result of [started, buildThreadResumeParams(params, { ...options, threadId: "fixture-existing-thread", preserveNativeModel: true })]) {
  assert.deepEqual(result.config.apps, selected.configPatch.apps);
  assert.equal(result.config["features.code_mode"], true);
  assert.equal(result.config.mcp_servers["fixture-separate-mcp"].enabled, true, "app exclusion does not claim to disable arbitrary inherited MCP servers");
}
console.log("PINNED CODEX ORDINARY THREAD POLICY: explicit app denial on start/resume; native code and reviewed dynamic tools retained; separate MCP scope explicit: PASS");
