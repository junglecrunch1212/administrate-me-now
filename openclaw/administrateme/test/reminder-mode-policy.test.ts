import { createHash, createHmac } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { canonical } from "../src/approval.js";
import type { Provenance } from "../src/contract.js";
import { REMINDER_MODE_RULE, modeOrigin, modePrompt, validateReminderMode } from "../src/reminder-mode-policy.js";
import { readPluginConfig } from "../src/config.js";
import type { PluginRuntime } from "../src/runtime.js";
import { recordModePresentation, rememberModeOffer } from "../src/reminder-mode.js";
const secret = "fixture-mode-policy-secret-" + "x".repeat(32);
const hash = (value: unknown) => createHash("sha256").update(canonical(value)).digest("hex");
const sha = (value: string) => createHash("sha256").update(value).digest("hex");
const now = Date.parse("2031-02-03T12:00:00Z");
const span = (text: string) => [{ start: 0, end: Array.from(text).length, text }];
function fixture(channel = "administrateme-portal") {
  vi.spyOn(Date, "now").mockReturnValue(now);
  const provenance = { channel_id: channel, account_id: "fixture-cos-account", actor_id: "fixture-verified-member", session_key: "fixture-private-session", ...(channel === "gmail" ? { thread_id: "fixture-thread" } : {}) } as Provenance;
  const origin = modeOrigin(provenance), due = "2031-02-04T14:30:00+00:00";
  const original = "Keep an Apple reminder for yourself to email me the brief tomorrow at 9:30 AM.";
  const draft = { title: "Email the brief", due_at: due, notes: null };
  const action = { provider: "apple_reminders", operation: "create_reminder", resource_owner: "service-principal:chief-of-staff", operating_principal: "service-principal:chief-of-staff",
    provider_account_id: "fixture-apple-account", source_id: "fixture-source", list_id: "fixture-work-list", device_identity_sha256: "a".repeat(64), os_uid: 501,
    selection_sha256: "b".repeat(64), write_policy_sha256: "c".repeat(64), ...draft, selected_by_policy: true };
  const offered = { member_id: "member-a", task_id: "fixture-task", action_id: "fixture-action", request_id: "fixture-request",
    original_source_event_id: "ev1", original_source_fingerprint: hash(original), original_submitted_at: new Date(now - 60000).toISOString(),
    draft_fingerprint: hash(draft), provider: "apple_reminders", operation: "create_reminder", resource_owner: action.resource_owner,
    provider_account_id: action.provider_account_id, ...draft, action, origin, clarification_kind: "reminder_only_or_email",
    prompt: modePrompt(draft.title, due, null), offered_at: new Date(now - 30000).toISOString(), pending_expires_at: due,
    expiry_reason: "requested_reminder_due_time", provider_effect_count: 0 };
  const proof = { source_message_event_id: "ev1", offered_event_id: "ev2", body: offered.prompt,
    run_id: "fixture-final-run", response_result_event_id: channel === "administrateme-portal" ? null : "ev2-sent",
    issued_at_ms: now - 11000, expires_at_ms: now + 19000, origin };
  const shown = { member_id: "member-a", task_id: offered.task_id, offered_event_id: "ev2", source_event_id: "ev1", origin,
    prompt_sha256: sha(offered.prompt), run_id: proof.run_id, presented_at: new Date(now - 10000).toISOString(),
    response_result_event_id: proof.response_result_event_id, issued_at_ms: proof.issued_at_ms, expires_at_ms: proof.expires_at_ms,
    host_signature: createHmac("sha256", secret).update("cos-reminder-presentation-v1|" + canonical(proof)).digest("hex"),
    presentation_kind: channel === "administrateme-portal" ? "native_portal_final" : "sent_reply", provider_effect_count: 0 };
  const conversation = channel === "gmail" ? { channel_id: channel, account_id: provenance.account_id, actor_id: provenance.actor_id, thread_id: provenance.thread_id } : null;
  const ticket = { member_id: "member-a", source_event_id: "ev4", rule_id: REMINDER_MODE_RULE,
    issued_at: new Date(now).toISOString(), expires_at: new Date(now + 600000).toISOString(), action, evidence: span("1"),
    continuation: { offered_event_id: "ev2", offered, original_instruction: original, original_evidence: span(original),
      presentation_event_id: "ev3", presentation: shown,
      selection: { answer_source_event_id: "ev4", answer_submitted_at: new Date(now - 1000).toISOString(), answer_conversation: conversation,
        eligible_tasks: [{ offered_event_id: "ev2", task_id: offered.task_id, title_fingerprint: hash(offered.title.toLowerCase()), pending_expires_at: due, conversation }] } } };
  return { ticket, input: { source_message_event_id: "ev4", instruction_text: "1", provenance }, proof };
}
afterEach(() => { vi.restoreAllMocks(); delete process.env.ADMINME_MODE_TEST_SECRET; });
describe("actual numbered reminder presentation", () => {
  it.each(["imessage", "gmail", "administrateme-portal"])("binds the fresh %s answer to an independently signed presented option", (channel) => {
    const f = fixture(channel);
    expect(() => validateReminderMode(f.ticket, f.input, secret, "fixture-task")).not.toThrow();
    expect(() => validateReminderMode(f.ticket, f.input, "wrong-secret")).toThrow();
  });
  it.each(["imessage", "gmail", "administrateme-portal"])("preserves event order for a whole-second %s answer", (channel) => {
    const f = fixture(channel), c = f.ticket.continuation;
    c.presentation.presented_at = new Date(now - 10000 + 375).toISOString();
    c.selection.answer_submitted_at = new Date(now - 10000).toISOString();
    expect(() => validateReminderMode(f.ticket, f.input, secret)).not.toThrow();
    // The same timestamp second cannot authorize an answer retained before presentation.
    c.presentation_event_id = "ev5";
    expect(() => validateReminderMode(f.ticket, f.input, secret)).toThrow(/actually presented/);
    c.presentation_event_id = "ev3";
    c.selection.answer_submitted_at = new Date(now - 11000).toISOString();
    expect(() => validateReminderMode(f.ticket, f.input, secret)).toThrow(/actually presented/);
  });
  it.each(["mapping", "title", "due", "owner", "send", "extra-send", "unsigned", "conversation", "member", "stale", "original", "before-offer", "unshown", "ambiguous", "email"])("rejects %s without converting the number into broader authority", (failure) => {
    const f = fixture(), t = f.ticket, c = t.continuation;
    if (failure === "mapping") c.offered.prompt = c.offered.prompt.replace("1. Create", "2. Create");
    if (failure === "title") t.action.title = "Changed reminder";
    if (failure === "due") t.action.due_at = "2031-02-05T14:30:00+00:00";
    if (failure === "owner") t.action.resource_owner = "requester";
    if (failure === "send") t.action.operation = "send_email";
    if (failure === "extra-send") (t.action as any).send_email_at = c.offered.due_at;
    if (failure === "unsigned") c.presentation.host_signature = "0".repeat(64);
    if (failure === "conversation") f.input.provenance.session_key = "another-private-session";
    if (failure === "member") t.member_id = "member-b";
    if (failure === "stale") t.expires_at = new Date(now).toISOString();
    if (failure === "original") c.original_instruction = "Only send email, no reminder";
    if (failure === "before-offer") c.selection.answer_submitted_at = new Date(now - 20000).toISOString();
    if (failure === "unshown") c.presentation_event_id = "ev5";
    if (failure === "ambiguous") c.selection.eligible_tasks.push({ ...c.selection.eligible_tasks[0]!, offered_event_id: "ev2-other", task_id: "another-task" });
    if (failure === "email") { f.input.instruction_text = "2"; t.evidence = span("2"); }
    expect(() => validateReminderMode(t, f.input, secret)).toThrow();
  });
  it("requires an authored task reference to move a numbered answer across conversations", () => {
    const f = fixture(); f.input.provenance.session_key = "another-session";
    expect(() => validateReminderMode(f.ticket, f.input, secret, "fixture-task")).toThrow();
    f.input.instruction_text = "fixture-task: 1"; f.ticket.evidence = span(f.input.instruction_text);
    expect(() => validateReminderMode(f.ticket, f.input, secret, "fixture-task")).not.toThrow();
  });
  it.each(["imessage", "gmail", "administrateme-portal"])("retains %s proof across registration objects, only after an exact final reply", async (channel) => {
    const f = fixture(channel); process.env.ADMINME_MODE_TEST_SECRET = secret;
    const request = vi.fn().mockResolvedValue({ state: "presented" });
    const config = readPluginConfig({ sharedSecretEnv: "ADMINME_MODE_TEST_SECRET" });
    const runtime = { transport: { config, request }, trackedSessions: new Map() } as unknown as PluginRuntime;
    const laterRegistration = { ...runtime, transport: { config, request } } as unknown as PluginRuntime;
    const original = { ...f.input, source_message_event_id: "ev1" };
    const result = { state: "mode_choice_required", presentation_prompt: f.ticket.continuation.offered.prompt, choice_event_id: "ev2", pending_expires_at: f.ticket.continuation.offered.due_at };
    rememberModeOffer(runtime, result, original);
    const terminal = channel === "administrateme-portal" ? undefined : "ev2-sent";
    await recordModePresentation(laterRegistration, original, "fixture-run", result.presentation_prompt + " Another option.", terminal);
    expect(request).not.toHaveBeenCalled();
    if (terminal) {
      await recordModePresentation(laterRegistration, original, "fixture-run", result.presentation_prompt);
      expect(request).not.toHaveBeenCalled();
    }
    await recordModePresentation(laterRegistration, original, "fixture-run", result.presentation_prompt, terminal);
    expect(request).toHaveBeenCalledTimes(1);
    const body = request.mock.calls[0]![1];
    const { provenance, signature, ...fields } = body;
    expect(signature).toBe(createHmac("sha256", secret).update("cos-reminder-presentation-v1|" + canonical({ ...fields, origin: modeOrigin(provenance) })).digest("hex"));
    await recordModePresentation(laterRegistration, original, "fixture-run", result.presentation_prompt, terminal);
    expect(request).toHaveBeenCalledTimes(1);
  });
});
