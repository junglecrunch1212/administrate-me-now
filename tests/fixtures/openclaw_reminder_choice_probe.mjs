/** Hosted-only reminder continuation proof through the exactly pinned host hooks.
 * Domain receipts are synthetic; this fixture constructs no provider client.
 */
import assert from "node:assert/strict";
import { createHash, createHmac, randomBytes } from "node:crypto";
import path from "node:path";
import { pathToFileURL } from "node:url";

export async function proveReminderChoice({ pluginDirectory, nativeRegistration, policy, api, hookSdk, createHookRunner, versions }) {
  assert.equal(process.env.GITHUB_ACTIONS, "true");
  const load = (name) => import(pathToFileURL(path.join(pluginDirectory, name + ".js")).href);
  const [{ registerHooks }, { registerAppleReminderTools }, { registerTools }, { registerConversationInput }, { readPluginConfig }, { canonical }] = await Promise.all([
    load("hooks"), load("apple-reminders"), load("tools"), load("conversation-input"), load("config"), load("approval"),
  ]);
  const hash = (value) => createHash("sha256").update(canonical(value)).digest("hex");
  const textHash = (value) => createHash("sha256").update(value).digest("hex");
  const span = (text) => [{ start: 0, end: Array.from(text).length, text }];
  const rule = "current-human-request.apple-reminder-list-choice.v1";
  const tool = "administrateme_apple_reminder_choice";
  const executeTool = "administrateme_execute_pending_provider_action";
  const secretName = "ADMINME_NATIVE_REMINDER_CHOICE_PROBE_SECRET";
  for (const channel of ["imessage", "administrateme-portal", "gmail"]) {
    const secret = randomBytes(32).toString("hex");
    process.env[secretName] = secret;
    try {
      const member = "member-choice-native-fixture", agent = `adminme-${textHash(member).slice(0, 16)}`;
      const actor = channel === "imessage" ? "+15555550119" : "fixture-trusted-thread-actor";
      const account = channel === "administrateme-portal" ? "member-portal" : "fixture-service-account";
      const thread = channel === "gmail" ? "fixture-member-thread" : "";
      const session = channel === "imessage" ? `agent:${agent}:imessage:${account}:direct:${actor}` :
        `agent:${agent}:administrateme-${channel === "gmail" ? "gmail" : "portal"}-fixture`;
      const context = { runId: `native-choice-${channel}`, agentId: agent, sessionKey: session };
      const registration = nativeRegistration(policy), tools = new Map(), gateways = new Map(), calls = [];
      const capturedAt = Date.now();
      const due = new Date(Math.floor((capturedAt + 172800000) / 1000) * 1000).toISOString().replace(/\.000Z$/, "+00:00");
      const draft = { title: "Call a relative", due_at: due, notes: null };
      const original = `Add a reminder to my Apple Reminders to call a relative at ${due}`;
      const answer = "Personal", sourceId = `native-choice-answer-${channel}`;
      const action = { provider: "apple_reminders", operation: "create_reminder", resource_owner: "requester", ...draft,
        list_id: "fixture-personal", selected_by_policy: false };
      const offered = { member_id: member, task_id: "fixture-pending-task", action_id: "fixture-original-action", request_id: "fixture-original-request",
        original_source_event_id: "fixture-original-source", original_source_fingerprint: hash(original),
        original_submitted_at: new Date(capturedAt - 86460000).toISOString(), draft_fingerprint: hash(draft),
        provider: "apple_reminders", operation: "create_reminder", resource_owner: "requester", provider_account_id: "fixture-apple-account",
        ...draft, choices: [{ list_id: "fixture-personal", label: "Personal" }, { list_id: "fixture-work", label: "Work" }],
        offered_at: new Date(capturedAt - 86400000).toISOString(), pending_expires_at: due,
        expiry_reason: "requested_reminder_due_time", provider_effect_count: 0 };
      const conversation = channel === "gmail" ? { channel_id: channel, account_id: account, actor_id: actor, thread_id: thread } : null;
      let ambiguous = true;
      const runtime = { trackedSessions: new Map(), transport: {
        config: readPluginConfig({ sharedSecretEnv: secretName, memberAgentIds: [agent], standingSelfReplyBindings: [], scheduledSelfReminderBindings: [],
          forwardIntakeAccounts: [{ channelId: "imessage", accountId: "fixture-service-account" }] }),
        async request(endpoint, body) {
          calls.push({ endpoint, body });
          if (endpoint.endsWith("/approvals/authenticated-request")) {
            assert.equal(body.decision, "authenticated_request");
            assert.equal(body.authorization.rule_id, rule);
            assert.equal(body.authorization.source_event_id, sourceId);
            assert.equal(body.authorization.action_fingerprint, hash(action));
            assert.equal(body.signature, createHmac("sha256", secret).update([
              "administrateme-request-approval-v1", body.receipt_id, body.tool_name, body.request_fingerprint,
              String(body.issued_at_ms), String(body.expires_at_ms), "authenticated_request", versions.openclaw.version,
              versions.openclaw.commit, canonical(body.authorization),
            ].join("|")).digest("hex"));
            return { recorded: true };
          }
          assert.equal(body.provenance.agent_id, agent);
          assert.equal(body.provenance.session_key, session);
          assert.equal(body.provenance.channel_id, channel);
          assert.equal(body.provenance.account_id, account);
          assert.equal(body.provenance.actor_id, actor);
          if (endpoint.endsWith("/forward")) return { intent_event_id: body.provenance.message_id === "fixture-current-message" ? sourceId : "fixture-other-source" };
          if (endpoint.endsWith("/apple-reminder-choice")) {
            assert.equal(body.source_message_event_id, sourceId);
            assert.equal(body.action, "answer");
            assert.equal(body.task_id, offered.task_id);
            assert.equal(body.openclaw_approval_receipt_id, undefined);
            const now = Date.now();
            const candidates = [{ offered_event_id: "fixture-offer-a", task_id: offered.task_id, title_fingerprint: hash(offered.title.toLowerCase()), pending_expires_at: due, conversation }];
            if (ambiguous || channel === "gmail") candidates.push({ offered_event_id: "fixture-offer-b", task_id: "fixture-other-task",
              title_fingerprint: hash("buy groceries"), pending_expires_at: due,
              conversation: ambiguous ? conversation : { ...conversation, actor_id: "other-thread-actor", thread_id: "other-thread" } });
            const ticket = { contract: "authenticated-request-v1", member_id: member, domain_approval_event_id: "fixture-choice-domain",
              grounding_event_id: "fixture-choice-grounding", source_event_id: sourceId, action_fingerprint: hash(action), action,
              rule_id: rule, issued_at: new Date(now).toISOString(), expires_at: new Date(now + 600000).toISOString(), evidence: span(answer),
              continuation: { offered_event_id: "fixture-offer-a", offered, original_instruction: original, original_evidence: span(original),
                selection: { answer_source_event_id: sourceId, answer_submitted_at: new Date(capturedAt).toISOString(), answer_conversation: conversation, eligible_tasks: candidates } } };
            return { state: "awaiting_request_policy", task_id: offered.task_id, request_authorization: {
              ...ticket, signature: createHmac("sha256", secret).update("authenticated-request-v1|" + canonical(ticket)).digest("hex"),
            } };
          }
          assert.equal(endpoint, "/api/openclaw/v1/tools/execute-pending-provider-action");
          assert.equal(body.domain_approval_event_id, "fixture-choice-domain");
          assert.equal(body.request_authorization_event_id, "fixture-choice-grounding");
          assert.match(body.openclaw_approval_receipt_id, /^ocreq_/);
          return { state: "pending", provider_effect_count: 0, fixture_only: true };
        },
      } };
      const nativeApi = { ...api, on: registration.on,
        registerTool: (factory, options) => tools.set(options.name, factory),
        registerGatewayMethod: (name, handler) => gateways.set(name, handler) };
      registerHooks(nativeApi, runtime); registerAppleReminderTools(nativeApi, runtime); registerTools(nativeApi, runtime); registerConversationInput(nativeApi, runtime);
      const runner = createHookRunner(registration.state.registry, { catchErrors: false });
      const hooks = (name) => registration.state.registry.typedHooks.filter((row) => row.hookName === name).map((row) => row.handler);
      const deliver = async (source, content) => {
        if (channel === "imessage") {
          const incoming = hookSdk.toPluginMessageReceivedEvent(hookSdk.deriveInboundMessageHookContext({
            From: actor, SenderId: actor, Body: content, Provider: "imessage", AccountId: account,
            MessageSid: source === sourceId ? "fixture-current-message" : "fixture-other-message", SessionKey: session,
          }));
          for (const handler of hooks("message_received")) await handler(incoming, { ...context, channelId: channel, accountId: account });
        } else {
          const fields = ["source_event_id", "session_key", "agent_id", "actor_id", "channel_id", "account_id", "message_id", "thread_id", "text_sha256", "nonce"];
          const ticket = { source_event_id: source, session_key: session, agent_id: agent, actor_id: actor, channel_id: channel,
            account_id: account, message_id: source, thread_id: thread, text_sha256: textHash(content), nonce: randomBytes(16).toString("hex"), expires_at_ms: Date.now() + 10000 };
          ticket.signature = createHmac("sha256", secret).update(["cos-conversation-input-v1", ...fields.map((key) => ticket[key]), String(ticket.expires_at_ms)].join("|")).digest("hex");
          let accepted = false;
          await gateways.get("administrateme.conversation.prepare")({ params: ticket, respond: (ok) => { accepted = ok; } });
          assert.equal(accepted, true);
        }
        for (const handler of hooks("before_agent_reply")) await handler({ cleanedBody: content }, context);
      };
      await deliver(sourceId, answer);
      const params = { action: "answer", task_id: offered.task_id };
      await runner.runBeforeToolCall({ toolName: tool, toolCallId: "ambiguous-choice", params }, context);
      await assert.rejects(() => tools.get(tool)(context).execute("ambiguous-choice", params), /uniquely select/);
      assert.equal(calls.filter((row) => row.endpoint.endsWith("/approvals/authenticated-request")).length, 0);
      ambiguous = false;
      assert.notEqual((await runner.runBeforeToolCall({ toolName: tool, toolCallId: "valid-choice", params }, context))?.block, true);
      await tools.get(tool)(context).execute("valid-choice", params);
      const executionParams = { domain_approval_event_id: "fixture-choice-domain" };
      const accepted = await runner.runBeforeToolCall({ toolName: executeTool, toolCallId: "valid-choice-execute", params: executionParams }, context);
      assert.notEqual(accepted.block, true);
      assert.equal(accepted.requireApproval, undefined, "A fresh exact answer became a fabricated human allow-once request");
      await tools.get(executeTool)(context).execute("valid-choice-execute", accepted.params);
      const originalNow = Date.now;
      try {
        Date.now = () => originalNow() + 600001;
        assert.equal((await runner.runBeforeToolCall({ toolName: executeTool, toolCallId: "expired-choice", params: executionParams }, context)).block, true);
      } finally { Date.now = originalNow; }
      await deliver("fixture-other-source", "Personal for another reminder");
      assert.equal((await runner.runBeforeToolCall({ toolName: executeTool, toolCallId: "wrong-answer-source", params: executionParams }, context)).block, true);
      for (const handler of hooks("before_agent_reply")) await handler({ cleanedBody: answer }, context);
      registration.on("before_tool_call", () => ({ block: true, blockReason: "Synthetic later choice veto" }), { priority: -1 });
      const veto = await runner.runBeforeToolCall({ toolName: executeTool, toolCallId: "vetoed-choice", params: executionParams }, context);
      assert.equal(veto.block, true); assert.equal(veto.blockReason, "Synthetic later choice veto");
      assert.equal(calls.filter((row) => row.endpoint.endsWith("/execute-pending-provider-action")).length, 1);
    } finally { delete process.env[secretName]; }
  }
}
