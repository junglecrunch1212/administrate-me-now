/** Pinned Codex native hook protocol + actual candidate worker policy, hosted only.
 * No Codex login, model/provider call, browser, command or connector is executed.
 */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { Readable, Writable } from "node:stream";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

assert.equal(process.env.GITHUB_ACTIONS, "true", "Use isolated hosted CI");
const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const source = path.resolve(process.argv[2]);
const versions = JSON.parse(await readFile(path.join(repository, "bootstrap/versions.json"), "utf8"));
assert.equal(path.basename(source), versions.openclaw.commit);
assert.equal(JSON.parse(await readFile(path.join(source, "package.json"), "utf8")).version, versions.openclaw.version);
const directory = await mkdtemp(path.join(tmpdir(), "adminme-codex-worker-policy-"));
const originalState = process.env.OPENCLAW_STATE_DIR;
process.env.OPENCLAW_STATE_DIR = path.join(directory, "state");
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
const { prepareCodexAttemptPrompt } = await load("extensions/codex/src/app-server/run-attempt-prompt.ts");
const { registerNativeHookRelay, invokeNativeHookRelay, testing: relayTesting } = await load("src/agents/harness/native-hook-relay.ts");
const { runNativeHookRelayCli } = await load("src/cli/native-hook-relay-cli.ts");
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
  if (hasBrowser) {
    const { OpenClawSchema } = await load("src/config/zod-schema.ts");
    const prepared = JSON.parse(execFileSync(process.argv[3], ["-c", "import json; from adminme.workstation_browser import prepare_browser_gateway; print(json.dumps(prepare_browser_gateway({})))"], {
      cwd: repository, env: { ...process.env, PYTHONPATH: `${repository}:${path.join(repository, "bridge")}` }, encoding: "utf8",
    }));
    OpenClawSchema.parse(prepared);
    assert.deepEqual(prepared.browser.profiles.chrome, { driver: "extension" });
    prepared.browser.profiles.chrome.color = "#4477AA";
    assert.equal(OpenClawSchema.safeParse(prepared).success, false, "exact pin must reject the failed production preparation shape");
    console.log("PINNED BROWSER CONFIG: actual supported preparation passes exact native schema; unsupported color rejected: PASS");
  }
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
    const browserConfig = { browser: { enabled: true, evaluateEnabled: true, allowSystemProfileImport: false, defaultProfile: "chrome", profiles: { chrome: { driver: "extension" } } } };
    const resource = { resource_id: "fixture-page", inventory_binding_id: "fixture-browser", binding_revision: 1, policy_revision: 1,
      native_target_id: "fixture-native-target", target_evidence_reference: "fixture:operator-tab", profile: "chrome",
      url: "https://fixture.invalid/read", expected_identity: { account: "cos@fixture.invalid" },
      identity_probes: { account: { selector: "#account", attribute: "textContent", extraction: "exact" } }, route_sha256: "a".repeat(64) };
    const state = createPluginRegistryState({ runtime: {}, logger: {}, activateGlobalSideEffects: false });
    const registrar = createToolHookRegistrars(state);
    const record = { id: "administrateme", origin: "config", enabled: true, explicitlyEnabled: true,
      source: path.join(plugin, "index.js"), hookCount: 0 };
    const api = {
      config: {}, logger: { warn() {}, info() {}, error() {} },
      on: (name, fn, options) => {
        assert.notEqual(name, "before_prompt_build", "worker must not use incompatible turn-scoped toolsAllow");
        if (name === "before_tool_call") assert.equal(options?.matcher, undefined, "guard must register for every native name");
        registrar.registerTypedHook(record, name, fn, options, { allowConversationAccess: true, allowPromptInjection: capability });
      },
      registerAgentToolResultMiddleware() {}, // This probe tests native admission, not result projection.
      runtime: { config: { current: () => browserConfig } },
    };
    const runtime = { transport: { config: readPluginConfig({ serviceWorkEnabled: true }), request: async (endpoint, body) => {
      assert.equal(body.provenance.actor_id, "service-principal:chief-of-staff");
      assert.equal(body.provenance.session_key, registration.sessionKey);
      if (unavailable) throw new Error("synthetic source-policy outage");
      assert(endpoint.endsWith("/policy"));
      return kind === "browser" ? { bound, mode: "read", resource } : { bound, mode: "create", artifact_request_id: "fixture-note", artifact_task_id: "fixture-task" };
    } }, trackedSessions: new Map() };
    (kind === "browser" ? registerWorkstationBrowserHooks : registerWorkstationWorkHooks)(api, runtime);
    initializeGlobalHookRunner(state.registry);
    assert.equal(nativeHookRelayEventHasLocalWork(registration, "pre_tool_use"), true);
    assert.equal(nativeHookRelayEventToolMatcher(registration, "pre_tool_use"), undefined, "native relay must match ambient connectors, not only dynamic exec");
    const tools = await getGlobalHookRunner().runBeforePromptBuild({ prompt: "fixture exact request", messages: [] }, registration);
    assert.equal(tools, undefined, "candidate supplies no turn-scoped filter regardless of legacy prompt capability");
    assert.equal(shouldEnableCodexAppServerNativeToolSurface({ config: {}, sessionId: registration.sessionId, sessionKey: registration.sessionKey }), true,
      "native tool visibility remains broader; execution must be intercepted");
    const promptContext = () => ({
      runtime: { connection: { params: { prompt: "fixture exact request", sessionId: registration.sessionId, modelId: "gpt-5.4", provider: "openai", config: {} },
        mutable: {}, contextSessionKey: registration.sessionKey, effectiveWorkspace: directory, appServer: { start: {} } },
        effectiveRuntimeModelId: "gpt-5.4", effectiveRuntimeProviderId: "openai", nativeToolSurfaceEnabled: true },
      attemptTools: { toolBridge: { specs: [], availableSpecs: [] } }, historyState: { messages: [] }, hookContext: registration,
      workspaceBootstrapContext: { bootstrapFiles: [] }, baseDeveloperInstructions: "Fixture policy", openClawPromptContext: "",
      skillsCollaborationInstructions: "", promptState: { promptText: "fixture exact request", developerInstructions: "Fixture policy" },
      codexContextProjectionMaxChars: 10000,
    });
    const preparedPrompt = await prepareCodexAttemptPrompt(promptContext());
    assert(preparedPrompt.turnState.codexTurnPromptText.includes("fixture exact request"), "actual Codex prompt boundary failed");
    const allowed = kind === "browser"
      ? await getGlobalHookRunner().runBeforeToolCall({ toolName: "browser", toolCallId: "fixture-read", params: { action: "snapshot" } }, { ...registration, toolCallId: "fixture-read" })
      : await getGlobalHookRunner().runBeforeToolCall({ toolName: "administrateme_service_private_state", toolCallId: "fixture-read", params: { task_id: "fixture-task" } }, registration);
    assert.notEqual(allowed?.block, true, "exact admitted dynamic call was blocked");
    if (kind === "browser") {
      assert.equal(allowed.params.action, "act");
      assert.equal(allowed.params.targetId, resource.native_target_id);
      assert.equal(allowed.params.request.kind, "evaluate");
      assert(allowed.params.request.fn.includes("identity_unverified"));
    }
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
    if (kind === "browser" && capability) {
      bound = true;
      const relay = registerNativeHookRelay({ ...registration, ttlMs: 1000, command: { executable: path.join(source, "openclaw.mjs") } });
      try {
        assert.equal(relay.shouldRelayEvent("pre_tool_use"), true);
        assert.equal(relay.toolMatcherForEvent("pre_tool_use"), undefined);
        assert(!relay.commandForEvent("pre_tool_use").includes("--pre-tool-use-unavailable"), "local policy must never generate a noop fallback");
        await new Promise((resolve) => setTimeout(resolve, Math.max(1, relay.expiresAtMs - Date.now() + 10)));
        for (const missing of [false, true]) {
          const relayId = missing ? "fixture-missing-relay" : relay.relayId;
          let output = "", gatewayFailure = "";
          const exitCode = await runNativeHookRelayCli({ provider: "codex", relayId, event: "pre_tool_use", timeout: "500" }, {
            stdin: Readable.from([JSON.stringify({ hook_event_name: "PreToolUse", tool_name: "Bash", tool_input: { command: "must not execute" } })]),
            stdout: new Writable({ write(chunk, _encoding, done) { output += chunk.toString(); done(); } }),
            stderr: new Writable({ write(_chunk, _encoding, done) { done(); } }),
            callGateway: async ({ params }) => {
              try { return await invokeNativeHookRelay(params); }
              catch (error) { gatewayFailure = error.message; throw error; }
            },
          });
          assert.equal(exitCode, 0);
          assert.match(gatewayFailure, missing ? /not found/ : /expired/, "native fallback must reject the actual missing/expired registration");
          assert.equal(JSON.parse(output).hookSpecificOutput.permissionDecision, "deny", "missing/expired relay must deny before any tool executes");
        }
      } finally { relay.unregister(); }
    }
    // Reproduce the rejected old prompt contract through the actual pinned
    // function; no model call or fabricated pre-admission attestation.
    if (capability) {
      registrar.registerTypedHook(record, "before_prompt_build", async () => ({ toolsAllow: ["browser"] }), undefined, { allowConversationAccess: true, allowPromptInjection: true });
      initializeGlobalHookRunner(state.registry);
      await assert.rejects(prepareCodexAttemptPrompt(promptContext()), /cannot enforce before_prompt_build toolsAllow/);
    }
    resetGlobalHookRunner();
    }
  }
  console.log("PINNED CODEX WORKER POLICY: actual Codex prompt boundary, exact dynamic admission, all-name relay, ambient MCP/apps and unavailable relay denial, ordinary service preserved: PASS");
} finally {
  resetGlobalHookRunner();
  relayTesting.clearNativeHookRelaysForTests();
  if (originalState === undefined) delete process.env.OPENCLAW_STATE_DIR;
  else process.env.OPENCLAW_STATE_DIR = originalState;
  if (originalSecret === undefined) delete process.env.ADMINME_OPENCLAW_SHARED_SECRET;
  else process.env.ADMINME_OPENCLAW_SHARED_SECRET = originalSecret;
  await rm(directory, { recursive: true, force: true });
}
