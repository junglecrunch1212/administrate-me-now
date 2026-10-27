/** Synthetic loopback endpoints for the real pinned native handoff. Hosted only. */
import assert from "node:assert/strict";
import http from "node:http";
import { spawn } from "node:child_process";
import path from "node:path";
import { createInterface } from "node:readline";
import { createHash, createHmac } from "node:crypto";

/** The real encrypted application; only hosted synthetic member/state fixtures. */
export async function startNativeFailureBackend({ repository, directory, secret }) {
  assert.equal(process.env.GITHUB_ACTIONS, "true");
  assert.equal(process.env.RUNNER_ENVIRONMENT, "github-hosted");
  const env = Object.fromEntries(["PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "VIRTUAL_ENV", "POETRY_CACHE_DIR", "GITHUB_ACTIONS", "RUNNER_ENVIRONMENT"].filter((key) => process.env[key] !== undefined).map((key) => [key, process.env[key]]));
  env.PYTHONPATH = [repository, path.join(repository, "bridge")].join(path.delimiter);
  const child = spawn("poetry", ["run", "python", "-m", "tests.acceptance.test_cos_browser_workstation", "--native-worker-failure", path.join(directory, "failure-instance")], { cwd: repository, env, stdio: ["pipe", "pipe", "pipe"] });
  const pending = new Map();
  let sequence = 0, stderr = "", stopped = false;
  child.stderr.on("data", (chunk) => { stderr = (stderr + chunk).slice(-8000); });
  const ready = new Promise((resolve, reject) => {
    pending.set("ready", { resolve, reject, timer: setTimeout(() => reject(new Error("Encrypted fixture startup timed out")), 60_000) });
  });
  const lines = createInterface({ input: child.stdout });
  lines.on("line", (line) => {
    if (!line.startsWith("ADMINME_NATIVE_BACKEND ")) return;
    try {
      const message = JSON.parse(line.slice("ADMINME_NATIVE_BACKEND ".length));
      const item = pending.get(message.ready ? "ready" : message.id);
      assert(item, "Unexpected encrypted fixture response");
      pending.delete(message.ready ? "ready" : message.id); clearTimeout(item.timer);
      item.resolve(message.ready ? message : message.result);
    } catch (error) { for (const item of pending.values()) { clearTimeout(item.timer); item.reject(error); } pending.clear(); }
  });
  const failed = (error) => { for (const item of pending.values()) { clearTimeout(item.timer); item.reject(error); } pending.clear(); };
  child.on("error", failed);
  child.on("exit", (code, signal) => { if (!stopped) failed(new Error(`Encrypted fixture exited ${code ?? signal}: ${stderr.replaceAll(secret, "[redacted]")}`)); });
  child.stdin.on("error", failed);
  child.stdin.write(JSON.stringify({ secret }) + "\n");
  const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  async function close() {
    stopped = true;
    if (child.pid && child.exitCode === null && child.signalCode === null) {
      const exited = new Promise((resolve) => child.once("exit", resolve));
      child.stdin.end(); await Promise.race([exited, pause(10_000)]);
      if (child.exitCode === null && child.signalCode === null) { child.kill("SIGTERM"); await Promise.race([exited, pause(3000)]); }
      if (child.exitCode === null && child.signalCode === null) { child.kill("SIGKILL"); await exited; }
    }
    lines.close(); failed(new Error("Encrypted fixture closed"));
  }
  try {
    const identity = await ready;
    return { ...identity, sourceId: undefined,
      call(command) {
        assert(child.exitCode === null && child.signalCode === null, "Encrypted fixture is not running");
        return new Promise((resolve, reject) => {
          const id = String(++sequence);
          const timer = setTimeout(() => { pending.delete(id); reject(new Error(`Encrypted fixture ${command.operation} timed out`)); }, 20_000);
          pending.set(id, { resolve, reject, timer });
          child.stdin.write(JSON.stringify({ id, ...command }) + "\n");
        });
      }, close };
  } catch (error) { await close(); throw error; }
}

export async function startWorkstationWorkFixtures({ secret, agent, sessions, validSession, conversations, browser, failureBackend }) {
  assert.equal(process.env.GITHUB_ACTIONS, "true");
  const errors = [], conversationReceipts = [], conversationReplies = [], parentResults = [], statusReads = [], nativeFailures = [], workers = new Map();
  let failureModelCalls = 0;
  const sha = (text) => createHash("sha256").update(text).digest("hex");
  const serviceSession = (nonce) => {
    const digest = sha(nonce).slice(0, 32);
    return `agent:${agent}:administrateme-service-${digest}-${createHmac("sha256", secret).update(`service-session|service-principal:chief-of-staff|${digest}`).digest("hex").slice(0, 32)}`;
  };
  const server = http.createServer(async (req, res) => {
    try {
      const chunks = []; for await (const chunk of req) chunks.push(chunk);
      const body = JSON.parse(Buffer.concat(chunks).toString() || "{}");
      if (req.url === "/v1/responses") {
        const input = Array.isArray(body.input) ? body.input : [];
        // A fresh status turn shares the authentic conversation's history. Do
        // not count the original turn's calls as this turn's tool results.
        const turn = input.slice(Math.max(0, input.findLastIndex((entry) => entry.role === "user")));
        const encoded = JSON.stringify(turn);
        const channel = encoded.includes("PROBE_WORKSTATION_GMAIL") ? "gmail" : "administrateme-portal";
        const source = `fixture-${channel}-instruction`;
        const names = body.tools.map((tool) => tool.name ?? tool.function?.name);
        const worker = !names.includes("administrateme_workstation_work");
        const outputs = turn.filter((entry) => entry.type === "function_call_output").length;
        const nativeFailure = encoded.includes("PROBE_WORKSTATION_NATIVE_FAILURE");
        const browserStatus = encoded.includes("PROBE_RETAINED_BROWSER_STATUS");
        if (worker && nativeFailure) { failureModelCalls++; throw new Error("Pre-provider worker failure reached the model endpoint"); }
        let step;
        if (worker) {
          assert.deepEqual([...names].sort(), [...(browser ? ["browser"] : []), "administrateme_service_private_work", "administrateme_service_private_work_state", "administrateme_service_private_state", "administrateme_service_context"].sort(), "worker retains its configured surface; per-call policy constrains execution");
        }
        if (worker && browser) {
          const plan = [
            ["administrateme_service_context", {}], // Must be denied before its backend.
            ["browser", { action: "snapshot" }],
          ];
          step = plan[outputs];
          if (outputs > 0) {
            const denied = turn.filter((entry) => entry.type === "function_call_output" && entry.call_id === `call_worker_${channel}_0`);
            assert.equal(denied.length, 1, "first service-context call must have exactly one result");
            assert.equal(typeof denied[0].output, "string");
            assert(denied[0].output.includes("A current authenticated browser request, verified CoS tab and source policy are required."), "unrelated dynamic call did not receive the public browser denial");
            if (outputs === 1) {
              const bound = [...workers.values()].find((entry) => entry.source === source);
              assert(bound);
              assert.equal(await browser.privateBodyReadCount(), bound.beforeReads, "denied dynamic call must not read the page body");
            }
          }
          if (outputs > 1) assert(encoded.includes("Review the garage plan on Friday."), "browser model did not receive the retained native observation");
        } else if (worker) {
          const plan = [
            ["administrateme_service_context", {}], // Must be denied before its backend.
            ["administrateme_service_private_work", { request_id: `note:${source}`, title: "Private garage plan", description: "Sort, label, store.", record_kind: "scratchpad" }],
            ["administrateme_service_private_state", { task_id: `task:${source}` }],
          ];
          step = plan[outputs];
          if (outputs > 0) assert(encoded.includes("This worker run may only create and read back its exact private scratchpad."), "unrelated dynamic call was not denied");
        } else {
          const text = nativeFailure ? "PROBE_WORKSTATION_NATIVE_FAILURE: Read the authorized CoS planning page." : channel === "gmail" ? "PROBE_WORKSTATION_GMAIL: draft a private garage plan." : "PROBE_WORKSTATION_PORTAL: draft a private garage plan.";
          step = outputs === 0 ? ["administrateme_workstation_work", browserStatus
            ? { action: "browser_status", browser_request_event_id: `browser:${source}` }
            : { action: browser ? "browser_read" : "create", instruction: text }] : undefined;
          if (outputs > 0) {
            const status = nativeFailure ? "failed" : browser && !browserStatus
              ? parentResults.findLast((entry) => entry.source === source)?.status : "verified";
            assert(["verified", "pending_or_unknown", "failed"].includes(status));
            assert(encoded.includes(`\\"status\\":\\"${status}\\"`) || encoded.includes(`"status":"${status}"`), "parent resumed without the actual worker terminal result");
            if (nativeFailure) assert(encoded.includes("native_worker_failed"));
          }
        }
        const events = [];
        if (step) {
          const [name, args] = step;
          const item = { type: "function_call", id: `fc_${worker ? "worker" : browserStatus ? "status" : "member"}_${channel}_${outputs}`, call_id: `call_${worker ? "worker" : browserStatus ? "status" : "member"}_${channel}_${outputs}`, name, arguments: "" };
          events.push({ type: "response.output_item.added", item });
          events.push({ type: "response.function_call_arguments.delta", delta: JSON.stringify(args) });
          events.push({ type: "response.output_item.done", item: { ...item, arguments: JSON.stringify(args) } });
        } else {
          const item = { type: "message", id: `msg_${worker ? "worker" : "member"}_${channel}`, role: "assistant", content: [], status: "in_progress" };
          events.push({ type: "response.output_item.added", item });
          const pending = !worker && browser && !browserStatus && parentResults.findLast((entry) => entry.source === source)?.status === "pending_or_unknown";
          const text = nativeFailure ? "The native worker failed; no browser read was completed." : pending
            ? "The browser read is still pending; its retained request can be checked without another read."
            : browser ? "The authorized browser observation was retained and verified." : "Your private scratchpad was created and read back.";
          events.push({ type: "response.output_text.delta", delta: text });
          events.push({ type: "response.output_item.done", item: { ...item, status: "completed", content: [{ type: "output_text", text, annotations: [] }] } });
        }
        events.push({ type: "response.completed", response: { status: "completed", usage: { input_tokens: 10, output_tokens: 10, total_tokens: 20 } } });
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        res.end(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("") + "data: [DONE]\n\n"); return;
      }
      assert.equal(req.headers.authorization, `Bearer ${secret}`);
      assert.notEqual(req.url, "/api/openclaw/v1/tools/service-context", "unrelated context tool reached its backend");
      if (failureBackend?.sourceId && body.source_message_event_id === failureBackend.sourceId) {
        const response = await failureBackend.call({ operation: "request", path: req.url, body });
        if (req.url.endsWith("/result")) nativeFailures.push(response.body);
        res.writeHead(response.status, { "Content-Type": "application/json" }); res.end(JSON.stringify(response.body)); return;
      }
      let result = { ok: true };
      const base = browser ? "/api/openclaw/v1/tools/workstation-browser" : "/api/openclaw/v1/tools/workstation-work";
      if (browser && req.url === "/api/openclaw/v1/tools/workstation-work/policy") {
        res.writeHead(200, { "Content-Type": "application/json" }); res.end(JSON.stringify({ bound: false })); return;
      }
      if (req.url === `${base}/admit`) {
        const p = body.provenance, input = conversations.get(body.source_message_event_id);
        assert(input); assert.equal(p.actor_id, input.actor_id); assert.equal(p.channel_id, input.channel_id);
        assert.equal(p.session_key, input.session_key); assert.equal(p.message_id, input.message_id);
        assert.equal(sha(body.instruction), input.text_sha256);
        assert(![...workers.values()].some((entry) => entry.source === input.source_event_id), "one request must not admit a second worker");
        const session = serviceSession(body.run_nonce); sessions.add(session);
        workers.set(session, { source: input.source_event_id, channel: input.channel_id, input, created: false, read: false, verified: false,
          ...(browser ? { beforeReads: await browser.privateBodyReadCount() } : {}) });
        result = { status: "ready", mode: "create", worker_request_id: body.run_nonce, follow_up_source_id: input.source_event_id,
          artifact_request_id: `note:${input.source_event_id}`, artifact_task_id: `task:${input.source_event_id}`, instruction: body.instruction, ...(browser ? { browser_request_event_id: `browser:${input.source_event_id}`, resource_id: browser.resource.resource_id, resource: browser.resource } : {}) };
      } else if (req.url === `${base}/result`) {
        const worker = workers.get(serviceSession(body.run_nonce)); assert(worker);
        assert.equal(body.follow_up_source_id, worker.source); assert.equal(body.provenance.actor_id, worker.input.actor_id);
        assert.equal(body.source_message_event_id, worker.source);
        assert.equal(body.provenance.session_key, worker.input.session_key);
        assert.equal(body.provenance.message_id, worker.input.message_id);
        if (!browser) assert(worker.verified);
        if (worker.verified) conversationReceipts.push({ source: worker.source, channel: worker.channel });
        result = { status: worker.verified ? "verified" : "pending_or_unknown", ...(worker.verified
          ? { content: browser ? worker.excerpt : "Sort, label, store." } : { reason: "no_terminal_read_receipt" }),
          ...(browser ? { browser_request_event_id: `browser:${worker.source}` } : {}), provider_effect_count: 0 };
        parentResults.push({ source: worker.source, ...result });
      } else if (browser && req.url === `${base}/retained`) {
        const input = conversations.get(body.source_message_event_id), p = body.provenance;
        assert(input?.original_source, "status requires a fresh authenticated instruction");
        assert.equal(p.actor_id, input.actor_id); assert.equal(p.channel_id, input.channel_id);
        assert.equal(p.session_key, input.session_key); assert.equal(p.message_id, input.message_id);
        assert.equal(body.browser_request_event_id, `browser:${input.original_source}`);
        const worker = [...workers.values()].find((entry) => entry.source === input.original_source);
        assert(worker?.verified, "status must not turn pending into verified");
        assert.equal(input.member_id, worker.input.member_id);
        assert.notEqual(input.source_event_id, worker.source);
        statusReads.push({ source: worker.source, channel: worker.channel, input_source: input.source_event_id });
        if (!conversationReceipts.some((entry) => entry.source === worker.source)) conversationReceipts.push({ source: worker.source, channel: worker.channel });
        result = { status: "verified", browser_request_event_id: body.browser_request_event_id, excerpt: worker.excerpt, provider_effect_count: 0 };
      } else if (req.url === "/api/openclaw/v1/conversations/reply") {
        const input = conversations.get(body.source_message_event_id); assert(input?.channel_id === "gmail");
        assert.equal(body.provenance.thread_id, input.thread_id);
        assert.equal(body.host_decision.body_sha256, sha(body.body));
        assert(input.original_source ? statusReads.some((row) => row.input_source === input.source_event_id)
          : parentResults.some((row) => row.source === input.source_event_id));
        conversationReplies.push({ source: input.source_event_id, channel: "gmail" }); result = { status: "sent" };
      } else if (req.url.startsWith(base) || req.url.includes("/service-private-")) {
        const p = body.provenance; assert.equal(p.actor_id, "service-principal:chief-of-staff");
        assert.equal(p.agent_id, agent); assert(validSession(p.session_key));
        assert.equal(p.channel_id, "administrateme-service"); assert.equal(p.account_id, undefined);
        const worker = workers.get(p.session_key); assert(worker, "worker lost exact admitted session");
        const note = { task_id: `task:${worker.source}`, last_event_id: `event:${worker.source}`, title: "Private garage plan", notes: "Sort, label, store." };
        if (browser && req.url === `${base}/policy`) result = { bound: true, mode: worker.verified ? "complete" : "read", resource: browser.resource };
        else if (browser && req.url === `${base}/capture`) {
          assert.equal(body.native_target_id, browser.targetId); assert.equal(body.url, browser.url);
          assert.deepEqual(body.actual_identity, browser.resource.expected_identity);
          assert(body.excerpt.includes("Review the garage plan on Friday.")); assert.equal(body.truncated, false);
          assert(!worker.verified, "native capture must happen once");
          assert(await browser.privateBodyReadCount() > worker.beforeReads, "verified capture must follow a real page body read");
          worker.verified = true; worker.excerpt = body.excerpt;
          result = { status: "verified", observation_event_id: `observed:${worker.source}`, excerpt: body.excerpt, provider_effect_count: 0 };
        } else if (browser && req.url === `${base}/failure`) throw new Error("Actual native browser result failed its installed guard");
        else if (req.url === `${base}/policy`) result = { bound: true, mode: worker.created ? "read" : "create", artifact_request_id: `note:${worker.source}`, artifact_task_id: note.task_id };
        else if (req.url.endsWith("/service-private-work")) {
          assert(!worker.created, "duplicate native note creation"); assert.equal(body.request_id, `note:${worker.source}`);
          assert.equal(body.record_kind, "scratchpad"); worker.created = true; result = { task_id: note.task_id, work: note };
        } else if (req.url.endsWith("/service-private-state")) {
          assert(worker.created); assert.equal(body.task_id, note.task_id); worker.read = true; result = { work: [note] };
        } else if (req.url === `${base}/read-back`) {
          assert(worker.read); assert.equal(body.task_id, note.task_id);
          assert.equal(body.content_sha256, sha(JSON.stringify([note.task_id, note.last_event_id, note.title, note.notes])));
          worker.verified = true; result = { verified: true };
        } else throw new Error("unexpected worker endpoint");
      }
      res.writeHead(200, { "Content-Type": "application/json" }); res.end(JSON.stringify(result));
    } catch (error) { errors.push(error.message); res.writeHead(500); res.end("Synthetic workstation assertion failed"); }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { baseUrl: `http://127.0.0.1:${server.address().port}`, errors, conversationReceipts, conversationReplies, parentResults, statusReads, nativeFailures,
    workerState(source) {
      const rows = [...workers].filter(([, value]) => value.source === source);
      assert(rows.length <= 1);
      return rows.length ? { source, session_digest: sha(rows[0][0]), verified: rows[0][1].verified } : undefined;
    },
    get workerCount() { return workers.size; },
    get failureModelCalls() { return failureModelCalls; },
    close: () => new Promise((resolve) => server.close(resolve)) };
}
