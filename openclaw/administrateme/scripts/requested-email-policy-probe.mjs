/** Called by the pinned native-hook lab after compiling the real plugin graph.
 * Only signed domain/provider responses below are fixtures; host hooks, tools,
 * current-input validation and the native hook runner are actual implementations.
 */
import assert from "node:assert/strict";
import crypto from "node:crypto";
import path from "node:path";
import { pathToFileURL } from "node:url";

export async function checkRequestedEmailPolicy({ compiledPlugin, nativeRegistration, preparedPolicy, api, hookSdk, createHookRunner, versions }) {
  const load = (name) => import(pathToFileURL(path.join(compiledPlugin, `${name}.js`)).href);
  const { registerHooks } = await load("hooks");
  const { registerRequestedEmailTools } = await load("requested-email");
  const { registerConversationInput } = await load("conversation-input");
  const { registerTools } = await load("tools");
  const { readPluginConfig } = await load("config");
  const { canonical } = await load("approval");
  const secretName = "ADMINME_NATIVE_EMAIL_REQUEST_PROBE_SECRET";
  const secret = crypto.randomBytes(32).toString("hex");
  process.env[secretName] = secret;
  const sha = (value) => crypto.createHash("sha256").update(value).digest("hex");
  const hmac = (value) => crypto.createHmac("sha256", secret).update(value).digest("hex");
  const member = "member-native-requested-email";
  const agent = `adminme-${sha(member).slice(0, 16)}`;
  const actor = "+12025550101"; // fixture:tenant_data:ok
  const sender = "cos-native-email@example.invalid";
  const recipient = "member-native-email@example.invalid";
  const command = { subject: "Travel checklist", body: "Please bring the blue folder.", request_id: "native-email-request" };
  const text = `Email me with subject "${command.subject}" and body "${command.body}"`;
  const action = {
    provider: "gmail", operation: "send_email", resource_owner: "service-principal:chief-of-staff",
    operating_principal: "service-principal:chief-of-staff", recipient_member_id: member,
    service_channel_id: "channel:chief-of-staff:gmail", provider_account_id: sender,
    recipient, subject: command.subject, body: command.body, sender_selected_by_policy: true, recipient_selected_by_policy: true,
    content_fingerprint: sha(canonical(["cos-response-plain-text-v1", "email", sender, recipient, command.subject, command.body])),
  };
  const actionFingerprint = sha(canonical(action));
  try {
    for (const channel of ["imessage", "administrateme-portal", "gmail"]) {
      const sourceId = `native-email-source-${channel}`;
      const approvalId = `native-email-approval-${channel}`;
      const groundingId = `native-email-grounding-${channel}`;
      const session = channel === "imessage" ? `agent:${agent}:imessage:fixture:direct:${actor}` :
        `agent:${agent}:${channel === "gmail" ? "administrateme-gmail" : "administrateme-portal"}-fixture`;
      const context = { runId: `native-email-run-${channel}`, agentId: agent, sessionKey: session };
      const registration = nativeRegistration(preparedPolicy);
      const tools = new Map(), gateways = new Map(), calls = [];
      const runtime = { trackedSessions: new Map(), transport: {
        config: readPluginConfig({ sharedSecretEnv: secretName, memberAgentIds: [agent], standingSelfReplyBindings: [], scheduledSelfReminderBindings: [],
          forwardIntakeAccounts: [{ channelId: "imessage", accountId: "fixture" }] }),
        async request(endpoint, value) {
          calls.push({ endpoint, value });
          if (endpoint.endsWith("/approvals/authenticated-request")) {
            assert.equal(value.tool_name, "administrateme_cos_response");
            assert.equal(value.decision, "authenticated_request");
            assert.equal(value.authorization.rule_id, "current-human-request.cos-email-to-requester.v1");
            assert.equal(value.authorization.source_event_id, sourceId);
            assert.equal(value.authorization.action_fingerprint, actionFingerprint);
            assert.equal(value.signature, hmac([
              "administrateme-request-approval-v1", value.receipt_id, value.tool_name, value.request_fingerprint,
              String(value.issued_at_ms), String(value.expires_at_ms), "authenticated_request", versions.openclaw.version,
              versions.openclaw.commit, canonical(value.authorization),
            ].join("|")));
            return { recorded: true };
          }
          assert.equal(value.provenance.agent_id, agent);
          assert.equal(value.provenance.session_key, session);
          if (endpoint.endsWith("/forward")) return { intent_event_id: value.provenance.message_id === "native-email-input" ? sourceId : "native-email-unrelated" };
          if (endpoint.endsWith("/confirm-requested-email")) {
            assert.equal(value.source_message_event_id, sourceId);
            assert.equal(value.subject, command.subject);
            assert.equal(value.body, command.body);
            assert.equal(value.openclaw_approval_receipt_id, undefined);
            const now = Date.now();
            const ticket = { contract: "authenticated-request-v1", member_id: member, domain_approval_event_id: approvalId,
              grounding_event_id: groundingId, source_event_id: sourceId, action_fingerprint: actionFingerprint, action,
              rule_id: "current-human-request.cos-email-to-requester.v1", issued_at: new Date(now).toISOString(), expires_at: new Date(now + 600000).toISOString(),
              evidence: [{ start: 0, end: Array.from(text).length, text }] };
            return { status: "awaiting_request_policy", approval_event_id: approvalId,
              request_authorization: { ...ticket, signature: hmac("authenticated-request-v1|" + canonical(ticket)) } };
          }
          assert.equal(endpoint, "/api/openclaw/v1/tools/cos-response");
          assert.equal(value.approval_event_id, approvalId);
          assert.equal(value.request_authorization_event_id, groundingId);
          assert.match(value.openclaw_approval_receipt_id, /^ocreq_/);
          return { status: "unknown", automatic_retry_allowed: false, provider_effect_count: null, fixture_only: true };
        },
      } };
      const pluginApi = { ...api, on: registration.on, registerTool: (factory, options) => tools.set(options.name, factory),
        registerGatewayMethod: (name, handler) => gateways.set(name, handler) };
      registerHooks(pluginApi, runtime); registerConversationInput(pluginApi, runtime);
      registerRequestedEmailTools(pluginApi, runtime); registerTools(pluginApi, runtime);
      const hook = (name) => registration.state.registry.typedHooks.filter((row) => row.hookName === name);
      const runner = createHookRunner(registration.state.registry, { catchErrors: false });
      const deliver = async (source, content) => {
        if (channel === "imessage") {
          const received = hookSdk.toPluginMessageReceivedEvent(hookSdk.deriveInboundMessageHookContext({
            From: actor, SenderId: actor, Body: content, Provider: "imessage", AccountId: "fixture",
            MessageSid: source === sourceId ? "native-email-input" : "native-email-other", SessionKey: session,
          }));
          for (const row of hook("message_received")) await row.handler(received, { agentId: agent, sessionKey: session, channelId: channel, accountId: "fixture" });
        } else {
          const signed = { source_event_id: source, session_key: session, agent_id: agent, actor_id: "native-email-peer", channel_id: channel,
            account_id: channel === "gmail" ? sender : "member-portal", message_id: `message:${source}`,
            thread_id: channel === "gmail" ? "native-email-thread" : "", text_sha256: sha(content), nonce: crypto.randomBytes(16).toString("hex"), expires_at_ms: Date.now() + 5000 };
          const fields = ["source_event_id", "session_key", "agent_id", "actor_id", "channel_id", "account_id", "message_id", "thread_id", "text_sha256", "nonce"];
          let accepted;
          await gateways.get("administrateme.conversation.prepare")({ params: { ...signed, signature: hmac(["cos-conversation-input-v1", ...fields.map((key) => signed[key]), String(signed.expires_at_ms)].join("|")) }, respond: (ok) => { accepted = ok; } });
          assert.equal(accepted, true);
        }
        for (const row of hook("before_agent_reply")) await row.handler({ cleanedBody: content }, context);
      };
      await deliver(sourceId, text);
      const confirmationGate = await runner.runBeforeToolCall({ toolName: "administrateme_confirm_requested_email", toolCallId: "native-email-confirm", params: command }, context);
      assert.notEqual(confirmationGate?.block, true);
      const confirmation = await tools.get("administrateme_confirm_requested_email")(context).execute("native-email-confirm", command);
      assert.equal(confirmation.details.status, "awaiting_request_policy");
      const params = { approval_event_id: approvalId, request_id: "native-email-execute" };
      const execution = await runner.runBeforeToolCall({ toolName: "administrateme_cos_response", toolCallId: "native-email-execute", params }, context);
      assert.notEqual(execution.block, true);
      assert.equal(execution.requireApproval, undefined, "Exact current email request asked for redundant human approval");
      const outcome = await tools.get("administrateme_cos_response")(context).execute("native-email-execute", execution.params);
      assert.equal(outcome.details.status, "unknown");
      const executeCount = () => calls.filter((row) => row.endpoint.endsWith("/cos-response")).length;
      assert.equal(executeCount(), 1);
      // One closed email rule cannot be borrowed by the independent Apple tool.
      const wrongTool = await runner.runBeforeToolCall({ toolName: "administrateme_execute_pending_provider_action", toolCallId: "native-email-wrong-tool", params: { domain_approval_event_id: approvalId } }, context);
      assert.equal(wrongTool.block, true);
      const originalNow = Date.now;
      try {
        Date.now = () => originalNow() + 600001;
        const expired = await runner.runBeforeToolCall({ toolName: "administrateme_cos_response", toolCallId: "native-email-expired", params }, context);
        assert.equal(expired.block, true);
      } finally { Date.now = originalNow; }
      await deliver("native-email-unrelated", "Hello");
      assert.equal((await runner.runBeforeToolCall({ toolName: "administrateme_cos_response", toolCallId: "native-email-other-source", params }, context)).block, true);
      await deliver(sourceId, text);
      registration.on("before_tool_call", () => ({ block: true, blockReason: "Synthetic later email host veto" }), { priority: -1 });
      const veto = await runner.runBeforeToolCall({ toolName: "administrateme_cos_response", toolCallId: "native-email-veto", params }, context);
      assert.equal(veto.block, true);
      assert.equal(veto.blockReason, "Synthetic later email host veto");
      assert.equal(veto.requireApproval, undefined);
      assert.equal(executeCount(), 1, "Blocked policy case reached the execution tool");
    }
  } finally { delete process.env[secretName]; }
  console.log("Verified three-origin requested-email binding, independent native policy, expired/foreign rule refusal and later native veto; domain/provider receipts are synthetic, with no provider effects.");
}
