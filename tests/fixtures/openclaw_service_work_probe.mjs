/** Hosted-only synthetic model + domain endpoints; never a production adapter. */
import assert from "node:assert/strict";
import http from "node:http";

export async function startServiceWorkFixtures({ secret, agent, sessions, validSession }) {
  assert.equal(process.env.GITHUB_ACTIONS, "true");
  const receipts = [];
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
      if (req.url.startsWith("/api/openclaw/v1/tools/service-") || req.url === "/api/openclaw/v1/tools/workstation-inventory") {
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
    baseUrl, receipts, initialInputs, errors,
    get modelCalls() { return modelCalls; },
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}
