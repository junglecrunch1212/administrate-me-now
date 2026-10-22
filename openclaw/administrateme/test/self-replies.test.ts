import { createHash } from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { readPluginConfig } from "../src/config.js";
import { OPENCLAW_COMMIT, OPENCLAW_VERSION } from "../src/contract.js";
import { registerHooks } from "../src/hooks.js";
import { replyParts, escapeReplyText } from "../src/self-replies.js";
import type { PluginRuntime } from "../src/runtime.js";

const agent = "adminme-0123456789abcdef";
const account = "fixture-self-reply";
const actor = "+12025550101"; // fixture:tenant_data:ok
const session = `agent:${agent}:imessage:${account}:direct:${actor}`;
const binding = createHash("sha256").update(JSON.stringify(["cos-self-reply-v1", agent, "imessage", account, actor])).digest("hex");
const context = { agentId: agent, sessionKey: session, channelId: "imessage", accountId: account, senderId: actor };
const answer = (text: string, messageId = "run-a") => [{ role: "user", content: "Question", __openclaw: { transport: { channel: "imessage", messageId } } }, { role: "assistant", stopReason: "stop", phase: "final", content: [{ type: "text", text }] }];

function fixture(bindings = [binding]) {
  const hooks = new Map<string, (event: any, context: any) => any>();
  const send = vi.fn(async (_context: Record<string, unknown>) => ({}));
  const logger = { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() };
  const api = { config: {}, on: (name: string, fn: any) => hooks.set(name, fn), logger,
    runtime: { channel: { outbound: { loadAdapter: async () => ({ sendText: send }) } } } } as unknown as OpenClawPluginApi;
  const request = vi.fn(async (path: string, body: Record<string, any>) => {
    if (path.endsWith("/forward")) return { intent_event_id: `receipt-${body.provenance.message_id}` };
    if (path.endsWith("/self-reply")) return { transport_dispatch: { channel: "imessage", account_id: account,
      recipient: actor, body: body.body, approval_event_id: "approval-fixture" } };
    if (path.endsWith("/result")) return { status: "sent" };
    throw new Error("unexpected endpoint");
  });
  const runtime = { transport: { config: readPluginConfig({ memberAgentIds: [agent], standingSelfReplyBindings: bindings,
    forwardIntakeAccounts: [{ channelId: "imessage", accountId: account }] }), request }, trackedSessions: new Map() } as unknown as PluginRuntime;
  registerHooks(api, runtime);
  const inbound = (runId: string) => hooks.get("message_received")!({ from: actor, senderId: actor, content: "Question", messageId: runId, runId }, { ...context, runId });
  const end = (runId: string, text = "Hello.", extra = {}) => hooks.get("agent_end")!({ runId, success: true, messages: answer(text, runId), ...extra }, { ...context, runId });
  return { hooks, request, send, inbound, end, logger };
}

describe("standing private self replies through production hooks", () => {
  it("sends normal final output once without a tool call or an approval prompt", async () => {
    const f = fixture();
    await f.inbound("run-a");
    await f.end("run-a", "Here is **literal text** at https://example.invalid/a?b=1.");
    expect(f.send).toHaveBeenCalledOnce();
    expect(f.send.mock.calls[0]?.[0]).toMatchObject({ to: actor, accountId: account });
    const request = f.request.mock.calls.find(([path]) => path.endsWith("/self-reply"))![1];
    expect(request).toMatchObject({ source_message_event_id: "receipt-run-a", host_binding: binding, reply_index: 0,
      provenance: { actor_id: actor, agent_id: agent, session_key: session, message_id: "run-a", call_kind: "bridge", openclaw_version: OPENCLAW_VERSION, openclaw_commit: OPENCLAW_COMMIT } });
    expect(f.hooks.get("message_sending")!({}, context)).toMatchObject({ cancel: true });
    await f.end("run-a");
    expect(f.send).toHaveBeenCalledOnce();
  });

  it("keeps concurrent turns and the pending durable receipt bound to their own run", async () => {
    const f = fixture();
    let accept!: (value: any) => void;
    f.request.mockImplementationOnce(() => new Promise((resolve) => { accept = resolve; }));
    const incoming = f.inbound("slow");
    await f.inbound("fast");
    const slowEnd = f.end("slow", "First answer");
    await f.end("fast", "Second answer");
    expect(f.send).toHaveBeenCalledOnce();
    accept({ intent_event_id: "receipt-slow" });
    await Promise.all([incoming, slowEnd]);
    const replies = f.request.mock.calls.filter(([path]) => path.endsWith("/self-reply")).map(([, body]) => [body.body, body.source_message_event_id]);
    expect(replies).toEqual([["Second answer", "receipt-fast"], ["First answer", "receipt-slow"]]);
  });

  it("uses the pinned host native transcript identity when inbound has no run ID", async () => {
    const f = fixture();
    await f.hooks.get("message_received")!({ from: actor, senderId: actor, content: "Question", messageId: "native-only" }, context);
    await f.end("generated-agent-run", "Answer", { messages: answer("Answer", "native-only") });
    expect(f.send).toHaveBeenCalledOnce();
    expect(f.request.mock.calls.find(([path]) => path.endsWith("/self-reply"))![1].source_message_event_id).toBe("receipt-native-only");
  });

  it("answers a host-collected followup once and consumes its matching inputs", async () => {
    const f = fixture();
    for (const id of ["first", "second"]) {
      await f.hooks.get("message_received")!({ from: actor, senderId: actor, content: `Question ${id}`, messageId: id }, context);
    }
    const messages = [{ role: "user", idempotencyKey: "followup-collect:fixture:digest", content: "Question first\nQuestion second" },
      { role: "assistant", content: "Combined answer", __openclaw: { mirrorIdentity: "turn:assistant" } }];
    await f.end("collected", "", { messages });
    await f.end("collected", "", { messages });
    expect(f.send).toHaveBeenCalledOnce();
    expect(f.request.mock.calls.find(([path]) => path.endsWith("/self-reply"))![1].source_message_event_id).toBe("receipt-second");
  });

  it.each(["no-grant", "wrong-run", "wrong-session", "wrong-agent", "failure", "no-final"])("does not send for %s", async (mode) => {
    const f = fixture(mode === "no-grant" ? [] : [binding]);
    await f.inbound("run-a");
    if (mode === "wrong-agent" || mode === "wrong-session") {
      await f.hooks.get("agent_end")!({ runId: "run-a", success: true, messages: answer("No") }, { ...context, [mode === "wrong-agent" ? "agentId" : "sessionKey"]: "other" });
    } else await f.end(mode === "wrong-run" ? "run-b" : "run-a", mode === "no-final" ? "NO_REPLY" : "Hello", mode === "failure" ? { success: false } : {});
    expect(f.send).not.toHaveBeenCalled();
  });

  it.each(["unknown", "denied", "suppressed"])("stops long answers at a %s terminal result", async (status) => {
    const f = fixture();
    const original = f.request.getMockImplementation()!;
    f.request.mockImplementation(async (path, body) => path.endsWith("/result") ? { status } as any : original(path, body));
    await f.inbound("run-long");
    await f.end("run-long", "long answer ".repeat(800));
    expect(f.send).toHaveBeenCalledOnce();
    await f.end("run-long", "long answer ".repeat(800));
    expect(f.send).toHaveBeenCalledOnce();
  });

  it("preserves Unicode and all content across bounded long answer parts", async () => {
    const text = "Some **formatting** with 😀 and a URL: https://example.invalid/a?b=2. ".repeat(100).trim();
    const parts = replyParts(answer(text));
    expect(parts.length).toBeGreaterThan(1);
    expect(parts.every((part) => part.length <= 3500 && Buffer.from(part).toString() === part)).toBe(true);
    expect(parts.join(" ")).toBe(text);
    const f = fixture(); await f.inbound("long"); await f.end("long", text);
    expect(f.send).toHaveBeenCalledTimes(parts.length);
    expect(f.request.mock.calls.filter(([path]) => path.endsWith("/self-reply")).map(([, body]) => body.reply_index)).toEqual(parts.map((_, i) => i));
  });

  it("excludes historical, tool, reasoning and unfinished output", () => {
    const messages = [...answer("old answer"), { role: "user", content: "New question" },
      { role: "assistant", phase: "analysis", content: "private reasoning" },
      { role: "assistant", stopReason: "stop", content: "Codex reasoning: private", __openclaw: { mirrorIdentity: "turn:reasoning" } },
      { role: "assistant", stopReason: "stop", content: "Codex plan: private", __openclaw: { mirrorIdentity: "turn:plan" } },
      { role: "toolResult", content: "untrusted" },
      { role: "assistant", stopReason: "toolUse", content: [{ type: "text", text: "progress" }] }];
    expect(replyParts(messages)).toEqual([]);
    expect(replyParts(answer("[[reply_to_current]] Hello\nworld"))).toEqual(["Hello world"]);
    expect(escapeReplyText("a*b[1]" )).toBe("a\\*b\\[1\\]");
  });
});
