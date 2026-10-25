/** Independent native policy for an actually presented reminder-only option. */
import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import { canonical } from "./approval.js";
import { spansMatch, taskAnswer } from "./reminder-choice-policy.js";
import type { Provenance } from "./contract.js";
export const REMINDER_MODE_RULE = "current-human-request.cos-reminder-mode-choice.v1";
const hash = (value: unknown) => createHash("sha256").update(canonical(value)).digest("hex");
const sha = (value: string) => createHash("sha256").update(value).digest("hex");
const object = (value: unknown): Record<string, any> => {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Reminder mode evidence is unavailable");
  return value as Record<string, any>;
};
export function modeOrigin(p: Provenance) {
  return { channel_id: p.channel_id, account_id: p.account_id ?? null, actor_id: p.actor_id, session_key: p.session_key ?? null, thread_id: p.thread_id ?? null };
}
export function modePrompt(title: string, due: string, notes: unknown): string {
  const text = "Choose how I should handle this request. The reminder would be in my authorized Apple Reminders work list.\n" +
    `Title: ${JSON.stringify(title)}\nDue: ${due}\nNotes: ${JSON.stringify(notes)}\n\n` +
    "1. Create only this reminder. Do not send or schedule an email.\n" +
    "2. I want an automatic email instead. Keep that workflow unresolved; this choice does not schedule it or create the reminder.\n\nReply 1 or 2.";
  return text.replace(/[\s\u0085]+/gu, " ").trim();
}
export function validateReminderMode(raw: unknown, input: { source_message_event_id: string; instruction_text: string; provenance: Provenance }, secret: string, requestedTaskId?: unknown): void {
  const t = object(raw), c = object(t.continuation), o = object(c.offered), a = object(t.action), shown = object(c.presentation);
  const due = Date.parse(o.due_at), issued = Date.parse(t.issued_at), expiry = Date.parse(t.expires_at), offeredAt = Date.parse(o.offered_at), submitted = Date.parse(o.original_submitted_at), presented = Date.parse(shown.presented_at);
  // Intake retains whole seconds; strict event order below still proves the answer came later.
  const presentedSecond = Math.floor(presented / 1000) * 1000;
  const original = c.original_instruction;
  const origin = object(o.origin);
  const selected = taskAnswer(c.selection, input, o, c.offered_event_id, issued).trim();
  const originalOffsetDue = String(o.due_at).replace(/Z$/, "+00:00");
  const prompt = modePrompt(o.title, originalOffsetDue, o.notes ?? null);
  const proof = { source_message_event_id: o.original_source_event_id, offered_event_id: c.offered_event_id,
    body: prompt, run_id: shown.run_id, response_result_event_id: shown.response_result_event_id ?? null,
    issued_at_ms: shown.issued_at_ms, expires_at_ms: shown.expires_at_ms, origin };
  const signature = createHmac("sha256", secret).update("cos-reminder-presentation-v1|" + canonical(proof)).digest("hex");
  const portal = origin.channel_id === "administrateme-portal";
  const expectedActionKeys = ["provider", "operation", "resource_owner", "operating_principal", "provider_account_id", "source_id", "list_id", "device_identity_sha256", "os_uid", "selection_sha256", "write_policy_sha256", "title", "notes", "due_at", "selected_by_policy"].sort();
  if (t.rule_id !== REMINDER_MODE_RULE || o.clarification_kind !== "reminder_only_or_email" ||
      typeof original !== "string" || !original || hash(original) !== o.original_source_fingerprint || !spansMatch(original, c.original_evidence) ||
      !spansMatch(input.instruction_text, t.evidence) || input.source_message_event_id !== t.source_event_id || t.source_event_id === o.original_source_event_id ||
      t.member_id !== o.member_id || requestedTaskId !== undefined && requestedTaskId !== o.task_id ||
      selected !== "1" || canonical(modeOrigin(input.provenance)) !== canonical(origin) && !input.instruction_text.trim().startsWith(o.task_id + ":") ||
      ![due, issued, expiry, offeredAt, submitted, presented].every(Number.isFinite) ||
      submitted > offeredAt || offeredAt > presented || presentedSecond > Date.parse(c.selection.answer_submitted_at) ||
      issued < presented || expiry <= Date.now() || expiry > due || expiry - issued > 600_000 || due !== Date.parse(o.pending_expires_at) ||
      !(c.offered_event_id < c.presentation_event_id && c.presentation_event_id < t.source_event_id) ||
      shown.member_id !== t.member_id || shown.task_id !== o.task_id || shown.offered_event_id !== c.offered_event_id || shown.source_event_id !== o.original_source_event_id ||
      canonical(shown.origin) !== canonical(origin) || o.prompt !== prompt || shown.prompt_sha256 !== sha(prompt) ||
      shown.issued_at_ms > presented || shown.expires_at_ms <= presented || shown.expires_at_ms - shown.issued_at_ms > 30_000 ||
      typeof shown.host_signature !== "string" || !/^[a-f0-9]{64}$/.test(shown.host_signature) ||
      !timingSafeEqual(Buffer.from(signature), Buffer.from(shown.host_signature)) ||
      (shown.presentation_kind === "native_portal_final") !== portal || (shown.response_result_event_id === null) !== portal ||
      !portal && (typeof shown.response_result_event_id !== "string" || !shown.response_result_event_id) ||
      hash({ title: o.title, due_at: originalOffsetDue, notes: o.notes ?? null }) !== o.draft_fingerprint ||
      canonical(Object.keys(a).sort()) !== canonical(expectedActionKeys) || canonical(a) !== canonical(o.action) ||
      a.provider !== "apple_reminders" || a.operation !== "create_reminder" || a.resource_owner !== "service-principal:chief-of-staff" || a.operating_principal !== a.resource_owner ||
      a.provider_account_id !== o.provider_account_id || a.title !== o.title || a.notes !== (o.notes ?? null) || Date.parse(a.due_at) !== due || a.selected_by_policy !== true ||
      o.provider_effect_count !== 0 || shown.provider_effect_count !== 0) {
    throw new Error("Fresh answer does not authorize the actually presented reminder-only option");
  }
}
