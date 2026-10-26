/** Explicit unavailable-capability fixture; not shipping advisor evidence. */
import { createHmac } from "node:crypto";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("../src/finance-release.js", async (importOriginal) => ({
  ...await importOriginal<typeof import("../src/finance-release.js")>(),
  FINANCE_ADVISOR_AVAILABLE: false,
}));
import { fakeApi } from "./fixtures/plugin-api.js";
import { FINANCE_ADVISOR_AVAILABLE, FINANCE_ADVISOR_TOOL_NAMES } from "../src/finance-release.js";

afterEach(() => { delete process.env.ADMINME_OPENCLAW_SHARED_SECRET; vi.restoreAllMocks(); });
it("omits all five Finance tools and refuses stale calls without tainting ordinary persistence", async () => {
  expect(FINANCE_ADVISOR_AVAILABLE).toBe(false);
  process.env.ADMINME_OPENCLAW_SHARED_SECRET = "fixture-secret-at-least-24-characters";
  const agent = "adminme-0123456789abcdef", peer = "0123456789abcdefabcd";
  const sig = createHmac("sha256", process.env.ADMINME_OPENCLAW_SHARED_SECRET!).update(`portal-session|${peer}`).digest("hex").slice(0,32);
  const context = {agentId:agent, sessionKey:`agent:${agent}:administrateme-portal-${peer}-${sig}`, runId:"ordinary-run"};
  const api = fakeApi({memberAgentIds:[agent]});
  expect(api.tools.size).toBe(40);
  for (const name of FINANCE_ADVISOR_TOOL_NAMES) expect(api.tools.has(name)).toBe(false);
  for (const h of api.hooks.get("before_agent_reply")!) await h.handler({cleanedBody:"An ordinary private question"}, context);
  for (const name of FINANCE_ADVISOR_TOOL_NAMES) {
    expect(await api.hooks.get("before_tool_call")![0]!.handler({toolName:name,params:{},toolCallId:name}, context)).toMatchObject({block:true});
  }
  const ordinary = {message:{role:"assistant",content:[{type:"text",text:"An ordinary private answer"}]}};
  for (const h of api.hooks.get("before_message_write")!) expect(h.handler(ordinary,context)).toBeUndefined();
  // Disabling new access preserves previously delivered authorized discussion.
  const retained = api.hooks.get("tool_result_persist")![0]!.handler({toolName:"administrateme_finance_query",message:{role:"toolResult",content:[{type:"text",text:'{"value_minor":765432}'}]}},context);
  expect(JSON.stringify(retained)).toContain("765432");
});
