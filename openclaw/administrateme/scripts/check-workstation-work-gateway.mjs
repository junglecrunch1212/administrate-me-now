/** Real pinned Gateway: plugin RPC identity, signed service context, internal lifecycle and revocation.
 * Hosted disposable state only; synthetic loopback model and real encrypted error receipt, no provider credentials.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash, createHmac, randomBytes, randomUUID } from "node:crypto";
import { copyFile, mkdtemp, mkdir, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";
import { startBrowserTransport } from "../../../tests/fixtures/openclaw_browser_transport.mjs";
import { startNativeFailureBackend, startWorkstationWorkFixtures } from "../../../tests/fixtures/openclaw_workstation_work_probe.mjs";

assert.equal(process.env.GITHUB_ACTIONS, "true", "Use isolated hosted CI for candidate execution");
const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const source = path.resolve(process.argv[2]);
const versions = JSON.parse(await readFile(path.join(repository, "bootstrap/versions.json"), "utf8"));
assert.equal(path.basename(source), versions.openclaw.commit);
assert.equal(JSON.parse(await readFile(path.join(source, "package.json"), "utf8")).version, versions.openclaw.version);
const directory = await mkdtemp(path.join(tmpdir(), "adminme-service-gateway-"));
const state = path.join(directory, "state");
const plugin = path.join(directory, "plugin");
const secret = randomBytes(32).toString("hex");
const token = randomBytes(32).toString("hex");
const principal = "service-principal:chief-of-staff";
const agent = `am-service-${createHash("sha256").update(principal).digest("hex").slice(0, 16)}`;
function validSession(candidate) {
  const prefix = `agent:${agent}:administrateme-service-`;
  if (typeof candidate !== "string" || !candidate.startsWith(prefix)) return false;
  const suffix = candidate.slice(prefix.length);
  if (!/^[0-9a-f]{32}-[0-9a-f]{32}$/.test(suffix)) return false;
  const [digest, signature] = suffix.split("-");
  const expected = createHmac("sha256", secret).update(`service-session|${principal}|${digest}`).digest("hex").slice(0, 32);
  return signature === expected;
}
const gmailSelf = process.argv[3] === "--browser-gmail";
const browserProbe = process.argv[3] === "--browser" || gmailSelf;
const relayPort = browserProbe ? await freePort() : undefined;
const browser = browserProbe ? await startBrowserTransport({ directory, state, relayPort, gmailSelf }) : undefined;
const sessions = new Set();
const conversations = new Map();
const member = "member-conversation-fixture";
const memberAgent = `adminme-${createHash("sha256").update(member).digest("hex").slice(0, 16)}`;
let failureBackend, fixtures;
let gateway, socket, logs = "";
const nativeWorkers = [], browserHooks = [];
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, label, timeout = 150_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await check(); if (value) return value;
    if (gateway && (gateway.exitCode !== null || gateway.signalCode !== null)) throw new Error(`Gateway exited during ${label}`);
    await pause(300);
  }
  throw new Error(`Timed out: ${label}`);
}
async function stop() {
  socket?.close(); socket = undefined;
  if (!gateway || gateway.exitCode !== null || gateway.signalCode !== null) return;
  const child = gateway; child.kill("SIGTERM");
  await Promise.race([new Promise((resolve) => child.once("exit", resolve)), pause(12_000)]);
  if (child.exitCode === null && child.signalCode === null) { child.kill("SIGKILL"); await new Promise((resolve) => child.once("exit", resolve)); }
}
async function freePort() {
  const server = net.createServer(); await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port; await new Promise((resolve) => server.close(resolve)); return port;
}
const port = await freePort();
const configPath = path.join(directory, "openclaw.json");
const env = Object.fromEntries(["PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "LOGNAME"].filter((key) => process.env[key] !== undefined).map((key) => [key, process.env[key]]));
Object.assign(env, { OPENCLAW_STATE_DIR: state, OPENCLAW_CONFIG_PATH: configPath, OPENCLAW_GATEWAY_PORT: String(port), OPENCLAW_GATEWAY_TOKEN: token,
  OPENCLAW_NO_RESPAWN: "1", OPENCLAW_DISABLE_BONJOUR: "1", NODE_DISABLE_COMPILE_CACHE: "1",
  OPENCLAW_SKIP_GMAIL_WATCHER: "1", OPENCLAW_SKIP_CANVAS_HOST: "1", OPENCLAW_SKIP_BROWSER_CONTROL_SERVER: "1", ADMINME_OPENCLAW_SHARED_SECRET: secret });
if (browserProbe) delete env.OPENCLAW_SKIP_BROWSER_CONTROL_SERVER;
async function start() {
  gateway = spawn(process.execPath, [path.join(source, "openclaw.mjs"), "gateway", "run"], { cwd: directory, env, stdio: ["ignore", "pipe", "pipe"] });
  for (const stream of [gateway.stdout, gateway.stderr]) {
    let buffered = "";
    stream.on("data", (chunk) => {
      logs = (logs + chunk).slice(-50_000); buffered += chunk;
      const lines = buffered.split("\n"); buffered = lines.pop().slice(-50_000);
      for (const line of lines) for (const [marker, target] of [["SYNTHETIC_WORKER_STATE ", nativeWorkers], ["SYNTHETIC_BROWSER_BOUNDARY ", browserHooks]]) {
        const offset = line.indexOf(marker); if (offset < 0) continue;
        try { target.push(JSON.parse(line.slice(offset + marker.length))); if (target.length > 200) target.shift(); }
        catch { /* Incomplete metadata cannot establish a native run or capture. */ }
      }
    });
  }
  await until(async () => {
    try { return (await fetch(`http://127.0.0.1:${port}/readyz`, { signal: AbortSignal.timeout(1_000) })).ok; } catch { return false; }
  }, "normal Gateway readiness");
  socket = new WebSocket(`ws://127.0.0.1:${port}`);
  let challenge = false;
  const pending = new Map();
  socket.addEventListener("error", () => {});
  socket.addEventListener("message", ({ data }) => {
    const frame = JSON.parse(String(data));
    if (frame.type === "event" && frame.event === "connect.challenge") challenge = true;
    if (frame.type !== "res") return;
    const found = pending.get(frame.id); if (!found) return;
    pending.delete(frame.id); clearTimeout(found.timer);
    if (frame.ok) found.resolve(frame.payload); else found.reject(new Error(`RPC ${found.method}: ${frame.error?.message ?? frame.error?.code}`));
  });
  function request(method, params) {
    return new Promise((resolve, reject) => {
      const id = randomUUID();
      const timer = setTimeout(() => { pending.delete(id); reject(new Error(`RPC timeout: ${method}`)); }, 20_000);
      pending.set(id, { resolve, reject, timer, method });
      socket.send(JSON.stringify({ type: "req", id, method, params }));
    });
  }
  await until(() => challenge, "native challenge", 10_000);
  const hello = await request("connect", { minProtocol: 4, maxProtocol: 4, client: { id: "gateway-client", version: "synthetic-service", platform: process.platform, mode: "backend" },
    role: "operator", scopes: ["operator.admin", "operator.read", "operator.write"], caps: [], auth: { token } });
  assert.equal(hello.type, "hello-ok");
  return request;
}
try {
  failureBackend = browserProbe && !gmailSelf ? await startNativeFailureBackend({ repository, directory, secret }) : undefined;
  fixtures = await startWorkstationWorkFixtures({ secret, agent, sessions, validSession, conversations, browser, failureBackend });
  await mkdir(state); await mkdir(path.join(plugin, "node_modules"), { recursive: true });
  const sourceDirectory = path.join(repository, "openclaw/administrateme/src");
  for (const name of (await readdir(sourceDirectory)).filter((entry) => entry.endsWith(".ts"))) {
    let input = await readFile(path.join(sourceDirectory, name), "utf8");
    if (browserProbe && name === "index.ts") {
      // Capture the actual host boundary only inside this synthetic hosted
      // fixture. Production logs never include native page/tool payloads.
      const registration = "registerWorkstationBrowserHooks(api, runtime);";
      assert(input.includes(registration));
      input = input.replace(registration, `registerWorkstationBrowserHooks({ ...api,
        on(name, callback, options) {
          api.on(name, name === "before_tool_call" ? async (event, context) => {
            const result = await callback(event, context);
            if (event.toolName === "browser") console.error("SYNTHETIC_BROWSER_BOUNDARY", JSON.stringify({ phase: "before_tool_call", at_ms: Date.now(), session_digest: typeof context.sessionKey === "string" ? fixtureHash("sha256").update(context.sessionKey).digest("hex") : undefined, toolCallId: event.toolCallId, contextCallId: context.toolCallId, admitted: !!result?.params, blocked: result?.block === true }));
            return result;
          } : callback, options);
        },
        registerAgentToolResultMiddleware(callback, options) {
          api.registerAgentToolResultMiddleware(async (event, context) => {
            console.error("SYNTHETIC_BROWSER_BOUNDARY", JSON.stringify({ phase: "native_result", at_ms: Date.now(), session_digest: typeof context.sessionKey === "string" ? fixtureHash("sha256").update(context.sessionKey).digest("hex") : undefined, toolCallId: event.toolCallId, isError: event.isError }));
            return callback(event, context);
          }, options);
        }
      }, runtime);`);
      // Observe the actual native API response without replacing the run, wait,
      // signing code, arguments, return value or exception. This gives the
      // fixture its real worker run ID when the parent's bounded wait expires.
      input = `import { createHash as fixtureHash } from "node:crypto";\n${input}`;
      const workRegistration = "registerWorkstationWorkTool(api, runtime);";
      assert.equal(input.split(workRegistration).length, 2);
      input = input.replace(workRegistration, `registerWorkstationWorkTool({ ...api, runtime: { ...api.runtime, subagent: {
        ...api.runtime.subagent,
        async run(params) {
          const session_digest = fixtureHash("sha256").update(params.sessionKey).digest("hex");
          console.error("SYNTHETIC_WORKER_STATE", JSON.stringify({ phase: "requested", session_digest, at_ms: Date.now() }));
          try {
            const result = await api.runtime.subagent.run(params);
            console.error("SYNTHETIC_WORKER_STATE", JSON.stringify({ phase: "admitted", session_digest, run_id: result.runId, at_ms: Date.now() }));
            return result;
          } catch (error) {
            console.error("SYNTHETIC_WORKER_STATE", JSON.stringify({ phase: "run_threw", session_digest, at_ms: Date.now() }));
            throw error;
          }
        },
        async waitForRun(params) {
          console.error("SYNTHETIC_WORKER_STATE", JSON.stringify({ phase: "wait_started", run_id: params.runId, at_ms: Date.now() }));
          try {
            const result = await api.runtime.subagent.waitForRun(params);
            console.error("SYNTHETIC_WORKER_STATE", JSON.stringify({ phase: "wait_returned", run_id: params.runId, status: result.status, at_ms: Date.now() }));
            return result;
          } catch (error) {
            console.error("SYNTHETIC_WORKER_STATE", JSON.stringify({ phase: "wait_threw", run_id: params.runId, at_ms: Date.now() }));
            throw error;
          }
        }
      } } }, runtime);`);
    }
    if (failureBackend && name === "index.ts") {
      // This test-only hook selects a deliberately unusable synthetic model.
      // The pinned runner must reject its context window before model/browser
      // contact. Native subagent.run, waitForRun and failure signing stay exact.
      const registration = "registerWorkstationWorkHooks(api, runtime);";
      assert.equal(input.split(registration).length, 2);
      input = input.replace(registration, `${registration}
        api.on("before_model_resolve", (event, context) => {
          if (context.agentId === ${JSON.stringify(agent)} && event.prompt.includes("PROBE_WORKSTATION_NATIVE_FAILURE")) {
            return { providerOverride: "fixture", modelOverride: "pre-provider-failure" };
          }
        });`);
    }
    await writeFile(path.join(plugin, name.replace(/\.ts$/, ".js")), ts.transpileModule(input, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ESNext } }).outputText);
  }
  await symlink(source, path.join(plugin, "node_modules/openclaw"), "dir");
  await symlink(path.join(repository, "openclaw/administrateme/node_modules/typebox"), path.join(plugin, "node_modules/typebox"), "dir");
  await writeFile(path.join(plugin, "package.json"), JSON.stringify({ name: "@administrateme/service-gateway-probe", type: "module", version: "1.0.0", openclaw: { extensions: ["./index.js"] } }));
  await copyFile(path.join(repository, "openclaw/administrateme/openclaw.plugin.json"), path.join(plugin, "openclaw.plugin.json"));
  const workspace = path.join(state, "service-workspace");
  await mkdir(workspace);
  for (const filename of ["AGENTS.md", "IDENTITY.md"]) {
    await copyFile(path.join(repository, "openclaw/administrateme/workspace/service-private", filename), path.join(workspace, filename));
  }
  const memberWorkspace = path.join(state, "member-workspace");
  await mkdir(memberWorkspace);
  await copyFile(path.join(repository, "openclaw/administrateme/workspace/AGENTS.md"), path.join(memberWorkspace, "AGENTS.md"));
  const model = "fixture/service-work";
  const memberAgents = [memberAgent, ...(failureBackend ? [failureBackend.member_agent_id] : [])];
  const modelDefinition = { id: "service-work", name: "service-work", api: "openai-responses", reasoning: false, input: ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 128000, maxTokens: 4096 };
  const modelDefinitions = [modelDefinition, ...(failureBackend ? [{ ...modelDefinition, id: "pre-provider-failure", name: "pre-provider-failure", contextWindow: 1, maxTokens: 1 }] : [])];
  const tools = [...(browserProbe ? ["browser"] : []), "administrateme_service_private_work", "administrateme_service_private_work_state", "administrateme_service_private_state", "administrateme_service_context"];
  const config = {
    gateway: { mode: "local", bind: "loopback", port, auth: { mode: "token", token }, controlUi: { enabled: false }, reload: { mode: "off" } },
    agents: { defaults: { workspace, maxConcurrent: 1, subagents: { maxConcurrent: 1 }, heartbeat: { every: "0m" }, model: { primary: model }, models: Object.fromEntries(modelDefinitions.map(({ id }) => [`fixture/${id}`, { params: { transport: "sse", openaiWsWarmup: false } }])) }, list: [{ id: agent, workspace, tools: { allow: tools } }, ...memberAgents.map((id) => ({ id, workspace: memberWorkspace, tools: { allow: ["administrateme_workstation_work"] } }))] },
    models: { mode: "replace", providers: { fixture: { baseUrl: `${fixtures.baseUrl}/v1`, apiKey: "synthetic-fixture-only", api: "openai-responses", models: modelDefinitions } } },
    tools: { codeMode: false },
    plugins: { allow: ["administrateme", ...(browserProbe ? ["browser"] : [])], load: { paths: [plugin] }, slots: { memory: "none" }, entries: { administrateme: { enabled: true, config: { baseUrl: fixtures.baseUrl, memberAgentIds: memberAgents }, hooks: { allowConversationAccess: true, allowPromptInjection: true } } } },
    channels: {}, cron: { enabled: false }, browser: browserProbe ? { enabled: true, evaluateEnabled: true, allowSystemProfileImport: false, defaultProfile: "chrome", profiles: { chrome: { driver: "extension", cdpPort: relayPort } } } : { enabled: false }, discovery: { mdns: { mode: "off" } }, update: { checkOnStart: false, auto: { enabled: false } }, logging: { file: path.join(state, "gateway.log") },
  };
  await writeFile(configPath, JSON.stringify(config), { mode: 0o600 });
  const request = await start();
  if (browser) {
    // Starting the native browser profile creates its host-local relay; the
    // synthetic extension joins with disposable state, never a user profile.
    const starting = request("browser.request", { method: "POST", path: "/start", query: { profile: "chrome" } }).catch((error) => error);
    await browser.connectRelay();
    if (await starting instanceof Error) await request("browser.request", { method: "POST", path: "/start", query: { profile: "chrome" } });
  }
  // Run both real chat.send routes through the actual pinned host. Signed input
  // metadata travels in a separate plugin RPC, never as a privileged prompt.
  for (const channel of ["administrateme-portal", "gmail"]) {
    const account = channel === "gmail" ? "cos@example.invalid" : "member-portal";
    const thread = channel === "gmail" ? "fixture-email-thread" : "";
    const peer = createHmac("sha256", secret).update(channel === "gmail" ? JSON.stringify(["gmail-peer", "fixture-tenant", member, account, thread]) : `portal-peer|fixture-tenant|${member}`).digest("hex").slice(0, channel === "gmail" ? 32 : 20);
    const signature = createHmac("sha256", secret).update(`${channel === "gmail" ? "gmail" : "portal"}-session|${peer}`).digest("hex").slice(0, 32);
    const session = `agent:${memberAgent}:administrateme-${channel === "gmail" ? "gmail" : "portal"}-${peer}-${signature}`;
    sessions.add(session);
    const inputText = channel === "gmail" ? "PROBE_WORKSTATION_GMAIL: draft a private garage plan." : "PROBE_WORKSTATION_PORTAL: draft a private garage plan.";
    const ticket = {
      source_event_id: `fixture-${channel}-instruction`, session_key: session, agent_id: memberAgent,
      actor_id: `${channel === "gmail" ? "gmail" : "portal"}:${peer}`, channel_id: channel, account_id: account,
      message_id: `fixture-${channel}-message`, thread_id: thread,
      text_sha256: createHash("sha256").update(inputText).digest("hex"), nonce: randomBytes(16).toString("hex"), expires_at_ms: Date.now() + 9000,
    };
    const fields = ["source_event_id", "session_key", "agent_id", "actor_id", "channel_id", "account_id", "message_id", "thread_id", "text_sha256", "nonce"];
    const signed = { ...ticket, signature: createHmac("sha256", secret).update(["cos-conversation-input-v1", ...fields.map((key) => ticket[key]), String(ticket.expires_at_ms)].join("|")).digest("hex") };
    conversations.set(ticket.source_event_id, { ...ticket, member_id: member });
    await assert.rejects(request("administrateme.conversation.prepare", { ...signed, signature: "0".repeat(64) }), /input unavailable/);
    assert.equal((await request("administrateme.conversation.prepare", signed)).accepted, true);
    await assert.rejects(request("administrateme.conversation.prepare", signed), /input unavailable/);
    const admitted = await request("chat.send", { sessionKey: session, message: inputText, deliver: false, idempotencyKey: `native-${channel}-input` });
    await until(async () => {
      if (fixtures.errors.length) throw new Error(fixtures.errors.join("; "));
      const result = await request("agent.wait", { runId: admitted.runId, timeoutMs: 1000 });
      if (result.status === "error") throw new Error(`Native ${channel} input failed`);
      return result.status === "ok";
    }, `native ${channel} conversation`);
    await until(() => fixtures.parentResults.some((row) => row.source === ticket.source_event_id), `${channel} truthful initial tool result`);
    if (channel === "gmail") await until(() => fixtures.conversationReplies.some((row) => row.source === ticket.source_event_id), "signed native Gmail final reply");
    if (browser) {
      // A parent wait timeout is not failure or success. Keep the real worker
      // alive, require its actual native terminal and capture, then use the
      // existing fresh-member status path rather than invoking browser again.
      const worker = fixtures.workerState(ticket.source_event_id); assert(worker);
      const run = await until(() => nativeWorkers.find((entry) => entry.phase === "admitted" && entry.session_digest === worker.session_digest), `${channel} actual native worker run ID`);
      assert.equal(nativeWorkers.filter((entry) => entry.phase === "admitted" && entry.session_digest === worker.session_digest).length, 1);
      await until(async () => {
        if (fixtures.errors.length) throw new Error(fixtures.errors.join("; "));
        const result = await request("agent.wait", { runId: run.run_id, timeoutMs: 1000 });
        if (["ok", "error"].includes(result.status)) nativeWorkers.push({ phase: "observed_terminal", run_id: run.run_id, status: result.status, at_ms: Date.now() });
        assert.notEqual(result.status, "error", `${channel} browser worker actually failed`);
        return result.status === "ok";
      }, `${channel} native browser terminal (pending is not a pass)`);
      assert.equal(fixtures.workerState(ticket.source_event_id)?.verified, true, "native completion must include verified capture");
      const initial = fixtures.parentResults.find((entry) => entry.source === ticket.source_event_id);
      assert(["verified", "pending_or_unknown"].includes(initial.status));
      assert.equal(initial.browser_request_event_id, `browser:${ticket.source_event_id}`);
      const beforeWorkers = fixtures.workerCount, beforeRuns = nativeWorkers.filter((entry) => entry.phase === "admitted").length;
      const beforeReads = await browser.privateBodyReadCount();
      const statusText = `${channel === "gmail" ? "PROBE_WORKSTATION_GMAIL" : "PROBE_WORKSTATION_PORTAL"} PROBE_RETAINED_BROWSER_STATUS: Check the retained result for ${initial.browser_request_event_id}.`;
      const statusTicket = { ...ticket, source_event_id: `fixture-${channel}-status-instruction`, message_id: `fixture-${channel}-status-message`,
        text_sha256: createHash("sha256").update(statusText).digest("hex"), nonce: randomBytes(16).toString("hex"), expires_at_ms: Date.now() + 9000 };
      const signedStatus = { ...statusTicket, signature: createHmac("sha256", secret).update(["cos-conversation-input-v1", ...fields.map((key) => statusTicket[key]), String(statusTicket.expires_at_ms)].join("|")).digest("hex") };
      conversations.set(statusTicket.source_event_id, { ...statusTicket, member_id: member, original_source: ticket.source_event_id });
      assert.equal((await request("administrateme.conversation.prepare", signedStatus)).accepted, true);
      const statusRun = await request("chat.send", { sessionKey: session, message: statusText, deliver: false, idempotencyKey: `native-${channel}-status` });
      await until(async () => {
        if (fixtures.errors.length) throw new Error(fixtures.errors.join("; "));
        const result = await request("agent.wait", { runId: statusRun.runId, timeoutMs: 1000 });
        assert.notEqual(result.status, "error", "fresh authenticated status failed"); return result.status === "ok";
      }, `${channel} retained browser status`);
      assert.equal(fixtures.statusReads.filter((entry) => entry.input_source === statusTicket.source_event_id).length, 1);
      if (channel === "gmail") await until(() => fixtures.conversationReplies.some((row) => row.source === statusTicket.source_event_id), "signed native Gmail retained-status reply");
      assert.equal(fixtures.workerCount, beforeWorkers, "status must not admit another worker");
      assert.equal(nativeWorkers.filter((entry) => entry.phase === "admitted").length, beforeRuns, "status must not start another native run");
      assert.equal(await browser.privateBodyReadCount(), beforeReads, "status must not read the browser again");
      console.log(`Native ${channel} initial=${initial.status}; actual worker terminal/capture and same-member retained status: PASS`);
    }
    await until(() => fixtures.conversationReceipts.some((row) => row.source === ticket.source_event_id), `${channel} verified source-bound receipt`);
  }
  if (failureBackend) {
    // Obtain the real encrypted source's short-lived signed input only after
    // startup/positive runs. No synthetic native result or worker provenance.
    const prepared = await failureBackend.call({ operation: "prepare" });
    failureBackend.sourceId = prepared.source_event_id;
    sessions.add(prepared.ticket.session_key);
    assert.equal((await request("administrateme.conversation.prepare", prepared.ticket)).accepted, true);
    const beforeReads = await browser.privateBodyReadCount();
    const admitted = await request("chat.send", { sessionKey: prepared.ticket.session_key, message: prepared.text, deliver: false, idempotencyKey: "native-worker-terminal-error" });
    await until(async () => {
      if (fixtures.errors.length) throw new Error(fixtures.errors.join("; "));
      const result = await request("agent.wait", { runId: admitted.runId, timeoutMs: 1000 });
      assert.notEqual(result.status, "error", "Parent failed instead of returning the worker error");
      return result.status === "ok";
    }, "authenticated parent receives actual native worker error");
    assert.equal(fixtures.nativeFailures.length, 1);
    const terminal = fixtures.nativeFailures[0];
    assert.equal(terminal.status, "failed"); assert.equal(terminal.reason, "native_worker_failed");
    const native = await request("agent.wait", { runId: terminal.native_run_id, timeoutMs: 1000 });
    assert.equal(native.status, "error", "Retained error must identify an actual failed native run");
    assert.match(native.error, /Model context window too small \(1 tokens;/, "Native failure must be the selected pre-provider guard");
    const retained = await failureBackend.call({ operation: "verify" });
    assert.equal(retained.verified, true);
    assert.equal(retained.native_run_id, terminal.native_run_id);
    assert.equal(retained.failure_receipt_event_id, terminal.failure_receipt_event_id);
    assert.equal(retained.provider_effect_count, 0);
    assert.equal(fixtures.failureModelCalls, 0);
    assert.equal(await browser.privateBodyReadCount(), beforeReads, "Failed worker must not read a browser body");
    console.log("Actual native pre-provider error -> production wait/sign -> encrypted failure -> same-admission replay/status: PASS");
  }
  assert.deepEqual(fixtures.conversationReceipts.map((row) => row.channel), ["administrateme-portal", "gmail"]);
  assert.deepEqual(fixtures.conversationReplies.map((row) => row.channel), browser ? ["gmail", "gmail"] : ["gmail"]);
  if (browser) assert.deepEqual(fixtures.statusReads.map((row) => row.channel), ["administrateme-portal", "gmail"]);
  assert.deepEqual(fixtures.errors, []);
  if (browser) {
    assert.deepEqual(browser.errors, []);
    if (gmailSelf) {
      const { identityBoundReadExpression, verifiedBrowserRead, prepareBrowserRead } = await import(pathToFileURL(path.join(plugin, "workstation-browser.js")).href);
      const native = { currentConfig: () => config };
      const prepared = prepareBrowserRead(native, browser.resource, { action: "snapshot" });
      await browser.armWrongAccountProof();
      const refused = await request("browser.request", { method: "POST", path: "/act", query: { profile: "chrome" },
        body: { kind: "evaluate", targetId: browser.targetId, fn: identityBoundReadExpression(browser.resource) } });
      assert.equal(refused.result.kind, "identity_unverified");
      assert.equal(refused.result.reason, "wrong_account_workspace_or_role");
      assert.equal(refused.result.text, undefined);
      assert.equal(await browser.privateBodyReadCount(), 0, "changed-account proof must precede mailbox text access");
      assert.throws(() => verifiedBrowserRead(native, prepared, browser.resource, { details: refused }));

      console.log("Gmail wrong-account refusal before mailbox text access: PASS");
    }
    console.log("Pinned native member -> CoS browser -> actual disposable Chrome DOM identity -> retained source -> same-origin return verified; synthetic extension/account only.");
  } else console.log("Pinned native conversation -> CoS subagent scratchpad -> actual read-back -> source-bound result verified for portal/Gmail; synthetic loopback fixtures only. No live provider effect.");
} catch (error) {
  let redacted = logs.replaceAll(token, "[redacted]").replaceAll(secret, "[redacted]");
  for (const session of sessions) redacted = redacted.replaceAll(session, "[service-session]");
  console.error(redacted);
  if (browser) {
    const stages = ["administrateme-portal", "gmail"].map((channel) => {
      const worker = fixtures?.workerState(`fixture-${channel}-instruction`);
      const run = nativeWorkers.find((entry) => entry.phase === "admitted" && entry.session_digest === worker?.session_digest);
      const terminal = run && nativeWorkers.findLast((entry) => entry.run_id === run.run_id && ["ok", "error"].includes(entry.status));
      const boundary = browserHooks.filter((entry) => worker && entry.session_digest === worker.session_digest);
      return { channel, stage: worker?.verified ? "terminal_capture" : terminal?.status === "error" ? "terminal_error"
        : terminal?.status === "ok" ? "terminal_without_capture" : !run ? "never_started"
        : boundary.some((entry) => entry.phase === "native_result") ? "native_tool_returned_without_capture"
        : boundary.some((entry) => entry.phase === "before_tool_call" && entry.admitted) ? "native_tool_still_running" : "native_worker_running" };
    });
    // Bound diagnostic CDP read; inability to read is unknown, never zero.
    const bodyReadCount = await Promise.race([browser.privateBodyReadCount().catch(() => "unknown"), pause(1000).then(() => "unknown")]);
    console.error("SYNTHETIC_BROWSER_TRANSPORT", JSON.stringify({ stages, nativeWorkers, browserHooks, errors: browser.errors, commands: browser.commands, bodyReadCount }));
  }
  throw error;
} finally { await stop(); await fixtures?.close(); await failureBackend?.close(); await browser?.close(); await rm(directory, { recursive: true, force: true }); }
