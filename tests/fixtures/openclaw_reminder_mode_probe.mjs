/** Hosted-only actual pinned hook proof; model/domain/provider boundaries synthetic. */
import assert from "node:assert/strict";
import { createHash, createHmac, randomBytes } from "node:crypto";
import path from "node:path";
import { pathToFileURL } from "node:url";

export async function proveReminderMode({ pluginDirectory, nativeRegistration, policy, api, hookSdk, createHookRunner, versions }) {
  assert.equal(process.env.GITHUB_ACTIONS, "true");
  const load = (name) => import(pathToFileURL(path.join(pluginDirectory, name + ".js")).href);
  const [{ registerHooks }, { registerReminderModeTool }, { registerTools }, { registerConversationInput }, { readPluginConfig }, { canonical }, { modeOrigin, modePrompt }] = await Promise.all([
    load("hooks"), load("reminder-mode"), load("tools"), load("conversation-input"), load("config"), load("approval"), load("reminder-mode-policy"),
  ]);
  const hash = (value) => createHash("sha256").update(canonical(value)).digest("hex");
  const sha = (value) => createHash("sha256").update(value).digest("hex");
  const span = (text) => [{ start: 0, end: Array.from(text).length, text }];
  const rule = "current-human-request.cos-reminder-mode-choice.v1", tool = "administrateme_cos_reminder_choice", executeTool = "administrateme_execute_pending_provider_action";
  const secretName = "ADMINME_NATIVE_REMINDER_MODE_PROBE_SECRET";
  for (const channel of ["imessage", "gmail", "administrateme-portal"]) {
    const secret = randomBytes(32).toString("hex"); process.env[secretName] = secret;
    try {
      const member = "member-mode-native-fixture", agent = `adminme-${sha(member).slice(0, 16)}`;
      const actor = channel === "imessage" ? "+15555550119" : "fixture-trusted-member";
      const account = channel === "administrateme-portal" ? "member-portal" : "fixture-service-account", thread = channel === "gmail" ? "fixture-private-thread" : "";
      const session = channel === "imessage" ? `agent:${agent}:imessage:${account}:direct:${actor}` : `agent:${agent}:administrateme-${channel === "gmail" ? "gmail" : "portal"}-fixture`;
      const context = { runId: `mode-original-${channel}`, agentId: agent, sessionKey: session };
      const registration = nativeRegistration(policy), tools = new Map(), gateways = new Map(), calls = [];
      const now = Date.now(), due = new Date(Math.floor((now + 172800000) / 1000) * 1000).toISOString().replace(/\.000Z$/, "+00:00");
      const draft = { title: "Email the household brief", due_at: due, notes: null };
      const original = `Make yourself an Apple reminder to email me the household brief at ${due}, so it tells you to send it.`;
      const origin = { channel_id: channel, account_id: account, actor_id: actor, session_key: session, thread_id: thread || null };
      const action = { provider: "apple_reminders", operation: "create_reminder", resource_owner: "service-principal:chief-of-staff", operating_principal: "service-principal:chief-of-staff",
        provider_account_id: "fixture-apple-account", source_id: "fixture-apple-source", list_id: "fixture-work-list", device_identity_sha256: "a".repeat(64), os_uid: 501,
        selection_sha256: "b".repeat(64), write_policy_sha256: "c".repeat(64), ...draft, selected_by_policy: true };
      const offered = { member_id: member, task_id: "fixture-mode-task", action_id: "fixture-mode-action", request_id: "fixture-mode-request",
        original_source_event_id: "ev-mode-1", original_source_fingerprint: hash(original), original_submitted_at: new Date(now - 2000).toISOString(), draft_fingerprint: hash(draft),
        provider: "apple_reminders", operation: "create_reminder", resource_owner: action.resource_owner, provider_account_id: action.provider_account_id,
        ...draft, offered_at: new Date(now - 1000).toISOString(), pending_expires_at: due, expiry_reason: "requested_reminder_due_time", provider_effect_count: 0,
        clarification_kind: "reminder_only_or_email", action, origin, prompt: modePrompt(draft.title, due, null) };
      let presentation, tamper = true;
      const runtime = { trackedSessions: new Map(), transport: { config: readPluginConfig({ sharedSecretEnv: secretName, memberAgentIds: [agent],
        standingSelfReplyBindings: channel === "imessage" ? [sha(JSON.stringify(["cos-self-reply-v1", agent, channel, account, actor]))] : [],
        scheduledSelfReminderBindings: [], forwardIntakeAccounts: [{ channelId: "imessage", accountId: "fixture-service-account" }] }),
        async request(endpoint, body) {
          calls.push({ endpoint, body });
          if (endpoint.endsWith("/forward")) return { intent_event_id: body.provenance.message_id };
          if (endpoint.endsWith("/self-reply")) return { transport_dispatch: { channel: "imessage", account_id: account, recipient: actor, body: body.body, approval_event_id: "fixture-mode-reply" } };
          if (endpoint.endsWith("/cos-responses/result") || endpoint.endsWith("/conversations/reply")) return { status: "sent", event_id: "ev-mode-2-reply" };
          if (endpoint.endsWith("/cos-reminder-choice/presented")) {
            const { signature, provenance, ...proof } = body;
            assert.equal(proof.body, offered.prompt);
            assert.equal(proof.source_message_event_id, offered.original_source_event_id);
            assert.deepEqual(modeOrigin(provenance), origin);
            assert.equal(signature, createHmac("sha256", secret).update("cos-reminder-presentation-v1|" + canonical({ ...proof, origin })).digest("hex"));
            assert.equal(proof.response_result_event_id, channel === "administrateme-portal" ? null : "ev-mode-2-reply");
            presentation = { member_id: member, task_id: offered.task_id, offered_event_id: "ev-mode-2", source_event_id: "ev-mode-1", origin,
              prompt_sha256: sha(proof.body), run_id: proof.run_id, presented_at: new Date().toISOString(),
              issued_at_ms: proof.issued_at_ms, expires_at_ms: proof.expires_at_ms, host_signature: signature,
              response_result_event_id: proof.response_result_event_id, presentation_kind: channel === "administrateme-portal" ? "native_portal_final" : "sent_reply", provider_effect_count: 0 };
            return { state: "presented", presentation_event_id: "ev-mode-3" };
          }
          if (endpoint.endsWith("/cos-reminder-choice")) {
            if (body.action === "offer") return { state: "mode_choice_required", task_id: offered.task_id, choice_event_id: "ev-mode-2", presentation_prompt: offered.prompt, pending_expires_at: due };
            assert.equal(body.source_message_event_id, "ev-mode-4"); assert.equal(body.action, "answer"); assert.ok(presentation);
            const current = Date.now();
            const conversation = channel === "gmail" ? { channel_id: channel, account_id: account, actor_id: actor, thread_id: thread } : null;
            const ticket = { contract: "authenticated-request-v1", member_id: member, domain_approval_event_id: "fixture-mode-domain", grounding_event_id: "fixture-mode-grounding",
              source_event_id: "ev-mode-4", action_fingerprint: hash(action), action, rule_id: rule, issued_at: new Date(current).toISOString(), expires_at: new Date(current + 600000).toISOString(), evidence: span("1"),
              continuation: { offered_event_id: "ev-mode-2", offered: { ...offered, prompt: tamper ? "2. Create the reminder instead" : offered.prompt },
                original_instruction: original, original_evidence: span(original), presentation_event_id: "ev-mode-3", presentation,
                selection: { answer_source_event_id: "ev-mode-4", answer_submitted_at: new Date(current).toISOString(), answer_conversation: conversation,
                  eligible_tasks: [{ offered_event_id: "ev-mode-2", task_id: offered.task_id, title_fingerprint: hash(offered.title.toLowerCase()), pending_expires_at: due, conversation }] } } };
            return { request_authorization: { ...ticket, signature: createHmac("sha256", secret).update("authenticated-request-v1|" + canonical(ticket)).digest("hex") } };
          }
          if (endpoint.endsWith("/approvals/authenticated-request")) {
            assert.equal(body.decision, "authenticated_request"); assert.equal(body.authorization.rule_id, rule); assert.equal(body.authorization.source_event_id, "ev-mode-4");
            assert.equal(body.signature, createHmac("sha256", secret).update(["administrateme-request-approval-v1", body.receipt_id, body.tool_name, body.request_fingerprint,
              String(body.issued_at_ms), String(body.expires_at_ms), "authenticated_request", versions.openclaw.version, versions.openclaw.commit, canonical(body.authorization)].join("|")).digest("hex"));
            return { recorded: true };
          }
          assert.equal(endpoint, "/api/openclaw/v1/tools/execute-pending-provider-action");
          assert.equal(body.domain_approval_event_id, "fixture-mode-domain"); assert.match(body.openclaw_approval_receipt_id, /^ocreq_/);
          return { state: "pending", provider_effect_count: 0, fixture_only: true };
        } } };
      const nativeApi = { ...api, on: registration.on,
        runtime: { ...api.runtime, channel: { ...api.runtime?.channel, outbound: { loadAdapter: async () => ({ sendText: async () => ({}) }) } } },
        registerTool: (factory, options) => tools.set(options.name, factory), registerGatewayMethod: (name, handler) => gateways.set(name, handler) };
      registerHooks(nativeApi, runtime); registerReminderModeTool(nativeApi, runtime); registerTools(nativeApi, runtime); registerConversationInput(nativeApi, runtime);
      const runner = createHookRunner(registration.state.registry, { catchErrors: false });
      const hooks = (name) => registration.state.registry.typedHooks.filter((row) => row.hookName === name).map((row) => row.handler);
      const deliver = async (source, content) => {
        context.runId = `mode-${source}-${channel}`;
        if (channel === "imessage") {
          const event = hookSdk.toPluginMessageReceivedEvent(hookSdk.deriveInboundMessageHookContext({ From: actor, SenderId: actor, Body: content, Provider: "imessage", AccountId: account, MessageSid: source, SessionKey: session }));
          for (const handler of hooks("message_received")) await handler(event, { ...context, channelId: channel, accountId: account });
        } else {
          const fields = ["source_event_id", "session_key", "agent_id", "actor_id", "channel_id", "account_id", "message_id", "thread_id", "text_sha256", "nonce"];
          const ticket = { source_event_id: source, session_key: session, agent_id: agent, actor_id: actor, channel_id: channel, account_id: account, message_id: source, thread_id: thread,
            text_sha256: sha(content), nonce: randomBytes(16).toString("hex"), expires_at_ms: Date.now() + 10000 };
          ticket.signature = createHmac("sha256", secret).update(["cos-conversation-input-v1", ...fields.map((key) => ticket[key]), String(ticket.expires_at_ms)].join("|")).digest("hex");
          let accepted = false; await gateways.get("administrateme.conversation.prepare")({ params: ticket, respond: (ok) => { accepted = ok; } }); assert.equal(accepted, true);
        }
        for (const handler of hooks("before_agent_reply")) await handler({ cleanedBody: content }, context);
      };
      await deliver("ev-mode-1", original);
      const stage = { action: "offer", title: draft.title, due_at: due, request_id: offered.request_id };
      assert.notEqual((await runner.runBeforeToolCall({ toolName: tool, toolCallId: "stage-mode", params: stage }, context))?.block, true);
      await tools.get(tool)(context).execute("stage-mode", stage);
      const messages = [{ role: "user", content: original }, { role: "assistant", content: offered.prompt, phase: "final", stopReason: "stop" }];
      for (const handler of hooks("agent_end")) await handler({ success: true, runId: context.runId, messages }, context);
      assert.ok(presentation, `actual ${channel} final hook did not retain presentation`);
      assert.equal(calls.filter((row) => row.endpoint.endsWith("/cos-reminder-choice/presented")).length, 1);
      await deliver("ev-mode-4", "1");
      const answer = { action: "answer", task_id: offered.task_id };
      await runner.runBeforeToolCall({ toolName: tool, toolCallId: "wrong-mode-map", params: answer }, context);
      await assert.rejects(() => tools.get(tool)(context).execute("wrong-mode-map", answer), /actually presented/);
      assert.equal(calls.filter((row) => row.endpoint.endsWith("/approvals/authenticated-request")).length, 0);
      tamper = false;
      await runner.runBeforeToolCall({ toolName: tool, toolCallId: "answer-mode", params: answer }, context);
      await tools.get(tool)(context).execute("answer-mode", answer);
      const params = { domain_approval_event_id: "fixture-mode-domain" };
      const admitted = await runner.runBeforeToolCall({ toolName: executeTool, toolCallId: "execute-mode", params }, context);
      assert.notEqual(admitted?.block, true); assert.equal(admitted.requireApproval, undefined);
      await tools.get(executeTool)(context).execute("execute-mode", admitted.params);
      registration.on("before_tool_call", () => ({ block: true, blockReason: "Synthetic independent mode veto" }), { priority: -1 });
      const veto = await runner.runBeforeToolCall({ toolName: executeTool, toolCallId: "mode-veto", params }, context);
      assert.equal(veto.block, true); assert.equal(veto.blockReason, "Synthetic independent mode veto");
      assert.equal(calls.filter((row) => row.endpoint.endsWith("/execute-pending-provider-action")).length, 1);
      assert.equal(calls.filter((row) => row.endpoint.includes("requested-email") || row.endpoint.includes("self-reminders")).length, 0);
    } finally { delete process.env[secretName]; }
  }
}
