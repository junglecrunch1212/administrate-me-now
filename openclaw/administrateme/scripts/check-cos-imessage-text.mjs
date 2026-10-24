/**
 * Hosted source-install gate: exercise the exact pinned OpenClaw normalizer.
 * Uses only synthetic text and pure formatting functions. No provider client,
 * account configuration, Messages database or channel send is constructed.
 */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const sourceRoot = path.resolve(process.argv[2]);
const versions = JSON.parse(await readFile(path.join(repository, "bootstrap/versions.json"), "utf8"));
const corpus = JSON.parse(await readFile(path.join(repository, "tests/fixtures/cos_imessage_text.json"), "utf8"));
assert.equal(path.basename(sourceRoot), versions.openclaw.commit);
assert.equal(corpus.openclaw_commit, versions.openclaw.commit);
const pkg = JSON.parse(await readFile(path.join(sourceRoot, "package.json"), "utf8"));
assert.equal(pkg.version, versions.openclaw.version);
const requirePinned = createRequire(path.join(sourceRoot, "package.json"));
const sdkUrl = pathToFileURL(requirePinned.resolve("openclaw/plugin-sdk/text-chunking")).href;
const sdk = await import(sdkUrl);
const sender = await readFile(path.join(sourceRoot, "extensions/imessage/src/send.ts"), "utf8");
for (const step of [
  "message = convertMarkdownTables(message, tableMode)",
  "message = stripInlineDirectiveTagsForDelivery(message).text",
  "extractMarkdownFormatRuns(message)",
]) assert.ok(sender.includes(step), `Pinned sender contract changed: ${step}`);
const source = await readFile(path.join(sourceRoot, "extensions/imessage/src/markdown-format.ts"), "utf8");
assert.ok(source.includes('from "openclaw/plugin-sdk/text-chunking"'));
const compiled = ts.transpileModule(
  source.replace('from "openclaw/plugin-sdk/text-chunking"', `from ${JSON.stringify(sdkUrl)}`),
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ESNext } },
).outputText;
const directory = await mkdtemp(path.join(tmpdir(), "adminme-imessage-format-"));
try {
  const modulePath = path.join(directory, "pinned-format.mjs");
  await writeFile(modulePath, compiled, { mode: 0o600 });
  const { extractMarkdownFormatRuns } = await import(pathToFileURL(modulePath).href);
  const cases = [
    ...corpus.cases,
    { text: "a".repeat(8000), allowed: true },
    { text: "😀".repeat(4000), allowed: true },
  ];
  for (const row of cases) {
    const tables = sdk.convertMarkdownTables(row.text, "bullets");
    const directives = sdk.stripInlineDirectiveTagsForDelivery(tables).text;
    const actual = extractMarkdownFormatRuns(directives);
    if (row.allowed) {
      assert.equal(actual.text, row.text, "Admitted text changed in pinned sender");
      assert.deepEqual(actual.ranges, [], "Admitted text acquired native formatting");
    } else if (row.transformed) {
      assert.ok(actual.text !== row.text || actual.ranges.length > 0,
        "Expected pinned transformation is missing from regression corpus");
    }
  }
  // Exercise the production standing-reply escape function against the actual
  // pinned sender pipeline, including punctuation that exact manual replies reject.
  // Compile the real plugin dependency graph: current-input policy is shared
  // by native replies and the new genuine portal/Gmail adapters.
  const compiledPlugin = path.join(directory, "compiled-plugin");
  await mkdir(path.join(compiledPlugin, "node_modules"), { recursive: true });
  await writeFile(path.join(compiledPlugin, "package.json"), JSON.stringify({ type: "module" }));
  await symlink(sourceRoot, path.join(compiledPlugin, "node_modules/openclaw"), "dir");
  await symlink(path.join(repository, "openclaw/administrateme/node_modules/typebox"), path.join(compiledPlugin, "node_modules/typebox"), "dir");
  const sourceModules = path.join(repository, "openclaw/administrateme/src");
  for (const name of (await readdir(sourceModules)).filter((entry) => entry.endsWith(".ts"))) {
    await writeFile(path.join(compiledPlugin, name.replace(/\.ts$/, ".js")), ts.transpileModule(await readFile(path.join(sourceModules, name), "utf8"), {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ESNext },
    }).outputText);
  }
  const selfReplyModule = path.join(compiledPlugin, "self-replies.js");
  const { escapeReplyText, replyParts, rememberSelfReply, registerSelfReplies, bindReminderTool, takeReminderInput } = await import(pathToFileURL(selfReplyModule).href);
  // Exercise the actual pinned inbound mapper, whose optional runId is absent.
  const hookSdk = await import(pathToFileURL(requirePinned.resolve("openclaw/plugin-sdk/hook-runtime")).href);
  const canonical = hookSdk.deriveInboundMessageHookContext({
    From: "+12025550101", SenderId: "+12025550101", Body: "Question", // fixture:tenant_data:ok
    Provider: "imessage", AccountId: "fixture", MessageSid: "native-fixture",
    SessionKey: "agent:adminme-0123456789abcdef:imessage:fixture:direct:+12025550101", // fixture:tenant_data:ok
  });
  const incoming = hookSdk.toPluginMessageReceivedEvent(canonical);
  assert.equal(incoming.runId, undefined);
  assert.equal(incoming.messageId, "native-fixture");
  const crypto = await import("node:crypto");
  const agent = "adminme-0123456789abcdef";
  const binding = crypto.createHash("sha256").update(JSON.stringify(["cos-self-reply-v1", agent, "imessage", "fixture", incoming.senderId])).digest("hex");
  // Use the pinned host's real registrar, not a permissive api.on stub. The
  // source-install test must exercise the same non-bundled plugin capability
  // check that runs during an ordinary Gateway startup.
  const { createJiti } = requirePinned("jiti");
  // Internal host modules use workspace aliases that are deliberately not
  // public package exports. Resolve all of them from this exact source pin's
  // own tsconfig, just as its source build does; do not stub a missing module.
  const jiti = createJiti(path.join(sourceRoot, "package.json"), {
    tsconfigPaths: path.join(sourceRoot, "tsconfig.json"),
  });
  const { createPluginRegistryState } = await jiti.import(path.join(sourceRoot, "src/plugins/registry-state.ts"));
  const { createToolHookRegistrars } = await jiti.import(path.join(sourceRoot, "src/plugins/registry-registrars-tools-hooks.ts"));
  const preparedPolicies = JSON.parse(execFileSync("python3", ["-c", `
import json
from bootstrap.cos_reply_hooks import configure_reply_hook
entry = {"hooks": {"allowPromptInjection": False}}
configure_reply_hook(entry, ["a" * 64])
enabled = dict(entry["hooks"])
configure_reply_hook(entry, [])
print(json.dumps([enabled, entry["hooks"]]))
`], { cwd: repository, encoding: "utf8" }));
  function nativeRegistration(policy) {
    const state = createPluginRegistryState({ runtime: {}, logger: {}, activateGlobalSideEffects: false });
    const registrar = createToolHookRegistrars(state);
    const record = { id: "administrateme", origin: "config", enabled: true, explicitlyEnabled: true,
      source: selfReplyModule, hookCount: 0 };
    return { state, on: (name, fn, options) => registrar.registerTypedHook(record, name, fn, options, policy) };
  }
  let sent = 0;
  const runtime = { transport: { config: {memberAgentIds: [agent], standingSelfReplyBindings: [binding]},
    request: async (endpoint, value) => endpoint.endsWith("/self-reply") ? {transport_dispatch: {
      channel: "imessage", account_id: "fixture", recipient: incoming.senderId, body: value.body, approval_event_id: "approval-fixture",
    }} : {status: "sent"} } };
  const admitted = nativeRegistration(preparedPolicies[0]);
  const api = { config: {}, on: admitted.on, logger: {warn() {}, info() {}, error() {}},
    runtime: {channel: {outbound: {loadAdapter: async () => ({sendText: async () => {sent++;}})}}} };
  for (const policy of [undefined, {allowConversationAccess: false}, preparedPolicies[1]]) {
    const denied = nativeRegistration(policy);
    registerSelfReplies({...api, on: denied.on}, runtime);
    assert.equal(denied.state.registry.typedHooks.length, 0, "Pinned host accepted a reply hook without conversation access");
    assert.ok(denied.state.registry.diagnostics.some((row) => row.message.includes("allowConversationAccess")));
  }
  registerSelfReplies(api, runtime);
  assert.deepEqual(admitted.state.registry.typedHooks.map((row) => row.hookName).sort(), ["agent_end", "before_agent_reply"], "Activated reply hooks are blocked by the pinned host");
  const handler = admitted.state.registry.typedHooks.find((row) => row.hookName === "agent_end").handler;
  rememberSelfReply(runtime, {actor_id: incoming.senderId, account_id: "fixture", channel_id: "imessage",
    message_id: incoming.messageId, session_key: incoming.sessionKey}, incoming.runId, Promise.resolve("receipt-fixture"), incoming.content);
  const requestHook = admitted.state.registry.typedHooks.find((row) => row.hookName === "before_agent_reply").handler;
  const reminderContext = {runId: "reminder-run", agentId: agent, sessionKey: incoming.sessionKey};
  const { buildReplyPromptEnvelope } = await jiti.import(path.join(sourceRoot, "src/auto-reply/reply/prompt-prelude.ts"));
  const envelope = buildReplyPromptEnvelope({ ctx: {}, sessionCtx: {}, baseBody: incoming.content,
    hasUserBody: true, inboundUserContext: "", isBareSessionReset: false, startupAction: "new",
    systemEventBlocks: ["Synthetic host status context"] });
  for (const cleanedBody of [incoming.content, envelope.queuedBody]) {
    await requestHook({cleanedBody}, reminderContext);
    assert.equal(bindReminderTool(runtime, reminderContext, "reminder-call"), true);
    assert.equal((await takeReminderInput(runtime, "reminder-call", reminderContext)).source_message_event_id, "receipt-fixture");
  }
  await requestHook({cleanedBody: `${incoming.content}\n\nA different current request.`}, reminderContext);
  assert.equal(bindReminderTool(runtime, reminderContext, "reminder-call"), false);
  // This protected transport metadata is preserved by the pinned Codex user
  // snapshot. Its reasoning mirrors are assistant rows without a phase tag.
  await handler({success: true, runId: "agent-generated", messages: [
    {role: "user", content: incoming.content, __openclaw: {transport: {channel: "imessage", messageId: incoming.messageId}}},
    {role: "assistant", stopReason: "stop", content: "private reasoning", __openclaw: {mirrorIdentity: "turn:reasoning"}},
    {role: "assistant", stopReason: "stop", content: "Hello", __openclaw: {mirrorIdentity: "turn:assistant"}},
  ]}, {agentId: agent, sessionKey: incoming.sessionKey});
  assert.equal(sent, 1, "Pinned inbound without a run ID did not produce its governed self reply");
  // Exercise the changed Apple create path through the pinned native inbound
  // mapper and actual conversation-hook registrar. Domain responses below are
  // synthetic; the Python acceptance composition proves the provider receipt.
  // Keep this separate from the existing scheduled-text timer/callback probe.
  const appleDirectory = path.join(directory, "apple-reminder-plugin");
  await mkdir(path.join(appleDirectory, "node_modules"), { recursive: true });
  await writeFile(path.join(appleDirectory, "package.json"), JSON.stringify({ type: "module" }));
  await symlink(sourceRoot, path.join(appleDirectory, "node_modules/openclaw"), "dir");
  await symlink(path.join(repository, "openclaw/administrateme/node_modules/typebox"), path.join(appleDirectory, "node_modules/typebox"), "dir");
  const pluginSource = path.join(repository, "openclaw/administrateme/src");
  for (const name of (await readdir(pluginSource)).filter((entry) => entry.endsWith(".ts"))) {
    await writeFile(path.join(appleDirectory, name.replace(/\.ts$/, ".js")), ts.transpileModule(await readFile(path.join(pluginSource, name), "utf8"), {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ESNext },
    }).outputText);
  }
  const { registerHooks } = await import(pathToFileURL(path.join(appleDirectory, "hooks.js")).href);
  const { registerAppleReminderTools } = await import(pathToFileURL(path.join(appleDirectory, "apple-reminders.js")).href);
  const { readPluginConfig } = await import(pathToFileURL(path.join(appleDirectory, "config.js")).href);
  const { registerTools } = await import(pathToFileURL(path.join(appleDirectory, "tools.js")).href);
  const { canonical: approvalCanonical } = await import(pathToFileURL(path.join(appleDirectory, "approval.js")).href);
  const { createHookRunner } = await jiti.import(path.join(sourceRoot, "src/plugins/hooks.ts"));
  const appleMessage = "Add a reminder to my Apple Reminders to call my mom at 9:30 AM today";
  const appleInbound = hookSdk.toPluginMessageReceivedEvent(hookSdk.deriveInboundMessageHookContext({
    From: incoming.senderId, SenderId: incoming.senderId, Body: appleMessage,
    Provider: "imessage", AccountId: "fixture", MessageSid: "native-apple-fixture", SessionKey: incoming.sessionKey,
  }));
  const appleCommand = { title: "Call my mom", due_at: "2026-10-24T09:30:00-04:00", list_id: "fixture-list", request_id: "native-apple-request" };
  const appleCalls = [];
  let appleState = "pending";
  const appleRuntime = { trackedSessions: new Map(), transport: {
    config: readPluginConfig({ memberAgentIds: [agent], standingSelfReplyBindings: [], scheduledSelfReminderBindings: [],
      forwardIntakeAccounts: [{ channelId: "imessage", accountId: "fixture" }] }),
    async request(endpoint, value) {
      appleCalls.push({ endpoint, value });
      assert.equal(value.provenance.actor_id, incoming.senderId);
      assert.equal(value.provenance.agent_id, agent);
      assert.equal(value.provenance.message_id, "native-apple-fixture");
      assert.equal(value.provenance.session_key, incoming.sessionKey);
      if (endpoint.endsWith("/forward")) return { intent_event_id: "native-apple-source" };
      assert.equal(value.source_message_event_id, "native-apple-source");
      assert.equal(value.openclaw_approval_receipt_id, undefined);
      if (endpoint.endsWith("/confirm-apple-reminder")) {
        for (const [key, expected] of Object.entries(appleCommand)) assert.equal(value[key], expected);
        return { state: "awaiting_openclaw_approval", domain_approval_event_id: "native-apple-domain",
          due_at: "2026-10-24T13:30:00+00:00", list_id: "fixture-list", provider_effect_count: 0 };
      }
      assert.equal(endpoint, "/api/openclaw/v1/tools/apple-reminder-status");
      assert.equal(value.domain_approval_event_id, "native-apple-domain");
      return { state: appleState, domain_approval_event_id: "native-apple-domain",
        due_at: "2026-10-24T13:30:00+00:00", list_id: "fixture-list", provider_effect_count: appleState === "succeeded" ? 1 : null };
    },
  } };
  const appleRegistration = nativeRegistration(preparedPolicies[0]);
  const appleTools = new Map();
  const appleApi = { ...api, on: appleRegistration.on,
    registerTool: (factory, options) => appleTools.set(options.name, factory) };
  registerHooks(appleApi, appleRuntime);
  registerAppleReminderTools(appleApi, appleRuntime);
  const nativeHook = (name) => appleRegistration.state.registry.typedHooks.find((row) => row.hookName === name).handler;
  const appleContext = { runId: "native-apple-run", agentId: agent, sessionKey: incoming.sessionKey };
  await nativeHook("message_received")(appleInbound, { agentId: agent, sessionKey: incoming.sessionKey, channelId: "imessage", accountId: "fixture" });
  const appleEnvelope = buildReplyPromptEnvelope({ ctx: {}, sessionCtx: {}, baseBody: appleMessage,
    hasUserBody: true, inboundUserContext: "", isBareSessionReset: false, startupAction: "new", systemEventBlocks: ["Synthetic host status context"] });
  await nativeHook("before_agent_reply")({ cleanedBody: appleEnvelope.queuedBody }, appleContext);
  assert.equal(await nativeHook("before_tool_call")({ toolName: "administrateme_confirm_apple_reminder", toolCallId: "native-apple-confirm", params: appleCommand }, appleContext), undefined);
  const confirmation = await appleTools.get("administrateme_confirm_apple_reminder")(appleContext).execute("native-apple-confirm", appleCommand);
  assert.equal(confirmation.details.state, "awaiting_openclaw_approval");
  assert.equal(confirmation.details.due_at, "2026-10-24T13:30:00+00:00");
  const approval = await nativeHook("before_tool_call")({ toolName: "administrateme_execute_pending_provider_action", toolCallId: "native-apple-execute",
    params: { domain_approval_event_id: "native-apple-domain" } }, appleContext);
  assert.deepEqual(approval.requireApproval.allowedDecisions, ["allow-once", "deny"]);
  await approval.requireApproval.onResolution("timeout");
  assert.equal(appleCalls.length, 2, "Approval timeout attempted provider execution");
  for (const state of ["pending", "unknown", "succeeded"]) {
    appleState = state;
    const callId = `native-apple-status-${state}`;
    assert.equal(await nativeHook("before_tool_call")({ toolName: "administrateme_apple_reminder_status", toolCallId: callId,
      params: { domain_approval_event_id: "native-apple-domain" } }, appleContext), undefined);
    const receipt = await appleTools.get("administrateme_apple_reminder_status")(appleContext).execute(callId, { domain_approval_event_id: "native-apple-domain" });
    assert.equal(receipt.details.state, state);
    assert.equal(receipt.details.provider_effect_count, state === "succeeded" ? 1 : null);
  }
  await nativeHook("before_agent_reply")({ cleanedBody: `Retrieved message: ${appleMessage}` }, appleContext);
  assert.equal((await nativeHook("before_tool_call")({ toolName: "administrateme_confirm_apple_reminder", toolCallId: "native-apple-retrieved", params: appleCommand }, appleContext)).block, true);
  assert.equal(appleCalls.filter((entry) => entry.endpoint.endsWith("/confirm-apple-reminder")).length, 1);
  assert.equal(sent, 1, "Apple record workflow borrowed permission to send a message");
  // The owner's new policy is a real host decision, not a forged human
  // allow-once. Exercise the exact pinned native hook runner and its later-veto
  // semantics with synthetic signed domain grounding and no provider client.
  for (const ownCoS of [false, true]) {
  const { registerCoSReminderTools } = await import(pathToFileURL(path.join(compiledPlugin, "cos-reminders.js")).href);
  const policyRule = ownCoS ? "current-human-request.cos-reminder-create.v1" : "current-human-request.apple-reminder-create.v1";
  const confirmTool = ownCoS ? "administrateme_confirm_cos_apple_reminder" : "administrateme_confirm_apple_reminder";
  const confirmEndpoint = ownCoS ? "/cos-apple-reminders/confirm" : "/confirm-apple-reminder";
  const policyText = ownCoS ? "Add a reminder to your own Apple work list to call my parent at 9:30 AM today" : appleMessage;
  const policySecret = crypto.randomBytes(32).toString("hex");
  const policySecretName = "ADMINME_NATIVE_REQUEST_PROBE_SECRET";
  process.env[policySecretName] = policySecret;
  const policyMember = "member-native-request-fixture";
  const policyAgent = `adminme-${crypto.createHash("sha256").update(policyMember).digest("hex").slice(0, 16)}`;
  const policySession = `agent:${policyAgent}:imessage:fixture:direct:${incoming.senderId}`;
  const policyContext = { runId: "native-authenticated-request", agentId: policyAgent, sessionKey: policySession };
  const policyRegistration = nativeRegistration(preparedPolicies[0]);
  const policyTools = new Map();
  const policyCalls = [];
  const action = { provider: "apple_reminders", operation: "create_reminder", resource_owner: ownCoS ? "service-principal:chief-of-staff" : "requester",
    ...(ownCoS ? {operating_principal: "service-principal:chief-of-staff", provider_account_id: "cos-native-fixture", source_id: "fixture-source",
      device_identity_sha256: "a".repeat(64), os_uid: 501, selection_sha256: "b".repeat(64), write_policy_sha256: "c".repeat(64)} : {}),
    title: appleCommand.title, due_at: "2026-10-24T13:30:00+00:00", notes: null, list_id: "fixture-list", selected_by_policy: false };
  const actionFingerprint = crypto.createHash("sha256").update(approvalCanonical(action)).digest("hex");
  const policyRuntime = { trackedSessions: new Map(), transport: {
    config: readPluginConfig({ sharedSecretEnv: policySecretName, memberAgentIds: [policyAgent], standingSelfReplyBindings: [], scheduledSelfReminderBindings: [],
      forwardIntakeAccounts: [{ channelId: "imessage", accountId: "fixture" }] }),
    async request(endpoint, value) {
      policyCalls.push({ endpoint, value });
      if (endpoint.endsWith("/approvals/authenticated-request")) {
        assert.equal(value.decision, "authenticated_request");
        assert.equal(value.authorization.source_event_id, "native-request-source");
        assert.equal(value.authorization.action_fingerprint, actionFingerprint);
        assert.equal(value.authorization.rule_id, policyRule);
        assert.equal(value.signature, crypto.createHmac("sha256", policySecret).update([
          "administrateme-request-approval-v1", value.receipt_id, value.tool_name, value.request_fingerprint,
          String(value.issued_at_ms), String(value.expires_at_ms), "authenticated_request", versions.openclaw.version,
          versions.openclaw.commit, approvalCanonical(value.authorization),
        ].join("|")).digest("hex"));
        return { recorded: true };
      }
      assert.equal(value.provenance.agent_id, policyAgent);
      assert.equal(value.provenance.actor_id, incoming.senderId);
      assert.equal(value.provenance.session_key, policySession);
      if (endpoint.endsWith("/forward")) return { intent_event_id: value.provenance.message_id === "native-request-input" ? "native-request-source" : "native-other-source" };
      if (endpoint.endsWith(confirmEndpoint)) {
        assert.equal(value.source_message_event_id, "native-request-source");
        assert.equal(value.request_authorization, true);
        const issued = Date.now();
        const ticket = { contract: "authenticated-request-v1", member_id: policyMember,
          domain_approval_event_id: "native-request-domain", grounding_event_id: "native-request-grounding",
          source_event_id: "native-request-source", action_fingerprint: actionFingerprint, action,
          rule_id: policyRule, issued_at: new Date(issued).toISOString(),
          expires_at: new Date(issued + 600000).toISOString(), evidence: [{ start: 0, end: Array.from(policyText).length, text: policyText }] };
        return { state: "request_authorized", domain_approval_event_id: ticket.domain_approval_event_id,
          request_authorization: { ...ticket, signature: crypto.createHmac("sha256", policySecret).update("authenticated-request-v1|" + approvalCanonical(ticket)).digest("hex") } };
      }
      assert.equal(endpoint, "/api/openclaw/v1/tools/execute-pending-provider-action");
      assert.equal(value.domain_approval_event_id, "native-request-domain");
      assert.match(value.openclaw_approval_receipt_id, /^ocreq_/);
      assert.equal(value.request_authorization_event_id, "native-request-grounding");
      return { state: "pending", provider_effect_count: 0, fixture_only: true };
    },
  } };
  const policyApi = { ...api, on: policyRegistration.on, registerTool: (factory, options) => policyTools.set(options.name, factory) };
  registerHooks(policyApi, policyRuntime); registerAppleReminderTools(policyApi, policyRuntime); registerCoSReminderTools(policyApi, policyRuntime); registerTools(policyApi, policyRuntime);
  const policyHook = (name) => policyRegistration.state.registry.typedHooks.find((row) => row.hookName === name).handler;
  const runner = createHookRunner(policyRegistration.state.registry, { catchErrors: false });
  const deliverPolicyInput = async (messageId, content) => {
    const received = hookSdk.toPluginMessageReceivedEvent(hookSdk.deriveInboundMessageHookContext({
      From: incoming.senderId, SenderId: incoming.senderId, Body: content, Provider: "imessage", AccountId: "fixture", MessageSid: messageId, SessionKey: policySession,
    }));
    await policyHook("message_received")(received, { agentId: policyAgent, sessionKey: policySession, channelId: "imessage", accountId: "fixture" });
    await policyHook("before_agent_reply")({ cleanedBody: content }, policyContext);
  };
  await deliverPolicyInput("native-request-input", policyText);
  const confirmGate = await runner.runBeforeToolCall({ toolName: confirmTool, toolCallId: "native-request-confirm", params: appleCommand }, policyContext);
  assert.notEqual(confirmGate?.block, true);
  await policyTools.get(confirmTool)(policyContext).execute("native-request-confirm", appleCommand);
  const executeParams = { domain_approval_event_id: "native-request-domain" };
  const execution = await runner.runBeforeToolCall({ toolName: "administrateme_execute_pending_provider_action", toolCallId: "native-request-execute", params: executeParams }, policyContext);
  assert.notEqual(execution.block, true);
  assert.equal(execution.requireApproval, undefined, "Authenticated exact request incorrectly asks another manual approval");
  await policyTools.get("administrateme_execute_pending_provider_action")(policyContext).execute("native-request-execute", execution.params);
  assert.equal(policyCalls.filter((row) => row.endpoint.endsWith("/execute-pending-provider-action")).length, 1);
  const originalNow = Date.now;
  try {
    Date.now = () => originalNow() + 600001;
    const expired = await runner.runBeforeToolCall({ toolName: "administrateme_execute_pending_provider_action", toolCallId: "native-request-expired", params: executeParams }, policyContext);
    assert.equal(expired.block, true);
  } finally { Date.now = originalNow; }
  await deliverPolicyInput("native-other-input", policyText + " for another day");
  const wrongSource = await runner.runBeforeToolCall({ toolName: "administrateme_execute_pending_provider_action", toolCallId: "native-request-wrong-source", params: executeParams }, policyContext);
  assert.equal(wrongSource.block, true);
  await policyHook("before_agent_reply")({ cleanedBody: policyText }, policyContext);
  policyRegistration.on("before_tool_call", () => ({ block: true, blockReason: "Synthetic later host veto" }), { priority: -1 });
  const vetoed = await runner.runBeforeToolCall({ toolName: "administrateme_execute_pending_provider_action", toolCallId: "native-request-vetoed", params: executeParams }, policyContext);
  assert.equal(vetoed.block, true, "Automatic authorization bypassed a later native hook veto");
  assert.equal(vetoed.blockReason, "Synthetic later host veto");
  assert.equal(vetoed.requireApproval, undefined);
  assert.equal(policyCalls.filter((row) => row.endpoint.endsWith("/execute-pending-provider-action")).length, 1);
  delete process.env[policySecretName];

  }

  const literalCases = [...corpus.cases.map((row) => row.text.replace(/\s+/gu, " ").trim()),
    "**bold** _italic_ ~~strike~~ `code` <u>underline</u> [link](https://example.invalid/?x=1&y=2)",
    "[[reply_to_current]] [[audio_as_voice]] MEDIA:https://example.invalid/a.jpg",
    Array.from({length: 94}, (_, i) => String.fromCharCode(i + 33)).join(""),
    ...replyParts([{role: "user", content: "Question"}, {role: "assistant", content: "😀 a_b **x** ".repeat(1000)}]),
  ];
  for (const text of literalCases) {
    const escaped = escapeReplyText(text);
    const tables = sdk.convertMarkdownTables(escaped, "bullets");
    const directives = sdk.stripInlineDirectiveTagsForDelivery(tables).text;
    const actual = extractMarkdownFormatRuns(directives);
    assert.equal(actual.text, text, "Standing reply text changed in pinned sender");
    assert.deepEqual(actual.ranges, [], "Standing reply acquired native formatting");
  }
  console.log(`Verified native reply-hook admission/revocation, Apple Reminder current-input confirmation/status, signed request authorization with expiry/source checks and later native veto, legacy independent execution gate, and ${cases.length} exact and ${literalCases.length} standing reply cases against pinned OpenClaw. Apple domain receipts are synthetic; no provider effects.`);
} finally {
  await rm(directory, { recursive: true, force: true });
}
