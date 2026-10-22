import { createHash } from "node:crypto";
import type { OpenClawPluginApi, PluginHookAgentContext } from "openclaw/plugin-sdk/plugin-entry";
import type { Provenance } from "./contract.js";
import type { PluginRuntime } from "./runtime.js";

type Inbound = { provenance: Provenance; sourceId: Promise<string | undefined>; binding: string; capturedAt: number };
const pending = new WeakMap<PluginRuntime, Map<string, Inbound>>();

export function rememberSelfReply(runtime: PluginRuntime, provenance: Provenance, runId: string | undefined, receipt: Promise<unknown>): void {
  if (!runId || provenance.channel_id !== "imessage" ||
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
  if (!runtime.transport.config.standingSelfReplyBindings.includes(binding)) return;
  let inbox = pending.get(runtime);
  if (!inbox) { inbox = new Map(); pending.set(runtime, inbox); }
  for (const [key, entry] of inbox) {
    if (Date.now() - entry.capturedAt > 3_600_000) inbox.delete(key);
  }
  if (inbox.size >= 100 && !inbox.has(runId)) return;
  inbox.set(runId, { provenance: { ...provenance, agent_id: agent },
    sourceId: receipt.then((value) => typeof value === "string" ? value : undefined, () => undefined),
    binding, capturedAt: Date.now() });
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
    if (!sawUser || m.role !== "assistant" || m.openclawStreamFallback ||
        (m.stopReason !== undefined && m.stopReason !== "stop") ||
        (m.phase !== undefined && m.phase !== "final")) continue;
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
  api.on("agent_end", async (event, context: PluginHookAgentContext) => {
    const inbox = pending.get(runtime);
    const session = context.sessionKey;
    const runId = event.runId ?? context.runId;
    const input = runId ? inbox?.get(runId) : undefined;
    if (!session || !runId || !input || session !== input.provenance.session_key || !event.success || context.agentId !== input.provenance.agent_id ||
        Date.now() - input.capturedAt > 3_600_000) return;
    const parts = replyParts(event.messages);
    if (!parts.length) return;
    // Consume before I/O: a failed/ambiguous native attempt is never retried.
    inbox!.delete(runId);
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
            text: escapeReplyText(parts[index]!), accountId: input.provenance.account_id });
        } catch { /* The guarded native terminal distinguishes non-contact from unknown. */ }
        const terminal = await runtime.transport.request<Record<string, unknown>>(
          "/api/openclaw/v1/cos-responses/result", {
            provenance: input.provenance, approval_event_id: dispatch.approval_event_id,
          });
        if (terminal.status !== "sent") {
          api.logger.error("CoS self reply was not proven sent; no automatic retry.");
          return;
        }
        api.logger.info("CoS self reply part delivered with an audited native receipt.");
      } catch {
        api.logger.error("CoS self reply failed closed; inspect private reply diagnostics.");
        return;
      }
    }
  });
}
