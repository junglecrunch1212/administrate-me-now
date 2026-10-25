/** Trusted portal/Gmail human input, transported outside prompts and tool args. */
import { createHash, createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { OPENCLAW_COMMIT, OPENCLAW_VERSION, type Provenance } from "./contract.js";
import type { PluginRuntime } from "./runtime.js";
import { replyParts } from "./self-replies.js";
import { canonical } from "./approval.js";
import { recordModePresentation } from "./reminder-mode.js";
import { TransportError } from "./transport.js";

type Input = { provenance: Provenance; source_message_event_id: string; textSha256: string; capturedAt: number; instructionText?: string };
type Context = { runId?: string | undefined; sessionKey?: string | undefined; agentId?: string | undefined };
const pending = new WeakMap<PluginRuntime, Map<string, Input>>();
const turns = new WeakMap<PluginRuntime, Map<string, Input>>();
const calls = new WeakMap<PluginRuntime, Map<string, Input>>();
const fields = ["source_event_id", "session_key", "agent_id", "actor_id", "channel_id", "account_id", "message_id", "thread_id", "text_sha256", "nonce"] as const;
const sha = (text: string) => createHash("sha256").update(text).digest("hex");
const matches = (input: Input, context: Context) => input.provenance.session_key === context.sessionKey &&
  input.provenance.agent_id === context.agentId && Date.now() - input.capturedAt <= 3_600_000;

/** Non-consuming immutable copy for the independent host authorization policy. */
export function peekConversationInput(runtime: PluginRuntime, context: Context) {
  const input = context.runId ? turns.get(runtime)?.get(context.runId) : undefined;
  return input && matches(input, context) && typeof input.instructionText === "string" ? {
    provenance: { ...input.provenance }, source_message_event_id: input.source_message_event_id, instruction_text: input.instructionText,
  } : undefined;
}

/** Generic member tools can reuse this run's verified real conversation identity. */
export function conversationToolProvenance(runtime: PluginRuntime, context: Context): Provenance | undefined {
  const inputs = [...(turns.get(runtime)?.values() ?? [])].filter((input) => matches(input, context));
  return inputs.length === 1 ? { ...inputs[0]!.provenance } : undefined;
}

export function bindConversationTool(runtime: PluginRuntime, context: Context, callId: string | undefined): boolean {
  const input = context.runId ? turns.get(runtime)?.get(context.runId) : undefined;
  if (!callId || !input || !matches(input, context)) return false;
  let map = calls.get(runtime);
  if (!map) { map = new Map(); calls.set(runtime, map); }
  if (map.size >= 100) return false;
  map.set(callId, input);
  return true;
}

export function takeConversationInput(runtime: PluginRuntime, callId: string, context: Context) {
  const input = calls.get(runtime)?.get(callId);
  calls.get(runtime)?.delete(callId);
  if (!input || !matches(input, context) || typeof input.instructionText !== "string") return undefined;
  return { provenance: { ...input.provenance }, source_message_event_id: input.source_message_event_id, host_binding: "", instruction_text: input.instructionText };
}

export function registerConversationInput(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  const used = new Map<string, number>();
  api.registerGatewayMethod("administrateme.conversation.prepare", async ({ params, respond }) => {
    try {
      const now = Date.now();
      for (const [nonce, expiry] of used) if (expiry <= now) used.delete(nonce);
      if (Object.keys(params).sort().join(",") !== [...fields, "expires_at_ms", "signature"].sort().join(",") ||
          fields.some((name) => typeof params[name] !== "string" || String(params[name]).includes("|"))) throw new Error("invalid fields");
      const p = params as Record<string, string>;
      const expires = Number(params.expires_at_ms);
      if (!Number.isSafeInteger(expires) || expires <= now || expires > now + 10000 ||
          !/^[0-9a-f]{32}$/.test(p.nonce!) || used.has(p.nonce!) ||
          !/^[0-9a-f]{64}$/.test(p.text_sha256!) || !p.source_event_id || !p.message_id || !p.account_id ||
          !runtime.transport.config.memberAgentIds.includes(p.agent_id!) ||
          !p.session_key!.startsWith(`agent:${p.agent_id}:administrateme-`) ||
          !["gmail", "administrateme-portal"].includes(p.channel_id!) ||
          (p.channel_id === "gmail" && (!p.thread_id || !p.session_key!.includes(":administrateme-gmail-"))) ||
          (p.channel_id === "administrateme-portal" && (p.thread_id || p.account_id !== "member-portal" || !p.session_key!.includes(":administrateme-portal-")))) throw new Error("invalid binding");
      const secret = process.env[runtime.transport.config.sharedSecretEnv];
      if (!secret || secret.length < 24) throw new Error("authentication unavailable");
      const expected = createHmac("sha256", secret).update(["cos-conversation-input-v1", ...fields.map((key) => p[key]), String(expires)].join("|")).digest("hex");
      if (!/^[0-9a-f]{64}$/.test(String(params.signature)) ||
          !timingSafeEqual(Buffer.from(expected), Buffer.from(String(params.signature)))) throw new Error("invalid signature");
      used.set(p.nonce!, expires);
      let inbox = pending.get(runtime);
      if (!inbox) { inbox = new Map(); pending.set(runtime, inbox); }
      for (const [id, input] of inbox) if (now - input.capturedAt > 3_600_000) inbox.delete(id);
      if (inbox.size >= 100 && !inbox.has(p.source_event_id!)) throw new Error("pending limit");
      const provenance: Provenance = {
        actor_id: p.actor_id!, channel_id: p.channel_id!, account_id: p.account_id!,
        agent_id: p.agent_id!, session_key: p.session_key!, message_id: p.message_id!,
        call_kind: "tool", correlation_id: randomUUID(),
        openclaw_version: OPENCLAW_VERSION, openclaw_commit: OPENCLAW_COMMIT,
      };
      if (p.thread_id) provenance.thread_id = p.thread_id;
      inbox.set(p.source_event_id!, { provenance, source_message_event_id: p.source_event_id!, textSha256: p.text_sha256!, capturedAt: now });
      respond(true, { accepted: true });
    } catch {
      respond(false, undefined, { code: "INSTRUCTION_UNAVAILABLE", message: "Authenticated conversation input unavailable" });
    }
  }, { scope: "operator.write" });
  api.on("before_agent_reply", (event, context) => {
    if (!context.runId || !context.sessionKey || !context.agentId) return;
    let map = turns.get(runtime);
    if (!map) { map = new Map(); turns.set(runtime, map); }
    for (const [id, input] of map) if (Date.now() - input.capturedAt > 3_600_000) map.delete(id);
    map.delete(context.runId);
    const matchesInput = [...(pending.get(runtime)?.values() ?? [])].filter((input) =>
      matches(input, context) && input.textSha256 === sha(event.cleanedBody));
    if (matchesInput.length === 1 && map.size < 100) map.set(context.runId, { ...matchesInput[0]!, instructionText: event.cleanedBody });
  }, { eligibleTriggers: ["user"] });
  api.on("agent_end", async (event, context) => {
    const runId = event.runId ?? context.runId;
    if (!runId) return;
    const input = turns.get(runtime)?.get(runId);
    if (input) pending.get(runtime)?.delete(input.source_message_event_id);
    turns.get(runtime)?.delete(runId);
    if (!input || !matches(input, context) || !event.success) return;
    // Only the final answer in this same authenticated human turn is a reply.
    // Tool output, assistant analysis and historical text cannot authorize sends.
    const latestUser = [...event.messages].reverse().find((value: any) => value?.role === "user") as Record<string, any> | undefined;
    const userText = typeof latestUser?.content === "string" ? latestUser.content :
      Array.isArray(latestUser?.content) ? latestUser.content.filter((part: any) => part?.type === "text" && typeof part.text === "string").map((part: any) => part.text).join("\n") : "";
    if (!latestUser || latestUser.display === false ||
        (latestUser.provenance?.kind && latestUser.provenance.kind !== "external_user") ||
        userText !== input.instructionText) return;
    const body = replyParts(event.messages).join("\n\n");
    if (!body || body.length > 8000) return;
    if (input.provenance.channel_id === "administrateme-portal") {
      try { await recordModePresentation(runtime, input, runId, body); }
      catch { api.logger.warn("Reminder clarification presentation was not retained; no numbered answer can execute it."); }
      return;
    }
    if (input.provenance.channel_id !== "gmail") return;
    const p = input.provenance;
    const decision = {
      contract: "authenticated-conversation-reply-v1", rule_id: "same_conversation_reply_v1",
      source_event_id: input.source_message_event_id, channel: "gmail",
      account_id: p.account_id!, actor_id: p.actor_id, session_key: p.session_key!,
      agent_id: p.agent_id!, message_id: p.message_id!, thread_id: p.thread_id!,
      body_sha256: sha(body), issued_at_ms: Date.now(), expires_at_ms: 0,
    };
    decision.expires_at_ms = decision.issued_at_ms + 30000;
    const secret = process.env[runtime.transport.config.sharedSecretEnv];
    if (!secret || secret.length < 24) return;
    const material = "authenticated-conversation-reply-v1|" + canonical(decision);
    try {
      const result = await runtime.transport.request<Record<string, unknown>>("/api/openclaw/v1/conversations/reply", {
        provenance: p, source_message_event_id: input.source_message_event_id, body,
        request_id: `gmail-reply:${sha(input.source_message_event_id + "|" + decision.body_sha256)}`,
        host_decision: { ...decision, signature: createHmac("sha256", secret).update(material).digest("hex") },
      });
      if (result.status === "sent" && typeof result.event_id === "string") {
        try { await recordModePresentation(runtime, input, runId, body, result.event_id); }
        catch { api.logger.warn("Sent reminder clarification lacks retained presentation evidence; do not resend it automatically."); }
      }
      if (result.status !== "sent") api.logger.warn("CoS email reply is not terminally proven sent; inspect its receipt, do not resend.");
    } catch (error) {
      // Only closed local classifications: never log response text, addresses,
      // message bodies, credentials, or arbitrary exception names/messages.
      const status = error instanceof TransportError && Number.isInteger(error.status) && error.status! >= 400 && error.status! <= 599
        ? String(error.status) : "unknown";
      const kind = error instanceof TransportError ? "transport" : "local";
      api.logger.error(`CoS email reply failed or has an unknown result (kind=${kind}, status=${status}); inspect its retained receipt, do not resend.`);
    }
  });
}
