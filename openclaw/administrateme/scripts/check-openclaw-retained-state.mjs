/** Hosted native public-RPC seed/readback, with no model or provider calls. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import net from "node:net";
import path from "node:path";

assert.equal(process.env.GITHUB_ACTIONS, "true");
assert.equal(process.env.RUNNER_ENVIRONMENT, "github-hosted");
const [source, state, proofPath, mode] = process.argv.slice(2);
assert.ok(["seed", "read"].includes(mode));
const configPath = path.join(state, "openclaw.json");
const config = JSON.parse(await readFile(configPath, "utf8"));
const token = config.gateway.auth.token;
const agents = ["upgrade-alpha", "upgrade-beta"];
const text = "Synthetic retained migration transcript; no requested external action.";
const timerText = "Synthetic disabled retained timer; never execute.";
const label = "Retained native upgrade fixture";
const server = net.createServer();
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const port = server.address().port;
await new Promise((resolve) => server.close(resolve));
const env = Object.fromEntries(["PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "LOGNAME"].filter((k) => process.env[k] !== undefined).map((k) => [k, process.env[k]]));
Object.assign(env, { OPENCLAW_STATE_DIR: state, OPENCLAW_CONFIG_PATH: configPath,
  OPENCLAW_GATEWAY_PORT: String(port), OPENCLAW_GATEWAY_TOKEN: token,
  OPENCLAW_NO_RESPAWN: "1", OPENCLAW_DISABLE_BONJOUR: "1", NODE_DISABLE_COMPILE_CACHE: "1",
  OPENCLAW_SKIP_GMAIL_WATCHER: "1", OPENCLAW_SKIP_CANVAS_HOST: "1", OPENCLAW_SKIP_BROWSER_CONTROL_SERVER: "1",
  OPENCLAW_SUPERVISOR_MODE: "external" });
const child = spawn(process.execPath, [path.join(source, "openclaw.mjs"), "gateway", "run", "--port", String(port)],
  { cwd: state, env, stdio: ["ignore", "pipe", "pipe"] });
let socket, logs = "";
for (const stream of [child.stdout, child.stderr]) stream.on("data", (chunk) => { logs = (logs + chunk).slice(-50_000); });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, title, timeout = 150_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) throw new Error(`Gateway exited: ${title}`);
    const found = await check(); if (found) return found;
    await sleep(200);
  }
  throw new Error(`Native upgrade timeout: ${title}`);
}
async function stop() {
  socket?.close();
  if (child.exitCode !== null || child.signalCode !== null) {
    assert.equal(child.exitCode, 0, "Gateway exited before owned shutdown"); return;
  }
  child.kill("SIGTERM");
  await Promise.race([new Promise((resolve) => child.once("exit", resolve)), sleep(12_000)]);
  if (child.exitCode === null && child.signalCode === null) {
    child.kill("SIGKILL"); await new Promise((resolve) => child.once("exit", resolve));
    throw new Error("Gateway required forced shutdown; quiescence not qualified");
  }
  assert.equal(child.exitCode, 0, "Native Gateway did not settle cleanly");
}
try {
  await until(async () => { try { return (await fetch(`http://127.0.0.1:${port}/readyz`, { signal: AbortSignal.timeout(1_000) })).ok; } catch { return false; } }, "readiness");
  socket = new WebSocket(`ws://127.0.0.1:${port}`);
  let challenge = false;
  const pending = new Map();
  socket.addEventListener("error", () => {});
  socket.addEventListener("message", ({ data }) => {
    const frame = JSON.parse(String(data));
    if (frame.type === "event" && frame.event === "connect.challenge") challenge = true;
    const request = pending.get(frame.id);
    if (frame.type !== "res" || !request) return;
    pending.delete(frame.id); clearTimeout(request.timer);
    if (frame.ok) request.resolve(frame.payload);
    else request.reject(new Error(`RPC ${request.method}: ${frame.error?.message ?? frame.error?.code}`));
  });
  function rpc(method, params) {
    assert.ok(["connect", "sessions.create", "chat.inject", "chat.history", "cron.add", "cron.get", "agents.list"].includes(method));
    if (method === "sessions.create") assert.ok(!("message" in params) && !("task" in params) && !("attachments" in params));
    if (method === "cron.add") assert.equal(params.enabled, false);
    return new Promise((resolve, reject) => {
      const id = randomUUID();
      const timer = setTimeout(() => { pending.delete(id); reject(new Error(`RPC timeout: ${method}`)); }, 20_000);
      pending.set(id, { resolve, reject, timer, method });
      socket.send(JSON.stringify({ type: "req", id, method, params }));
    });
  }
  await until(() => challenge, "connect challenge", 10_000);
  const hello = await rpc("connect", { minProtocol: 4, maxProtocol: 4,
    client: { id: "gateway-client", version: "synthetic-upgrade", platform: process.platform, mode: "backend" },
    role: "operator", scopes: ["operator.admin", "operator.read", "operator.write"], caps: [], auth: { token } });
  assert.equal(hello.type, "hello-ok");
  let proof;
  if (mode === "seed") {
    // Native cron bounds one-shot timestamps to ten years. Retain one actual
    // seed value; reads must never replace it with a newly calculated date.
    proof = { sessions: [], timer: null, timerAt: new Date(Date.now() + 24 * 60 * 60 * 1_000).toISOString() };
    for (const agentId of agents) {
      const key = `agent:${agentId}:retained-upgrade`;
      await rpc("sessions.create", { key, agentId, label: `${label} ${agentId}` });
      await rpc("chat.inject", { sessionKey: key, message: text, label: "migration-fixture" });
      const history = await rpc("chat.history", { sessionKey: key });
      assert.equal(typeof history.sessionId, "string");
      proof.sessions.push({ key, agentId, sessionId: history.sessionId });
    }
    const created = await rpc("cron.add", { name: label, agentId: agents[0], enabled: false,
      schedule: { kind: "at", at: proof.timerAt }, sessionTarget: "main", wakeMode: "next-heartbeat",
      payload: { kind: "systemEvent", text: timerText }, deleteAfterRun: false, failureAlert: false });
    proof.timer = (created.job ?? created).id;
    assert.equal(typeof proof.timer, "string");
    await writeFile(proofPath, JSON.stringify(proof), { mode: 0o600, flag: "wx" });
  } else proof = JSON.parse(await readFile(proofPath, "utf8"));
  const roster = await rpc("agents.list", {});
  for (const agent of agents) assert.ok(roster.agents.some((row) => row.id === agent), `retained agent ${agent}`);
  for (const retained of proof.sessions) {
    const history = await rpc("chat.history", { sessionKey: retained.key });
    assert.equal(history.sessionId, retained.sessionId, "retained session identity");
    const matches = history.messages.filter((message) => JSON.stringify(message).includes(text));
    assert.equal(matches.length, 1, "retained transcript exactly once");
  }
  const timer = await rpc("cron.get", { id: proof.timer });
  assert.equal(timer.id, proof.timer);
  assert.equal(timer.agentId, agents[0]);
  assert.equal(timer.enabled, false);
  assert.equal(timer.payload.kind, "systemEvent");
  assert.equal(timer.payload.text, timerText);
  assert.equal(typeof proof.timerAt, "string");
  assert.equal(new Date(proof.timerAt).toISOString(), proof.timerAt);
  assert.equal(timer.schedule.at, proof.timerAt);
  assert.equal(timer.state?.lastRunAtMs, undefined, "disabled fixture timer never ran");
  await stop();
  console.log(`Native retained ${mode}: two agent/session identities, exact transcripts and disabled timer verified; no model/provider calls.`);
} catch (error) {
  console.error(logs.replaceAll(token, "[redacted]"));
  throw error;
} finally { await stop(); }
