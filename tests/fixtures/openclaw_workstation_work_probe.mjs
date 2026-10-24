/** Synthetic loopback endpoints for the real pinned native handoff. Hosted only. */
import assert from "node:assert/strict";
import http from "node:http";
import { createHash, createHmac } from "node:crypto";

export async function startWorkstationWorkFixtures({ secret, agent, sessions, validSession, conversations, browser }) {
  assert.equal(process.env.GITHUB_ACTIONS, "true");
  const errors = [], conversationReceipts = [], conversationReplies = [], workers = new Map();
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
        const encoded = JSON.stringify(input);
        const channel = encoded.includes("PROBE_WORKSTATION_GMAIL") ? "gmail" : "administrateme-portal";
        const source = `fixture-${channel}-instruction`;
        const names = body.tools.map((tool) => tool.name ?? tool.function?.name);
        const worker = !names.includes("administrateme_workstation_work");
        const outputs = input.filter((entry) => entry.type === "function_call_output").length;
        let step;
        if (worker && browser) {
          assert.deepEqual(names, ["browser"], "browser worker exposes only the reviewed native tool");
          step = outputs === 0 ? ["browser", { action: "snapshot" }] : undefined;
          if (outputs > 0) assert(encoded.includes("Review the garage plan on Friday."), "browser model did not receive the retained native observation");
        } else if (worker) {
          assert(names.includes("administrateme_service_private_state"), "worker lacks real private read tool");
          assert(!names.includes("exec") && !names.includes("browser"));
          const plan = [
            ["administrateme_service_private_work", { request_id: `note:${source}`, title: "Private garage plan", description: "Sort, label, store.", record_kind: "scratchpad" }],
            ["administrateme_service_private_state", { task_id: `task:${source}` }],
          ];
          step = plan[outputs];
        } else {
          const text = channel === "gmail" ? "PROBE_WORKSTATION_GMAIL: draft a private garage plan." : "PROBE_WORKSTATION_PORTAL: draft a private garage plan.";
          step = outputs === 0 ? ["administrateme_workstation_work", { action: browser ? "browser_read" : "create", instruction: text }] : undefined;
          if (outputs > 0) assert(encoded.includes('\\"status\\":\\"verified\\"') || encoded.includes('"status":"verified"'), "parent resumed without verified worker result");
        }
        const events = [];
        if (step) {
          const [name, args] = step;
          const item = { type: "function_call", id: `fc_${worker ? "worker" : "member"}_${channel}_${outputs}`, call_id: `call_${worker ? "worker" : "member"}_${channel}_${outputs}`, name, arguments: "" };
          events.push({ type: "response.output_item.added", item });
          events.push({ type: "response.function_call_arguments.delta", delta: JSON.stringify(args) });
          events.push({ type: "response.output_item.done", item: { ...item, arguments: JSON.stringify(args) } });
        } else {
          const item = { type: "message", id: `msg_${worker ? "worker" : "member"}_${channel}`, role: "assistant", content: [], status: "in_progress" };
          events.push({ type: "response.output_item.added", item });
          events.push({ type: "response.output_text.delta", delta: "Your private scratchpad was created and read back." });
          events.push({ type: "response.output_item.done", item: { ...item, status: "completed", content: [{ type: "output_text", text: "Your private scratchpad was created and read back.", annotations: [] }] } });
        }
        events.push({ type: "response.completed", response: { status: "completed", usage: { input_tokens: 10, output_tokens: 10, total_tokens: 20 } } });
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        res.end(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("") + "data: [DONE]\n\n"); return;
      }
      assert.equal(req.headers.authorization, `Bearer ${secret}`);
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
        const session = serviceSession(body.run_nonce); sessions.add(session);
        workers.set(session, { source: input.source_event_id, channel: input.channel_id, input, created: false, read: false, verified: false });
        result = { status: "ready", mode: "create", worker_request_id: body.run_nonce, follow_up_source_id: input.source_event_id,
          artifact_request_id: `note:${input.source_event_id}`, artifact_task_id: `task:${input.source_event_id}`, instruction: body.instruction, ...(browser ? { browser_request_event_id: `browser:${input.source_event_id}`, resource_id: browser.resource.resource_id, resource: browser.resource } : {}) };
      } else if (req.url === `${base}/result`) {
        const worker = workers.get(serviceSession(body.run_nonce)); assert(worker?.verified);
        assert.equal(body.follow_up_source_id, worker.source); assert.equal(body.provenance.actor_id, worker.input.actor_id);
        conversationReceipts.push({ source: worker.source, channel: worker.channel });
        result = { status: "verified", content: browser ? worker.excerpt : "Sort, label, store.", provider_effect_count: 0 };
      } else if (req.url === "/api/openclaw/v1/conversations/reply") {
        const input = conversations.get(body.source_message_event_id); assert(input?.channel_id === "gmail");
        assert.equal(body.provenance.thread_id, input.thread_id);
        assert.equal(body.host_decision.body_sha256, sha(body.body));
        assert(conversationReceipts.some((row) => row.source === input.source_event_id));
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
  return { baseUrl: `http://127.0.0.1:${server.address().port}`, errors, conversationReceipts, conversationReplies,
    close: () => new Promise((resolve) => server.close(resolve)) };
}
