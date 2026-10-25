import { recordModePresentation } from "./reminder-mode.js";
import { createHash } from "node:crypto";
import type { OpenClawPluginApi, PluginHookAgentContext } from "openclaw/plugin-sdk/plugin-entry";
import type { Provenance } from "./contract.js";
import type { PluginRuntime } from "./runtime.js";
import { bindConversationTool, takeConversationInput, peekConversationInput } from "./conversation-input.js";

type Inbound = { provenance: Provenance; sourceId: Promise<string | undefined>; binding: string; capturedAt: number; runId: string | undefined; text: string };
const pending = new WeakMap<PluginRuntime, Map<string, Inbound>>();

export function rememberSelfReply(runtime: PluginRuntime, provenance: Provenance, runId: string | undefined, receipt: Promise<unknown>, text: string): void {
  if (provenance.channel_id !== "imessage" ||
      !provenance.account_id || !provenance.message_id || provenance.thread_id ||
      !/^\+[1-9][0-9]{7,14}$/.test(provenance.actor_id)) return;
  const session = provenance.session_key;
  const agent = /^agent:(adminme-[0-9a-f]{16}):imessage:/.exec(session ?? "")?.[1];
  if (!agent || session !== `agent:${agent}:imessage:${provenance.account_id}:direct:${provenance.actor_id}` ||
      (provenance.agent_id !== undefined && provenance.agent_id !== agent) ||
      !runtime.transport.config.memberAgentIds.includes(agent)) return;
  const binding = createHash("sha256").update(JSON.stringify([
    "cos-self-reply-v1", agent, "imessage", provenance.account_id, provenance.actor_id,
  ])).digest("hex");
  // Capture the current instruction independently of permission to send a reply.
  let inbox = pending.get(runtime);
  if (!inbox) { inbox = new Map(); pending.set(runtime, inbox); }
  for (const [key, entry] of inbox) {
    if (Date.now() - entry.capturedAt > 3_600_000) inbox.delete(key);
  }
  const key = JSON.stringify([session, provenance.message_id]);
  if (inbox.size >= 100 && !inbox.has(key)) return;
  inbox.set(key, { provenance: { ...provenance, agent_id: agent },
    sourceId: receipt.then((value) => typeof value === "string" ? value : undefined, () => undefined),
    binding, capturedAt: Date.now(), runId, text });
}

const reminderTurns = new WeakMap<PluginRuntime, Map<string, Inbound>>();
const reminderCalls = new WeakMap<PluginRuntime, Map<string, Inbound>>();

export function bindReminderTool(runtime: PluginRuntime, context: { runId?: string | undefined; sessionKey?: string | undefined; agentId?: string | undefined }, callId: string | undefined): boolean {
  if (bindConversationTool(runtime, context, callId)) return true;
  if (!callId || !context.runId) return false;
  const input = reminderTurns.get(runtime)?.get(context.runId);
  if (!input || input.provenance.session_key !== context.sessionKey || input.provenance.agent_id !== context.agentId || Date.now() - input.capturedAt > 3_600_000) return false;
  let calls = reminderCalls.get(runtime);
  if (!calls) { calls = new Map(); reminderCalls.set(runtime, calls); }
  if (calls.size > 100) calls.clear();
  calls.set(callId, input);
  return true;
}

export async function takeReminderInput(runtime: PluginRuntime, callId: string, context: { sessionKey?: string; agentId?: string }) {
  const conversation = takeConversationInput(runtime, callId, context);
  if (conversation) return conversation;
  const input = reminderCalls.get(runtime)?.get(callId);
  reminderCalls.get(runtime)?.delete(callId);
  if (!input || input.provenance.session_key !== context.sessionKey || input.provenance.agent_id !== context.agentId || Date.now() - input.capturedAt > 3_600_000) throw new Error("No current private reminder request");
  const sourceId = await input.sourceId;
  if (!sourceId) throw new Error("Reminder request was not durably acknowledged");
  return { provenance: input.provenance, source_message_event_id: sourceId, host_binding: input.binding, instruction_text: input.text };
}

/** Read-only current-input binding for independent action policy evaluation. */
export async function peekReminderInput(runtime: PluginRuntime, context: { runId?: string | undefined; sessionKey?: string | undefined; agentId?: string | undefined }) {
  const conversation = peekConversationInput(runtime, context);
  if (conversation) return conversation;
  const input = context.runId ? reminderTurns.get(runtime)?.get(context.runId) : undefined;
  if (!input || input.provenance.session_key !== context.sessionKey || input.provenance.agent_id !== context.agentId || Date.now() - input.capturedAt > 3_600_000) return undefined;
  const source = await input.sourceId;
  return source ? { provenance: { ...input.provenance }, source_message_event_id: source, instruction_text: input.text } : undefined;
}

function record(value: unknown): Record<string, any> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, any> : {};
}

function currentInput(messages: unknown[]): Record<string, any> {
  return record([...messages].reverse().find((value) => record(value).role === "user"));
}

function matchInputs(inbox: Map<string, Inbound>, messages: unknown[], session: string, runId: string | undefined): Array<[string, Inbound]> {
  const user = currentInput(messages);
  if (!user.role || user.display === false || (user.provenance?.kind && user.provenance.kind !== "external_user")) return [];
  const metadata = record(user.__openclaw);
  const transport = record(metadata.transport);
  const candidates = [...inbox].filter(([, value]) => value.provenance.session_key === session &&
    (!value.runId || value.runId === runId) && Date.now() - value.capturedAt <= 3_600_000);
  if (transport.channel === "imessage" && typeof transport.messageId === "string" && !transport.threadId) {
    return candidates.filter(([, value]) => value.provenance.message_id === transport.messageId);
  }
  // The pinned host's collected followup deliberately drops individual transport
  // metadata. Its host-created transcript marker and exact source text identify
  // the pending inputs in this same private conversation. No model output or
  // prompt-embedded message ID is used as source authority.
  if (typeof user.idempotencyKey === "string" && user.idempotencyKey.startsWith("followup-collect:") &&
      typeof user.content === "string") {
    return candidates.filter(([, value]) => value.text.trim() && user.content.includes(value.text.trim()));
  }
  return [];
}

export function replyParts(messages: unknown[]): string[] {
  // Only the final assistant answer after the latest user input is eligible.
  // Tool results, analysis, progress, historical answers and silence tokens are not replies.
  let final = "";
  let sawUser = false;
  for (const value of messages) {
    if (!value || typeof value !== "object") continue;
    const m = value as Record<string, unknown>;
    if (m.role === "user") { final = ""; sawUser = true; }
    if (!sawUser || m.role !== "assistant" || m.display === false || m.openclawStreamFallback ||
        (m.stopReason !== undefined && m.stopReason !== "stop") ||
        (m.phase !== undefined && m.phase !== "final")) continue;
    const mirror = record(m.__openclaw).mirrorIdentity;
    if (typeof mirror === "string" && !mirror.endsWith(":assistant")) continue;
    const content = m.content;
    if (typeof content === "string") final = content;
    else if (Array.isArray(content)) {
      final = content.filter((part) => part && typeof part === "object" &&
        part.type === "text" && typeof part.text === "string")
        .map((part) => part.text as string).join("\n");
    }
  }
  final = final.replace(/\[\[\s*reply_to(?:_current|\s*:[^\]]*)?\s*\]\]/gi, "")
    .replace(/\s+/gu, " ").trim();
  if (!final || ["NO_REPLY", "HEARTBEAT_OK"].includes(final) || final.length > 50_000 ||
      /[\u0000-\u001f\u007f]/u.test(final)) return [];
  const parts: string[] = [];
  while (final) {
    let length = Math.min(3500, final.length);
    if (length < final.length) {
      if (/[\uD800-\uDBFF]/.test(final[length - 1]!)) length--;
      const space = final.lastIndexOf(" ", length);
      if (space > 2000) length = space;
    }
    parts.push(final.slice(0, length).trim());
    final = final.slice(length).trim();
  }
  return parts.length <= 16 ? parts : [];
}

export function escapeReplyText(text: string): string {
  // CommonMark escapes all ASCII punctuation. The pinned native renderer then
  // produces exactly the admitted text, without formatting ranges or directives.
  return text.replace(/[!-/:-@\[-`{-~]/g, "\\$&");
}

export function registerSelfReplies(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  api.on("before_agent_reply", (event, context) => {
    if (!context.runId || !context.sessionKey) return;
    let turns = reminderTurns.get(runtime);
    if (!turns) { turns = new Map(); reminderTurns.set(runtime, turns); }
    for (const [key, value] of turns) if (Date.now() - value.capturedAt > 3_600_000) turns.delete(key);
    const inbox = pending.get(runtime);
    const matches = inbox && [...inbox.values()].filter((input) =>
      input.provenance.session_key === context.sessionKey && input.provenance.agent_id === context.agentId &&
      (!input.runId || input.runId === context.runId) && Date.now() - input.capturedAt <= 3_600_000 &&
      // The pinned host may prepend status/media context separated by a blank
      // line. The exact current private text must remain the complete suffix;
      // a mention elsewhere in history or a retrieved result is insufficient.
      (input.text.trim() === event.cleanedBody.trim() ||
       event.cleanedBody.trim().endsWith(`\n\n${input.text.trim()}`)));
    const input = matches?.length === 1 ? matches[0] : undefined;
    turns.delete(context.runId);
    if (input && context.agentId === input.provenance.agent_id && turns.size < 100) turns.set(context.runId, input);
  }, { eligibleTriggers: ["user"] });
  api.on("agent_end", async (event, context: PluginHookAgentContext) => {
    const inbox = pending.get(runtime);
    const session = context.sessionKey;
    const runId = event.runId ?? context.runId;
    if (runId) reminderTurns.get(runtime)?.delete(runId);
    if (!session || !inbox || !event.success) return;
    const matches = matchInputs(inbox, event.messages, session, runId);
    const input = matches.at(-1)?.[1];
    if (!input || context.agentId !== input.provenance.agent_id) return;
    if (!runtime.transport.config.standingSelfReplyBindings.includes(input.binding)) {
      // Internal-only turns still finish: stale identical inputs must not make
      // the next explicit request ambiguous or retain unnecessary private text.
      for (const [key] of matches) inbox.delete(key);
      return;
    }
    const accountId = input.provenance.account_id;
    if (!accountId) return;
    const parts = replyParts(event.messages);
    if (!parts.length) return;
    // Consume all collected inputs before I/O. The newest acknowledged input
    // is the durable authority for this one combined answer; no automatic retry.
    for (const [key] of matches) inbox.delete(key);
    const sourceId = await input.sourceId;
    if (!sourceId) {
      api.logger.error("CoS self reply refused: its inbound receipt was not acknowledged.");
      return;
    }
    for (let index = 0; index < parts.length; index++) {
      try {
        const result = await runtime.transport.request<Record<string, unknown>>(
          "/api/openclaw/v1/cos-responses/self-reply", {
            provenance: input.provenance, source_message_event_id: sourceId,
            host_binding: input.binding, body: parts[index], reply_index: index,
          });
        const dispatch = result.transport_dispatch as Record<string, unknown> | undefined;
        if (!dispatch) {
          api.logger.warn("CoS self reply stopped before dispatch; inspect its private terminal receipt.");
          return;
        }
        if (dispatch.channel !== "imessage" || dispatch.account_id !== input.provenance.account_id ||
            dispatch.recipient !== input.provenance.actor_id || dispatch.body !== parts[index] ||
            typeof dispatch.approval_event_id !== "string") throw new Error("self reply dispatch mismatch");
        const adapter = await api.runtime.channel.outbound.loadAdapter("imessage");
        if (!adapter?.sendText) throw new Error("self reply transport unavailable");
        try {
          await adapter.sendText({ cfg: api.config, to: input.provenance.actor_id,
            text: escapeReplyText(parts[index]!), accountId });
        } catch { /* The guarded native terminal distinguishes non-contact from unknown. */ }
        const terminal = await runtime.transport.request<Record<string, unknown>>(
          "/api/openclaw/v1/cos-responses/result", {
            provenance: input.provenance, approval_event_id: dispatch.approval_event_id,
          });
        if (terminal.status !== "sent") {
          api.logger.error("CoS self reply was not proven sent; no automatic retry.");
          return;
        }
        if (parts.length === 1 && runId && typeof terminal.event_id === "string") {
          try { await recordModePresentation(runtime, { source_message_event_id: sourceId, provenance: input.provenance }, runId, parts[index]!, terminal.event_id); }
          catch { api.logger.warn("Sent reminder clarification lacks retained presentation evidence; do not resend it automatically."); }
        }
        api.logger.info("CoS self reply part delivered with an audited native receipt.");
      } catch {
        api.logger.error("CoS self reply failed closed; inspect private reply diagnostics.");
        return;
      }
    }
  });
}
