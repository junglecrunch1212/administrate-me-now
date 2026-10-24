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
import { startServiceWorkFixtures } from "../../../tests/fixtures/openclaw_service_work_probe.mjs";

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
const sessions = new Set();
const fixtures = await startServiceWorkFixtures({ secret, agent, sessions, validSession });
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
  const model = "fixture/service-work";
  const tools = ["administrateme_service_private_work", "administrateme_service_private_work_state", "administrateme_service_private_state", "administrateme_service_context", "administrateme_workstation_inventory"];
  const config = {
    gateway: { mode: "local", bind: "loopback", port, auth: { mode: "token", token }, controlUi: { enabled: false }, reload: { mode: "off" } },
    agents: { defaults: { workspace, heartbeat: { every: "0m" }, model: { primary: model }, models: { [model]: { params: { transport: "sse", openaiWsWarmup: false } } } }, list: [{ id: agent, workspace, tools: { allow: tools } }] },
    models: { mode: "replace", providers: { fixture: { baseUrl: `${fixtures.baseUrl}/v1`, apiKey: "synthetic-fixture-only", api: "openai-responses", models: [{ id: "service-work", name: "service-work", api: "openai-responses", reasoning: false, input: ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 128000, maxTokens: 4096 }] } } },
    tools: { codeMode: false },
    plugins: { allow: ["administrateme"], load: { paths: [plugin] }, slots: { memory: "none" }, entries: { administrateme: { enabled: true, config: { baseUrl: fixtures.baseUrl } } } },
    channels: {}, cron: { enabled: false }, browser: { enabled: false }, discovery: { mdns: { mode: "off" } }, update: { checkOnStart: false, auto: { enabled: false } }, logging: { file: path.join(state, "gateway.log") },
  };
  await writeFile(configPath, JSON.stringify(config), { mode: 0o600 });
  let request = await start();
  await assert.rejects(request("administrateme.service.run", { message: "Denied", request_id: "native-denied-001", agentId: "member" }), /Expected bounded/);
  const first = await request("administrateme.service.run", { message: "PROBE_INTERNAL_WORK: inspect granted context, save a sourced note, complete it, then verify saved work.", request_id: "native-service-probe-001" });
  assert.equal(first.status, "accepted");
  assert.equal(typeof first.run_id, "string");
  assert.deepEqual(Object.keys(first).sort(), ["run_id", "service_principal_id", "status"]);
  await until(async () => {
    if (fixtures.errors.length) throw new Error(fixtures.errors.join("; "));
    const result = await request("agent.wait", { runId: first.run_id, timeoutMs: 1000 });
    if (result.status === "error") throw new Error("Native service run failed");
    return result.status === "ok";
  }, "native service lifecycle");
  await assert.rejects(readFile(path.join(workspace, "BOOTSTRAP.md")), { code: "ENOENT" });
  assert.deepEqual(fixtures.receipts.map((entry) => entry.path), [
    "/api/openclaw/v1/tools/workstation-inventory",
    "/api/openclaw/v1/tools/workstation-inventory",
    "/api/openclaw/v1/tools/workstation-inventory",
    "/api/openclaw/v1/tools/service-context",
    "/api/openclaw/v1/tools/service-private-work",
    "/api/openclaw/v1/tools/service-private-work/cos-task%3Afixture/state",
    "/api/openclaw/v1/tools/service-private-state",
  ]);
  assert.equal(fixtures.inventoryObserved.length, 3);
  assert.equal(fixtures.inventoryObserved[0].counts.identities, 18);
  assert.equal(fixtures.inventoryObserved[0].items.length, 18);
  assert.equal(fixtures.inventoryObserved[0].items.at(-1)[1], "fixture-route-17");
  assert.equal(fixtures.inventoryObserved[0].page.next_offset, null);
  // A restart clears the native five-minute admission cache. Reusing the public
  // ID must still get a fresh signed session, never old granted model context.
  await stop(); request = await start();
  const second = await request("administrateme.service.run", { message: "PROBE_FRESH_READ: read saved internal work only.", request_id: "native-service-probe-001" });
  await until(async () => {
    if (fixtures.errors.length) throw new Error(fixtures.errors.join("; "));
    const result = await request("agent.wait", { runId: second.run_id, timeoutMs: 1000 });
    if (result.status === "error") throw new Error("Fresh native service run failed");
    return result.status === "ok";
  }, "fresh signed request context");
  await assert.rejects(readFile(path.join(workspace, "BOOTSTRAP.md")), { code: "ENOENT" });
  assert.equal(fixtures.initialInputs.length, 2);
  assert.equal(new Set(fixtures.receipts.map((entry) => entry.session)).size, 2);
  await stop();
  config.plugins.entries.administrateme.config.serviceWorkEnabled = false;
  await writeFile(configPath, JSON.stringify(config), { mode: 0o600 });
  request = await start();
  const callsBeforeRevoked = fixtures.modelCalls;
  await assert.rejects(request("administrateme.service.run", { message: "Denied after revocation", request_id: "native-revoked-001" }), /disabled/);
  assert.equal(fixtures.modelCalls, callsBeforeRevoked);
  assert.deepEqual(fixtures.errors, []);
  console.log("Pinned Gateway service work: established CoS identity without onboarding, sealed operator entry, native hook/context, granted context/create/lifecycle/read, fresh per-request session, persisted revocation. Synthetic loopback fixtures only.");
} catch (error) {
  let redacted = logs.replaceAll(token, "[redacted]").replaceAll(secret, "[redacted]");
  for (const session of sessions) redacted = redacted.replaceAll(session, "[service-session]");
  console.error(redacted);
  throw error;
} finally { await stop(); await fixtures.close(); await rm(directory, { recursive: true, force: true }); }
