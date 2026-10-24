/** Real pinned Gateway: plugin RPC identity, signed service context, internal lifecycle and revocation.
 * Hosted disposable state only; synthetic loopback model/domain only, no provider credentials.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash, createHmac, randomBytes, randomUUID } from "node:crypto";
import { copyFile, mkdtemp, mkdir, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { startBrowserTransport } from "../../../tests/fixtures/openclaw_browser_transport.mjs";
import { startWorkstationWorkFixtures } from "../../../tests/fixtures/openclaw_workstation_work_probe.mjs";

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
const browserProbe = process.argv[3] === "--browser";
const relayPort = browserProbe ? await freePort() : undefined;
const browser = browserProbe ? await startBrowserTransport({ directory, state, relayPort }) : undefined;
const sessions = new Set();
const conversations = new Map();
const member = "member-conversation-fixture";
const memberAgent = `adminme-${createHash("sha256").update(member).digest("hex").slice(0, 16)}`;
const fixtures = await startWorkstationWorkFixtures({ secret, agent, sessions, validSession, conversations, browser });
let gateway, socket, logs = "";
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
  for (const stream of [gateway.stdout, gateway.stderr]) stream.on("data", (chunk) => { logs = (logs + chunk).slice(-50_000); });
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
  await mkdir(state); await mkdir(path.join(plugin, "node_modules"), { recursive: true });
  const sourceDirectory = path.join(repository, "openclaw/administrateme/src");
  for (const name of (await readdir(sourceDirectory)).filter((entry) => entry.endsWith(".ts"))) {
    const input = await readFile(path.join(sourceDirectory, name), "utf8");
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
  const tools = [...(browserProbe ? ["browser"] : []), "administrateme_service_private_work", "administrateme_service_private_work_state", "administrateme_service_private_state", "administrateme_service_context"];
  const config = {
    gateway: { mode: "local", bind: "loopback", port, auth: { mode: "token", token }, controlUi: { enabled: false }, reload: { mode: "off" } },
    agents: { defaults: { workspace, maxConcurrent: 1, subagents: { maxConcurrent: 1 }, heartbeat: { every: "0m" }, model: { primary: model }, models: { [model]: { params: { transport: "sse", openaiWsWarmup: false } } } }, list: [{ id: agent, workspace, tools: { allow: tools } }, { id: memberAgent, workspace: memberWorkspace, tools: { allow: ["administrateme_workstation_work"] } }] },
    models: { mode: "replace", providers: { fixture: { baseUrl: `${fixtures.baseUrl}/v1`, apiKey: "synthetic-fixture-only", api: "openai-responses", models: [{ id: "service-work", name: "service-work", api: "openai-responses", reasoning: false, input: ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 128000, maxTokens: 4096 }] } } },
    tools: { codeMode: false },
    plugins: { allow: ["administrateme", ...(browserProbe ? ["browser"] : [])], load: { paths: [plugin] }, slots: { memory: "none" }, entries: { administrateme: { enabled: true, config: { baseUrl: fixtures.baseUrl, memberAgentIds: [memberAgent] }, hooks: { allowConversationAccess: true, allowPromptInjection: false } } } },
    channels: {}, cron: { enabled: false }, browser: browserProbe ? { enabled: true, evaluateEnabled: true, allowSystemProfileImport: false, defaultProfile: "chrome", profiles: { chrome: { driver: "extension", cdpPort: relayPort, color: "#00AA44" } } } : { enabled: false }, discovery: { mdns: { mode: "off" } }, update: { checkOnStart: false, auto: { enabled: false } }, logging: { file: path.join(state, "gateway.log") },
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
    conversations.set(ticket.source_event_id, ticket);
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
    await until(() => fixtures.conversationReceipts.some((row) => row.source === ticket.source_event_id), `${channel} source-bound tool receipt`);
    if (channel === "gmail") await until(() => fixtures.conversationReplies.some((row) => row.source === ticket.source_event_id), "signed native Gmail final reply");
  }
  assert.deepEqual(fixtures.conversationReceipts.map((row) => row.channel), ["administrateme-portal", "gmail"]);
  assert.deepEqual(fixtures.conversationReplies.map((row) => row.channel), ["gmail"]);
  assert.deepEqual(fixtures.errors, []);
  if (browser) {
    assert.deepEqual(browser.errors, []);
    console.log("Pinned native member -> CoS browser -> actual disposable Chrome DOM identity -> retained source -> same-origin return verified; synthetic extension/account only.");
  } else console.log("Pinned native conversation -> CoS subagent scratchpad -> actual read-back -> source-bound result verified for portal/Gmail; synthetic loopback fixtures only. No live provider effect.");
} catch (error) {
  let redacted = logs.replaceAll(token, "[redacted]").replaceAll(secret, "[redacted]");
  for (const session of sessions) redacted = redacted.replaceAll(session, "[service-session]");
  console.error(redacted);
  throw error;
} finally { await stop(); await fixtures.close(); await browser?.close(); await rm(directory, { recursive: true, force: true }); }
