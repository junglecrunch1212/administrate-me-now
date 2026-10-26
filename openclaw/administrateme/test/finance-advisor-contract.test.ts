/** Isolated unreleased-advisor contracts. NOT owner-release acceptance evidence. */
import { createHash, createHmac } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AnyAgentTool } from "openclaw/plugin-sdk/plugin-entry";
vi.mock("../src/finance-release.js", async (importOriginal) => ({
  ...await importOriginal<typeof import("../src/finance-release.js")>(),
  FINANCE_ADVISOR_AVAILABLE: true,
}));
import { fakeApi } from "./fixtures/plugin-api.js";
function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {status: 200, headers: {"Content-Type": "application/json"}});
}
function requestBody(fetchMock: ReturnType<typeof vi.fn>, index = 0): Record<string, unknown> {
  const init = fetchMock.mock.calls[index]?.[1] as RequestInit;
  return JSON.parse(String(init.body)) as Record<string, unknown>;
}
describe("unreleased Finance advisor binding contracts", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    process.env.ADMINME_OPENCLAW_SHARED_SECRET = "fixture-secret-at-least-24-characters";
    fetchMock = vi.fn(async () => jsonResponse({ok: true}));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    delete process.env.ADMINME_OPENCLAW_SHARED_SECRET;
    vi.restoreAllMocks(); vi.unstubAllGlobals();
  });
  it("binds finance calls to the host run and rejects missing or reused bindings", async () => {
    const agent = "adminme-0123456789abcdef";
    const peer = "0123456789abcdefabcd";
    const signature = createHmac("sha256", process.env.ADMINME_OPENCLAW_SHARED_SECRET!).update(`portal-session|${peer}`).digest("hex").slice(0, 32);
    const runtime = fakeApi({ memberAgentIds: [agent] });
    const hook = runtime.hooks.get("before_tool_call")![0]!.handler;
    const context = { agentId: agent, sessionKey: `agent:${agent}:administrateme-portal-${peer}-${signature}`, sessionId: "private-finance-session", messageChannel: "gateway" };
    for (const handler of runtime.hooks.get("before_agent_reply")!) {
      await handler.handler({ cleanedBody: "Review my finance question." }, { ...context, runId: "host-run" });
    }
    expect(runtime.hooks.get("before_agent_reply")![0]!.options).toMatchObject({ eligibleTriggers: ["user"] });
    const names = ["status", "query", "calculate", "job", "bindings"];
    for (const suffix of names) {
      const name = `administrateme_finance_${suffix}`;
      const tool = runtime.tools.get(name)!(context) as AnyAgentTool;
      await expect(tool.execute(`missing-${suffix}`, {})).rejects.toThrow("host-bound");
      const callId = `finance-${suffix}`;
      expect(await hook({ toolName: name, params: {}, toolCallId: callId }, {
        runId: "host-run", sessionKey: context.sessionKey, agentId: context.agentId, toolCallId: callId,
      })).toBeUndefined();
      await tool.execute(callId, {});
      expect(requestBody(fetchMock, names.indexOf(suffix)).provenance).toMatchObject({
        message_id: "finance-run:host-run", actor_id: `portal:${peer}`, session_key: context.sessionKey,
      });
      await expect(tool.execute(callId, {})).rejects.toThrow("host-bound");
    }
    expect(await hook({ toolName: "administrateme_finance_status", params: {} }, {
      sessionKey: context.sessionKey, agentId: context.agentId,
    })).toMatchObject({ block: true });
    expect(await hook({ toolName: "administrateme_finance_status", params: {}, toolCallId: "background" }, {
      ...context, runId: "unadmitted-background", toolCallId: "background",
    })).toMatchObject({ block: true });
    for (const sessionKey of ["main", "shared", `agent:${agent}:imessage:fixture:group:chat`, `${context.sessionKey}:thread:group`, `adminme:tenant:actor:dm`]) {
      for (const handler of runtime.hooks.get("before_agent_reply")!) await handler.handler({ cleanedBody: "Review" }, { ...context, sessionKey, runId: "unsafe-run" });
      expect(await hook({ toolName: "administrateme_finance_status", params: {}, toolCallId: "unsafe" }, {
        ...context, sessionKey, runId: "unsafe-run", toolCallId: "unsafe",
      })).toMatchObject({ block: true });
    }
    const count = fetchMock.mock.calls.length;
    for (const deliveryContext of [{ channel: "discord", accountId: "group", to: "channel" }, { channel: "administrateme-portal", accountId: "member-portal", threadId: "group" }]) {
      await hook({ toolName: "administrateme_finance_status", params: {}, toolCallId: "contradiction" }, { ...context, runId: "host-run", toolCallId: "contradiction" });
      const tool = runtime.tools.get("administrateme_finance_status")!({ ...context, deliveryContext }) as AnyAgentTool;
      await expect(tool.execute("contradiction", {})).rejects.toThrow(/private/);
    }
    expect(fetchMock.mock.calls.length).toBe(count);
  });

  it("binds metadata to a private host turn without enabling financial answer state", async () => {
    const agent = "adminme-0123456789abcdef";
    const peer = "0123456789abcdefabcd";
    const signature = createHmac("sha256", process.env.ADMINME_OPENCLAW_SHARED_SECRET!).update(`portal-session|${peer}`).digest("hex").slice(0, 32);
    const runtime = fakeApi({ memberAgentIds: [agent] });
    const context = { agentId: agent, sessionKey: `agent:${agent}:administrateme-portal-${peer}-${signature}`, messageChannel: "gateway", runId: "metadata-run", toolCallId: "metadata-call" };
    for (const handler of runtime.hooks.get("before_agent_reply")!) await handler.handler({ cleanedBody: "Inspect my bindings." }, context);
    expect(await runtime.hooks.get("before_tool_call")![0]!.handler({ toolName: "administrateme_finance_bindings", params: {}, toolCallId: "metadata-call" }, context)).toBeUndefined();
    const tool = runtime.tools.get("administrateme_finance_bindings")!(context) as AnyAgentTool;
    await tool.execute("metadata-call", { book_id: "known-book" });
    expect(String(fetchMock.mock.calls[0]![0])).toContain("/api/openclaw/v1/tools/finance-bindings");
    expect(requestBody(fetchMock)).toMatchObject({ book_id: "known-book", provenance: { message_id: "finance-run:metadata-run" } });
    const write = runtime.hooks.get("before_message_write")![0]!.handler;
    expect(await write({ sessionKey: context.sessionKey, message: { role: "assistant", content: [{ type: "text", text: "The retained history is empty." }] } }, context)).toBeUndefined();
    const persist = runtime.hooks.get("tool_result_persist")![0]!.handler;
    expect(await persist({ toolName: "administrateme_finance_bindings", message: { role: "toolResult", content: [{ type: "text", text: "verified_empty" }] } }, context)).toBeUndefined();
  });

  it("admits only the exactly configured private iMessage finance audience", async () => {
    const agent = "adminme-0123456789abcdef";
    const account = "fixture-finance";
    const actor = "+12025550101"; // fixture:tenant_data:ok
    const binding = createHash("sha256").update(JSON.stringify(["cos-self-reply-v1", agent, "imessage", account, actor])).digest("hex");
    const runtime = fakeApi({ memberAgentIds: [agent], standingSelfReplyBindings: [binding] });
    const context = { agentId: agent, sessionKey: `agent:${agent}:imessage:${account}:direct:${actor}`, requesterSenderId: actor, agentAccountId: account, messageChannel: "imessage", deliveryContext: { channel: "imessage", accountId: account, to: actor } };
    for (const handler of runtime.hooks.get("before_agent_reply")!) await handler.handler({ cleanedBody: "Review my budget" }, { ...context, runId: "private-run" });
    const hook = runtime.hooks.get("before_tool_call")![0]!.handler;
    expect(await hook({ toolName: "administrateme_finance_status", params: {}, toolCallId: "private-call" }, { ...context, runId: "private-run", toolCallId: "private-call" })).toBeUndefined();
    const tool = runtime.tools.get("administrateme_finance_status")!(context) as AnyAgentTool;
    await tool.execute("private-call", {});
    expect(requestBody(fetchMock).provenance).toMatchObject({ actor_id: actor, account_id: account, channel_id: "imessage", message_id: "finance-run:private-run" });
  });

});
