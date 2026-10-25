/** Numbered clarification uses actual final replies, not model-supplied proof. */
import { createHmac } from "node:crypto";
import { Type } from "typebox";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { resolveGlobalMap } from "openclaw/plugin-sdk/global-singleton";
import type { PluginRuntime } from "./runtime.js";
import type { Provenance } from "./contract.js";
import { canonical } from "./approval.js";
import { takeReminderInput } from "./self-replies.js";
import { rememberReminderChoiceAuthorization } from "./request-policy.js";
import { modeOrigin } from "./reminder-mode-policy.js";
import { toolResult } from "./result.js";

type Input = { source_message_event_id: string; provenance: Provenance; instruction_text: string };
type Offer = { source: string; origin: ReturnType<typeof modeOrigin>; offered: string; prompt: string; expires: number };
const pending = () => resolveGlobalMap<string, Offer>(Symbol.for("administrateme.reminder-mode-presentation.v1"));
function secret(runtime: PluginRuntime): string {
  const value = process.env[runtime.transport.config.sharedSecretEnv];
  if (!value || value.length < 24) throw new Error("Reminder presentation credential unavailable");
  return value;
}
function key(runtime: PluginRuntime, input: { source_message_event_id: string; provenance: Provenance }): string {
  return createHmac("sha256", secret(runtime)).update(canonical([runtime.transport.config.baseUrl, input.source_message_event_id, modeOrigin(input.provenance)])).digest("hex");
}
export function rememberModeOffer(runtime: PluginRuntime, result: unknown, input: Input): void {
  if (!result || typeof result !== "object") return;
  const r = result as Record<string, unknown>;
  if (r.state !== "mode_choice_required" || typeof r.presentation_prompt !== "string" || !r.presentation_prompt || r.presentation_prompt.length > 3500 || typeof r.choice_event_id !== "string") return;
  const now = Date.now();
  for (const [id, row] of pending()) if (row.expires <= now) pending().delete(id);
  if (pending().size >= 100) throw new Error("Pending reminder presentation limit");
  pending().set(key(runtime, input), { source: input.source_message_event_id, origin: modeOrigin(input.provenance), offered: r.choice_event_id, prompt: r.presentation_prompt, expires: Math.min(now + 3_600_000, Date.parse(String(r.pending_expires_at))) });
}
/** Called only by trusted final-turn hooks, after sent evidence where applicable. */
export async function recordModePresentation(runtime: PluginRuntime, input: { source_message_event_id: string; provenance: Provenance }, runId: string, body: string, terminalEventId?: string): Promise<void> {
  const id = key(runtime, input), offer = pending().get(id);
  if (!offer || offer.expires <= Date.now() || offer.prompt !== body || !runId) return;
  const portal = input.provenance.channel_id === "administrateme-portal";
  if (portal === (terminalEventId !== undefined)) return;
  const proof = {
    source_message_event_id: input.source_message_event_id, offered_event_id: offer.offered,
    body, run_id: runId, response_result_event_id: terminalEventId ?? null,
    issued_at_ms: Date.now(), expires_at_ms: 0, origin: offer.origin,
  };
  proof.expires_at_ms = proof.issued_at_ms + 30000;
  const signature = createHmac("sha256", secret(runtime)).update("cos-reminder-presentation-v1|" + canonical(proof)).digest("hex");
  const { origin: _origin, ...fields } = proof;
  const result = await runtime.transport.request<Record<string, unknown>>("/api/openclaw/v1/tools/cos-reminder-choice/presented", {
    ...fields, signature, provenance: input.provenance,
  });
  if (result.state === "presented") pending().delete(id);
}
export function registerReminderModeTool(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  api.registerTool((context) => ({
    name: "administrateme_cos_reminder_choice", label: "Clarify CoS reminder or email",
    description: "Use offer only when a fresh authenticated request ambiguously mixes a reminder in CoS's own Apple work list with automatic email. Retain a natural short title, exact original due time and optional notes; return presentation_prompt verbatim as your final reply, without extra text or reordered numbering. This records work, not execution authority. Later use answer for the member's fresh 1 or 2; never resubmit their number as a standalone create request. 1 authorizes only the actually presented unchanged CoS reminder. 2 remains an unresolved email workflow: it schedules no email/timer and creates no reminder. A bare number belongs to its original conversation and must select one pending offer; cross-channel continuation requires the member's exact task_id followed by a colon and the number. Tool task_id alone is not authority. Use status or a fresh explicit cancel without replacing draft fields. Pass a returned domain_approval_event_id to administrateme_execute_pending_provider_action, then verify administrateme_cos_apple_reminder_status. Only terminal succeeded with native read-back proves creation. Never resume an expired or historical failed request, create a new ID after uncertainty, or infer send authority from an assistant/tool suggestion.",
    parameters: Type.Object({
      action: Type.Union([Type.Literal("offer"), Type.Literal("answer"), Type.Literal("status"), Type.Literal("cancel")]),
      task_id: Type.Optional(Type.String({ minLength: 1, maxLength: 240 })),
      title: Type.Optional(Type.String({ minLength: 1, maxLength: 1000 })),
      due_at: Type.Optional(Type.String({ maxLength: 100, pattern: "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:Z|[+-]\\d{2}:\\d{2})$" })),
      notes: Type.Optional(Type.String({ maxLength: 1000 })),
      request_id: Type.Optional(Type.String({ minLength: 8, maxLength: 120 })),
    }, { additionalProperties: false }),
    async execute(id, params) {
      const input = await takeReminderInput(runtime, id, context);
      const result = await runtime.transport.request("/api/openclaw/v1/tools/cos-reminder-choice", {
        ...params, provenance: input.provenance, source_message_event_id: input.source_message_event_id,
      });
      if (params.action === "offer") rememberModeOffer(runtime, result, input);
      rememberReminderChoiceAuthorization(runtime, result, input, params as Record<string, unknown>);
      // The host keeps the complete signed evidence; the model needs the task,
      // domain receipt and truthful status, not a repeated original transcript.
      if (result && typeof result === "object") {
        const { request_authorization: _proof, ...visible } = result as Record<string, unknown>;
        return toolResult(visible);
      }
      return toolResult(result);
    },
  }), { name: "administrateme_cos_reminder_choice", optional: true });
}
