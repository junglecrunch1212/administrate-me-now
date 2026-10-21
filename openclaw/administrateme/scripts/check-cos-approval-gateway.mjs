/**
 * Hosted exact-source normal-startup proof of the production CoS approval relay.
 * No SDK, sockets, registry, service lifecycle or approval handlers are mocked.
 * Only synthetic remembered tool contexts and HMAC member tickets are supplied.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash, createHmac, randomBytes } from "node:crypto";
import { once } from "node:events";
import {
  copyFile, mkdir, mkdtemp, readFile, readdir, realpath, rm, symlink, writeFile,
} from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { checkPermissionPins } from "./check-cos-permission-pins.mjs";

assert.equal(process.env.GITHUB_ACTIONS, "true", "Hosted CI is required");
assert.equal(process.env.RUNNER_ENVIRONMENT, "github-hosted", "Disposable hosted runner required");
const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const source = await realpath(process.argv[2]);
const permissionEnvironment = JSON.parse(await readFile(process.argv[3], "utf8"));
const runnerTemp = await realpath(process.env.RUNNER_TEMP);
const pin = JSON.parse(await readFile(path.join(repository, "bootstrap/versions.json"), "utf8")).openclaw;
assert.equal(path.basename(source), pin.commit);
assert.equal(JSON.parse(await readFile(path.join(source, "package.json"), "utf8")).version, pin.version);
const directory = await mkdtemp(path.join(runnerTemp, "adminme-approval-gateway-"));
const artifact = path.join(runnerTemp, "adminme-approval-gateway-evidence");
await mkdir(artifact, { recursive: true, mode: 0o700 });
const state = path.join(directory, "state");
const plugin = path.join(directory, "plugin");
await mkdir(state, { mode: 0o700 });
await mkdir(path.join(plugin, "node_modules"), { recursive: true, mode: 0o700 });
const readyFile = path.join(directory, "ready");
const seedFile = path.join(directory, "seeds.json");
const token = randomBytes(32).toString("hex");
const secret = randomBytes(32).toString("hex");
const agent = "approval-probe";
const session = `agent:${agent}:synthetic`;
const seeds = ["allow", "deny", "finalize"].map((name) => ({
  params: { request_id: `synthetic-${name}`, approval_event_id: `synthetic-domain-${name}` },
  context: { sessionKey: session, agentId: agent, toolCallId: `synthetic-call-${name}` },
  receipt: `synthetic-receipt-${name}`,
}));
const evidence = {
  contract: "cos-approval-real-gateway-v1",
  openclaw_commit: pin.commit,
  status: "failed",
  phase: "prepare",
  checks: [],
};
let gateway;
let launchError;
let logs = "";
const clients = [];
const privateJson = (target, value) =>
  writeFile(target, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });

async function until(check, label, timeout = 15_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (launchError) throw new Error("Gateway spawn failed");
    if (gateway && gateway.exitCode !== null) throw new Error("Gateway exited before probe completion");
    const result = await check();
    if (result) return result;
    await delay(100);
  }
  throw new Error(`Timed out: ${label}`);
}

async function freePort() {
  const server = net.createServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const port = server.address().port;
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return port;
}

async function connect(port, scopes, caps = []) {
  const socket = new WebSocket(`ws://127.0.0.1:${port}`);
  const pending = new Map();
  let challenge = false;
  let sequence = 0;
  const client = {
    events: [],
    close() {
      for (const entry of pending.values()) {
        clearTimeout(entry.timer);
        entry.reject(new Error("Probe connection closed"));
      }
      pending.clear();
      socket.close();
    },
    request(method, params, timeout = 10_000) {
      const id = `probe-${++sequence}`;
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          pending.delete(id);
          reject(new Error(`RPC timed out: ${method}`));
        }, timeout);
        pending.set(id, { resolve, reject, timer });
        socket.send(JSON.stringify({ type: "req", id, method, params }));
      });
    },
  };
  clients.push(client);
  socket.addEventListener("error", () => {});
  socket.addEventListener("message", ({ data }) => {
    const frame = JSON.parse(String(data));
    if (frame.type === "event" && frame.event === "connect.challenge") challenge = true;
    if (frame.type === "event") client.events.push(frame);
    if (frame.type !== "res") return;
    const entry = pending.get(frame.id);
    if (!entry) return; // A two-phase request also emits its terminal response.
    pending.delete(frame.id);
    clearTimeout(entry.timer);
    if (frame.ok) entry.resolve(frame.payload);
    else {
      const stage = frame.error?.details?.stage;
      // Only this fixed production diagnostic vocabulary may enter artifacts.
      const suffix = ["authorization", "helper_pin", "python_pin", "helper_location", "child_start", "database_check"].includes(stage)
        ? ` (${stage})` : "";
      entry.reject(new Error(`RPC refused: ${frame.error?.code ?? "unknown"}${suffix}`));
    }
  });
  await until(() => challenge, "Gateway challenge");
  // This independent process never imports the SDK or reads its runtime token.
  // No approvals capability: the relay must supply the delivery route itself.
  const hello = await client.request("connect", {
    minProtocol: 4,
    maxProtocol: 4,
    client: {
      id: "gateway-client", version: "synthetic-probe", platform: process.platform,
      mode: "backend", displayName: "Isolated CoS approval requester",
    },
    role: "operator",
    scopes,
    caps,
    auth: { token },
  });
  assert.equal(hello.type, "hello-ok");
  for (const scope of scopes) assert.ok(hello.auth.scopes.includes(scope));
  return client;
}

function ticket(operation, overrides = {}) {
  const fields = [
    "session_key", "agent_id", "member_id", "approval_id", "revision", "decision", "nonce",
  ];
  const value = {
    session_key: session, agent_id: agent, member_id: "synthetic-member",
    approval_id: "", revision: "", decision: "", nonce: randomBytes(16).toString("hex"),
    expires_at_ms: Date.now() + 5_000,
    ...overrides,
  };
  value.signature = createHmac("sha256", secret).update(
    ["cos-member-approval-v1", operation, ...fields.map((key) => value[key]),
      String(value.expires_at_ms)].join("|"),
  ).digest("hex");
  return value;
}

try {
  evidence.phase = "permission-file-prerequisites";
  evidence.permission_pins = [];
  // Mirror the production pre-execution checks before starting the Gateway.
  // Keep metadata content-free: no private paths, file bytes, or credentials.
  await checkPermissionPins(permissionEnvironment, evidence.permission_pins);
  // Transpile the actual release source without rewriting its SDK imports.
  // Gateway loads it as a real plugin through its normal public SDK resolver.
  for (const name of ["cos-approval-relay", "approval", "contract", "cos-permission-preflight", "cos-permission-database"]) {
    const input = await readFile(path.join(repository, `openclaw/administrateme/src/${name}.ts`), "utf8");
    if (name === "cos-approval-relay") {
      evidence.relay_sha256 = createHash("sha256").update(input).digest("hex");
    }
    const output = ts.transpileModule(input, {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ESNext },
    }).outputText;
    await writeFile(path.join(plugin, `${name}.js`), output, { mode: 0o600 });
  }
  await copyFile(
    path.join(repository, "tests/fixtures/openclaw_cos_approval_probe.mjs"),
    path.join(plugin, "index.mjs"),
  );
  await symlink(source, path.join(plugin, "node_modules/openclaw"), "dir");
  await privateJson(path.join(plugin, "package.json"), {
    name: "@administrateme/approval-gateway-probe", version: "1.0.0", type: "module",
    openclaw: { extensions: ["./index.mjs"] },
  });
  const productionManifest = JSON.parse(await readFile(
    path.join(repository, "openclaw/administrateme/openclaw.plugin.json"), "utf8",
  ));
  assert.equal(productionManifest.activation?.onStartup, true);
  await privateJson(path.join(plugin, "openclaw.plugin.json"), {
    id: "administrateme", name: "Isolated CoS approval relay",
    // Enabled alone does not eagerly load service-only plugins on this pin.
    // Keep the probe bound to the release's actual startup activation contract.
    activation: { onStartup: productionManifest.activation.onStartup },
    configSchema: { type: "object", additionalProperties: false, properties: {} },
  });
  await privateJson(seedFile, seeds);
  const port = await freePort();
  const configPath = path.join(directory, "openclaw.json");
  await privateJson(configPath, {
    gateway: {
      mode: "local", bind: "loopback", port, auth: { mode: "token", token },
      controlUi: { enabled: false }, reload: { mode: "off" },
    },
    agents: {
      defaults: { workspace: path.join(state, "workspace"), heartbeat: { every: "0m" } },
      list: [{ id: agent, tools: { deny: ["*"] } }],
    },
    plugins: {
      allow: ["administrateme"], load: { paths: [plugin] }, slots: { memory: "none" },
      entries: { administrateme: { enabled: true } },
    },
    // Exercise the ordinary sidecar/scheduler lifecycle without any provider
    // accounts, model calls, heartbeat work, or scheduled jobs.
    channels: {}, cron: { enabled: true }, browser: { enabled: false },
    logging: { file: path.join(state, "gateway.log") },
    discovery: { mdns: { mode: "off" } },
    update: { checkOnStart: false, auto: { enabled: false } },
  });
  const env = Object.fromEntries(
    ["PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "LOGNAME"]
      .filter((key) => process.env[key] !== undefined).map((key) => [key, process.env[key]]),
  );
  Object.assign(env, {
    ...permissionEnvironment,
    OPENCLAW_STATE_DIR: state, OPENCLAW_CONFIG_PATH: configPath,
    OPENCLAW_GATEWAY_PORT: String(port), OPENCLAW_GATEWAY_TOKEN: token,
    OPENCLAW_NO_RESPAWN: "1", OPENCLAW_DISABLE_BONJOUR: "1",
    NODE_DISABLE_COMPILE_CACHE: "1",
    OPENCLAW_SKIP_GMAIL_WATCHER: "1",
    OPENCLAW_SKIP_CANVAS_HOST: "1", OPENCLAW_SKIP_BROWSER_CONTROL_SERVER: "1",
    ADMINME_APPROVAL_PROBE_SECRET: secret,
    ADMINME_APPROVAL_PROBE_SEEDS: seedFile, ADMINME_APPROVAL_PROBE_READY: readyFile,
  });
  // On this exact pin the first two flags select sidecarStartup="defer",
  // hiding the effect of synchronous service-start waits on normal readiness.
  // Explicitly clear these even if the parent or permission environment set
  // them; an enabled empty scheduler must also execute its normal lifecycle.
  for (const key of ["OPENCLAW_SKIP_CHANNELS", "OPENCLAW_SKIP_PROVIDERS", "OPENCLAW_SKIP_CRON"]) {
    delete env[key];
    assert.equal(env[key], undefined);
  }
  evidence.startup_mode = "normal";
  evidence.phase = "gateway-normal-startup";
  const startupAt = Date.now();
  gateway = spawn(process.execPath, [path.join(source, "openclaw.mjs"), "gateway", "run"], {
    cwd: directory, env, stdio: ["ignore", "pipe", "pipe"],
  });
  gateway.on("error", (error) => { launchError = error; });
  const capture = (chunk) => { logs = (logs + chunk.toString()).slice(-200_000); };
  gateway.stdout.on("data", capture);
  gateway.stderr.on("data", capture);
  await until(async () => {
    try { if (await readFile(readyFile, "utf8") !== "ready\n") return false; }
    catch (error) { if (error.code !== "ENOENT") throw error; return false; }
    const response = await fetch(`http://127.0.0.1:${port}/readyz`, {
      signal: AbortSignal.timeout(2_000),
    }).catch(() => null);
    if (!response || response.status !== 200) return false;
    const readiness = await response.json();
    assert.equal(readiness.ready, true);
    assert.deepEqual(readiness.failing, []);
    return true;
  }, "normal Gateway readiness after production service startup", 60_000);
  evidence.checks.push("production-service-started");
  evidence.normal_startup_ms = Date.now() - startupAt;
  evidence.phase = "normal-cron-readiness";
  const scheduler = await connect(port, ["operator.read"]);
  const [cronStatus, cronPage] = await Promise.all([
    scheduler.request("cron.status", {}),
    scheduler.request("cron.list", { includeDisabled: true, includeDeliveryPreviews: false }),
  ]);
  assert.equal(cronStatus.enabled, true);
  assert.equal(cronStatus.jobs, 0);
  assert.equal(cronStatus.nextWakeAtMs, null);
  assert.deepEqual(cronPage.jobs, []);
  assert.equal(cronPage.total, 0);
  assert.equal(cronPage.hasMore, false);
  evidence.cron = { enabled: cronStatus.enabled, jobs: cronStatus.jobs, listed_jobs: cronPage.jobs.length };
  evidence.checks.push("normal-startup-ready-and-empty-cron-responsive");
  const requester = await connect(port, ["operator.approvals"]);
  const dashboard = await connect(port, ["operator.read", "operator.write"]);
  const unrelatedApprover = await connect(port, ["operator.approvals"]);
  const list = (overrides = {}) => dashboard.request(
    "administrateme.cosApprovals.list", ticket("list", overrides),
  );
  assert.deepEqual(await requester.request("plugin.approval.list", {}), []);
  // The service hook must return before the public native SDK hello can finish.
  // Poll the production fail-closed read seam with a fresh ticket on every try;
  // never request an approval before that subscription is actually usable.
  const initial = await until(async () => {
    try { return await list(); }
    catch (error) {
      if (error.message === "RPC refused: APPROVAL_UNAVAILABLE") return false;
      throw error;
    }
  }, "production relay native subscription", 30_000);
  assert.deepEqual(initial.items, []);
  await assert.rejects(dashboard.request("plugin.approval.list", {}), /RPC refused/);
  evidence.checks.push("empty-isolated-registry-and-least-privilege-dashboard");

  evidence.phase = "no-send-permission-check";
  const permissionMethod = "administrateme.cosPermissions.preflight";
  const permissionTicket = () => {
    const value = { operation: "check", nonce: randomBytes(16).toString("hex"), expires_at_ms: Date.now() + 5_000 };
    return { ...value, signature: createHmac("sha256", secret).update(
      ["cos-apple-events-preflight-v1", value.operation, value.nonce, String(value.expires_at_ms)].join("|"),
    ).digest("hex") };
  };
  await assert.rejects(dashboard.request(permissionMethod, permissionTicket()), /RPC refused/);
  const operator = await connect(port, ["operator.admin"]);
  evidence.phase = "member-session-subscription";
  const scoped = await connect(port, ["operator.read", "operator.write"], ["session-scoped-events"]);
  const unrelated = await connect(port, ["operator.read", "operator.write"], ["session-scoped-events"]);
  const emit = (key) => operator.request("administrateme.probe.chat.emit", { key });
  const chatEvents = (client) => client.events.filter((frame) => frame.event === "chat");
  await emit(session);
  await delay(100);
  assert.deepEqual(chatEvents(scoped), []);
  assert.deepEqual(await scoped.request("sessions.messages.subscribe", { key: session }), {
    subscribed: true, key: session,
  });
  await unrelated.request("sessions.messages.subscribe", { key: `agent:${agent}:other` });
  await emit(session);
  await until(() => chatEvents(scoped).length === 1, "subscribed member terminal delivery");
  assert.equal(chatEvents(scoped)[0].payload.state, "final");
  assert.equal(chatEvents(scoped)[0].payload.sessionKey, session);
  assert.deepEqual(chatEvents(unrelated), []);
  await scoped.request("sessions.messages.unsubscribe", { key: session });
  await emit(session);
  await delay(100);
  assert.equal(chatEvents(scoped).length, 1);
  assert.deepEqual(chatEvents(unrelated), []);
  evidence.checks.push("scoped-chat-requires-exact-subscription-and-isolates-other-member");
  evidence.phase = "no-send-permission-check";
  await assert.rejects(operator.request(permissionMethod, { ...permissionTicket(), signature: "0".repeat(64) }), /RPC refused/);
  await assert.rejects(operator.request(permissionMethod, { ...permissionTicket(), operation: "request" }), /RPC refused/);
  const permissionAuthorization = permissionTicket();
  const proof = await operator.request(permissionMethod, permissionAuthorization, 25_000);
  const { signature: proofSignature, ...unsignedProof } = proof;
  assert.equal(proofSignature, createHmac("sha256", secret).update(JSON.stringify(unsignedProof)).digest("hex"));
  assert.equal(proof.operation, "check");
  assert.equal(proof.nonce, permissionAuthorization.nonce);
  assert.equal(proof.target, "com.apple.MobileSMS");
  assert.equal(proof.stage, "permission");
  assert.ok(Number.isInteger(proof.os_status));
  assert.ok(["authorized", "needs_consent", "denied", "target_not_running", "target_not_accessible"].includes(proof.status));
  assert.equal(proof.helper_sha256, permissionEnvironment.ADMINISTRATEME_PERMISSION_HELPER_SHA256);
  assert.equal(proof.python_sha256, permissionEnvironment.ADMINISTRATEME_PERMISSION_PYTHON_SHA256);
  assert.equal(proof.database.contract, "cos-messages-database-preflight-v1");
  assert.equal(proof.database.target, "service_messages_database");
  assert.ok(["ready", "not_found", "unavailable", "invalid_schema"].includes(proof.database.status));
  assert.equal(proof.database.ready, proof.database.status === "ready");
  await assert.rejects(operator.request(permissionMethod, permissionAuthorization), /RPC refused/);
  evidence.permission_check = { status: proof.status, os_status: proof.os_status, stage: proof.stage };
  evidence.checks.push("real-public-apple-events-check-only-signed-result-and-admin-refusal");

  for (const seed of seeds) {
    const name = seed.params.request_id.slice("synthetic-".length);
    evidence.phase = `roundtrip-${name}`;
    const accepted = await requester.request("plugin.approval.request", {
      pluginId: "administrateme", toolName: "administrateme_cos_response",
      toolCallId: seed.context.toolCallId, sessionKey: session, agentId: agent,
      title: "Synthetic CoS reply", description: "Hosted approval boundary probe",
      allowedDecisions: ["allow-once", "deny"], timeoutMs: 30_000, twoPhase: true,
    });
    assert.equal(accepted.status, "accepted");
    assert.equal(accepted.deliveryRoute, "approval-client");
    assert.match(accepted.id, /^plugin:[a-f0-9-]{36}$/);
    const row = await until(async () =>
      (await list()).items.find((item) => item.approval_id === accepted.id), "native pending delivery");
    assert.equal(row.request_id, seed.params.request_id);
    assert.equal(row.domain_approval_id, seed.params.approval_event_id);
    assert.equal(row.session_key, session);
    assert.equal(row.agent_id, agent);
    assert.equal(row.receipt_id, undefined);
    assert.equal(row.tool_call_id, undefined);
    // Shared-token possession does not grant this separate client the
    // process-local runtime authority that the real native relay needs.
    assert.deepEqual(await unrelatedApprover.request("plugin.approval.list", {}), []);
    await assert.rejects(unrelatedApprover.request(
      "plugin.approval.resolve", { id: accepted.id, decision: "allow-once" },
    ), /RPC refused/);
    assert.deepEqual((await list({ session_key: "agent:other:synthetic" })).items, []);
    const resolve = (overrides = {}) => dashboard.request(
      "administrateme.cosApprovals.resolve",
      ticket("resolve", { approval_id: row.approval_id, revision: row.revision, decision: "allow-once", ...overrides }),
    );
    await assert.rejects(resolve({ revision: "wrong-revision" }), /RPC refused/);
    await assert.rejects(resolve({ session_key: "agent:other:synthetic" }), /RPC refused/);
    await assert.rejects(resolve({ expires_at_ms: Date.now() - 1 }), /RPC refused/);
    assert.equal((await list()).items.length, 1);
    const waitDecision = requester.request("plugin.approval.waitDecision", { id: accepted.id });
    void waitDecision.catch(() => {});
    const decision = name === "allow" ? "allow-once" : "deny";
    if (name === "finalize") {
      // Resolution on another real connection must reach finalizeResolved,
      // rather than relying on the relay's pre-resolution pending deletion.
      await requester.request("plugin.approval.resolve", { id: accepted.id, decision });
    } else {
      assert.equal((await resolve({ decision })).resolved, true);
    }
    assert.equal((await waitDecision).decision, decision);
    await until(async () => (await list()).items.length === 0, "native finalized");
    assert.deepEqual(await requester.request("plugin.approval.list", {}), []);
    await assert.rejects(resolve({ decision }), /RPC refused/);
    evidence.checks.push(`${name}-decision-native-pending-finalized-and-replay-denied`);
  }
  const stateFiles = await readdir(state, { recursive: true });
  assert.deepEqual(stateFiles.filter((name) => /sessions\/.*\.jsonl$/.test(name)), []);
  evidence.checks.push("no-agent-transcripts-or-pending-approvals");
  evidence.status = "passed";
  evidence.phase = "complete";
} catch (error) {
  // Errors contain operation names/codes only; never echo tickets or credentials.
  evidence.error = error instanceof assert.AssertionError ? "Assertion failed" : String(error.message);
  process.exitCode = 1;
} finally {
  for (const client of clients) client.close();
  if (gateway && gateway.exitCode === null && !launchError) {
    const exited = once(gateway, "exit");
    gateway.kill("SIGTERM");
    await Promise.race([exited, delay(8_000)]);
    if (gateway.exitCode === null && gateway.signalCode === null) {
      gateway.kill("SIGKILL");
      await Promise.race([exited, delay(3_000)]);
    }
    if (gateway.exitCode === null && gateway.signalCode === null) {
      evidence.status = "failed";
      evidence.error = "Owned Gateway child did not stop within the cleanup deadline";
      process.exitCode = 1;
    }
  }
  const redacted = logs.replaceAll(token, "<gateway-token>").replaceAll(secret, "<probe-secret>")
    .replaceAll(directory, "<isolated-probe>").replaceAll(source, "<pinned-source>");
  await privateJson(path.join(artifact, "result.json"), evidence);
  if (evidence.status !== "passed") {
    await writeFile(path.join(artifact, "gateway.log"), redacted, { mode: 0o600 });
  }
  await rm(directory, { recursive: true, force: true });
}
console.log(JSON.stringify(evidence));
