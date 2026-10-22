/**
 * Hosted source-install gate: exercise the exact pinned OpenClaw normalizer.
 * Uses only synthetic text and pure formatting functions. No provider client,
 * account configuration, Messages database or channel send is constructed.
 */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
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
  const selfReplySource = await readFile(path.join(repository, "openclaw/administrateme/src/self-replies.ts"), "utf8");
  const selfReplyModule = path.join(directory, "self-replies.mjs");
  await writeFile(selfReplyModule, ts.transpileModule(selfReplySource, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ESNext },
  }).outputText);
  const { escapeReplyText, replyParts, rememberSelfReply, registerSelfReplies } = await import(pathToFileURL(selfReplyModule).href);
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
  assert.equal(admitted.state.registry.typedHooks.length, 1, "Activated reply hook is blocked by the pinned host");
  assert.equal(admitted.state.registry.typedHooks[0].hookName, "agent_end");
  const handler = admitted.state.registry.typedHooks[0].handler;
  rememberSelfReply(runtime, {actor_id: incoming.senderId, account_id: "fixture", channel_id: "imessage",
    message_id: incoming.messageId, session_key: incoming.sessionKey}, incoming.runId, Promise.resolve("receipt-fixture"), incoming.content);
  // This protected transport metadata is preserved by the pinned Codex user
  // snapshot. Its reasoning mirrors are assistant rows without a phase tag.
  await handler({success: true, runId: "agent-generated", messages: [
    {role: "user", content: incoming.content, __openclaw: {transport: {channel: "imessage", messageId: incoming.messageId}}},
    {role: "assistant", stopReason: "stop", content: "private reasoning", __openclaw: {mirrorIdentity: "turn:reasoning"}},
    {role: "assistant", stopReason: "stop", content: "Hello", __openclaw: {mirrorIdentity: "turn:assistant"}},
  ]}, {agentId: agent, sessionKey: incoming.sessionKey});
  assert.equal(sent, 1, "Pinned inbound without a run ID did not produce its governed self reply");
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
  console.log(`Verified native reply-hook admission/revocation and ${cases.length} exact and ${literalCases.length} standing reply cases against pinned OpenClaw.`);
} finally {
  await rm(directory, { recursive: true, force: true });
}
