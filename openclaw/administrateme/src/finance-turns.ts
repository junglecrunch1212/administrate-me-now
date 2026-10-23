import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import type { OpenClawPluginApi, OpenClawPluginToolContext } from "openclaw/plugin-sdk/plugin-entry";
import type { PluginRuntime } from "./runtime.js";

export const FINANCE_TOOLS = new Set([
  "administrateme_finance_status", "administrateme_finance_query",
  "administrateme_finance_calculate", "administrateme_finance_job",
]);

type HostContext = { runId?: string | undefined; sessionKey?: string | undefined; agentId?: string | undefined };
type Audience = { sessionKey: string; agentId: string; actorId: string; accountId: string; channelId: string };
type Binding = Audience & { runId: string; expiresAt: number };
const runs = new WeakMap<PluginRuntime, Map<string, Binding>>();
const calls = new WeakMap<PluginRuntime, Map<string, Binding>>();
const activeFinance = new WeakMap<PluginRuntime, Map<string, string>>();

function retainedReference(text: unknown): string | undefined {
  if (typeof text !== "string" || text.length > 6000) return;
  try {
    const value = JSON.parse(text);
    const ref = value.answer_ref;
    if (typeof ref === "string" && /^[0-9a-f]{64}$/.test(ref)) return ref;
  } catch { /* Malformed results retain no financial content. */ }
  return;
}

export function privateFinanceTranscript(message: Record<string, unknown>, toolResult: boolean): Record<string, unknown> {
  const content = Array.isArray(message.content) ? message.content : [];
  if (toolResult) {
    const refs = content.map((item: any) => retainedReference(item?.text)).filter((ref): ref is string => ref !== undefined);
    return { role: "toolResult", toolCallId: message.toolCallId, toolName: message.toolName,
      timestamp: message.timestamp, isError: message.isError,
      content: [{ type: "text", text: JSON.stringify({ finance_result_refs: [...new Set(refs)],
        boundary: "Private values are held in the authorized finance service. Re-read current evidence before another answer." }) }] };
  }
  const safeContent = content.map((item: any) => {
    if (item?.type === "toolCall") {
      return { type: "toolCall", id: item.id, name: item.name, arguments: {} };
    }
    if (item?.type === "text") {
      try {
        const value = JSON.parse(item.text);
        const refs = value.finance_answer?.result_refs;
        if (Object.keys(value).length === 1 && Object.keys(value.finance_answer).length === 1 &&
            Array.isArray(refs) && refs.length > 0 && refs.length <= 3 && refs.every((ref: unknown) => typeof ref === "string" && /^[0-9a-f]{64}$/.test(ref))) {
          return { type: "text", text: JSON.stringify({ finance_answer: { result_refs: refs } }) };
        }
      } catch { /* Narrative finance output is unverified and is not retained. */ }
    }
    return { type: "text", text: "[Private finance content omitted; retrieve current authorized evidence.]" };
  });
  return { role: "assistant", api: message.api, provider: message.provider, model: message.model,
    usage: message.usage, stopReason: message.stopReason, timestamp: message.timestamp,
    content: safeContent };
}


function privateAudience(runtime: PluginRuntime, context: HostContext): Audience | undefined {
  const sessionKey = context.sessionKey;
  const agentId = context.agentId;
  if (!sessionKey || !agentId || !runtime.transport.config.memberAgentIds.includes(agentId)) return;
  const portal = /^agent:(adminme-[0-9a-f]{16}):administrateme-portal-([0-9a-f]{20})-([0-9a-f]{32})$/.exec(sessionKey);
  if (portal) {
    if (portal[1] !== agentId) return;
    const secret = process.env[runtime.transport.config.sharedSecretEnv];
    if (!secret || secret.length < 24) return;
    const expected = createHmac("sha256", secret).update(`portal-session|${portal[2]}`).digest("hex").slice(0, 32);
    if (!timingSafeEqual(Buffer.from(portal[3]!, "utf8"), Buffer.from(expected, "utf8"))) return;
    return { sessionKey, agentId, actorId: `portal:${portal[2]}`, accountId: "member-portal", channelId: "administrateme-portal" };
  }
  // The pinned host's exact account-aware iMessage direct format. No other
  // channel/session syntax is inferred from a substring such as ':dm'.
  const direct = /^agent:(adminme-[0-9a-f]{16}):imessage:([A-Za-z0-9_-]{1,80}):direct:(\+[1-9][0-9]{7,14})$/.exec(sessionKey);
  if (!direct || direct[1] !== agentId) return;
  const binding = createHash("sha256").update(JSON.stringify([
    "cos-self-reply-v1", agentId, "imessage", direct[2], direct[3],
  ])).digest("hex");
  if (!runtime.transport.config.standingSelfReplyBindings.includes(binding)) return;
  return { sessionKey, agentId, actorId: direct[3]!, accountId: direct[2]!, channelId: "imessage" };
}

function prune(bindings: Map<string, Binding>): void {
  const now = Date.now();
  for (const [key, value] of bindings) if (value.expiresAt <= now) bindings.delete(key);
}

export function registerFinanceTurns(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  // The host, not tool parameters or source content, attests an interactive
  // user trigger. Cron/heartbeat/background turns never get an admission.
  api.on("before_agent_reply", (_event, context) => {
    if (!context.runId || context.runId.length > 200) return;
    let admitted = runs.get(runtime);
    if (!admitted) { admitted = new Map(); runs.set(runtime, admitted); }
    prune(admitted);
    for (const [session, activeRun] of activeFinance.get(runtime) ?? []) if (!admitted.has(activeRun)) activeFinance.get(runtime)?.delete(session);
    admitted.delete(context.runId);
    const audience = privateAudience(runtime, context);
    if (!audience || admitted.size >= 256) return;
    admitted.set(context.runId, { ...audience, runId: context.runId, expiresAt: Date.now() + 30_000 });
  }, { eligibleTriggers: ["user"] });
  api.on("tool_result_persist", (event, context) => {
    if (FINANCE_TOOLS.has(event.toolName ?? context.toolName ?? String(event.message.toolName))) {
      return { message: privateFinanceTranscript(event.message, true) };
    }
    return;
  });
  api.on("before_message_write", (event, context) => {
    const session = event.sessionKey ?? context.sessionKey;
    const invokesFinance = Array.isArray(event.message.content) && event.message.content.some((part: any) => part?.type === "toolCall" && FINANCE_TOOLS.has(part.name));
    if (event.message.role === "assistant" && (invokesFinance || (session && activeFinance.get(runtime)?.has(session)))) {
      return { message: privateFinanceTranscript(event.message, false) };
    }
    return;
  });
  api.on("agent_end", (event, context) => {
    const runId = event.runId ?? context.runId;
    if (!runId) return;
    runs.get(runtime)?.delete(runId);
    for (const [session, activeRun] of activeFinance.get(runtime) ?? []) if (activeRun === runId) activeFinance.get(runtime)?.delete(session);
    for (const [key, value] of calls.get(runtime) ?? []) if (value.runId === runId) calls.get(runtime)?.delete(key);
  });
}

export function bindFinanceTurn(runtime: PluginRuntime, context: HostContext, callId: string | undefined): boolean {
  if (!callId || callId.length > 300 || !context.runId) return false;
  const admitted = runs.get(runtime)?.get(context.runId);
  const audience = privateAudience(runtime, context);
  if (!admitted || !audience || admitted.expiresAt <= Date.now() || admitted.sessionKey !== audience.sessionKey || admitted.agentId !== audience.agentId) return false;
  let bindings = calls.get(runtime);
  if (!bindings) { bindings = new Map(); calls.set(runtime, bindings); }
  prune(bindings);
  if (bindings.size >= 256) return false;
  bindings.set(callId, admitted);
  let active = activeFinance.get(runtime);
  if (!active) { active = new Map(); activeFinance.set(runtime, active); }
  // At most the already bounded 256 active run bindings can be represented.
  active.set(admitted.sessionKey, admitted.runId);
  return true;
}

export function takeFinanceTurn(runtime: PluginRuntime, callId: string, context: OpenClawPluginToolContext): string {
  const binding = calls.get(runtime)?.get(callId);
  calls.get(runtime)?.delete(callId);
  const audience = privateAudience(runtime, context);
  const delivery = context.deliveryContext;
  if (!binding || !audience || binding.expiresAt <= Date.now() || binding.sessionKey !== audience.sessionKey || binding.agentId !== audience.agentId ||
      !runs.get(runtime)?.has(binding.runId) || delivery?.threadId !== undefined) {
    throw new Error("Finance requires a current host-bound private turn.");
  }
  if (audience.channelId === "administrateme-portal") {
    if ((context.messageChannel !== undefined && !["gateway", "webchat", "administrateme-portal"].includes(context.messageChannel)) ||
        (context.requesterSenderId !== undefined && context.requesterSenderId !== audience.actorId) ||
        (context.agentAccountId !== undefined && context.agentAccountId !== audience.accountId) ||
        (delivery !== undefined && (delivery.channel !== "administrateme-portal" || delivery.accountId !== audience.accountId || (delivery.to !== undefined && delivery.to !== audience.actorId)))) {
      throw new Error("Finance private audience is contradictory.");
    }
  } else if (context.messageChannel !== audience.channelId || context.requesterSenderId !== audience.actorId ||
      context.agentAccountId !== audience.accountId || delivery?.channel !== audience.channelId ||
      delivery.accountId !== audience.accountId || delivery.to !== audience.actorId) {
    throw new Error("Finance private audience is contradictory.");
  }
  return `finance-run:${binding.runId}`;
}
