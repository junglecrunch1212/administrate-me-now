/** Pinned Codex native hook protocol + actual candidate worker policy, hosted only.
 * No Codex login, model/provider call, browser, command or connector is executed.
 */
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

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
const { createPluginRegistryState } = await load("src/plugins/registry-state.ts");
const { createToolHookRegistrars } = await load("src/plugins/registry-registrars-tools-hooks.ts");
const { initializeGlobalHookRunner, getGlobalHookRunner, resetGlobalHookRunner } = await load("src/plugins/hook-runner-global.ts");
const { processNativeHookRelayInvocation, nativeHookRelayEventHasLocalWork, nativeHookRelayEventToolMatcher } = await load("src/agents/harness/native-hook-relay-events.ts");
const { normalizeNativeHookInvocation, getNativeHookRelayProviderAdapter } = await load("src/agents/harness/native-hook-relay-codec.ts");
const { shouldEnableCodexAppServerNativeToolSurface } = await load("extensions/codex/src/app-server/dynamic-tool-build.ts");
const directory = await mkdtemp(path.join(tmpdir(), "adminme-codex-worker-policy-"));
const originalSecret = process.env.ADMINME_OPENCLAW_SHARED_SECRET;
try {
  const plugin = path.join(directory, "plugin");
  await mkdir(path.join(plugin, "node_modules"), { recursive: true });
  await writeFile(path.join(plugin, "package.json"), JSON.stringify({ type: "module" }));
  await symlink(source, path.join(plugin, "node_modules/openclaw"), "dir");
  await symlink(path.join(repository, "openclaw/administrateme/node_modules/typebox"), path.join(plugin, "node_modules/typebox"), "dir");
  const sourceModules = path.join(repository, "openclaw/administrateme/src");
  for (const name of (await readdir(sourceModules)).filter((entry) => entry.endsWith(".ts"))) {
    await writeFile(path.join(plugin, name.replace(/\.ts$/, ".js")), ts.transpileModule(await readFile(path.join(sourceModules, name), "utf8"), {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ESNext },
    }).outputText);
  }
  const module = (name) => import(pathToFileURL(path.join(plugin, name)).href);
  const { registerWorkstationWorkHooks } = await module("workstation-work.js");
  const hasBrowser = (await readdir(sourceModules)).includes("workstation-browser-hooks.ts");
  const registerWorkstationBrowserHooks = hasBrowser ? (await module("workstation-browser-hooks.js")).registerWorkstationBrowserHooks : undefined;
  const { serviceAgentId, serviceSessionKey } = await module("service-private.js");
  const { readPluginConfig } = await module("config.js");
  const secret = "synthetic-native-hook-proof-never-production";
  process.env.ADMINME_OPENCLAW_SHARED_SECRET = secret;
  const registration = {
    relayId: "fixture-codex-worker", provider: "codex", agentId: serviceAgentId(),
    sessionId: "fixture-native-session", sessionKey: serviceSessionKey(secret, undefined, "codex-policy-proof"),
    runId: "fixture-native-run", config: { tools: { loopDetection: { enabled: false } } },
    preToolUseLoopDetection: false,
  };
  for (const kind of hasBrowser ? ["scratchpad", "browser"] : ["scratchpad"]) {
    for (const capability of [false, true]) {
    let bound = true, unavailable = false;
    const state = createPluginRegistryState({ runtime: {}, logger: {}, activateGlobalSideEffects: false });
    const registrar = createToolHookRegistrars(state);
    const record = { id: "administrateme", origin: "config", enabled: true, explicitlyEnabled: true,
      source: path.join(plugin, "index.js"), hookCount: 0 };
    const api = {
      config: {}, logger: { warn() {}, info() {}, error() {} },
      on: (name, fn, options) => registrar.registerTypedHook(record, name, name === "before_prompt_build" ? async (...args) => {
        const result = await fn(...args);
        if (result !== undefined) assert.deepEqual(Object.keys(result), ["toolsAllow"], "candidate hook must supply only finite toolsAllow");
        return result;
      } : fn, options, { allowConversationAccess: true, allowPromptInjection: capability }),
      registerAgentToolResultMiddleware() {}, // This probe tests native admission, not result projection.
      runtime: { config: { current: () => ({}) } },
    };
    const runtime = { transport: { config: readPluginConfig({ serviceWorkEnabled: true }), request: async (endpoint, body) => {
      assert.equal(body.provenance.actor_id, "service-principal:chief-of-staff");
      assert.equal(body.provenance.session_key, registration.sessionKey);
      if (unavailable) throw new Error("synthetic source-policy outage");
      assert(endpoint.endsWith("/policy"));
      return kind === "browser" ? { bound, mode: "read" } : { bound, mode: "create", artifact_request_id: "fixture-note", artifact_task_id: "fixture-task" };
    } }, trackedSessions: new Map() };
    (kind === "browser" ? registerWorkstationBrowserHooks : registerWorkstationWorkHooks)(api, runtime);
    initializeGlobalHookRunner(state.registry);
    assert.equal(nativeHookRelayEventHasLocalWork(registration, "pre_tool_use"), true);
    assert.equal(nativeHookRelayEventToolMatcher(registration, "pre_tool_use"), undefined, "native relay must match ambient connectors, not only dynamic exec");
    const tools = await getGlobalHookRunner().runBeforePromptBuild({ prompt: "fixture exact request", messages: [] }, registration);
    if (!capability) {
      assert.equal(tools, undefined, "false-setting must reject the entire prompt-build hook");
      assert(state.registry.diagnostics.some((row) => row.message.includes("allowPromptInjection=false")));
      resetGlobalHookRunner();
      continue;
    }
    assert.deepEqual(Object.entries(tools).filter(([, value]) => value !== undefined).map(([key]) => key), ["toolsAllow"], "merged hook must contain no defined prose");
    assert.deepEqual(tools.toolsAllow, kind === "browser" ? ["browser"] : ["administrateme_service_private_work", "administrateme_service_private_state"]);
    assert.equal(shouldEnableCodexAppServerNativeToolSurface({ toolsAllow: tools.toolsAllow, config: {}, sessionId: registration.sessionId, sessionKey: registration.sessionKey }), false);
    const invoke = async (name, input = {}) => {
      const invocation = normalizeNativeHookInvocation({ registration, event: "pre_tool_use", rawPayload: {
        hook_event_name: "PreToolUse", tool_name: name, tool_use_id: `fixture-${name}`, tool_input: input,
      } });
      return processNativeHookRelayInvocation({ registration, invocation, adapter: getNativeHookRelayProviderAdapter("codex") });
    };
    for (const name of ["Bash", "exec_command", "apply_patch", "codex_apps.google_calendar.get_profile", "codex_apps.google_calendar.create_event", "mcp__fixture__read_record"]) {
      const denied = await invoke(name);
      const response = JSON.parse(denied.stdout);
      assert.equal(response.hookSpecificOutput.permissionDecision, "deny", `${kind} admitted native ${name}`);
    }
    unavailable = true;
    assert.equal(JSON.parse((await invoke("codex_apps.google_calendar.get_profile")).stdout).hookSpecificOutput.permissionDecision, "deny");
    unavailable = false; bound = false;
    const unbound = await invoke("Bash", { command: "read installed skill" });
    assert.equal(unbound.exitCode, 0);
    assert(!unbound.stdout.includes('"deny"'), "ordinary unbound service work was globally disabled");
    resetGlobalHookRunner();
    }
  }
  console.log("PINNED CODEX WORKER POLICY: finite native surface, all-name relay, ambient MCP/apps denial, outage denial, ordinary service preserved: PASS");
} finally {
  resetGlobalHookRunner();
  if (originalSecret === undefined) delete process.env.ADMINME_OPENCLAW_SHARED_SECRET;
  else process.env.ADMINME_OPENCLAW_SHARED_SECRET = originalSecret;
  await rm(directory, { recursive: true, force: true });
}
