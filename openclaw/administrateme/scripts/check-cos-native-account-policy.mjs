/** Exact pinned ordinary Codex thread policy; no account/model/provider calls. */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

assert.equal(process.env.GITHUB_ACTIONS, "true", "Use isolated hosted CI");
assert.equal(process.env.RUNNER_ENVIRONMENT, "github-hosted", "Use a disposable hosted runner");
const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const source = path.resolve(process.argv[2]);
const versions = JSON.parse(await readFile(path.join(repository, "bootstrap/versions.json"), "utf8"));
assert.equal(path.basename(source), versions.openclaw.commit);
assert.equal(JSON.parse(await readFile(path.join(source, "package.json"), "utf8")).version, versions.openclaw.version);
const requirePinned = createRequire(path.join(source, "package.json"));
const { createJiti } = requirePinned("jiti");
const jiti = createJiti(path.join(source, "package.json"), { tsconfigPaths: path.join(source, "tsconfig.json") });
const load = (relative) => jiti.import(path.join(source, relative));
// Real configuration I/O runs in a fresh child with only synthetic private paths.
// No agent turn, desktop process, plugin installation or provider transport runs.
async function computerUseConfigProof(roster) {
  assert(["list", "entries"].includes(roster));
  const directory = process.env.ADMINME_COMPUTER_USE_FIXTURE;
  assert(directory && path.isAbsolute(directory));
  assert.equal(process.env.HOME, directory);
  assert.equal(process.env.OPENCLAW_HOME, directory);
  assert.equal(process.env.CODEX_HOME, path.join(directory, "codex"));
  const state = path.join(directory, "state");
  const configPath = path.join(state, "openclaw.json");
  assert.equal(process.env.OPENCLAW_STATE_DIR, state);
  assert.equal(process.env.OPENCLAW_CONFIG_PATH, configPath);
  await mkdir(state, { mode: 0o700 });
  const agent = { workspace: path.join(directory, "workspace"), tools: { allow: ["browser"] } };
  const authored = {
    meta: { lastTouchedVersion: roster === "list" ? versions.openclaw.predecessor.version : versions.openclaw.version,
      migrations: { modelPolicyAllowlist: true, ...(roster === "entries" ? { utilityModelSeparation: true } : {}) } },
    gateway: { mode: "local", bind: "loopback", auth: { mode: "token", token: "synthetic-config-proof-only" } },
    agents: {
      defaults: { workspace: path.join(directory, "default-workspace") },
      [roster]: roster === "list" ? [{ id: "fixture-agent", ...agent }] : { "fixture-agent": agent },
    },
    channels: {}, bindings: [],
    plugins: { entries: { codex: { enabled: true, config: { codexPlugins: { enabled: false } } } } },
    browser: { enabled: true, evaluateEnabled: true, defaultProfile: "chrome", allowSystemProfileImport: false, profiles: { chrome: { driver: "extension" } } },
  };
  await writeFile(configPath, JSON.stringify(authored, null, 2) + "\n", { mode: 0o600 });
  if (roster === "list") {
    // Retired lists are invalid in 10.8. Exercise the actual pinned Doctor before
    // testing exact config.patch; never silently author a canonical replacement.
    execFileSync(process.execPath, [path.join(source, "openclaw.mjs"), "doctor", "--fix", "--non-interactive"], {
      cwd: directory, env: process.env, encoding: "utf8", timeout: 90_000,
    });
  }
  const original = JSON.parse(await readFile(configPath, "utf8"));
  assert.deepEqual(original.agents.entries, { "fixture-agent": agent });
  assert.equal(Object.hasOwn(original.agents, "list"), false);
  assert.equal(original.agents.defaults.workspace, authored.agents.defaults.workspace);
  for (const key of ["gateway", "channels", "bindings", "plugins", "browser"]) {
    assert.deepEqual(original[key], authored[key], `Doctor must preserve the exact ${key} policy`);
  }
  assert.equal(original.meta.lastTouchedVersion, versions.openclaw.version);
  assert.equal(original.meta.migrations.modelPolicyAllowlist, true);
  assert.equal(original.meta.migrations.utilityModelSeparation, true);
  const { configHandlers } = await load("src/gateway/server-methods/config.ts");
  const { readConfigFileSnapshot } = await load("src/config/io.ts");
  const { stampConfigWriteMetadata } = await load("src/config/io.meta.ts");
  const { VERSION } = await load("src/version.ts");
  assert.equal(VERSION, versions.openclaw.version, "writer metadata must use the actual pinned version");
  const client = { connId: "fixture-operator", connect: { client: { id: "fixture-control" }, scopes: ["operator.admin"] } };
  const context = { logGateway: { info() {}, warn() {}, error() {} } };
  const call = async (method, params) => {
    let result;
    await configHandlers[method]({ params, client, context, respond: (ok, payload, error) => {
      assert.equal(result, undefined, "one native response required"); result = { ok, payload, error };
    } });
    assert(result, "native handler did not respond");
    return result;
  };
  const python = (action, input) => JSON.parse(execFileSync(process.argv[3], ["-c", `
import json, sys
from bootstrap.workstation_browser import prepare_computer_use_patch, _computer_use_delta, _canonical
value = json.load(sys.stdin)
action = sys.argv[1]
if action == "prepare":
    print(json.dumps(prepare_computer_use_patch(value["snapshot"], disable=value["disable"])))
elif action == "verify":
    assert _canonical(_computer_use_delta(value["previous"], value["patch"])) == _canonical(value["current"])
    print(json.dumps({"exact": True}))
else:
    try:
        prepare_computer_use_patch(value, disable=False)
    except ValueError as exc:
        assert "already-current native write metadata" in str(exc)
        print(json.dumps({"refused": True}))
    else:
        raise AssertionError("unrelated native migration was admitted")
`, action], {
    cwd: repository, env: { ...process.env, PYTHONPATH: `${repository}:${path.join(repository, "bridge")}` },
    input: JSON.stringify(input), encoding: "utf8", timeout: 20_000,
  }));
  const hash = (raw) => createHash("sha256").update(raw).digest("hex");
  const observed = await readConfigFileSnapshot();
  assert.equal(observed.valid, true, "synthetic authored native configuration must validate");
  assert(observed.config.agents.entries["fixture-agent"], "read must exercise resolved native agent representation");
  assert.notDeepEqual(observed.config, original, "fixture must exercise authored-vs-runtime default projection");
  assert.deepEqual(stampConfigWriteMetadata(original, VERSION, original), original);
  for (const mutation of ["version", "modelPolicyAllowlist", "utilityModelSeparation"]) {
    const unsafe = structuredClone(original);
    if (mutation === "version") unsafe.meta.lastTouchedVersion = "2020.1.1";
    else delete unsafe.meta.migrations[mutation];
    assert.notDeepEqual(stampConfigWriteMetadata(unsafe, VERSION, unsafe), unsafe);
    const rawBefore = await readFile(configPath, "utf8");
    assert.deepEqual(python("refuse", { valid: true, hash: hash(rawBefore), config: unsafe }), { refused: true });
    assert.equal(await readFile(configPath, "utf8"), rawBefore, "refusal must happen before native write");
  }
  for (const disable of [false, true]) {
    const rawBefore = await readFile(configPath, "utf8");
    const previous = JSON.parse(rawBefore);
    const before = await call("config.get", {});
    assert.equal(before.ok, true);
    assert.equal(before.payload.valid, true);
    assert.equal(before.payload.hash, hash(rawBefore));
    const patch = python("prepare", { snapshot: { valid: before.payload.valid, hash: before.payload.hash, config: previous }, disable });
    // Real CAS/schema refusals must leave the synthetic file byte-identical.
    const stale = await call("config.patch", { ...patch, baseHash: "0".repeat(64) });
    assert.equal(stale.ok, false);
    assert.equal(await readFile(configPath, "utf8"), rawBefore);
    const invalid = await call("config.patch", { baseHash: patch.baseHash, raw: JSON.stringify({ plugins: { entries: { codex: { config: { computerUse: { enabled: "invalid-boolean" } } } } } }) });
    assert.equal(invalid.ok, false, "actual pinned plugin config schema must reject wrong type");
    assert.equal(await readFile(configPath, "utf8"), rawBefore);
    const committed = await call("config.patch", patch);
    assert.equal(committed.ok, true, `actual native config.patch failed: ${JSON.stringify(committed.error)}`);
    const rawAfter = await readFile(configPath, "utf8");
    const current = JSON.parse(rawAfter);
    const after = await call("config.get", {});
    assert.equal(after.ok, true);
    assert.equal(after.payload.valid, true);
    assert.equal(committed.payload.hash, hash(rawAfter));
    assert.equal(after.payload.hash, hash(rawAfter));
    assert.deepEqual(python("verify", { previous, patch: JSON.parse(patch.raw), current }), { exact: true });
    assert.deepEqual(current.agents, original.agents, "writer must retain authored roster shape and defaults");
    assert.deepEqual(current.meta, original.meta, "no metadata refresh may enter the approved delta");
    assert.equal(current.plugins.entries.codex.config.codexPlugins.enabled, false);
    if (disable) assert.deepEqual(current, original, "native null inverse must restore all original parsed fields");
    else {
      // config.get resolves plugin schema defaults; raw authoring stays exact
      // through the separate _computer_use_delta/roster/metadata checks above.
      const resolvedDefaults = {
        marketplaceDiscoveryTimeoutMs: 60_000, liveTestTimeoutMs: 60_000,
        toolCallTimeoutMs: 60_000, healthCheckIntervalMinutes: 60,
        pluginCacheMode: "independent",
      };
      const manifest = JSON.parse(await readFile(path.join(source, "extensions/codex/openclaw.plugin.json"), "utf8"));
      const properties = manifest.configSchema.properties.computerUse.properties;
      assert.equal(properties.strictReadiness.default, false);
      assert.equal(current.plugins.entries.codex.config.computerUse.strictReadiness, true,
        "managed unified Computer Use must retain strict readiness instead of the schema default");
      assert.equal(Object.hasOwn(current.plugins.entries.codex.config.computerUse, "marketplacePath"), false,
        "legacy marketplace override prevents native unified runtime discovery");
      for (const [key, value] of Object.entries(resolvedDefaults)) {
        assert.deepEqual(properties[key].default, value, `pinned Computer Use default changed: ${key}`);
        assert.equal(Object.hasOwn(current.plugins.entries.codex.config.computerUse, key), false, "resolved defaults must not be authored");
      }
      assert.deepEqual(after.payload.config.plugins.entries.codex.config.computerUse, {
        ...current.plugins.entries.codex.config.computerUse, ...resolvedDefaults,
      });
    }
  }
  console.log(`PINNED COMPUTER USE CONFIG ${roster}: actual prepare -> config.patch -> config.get/raw readback -> null inverse; CAS/schema refusal, defaults/metadata/account policy retained: PASS`);
}
if (process.argv[4] === "--computer-use-config") {
  await computerUseConfigProof(process.argv[5]);
  process.exit(0);
}
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

for (const roster of ["list", "entries"]) {
  const directory = await mkdtemp(path.join(tmpdir(), "adminme-computer-use-config-"));
  try {
    execFileSync(process.execPath, [fileURLToPath(import.meta.url), source, process.argv[3], "--computer-use-config", roster], {
      cwd: repository, timeout: 180_000, stdio: "inherit",
      env: {
        PATH: process.env.PATH,
        GITHUB_ACTIONS: "true", RUNNER_ENVIRONMENT: "github-hosted",
        OPENCLAW_SERVICE_REPAIR_POLICY: "external", OPENCLAW_NO_AUTO_UPDATE: "1", OPENCLAW_NO_RESPAWN: "1",
        HOME: directory, OPENCLAW_HOME: directory, CODEX_HOME: path.join(directory, "codex"),
        OPENCLAW_STATE_DIR: path.join(directory, "state"), OPENCLAW_CONFIG_PATH: path.join(directory, "state/openclaw.json"),
        ADMINME_COMPUTER_USE_FIXTURE: directory,
        ...(process.env.TMPDIR ? { TMPDIR: process.env.TMPDIR } : {}),
      },
    });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}
