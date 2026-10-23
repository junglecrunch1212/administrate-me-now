/** Real pinned Gateway: plugin RPC identity, persisted timer, restart and callback.
 * Hosted disposable state only; synthetic terminal receipt, no model or provider.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomBytes, randomUUID } from "node:crypto";
import { copyFile, mkdtemp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

assert.equal(process.env.GITHUB_ACTIONS, "true", "Use isolated hosted CI for candidate execution");
const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const source = path.resolve(process.argv[2]);
const versions = JSON.parse(await readFile(path.join(repository, "bootstrap/versions.json"), "utf8"));
assert.equal(path.basename(source), versions.openclaw.commit);
assert.equal(JSON.parse(await readFile(path.join(source, "package.json"), "utf8")).version, versions.openclaw.version);
const directory = await mkdtemp(path.join(tmpdir(), "adminme-reminder-gateway-"));
const state = path.join(directory, "state");
const plugin = path.join(directory, "plugin");
const marker = path.join(directory, "callback.json");
const token = randomBytes(32).toString("hex");
const agent = "adminme-0123456789abcdef";
let gateway, socket, logs = "";
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, label, timeout = 150_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await check(); if (value) return value;
    if (gateway?.exitCode !== null) throw new Error(`Gateway exited during ${label}`);
    await pause(300);
  }
  throw new Error(`Timed out: ${label}`);
}
async function stop() {
  socket?.close(); socket = undefined;
  if (!gateway || gateway.exitCode !== null) return;
  const child = gateway; child.kill("SIGTERM");
  await Promise.race([new Promise((resolve) => child.once("exit", resolve)), pause(12_000)]);
  if (child.exitCode === null) { child.kill("SIGKILL"); await new Promise((resolve) => child.once("exit", resolve)); }
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
  OPENCLAW_SKIP_GMAIL_WATCHER: "1", OPENCLAW_SKIP_CANVAS_HOST: "1", OPENCLAW_SKIP_BROWSER_CONTROL_SERVER: "1", ADMINME_REMINDER_PROBE_MARKER: marker });
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
  const hello = await request("connect", { minProtocol: 4, maxProtocol: 4, client: { id: "gateway-client", version: "synthetic-reminder", platform: process.platform, mode: "backend" },
    role: "operator", scopes: ["operator.admin", "operator.read", "operator.write"], caps: [], auth: { token } });
  assert.equal(hello.type, "hello-ok");
  return request;
}
async function saved() { try { return JSON.parse(await readFile(marker, "utf8")); } catch (error) { if (error.code === "ENOENT") return null; throw error; } }
try {
  await mkdir(state); await mkdir(path.join(plugin, "node_modules"), { recursive: true });
  for (const name of ["self-reminders", "self-replies", "result"]) {
    const input = await readFile(path.join(repository, `openclaw/administrateme/src/${name}.ts`), "utf8");
    await writeFile(path.join(plugin, `${name}.js`), ts.transpileModule(input, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ESNext } }).outputText);
  }
  await copyFile(path.join(repository, "tests/fixtures/openclaw_self_reminder_probe.mjs"), path.join(plugin, "index.mjs"));
  await symlink(source, path.join(plugin, "node_modules/openclaw"), "dir");
  await symlink(path.join(repository, "openclaw/administrateme/node_modules/typebox"), path.join(plugin, "node_modules/typebox"), "dir");
  await writeFile(path.join(plugin, "package.json"), JSON.stringify({ name: "@administrateme/reminder-gateway-probe", type: "module", version: "1.0.0", openclaw: { extensions: ["./index.mjs"] } }));
  await writeFile(path.join(plugin, "openclaw.plugin.json"), JSON.stringify({ id: "administrateme", activation: { onStartup: true }, configSchema: { type: "object", additionalProperties: false, properties: {} } }));
  await writeFile(configPath, JSON.stringify({ gateway: { mode: "local", bind: "loopback", port, auth: { mode: "token", token }, controlUi: { enabled: false }, reload: { mode: "off" } },
    agents: { defaults: { workspace: path.join(state, "workspace"), heartbeat: { every: "0m" } }, list: [{ id: agent, tools: { allow: ["administrateme"] } }] },
    plugins: { allow: ["administrateme"], load: { paths: [plugin] }, slots: { memory: "none" }, entries: { administrateme: { enabled: true } } },
    channels: {}, cron: { enabled: true, triggers: { enabled: true } }, browser: { enabled: false }, discovery: { mdns: { mode: "off" } }, update: { checkOnStart: false, auto: { enabled: false } }, logging: { file: path.join(state, "gateway.log") } }), { mode: 0o600 });
  let request = await start();
  await request("cron.add", { name: "Synthetic reminder creator", agentId: agent, enabled: true, deleteAfterRun: true,
    schedule: { kind: "at", at: new Date(Date.now() + 3_000).toISOString() }, sessionTarget: "isolated", wakeMode: "now", delivery: { mode: "none" }, failureAlert: false,
    payload: { kind: "script", toolsAllow: ["administrateme_probe_schedule", "administrateme_self_reminder_fire"], toolBudget: 1, timeoutSeconds: 45,
      script: "const r = await tools.call('administrateme_probe_schedule', {}); if(r?.result?.details?.status !== 'scheduled') throw new Error('Schedule unconfirmed'); json({scheduled:true});" } });
  const registered = await until(saved, "agent-scoped production timer RPC");
  assert.equal(registered.callback_count, 0);
  const timer = await request("cron.get", { id: registered.job_id });
  assert.equal(timer.enabled, true);
  assert.deepEqual(timer.payload.toolsAllow, ["administrateme_self_reminder_fire"]);
  assert.equal(timer.delivery.mode, "none");
  await stop(); request = await start();
  const restored = await request("cron.get", { id: registered.job_id });
  assert.equal(restored.id, registered.job_id); assert.equal(restored.enabled, true);
  const delivered = await until(async () => { const row = await saved(); return row?.callback_count === 1 && row; }, "native timer callback after restart");
  assert.equal(delivered.session_verified, true);
  await until(async () => (await request("cron.get", { id: registered.job_id })).state?.lastRunStatus === "ok", "successful fixed script result");
  const terminal = await request("cron.get", { id: registered.job_id });
  assert.equal(terminal.enabled, false);
  await stop(); request = await start();
  assert.equal((await request("cron.get", { id: registered.job_id })).enabled, false);
  assert.equal((await saved()).callback_count, 1);
  console.log("Pinned Gateway reminder timer: scoped RPC, durable restart, exact callback, one completion; no model or provider effects.");
} catch (error) {
  // Only synthetic fixture state exists. Suppress connection credentials in logs.
  console.error(logs.replaceAll(token, "[redacted]"));
  throw error;
} finally { await stop(); await rm(directory, { recursive: true, force: true }); }
