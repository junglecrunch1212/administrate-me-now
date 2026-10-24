/** Hosted-only synthetic model + domain endpoints; never a production adapter. */
import assert from "node:assert/strict";
import http from "node:http";
import { createHash, createHmac } from "node:crypto";

export async function startServiceWorkFixtures({ secret, agent, sessions, validSession, conversations = new Map() }) {
  assert.equal(process.env.GITHUB_ACTIONS, "true");
  const receipts = [];
  const conversationReceipts = [];
  const conversationReplies = [];
  const initialInputs = [];
  const errors = [];
  let modelCalls = 0;
  let completed = false;
  const sourceEvent = "fixture-source-event";
  const steps = [
    ["administrateme_workstation_inventory", {}],
    ["administrateme_service_context", { limit: 2 }],
    ["administrateme_service_private_work", { request_id: "fixture-private-note", title: "Prepare an internal brief", record_kind: "note", source_event_ids: [sourceEvent] }],
    ["administrateme_service_private_work_state", { task_id: "cos-task:fixture", request_id: "fixture-complete-work", expected_last_event_id: "fixture-created", action: "complete", reason_code: "completed" }],
    ["administrateme_service_private_state", { task_id: "cos-task:fixture", limit: 10 }],
  ];
  const server = http.createServer(async (req, res) => {
    try {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const body = JSON.parse(Buffer.concat(chunks).toString() || "{}");
      if (req.url === "/v1/responses") {
        modelCalls++;
        const input = Array.isArray(body.input) ? body.input : [];
        const outputs = input.filter((entry) => entry.type === "function_call_output").length;
        const encodedInput = JSON.stringify(input);
        const channel = encodedInput.includes("PROBE_CONVERSATION_GMAIL") ? "gmail" :
          encodedInput.includes("PROBE_CONVERSATION_PORTAL") ? "administrateme-portal" : undefined;
        if (channel) {
          const names = body.tools.map((tool) => tool.name ?? tool.function?.name);
          assert(names.includes("administrateme_apple_reminder_status"), "actual conversation lacks current-input status tool");
          const events = [];
          if (outputs === 0) {
            const args = { domain_approval_event_id: `fixture-${channel}-approval` };
            const item = { type: "function_call", id: `fc_conversation_${channel}`, call_id: `call_conversation_${channel}`, name: "administrateme_apple_reminder_status", arguments: "" };
            events.push({ type: "response.output_item.added", item });
            events.push({ type: "response.function_call_arguments.delta", delta: JSON.stringify(args) });
            events.push({ type: "response.output_item.done", item: { ...item, arguments: JSON.stringify(args) } });
          } else {
            const item = { type: "message", id: `msg_conversation_${channel}`, role: "assistant", content: [], status: "in_progress" };
            const answer = "Synthetic channel round trip complete.";
            events.push({ type: "response.output_item.added", item });
            events.push({ type: "response.output_text.delta", delta: answer });
            events.push({ type: "response.output_item.done", item: { ...item, status: "completed", content: [{ type: "output_text", text: answer, annotations: [] }] } });
          }
          events.push({ type: "response.completed", response: { status: "completed", usage: { input_tokens: 10, output_tokens: 10, total_tokens: 20 } } });
          res.writeHead(200, { "Content-Type": "text/event-stream" });
          res.end(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("") + "data: [DONE]\n\n");
          return;
        }
        const freshRead = JSON.stringify(input).includes("PROBE_FRESH_READ");
        const plan = freshRead ? [steps.at(-1)] : steps;
        if (outputs === 0) {
          const encoded = JSON.stringify(input);
          const prompt = JSON.stringify({ instructions: body.instructions, input });
          assert(prompt.includes("# Established Chief-of-Staff identity"), "native model prompt lacks established service identity");
          assert(!prompt.includes("BOOTSTRAP.md - Birth Sequence"), "native model prompt includes first-run onboarding");
          assert(!prompt.includes("Introduce yourself as the user's new assistant"), "native model prompt requests a new-assistant introduction");
          assert(!encoded.includes("GRANTED_SOURCE_FIXTURE"), "new native request reused earlier granted context");
          initialInputs.push(encoded);
          const names = body.tools.map((tool) => tool.name ?? tool.function?.name);
          for (const [name] of steps) assert(names.includes(name), `native agent lacks ${name}`);
          assert(!names.includes("administrateme_state"), "service agent inherited member tool");
        }
        const events = [];
        if (outputs < plan.length) {
          const [name, args] = plan[outputs];
          const item = { type: "function_call", id: `fc_fixture_${outputs}`, call_id: `call_fixture_${outputs}`, name, arguments: "" };
          events.push({ type: "response.output_item.added", item });
          events.push({ type: "response.function_call_arguments.delta", delta: JSON.stringify(args) });
          events.push({ type: "response.output_item.done", item: { ...item, arguments: JSON.stringify(args) } });
        } else {
          const item = { type: "message", id: "msg_fixture", role: "assistant", content: [], status: "in_progress" };
          events.push({ type: "response.output_item.added", item });
          events.push({ type: "response.output_text.delta", delta: "Internal work verified." });
          events.push({ type: "response.output_item.done", item: { ...item, status: "completed", content: [{ type: "output_text", text: "Internal work verified.", annotations: [] }] } });
        }
        events.push({ type: "response.completed", response: { status: "completed", usage: { input_tokens: 10, output_tokens: 10, total_tokens: 20 } } });
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        res.end(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("") + "data: [DONE]\n\n");
        return;
      }
      assert.equal(req.headers.authorization, `Bearer ${secret}`);
      let result = { ok: true };
      if (req.url === "/api/openclaw/v1/tools/apple-reminder-status" || req.url === "/api/openclaw/v1/conversations/reply") {
        const expected = conversations.get(body.source_message_event_id);
        assert(expected, "conversation tool did not retain signed original source receipt");
        assert.deepEqual(body.provenance, {
          actor_id: expected.actor_id, channel_id: expected.channel_id, account_id: expected.account_id,
          agent_id: expected.agent_id, session_key: expected.session_key, message_id: expected.message_id,
          call_kind: "tool", correlation_id: body.provenance.correlation_id,
          openclaw_version: "2026.7.2", openclaw_commit: "ce67ffb70e83e5ebb56ea02e814e577153f84f61",
          ...(expected.thread_id ? { thread_id: expected.thread_id } : {}),
        });
        assert.equal(body.openclaw_approval_receipt_id, undefined, "status or same-conversation output fabricated a human allow-once");
        if (req.url.endsWith("/apple-reminder-status")) {
          assert.equal(body.domain_approval_event_id, `fixture-${expected.channel_id}-approval`);
          conversationReceipts.push({ source: body.source_message_event_id, channel: expected.channel_id });
          result = { status: "pending", provider_effect_count: 0, fixture_only: true };
        } else {
          assert.equal(expected.channel_id, "gmail", "portal output must return in its existing stream");
          assert.equal(body.body, "Synthetic channel round trip complete.");
          const { signature, ...decision } = body.host_decision;
          const bodyHash = createHash("sha256").update(body.body).digest("hex");
          assert.deepEqual(decision, {
            contract: "authenticated-conversation-reply-v1", rule_id: "same_conversation_reply_v1",
            source_event_id: body.source_message_event_id, channel: "gmail", account_id: expected.account_id,
            actor_id: expected.actor_id, session_key: expected.session_key, agent_id: expected.agent_id,
            message_id: expected.message_id, thread_id: expected.thread_id, body_sha256: bodyHash,
            issued_at_ms: decision.issued_at_ms, expires_at_ms: decision.expires_at_ms,
          });
          assert(decision.issued_at_ms <= Date.now() && decision.expires_at_ms > Date.now());
          assert(decision.expires_at_ms - decision.issued_at_ms <= 30000);
          // All fixture coordinates are ASCII. Sorted keys match Python's compact
          // canonical JSON; production Unicode hashing is covered separately.
          const canonical = JSON.stringify(Object.fromEntries(Object.entries(decision).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)));
          assert.equal(signature, createHmac("sha256", secret).update("authenticated-conversation-reply-v1|" + canonical).digest("hex"));
          assert.equal(body.request_id, "gmail-reply:" + createHash("sha256").update(body.source_message_event_id + "|" + bodyHash).digest("hex"));
          assert(!conversationReplies.some((row) => row.source === body.source_message_event_id), "a native completion emitted a duplicate reply");
          conversationReplies.push({ source: body.source_message_event_id, channel: expected.channel_id });
          result = { status: "sent", fixture_only: true, provider_effect_count: 0 };
        }
      } else if (req.url.startsWith("/api/openclaw/v1/tools/service-") || req.url === "/api/openclaw/v1/tools/workstation-inventory") {
        assert.equal(body.provenance.actor_id, "service-principal:chief-of-staff");
        assert.equal(body.provenance.agent_id, agent);
        sessions.add(body.provenance.session_key);
        assert(validSession(body.provenance.session_key), "native tool lost signed per-request binding");
        assert.equal(body.provenance.channel_id, "administrateme-service");
        assert.equal(body.provenance.account_id, undefined);
        assert.equal(body.openclaw_approval_receipt_id, undefined);
        receipts.push({ path: req.url, session: body.provenance.session_key });
        if (req.url.endsWith("/workstation-inventory")) {
          result = { contract: "administrateme-workstation-inventory-v1", inventory_grants_authority: false, provider_effect_count: 0 };
        } else if (req.url.endsWith("/service-context")) {
          result = { source_events: [{ event_id: sourceEvent, title: "GRANTED_SOURCE_FIXTURE", scope: "member-private:fixture", authority_grant_id: "fixture-standing-grant" }] };
        } else if (req.url.endsWith("/service-private-work")) {
          assert.equal(body.record_kind, "note");
          assert.deepEqual(body.source_event_ids, [sourceEvent]);
          result = { task_id: "cos-task:fixture", last_event_id: "fixture-created" };
        } else if (req.url.endsWith("/state")) {
          assert.equal(body.expected_last_event_id, "fixture-created");
          completed = true;
          result = { task_id: "cos-task:fixture", status: "completed", last_event_id: "fixture-completed" };
        } else if (req.url.endsWith("/service-private-state")) {
          assert(completed, "state read preceded completed work");
          result = { work: [{ task_id: "cos-task:fixture", status: "completed" }], next_cursor: null };
        } else throw new Error("Unexpected service route");
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(result));
    } catch (error) {
      errors.push(error.message);
      res.writeHead(500); res.end("Synthetic fixture assertion failed");
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const baseUrl = `http://127.0.0.1:${server.address().port}`;
  return {
    baseUrl, receipts, initialInputs, errors, conversationReceipts, conversationReplies,
    get modelCalls() { return modelCalls; },
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}
