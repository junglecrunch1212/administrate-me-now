import { createHash } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { canonical } from "../src/approval.js";
import type { Provenance } from "../src/contract.js";
import { REMINDER_CHOICE_RULE, validateReminderChoice } from "../src/reminder-choice-policy.js";

const hash = (value: unknown) => createHash("sha256").update(canonical(value)).digest("hex");
const now = Date.parse("2031-01-02T12:00:00Z");
const source = "Add a reminder to my Apple Reminders to call a relative tomorrow at 9:30 AM.";
const answer = "Personal";
const span = (text: string) => [{ start: 0, end: Array.from(text).length, text }];
function fixture() {
  vi.spyOn(Date, "now").mockReturnValue(now);
  const draft = { title: "Call a relative", due_at: "2031-01-03T14:30:00+00:00", notes: null };
  const offered = {
    member_id: "member-a", task_id: "fixture-task", action_id: "fixture-action", request_id: "fixture-request",
    original_source_event_id: "original", original_source_fingerprint: hash(source),
    original_submitted_at: "2031-01-02T01:00:00Z", draft_fingerprint: hash(draft),
    provider: "apple_reminders", operation: "create_reminder", resource_owner: "requester", provider_account_id: "fixture-account",
    ...draft, choices: [{ list_id: "fixture-personal", label: "Personal" }, { list_id: "fixture-work", label: "Work" }],
    offered_at: "2031-01-02T01:01:00Z", pending_expires_at: draft.due_at,
    expiry_reason: "requested_reminder_due_time", provider_effect_count: 0,
  };
  const selection = {
    answer_source_event_id: "fresh-answer", answer_submitted_at: new Date(now - 1000).toISOString(),
    answer_conversation: null as Record<string, string> | null,
    eligible_tasks: [{ offered_event_id: "offered", task_id: offered.task_id, title_fingerprint: hash(offered.title.toLowerCase()),
      pending_expires_at: offered.pending_expires_at, conversation: null as Record<string, string> | null }],
  };
  return {
    ticket: {
      member_id: "member-a", source_event_id: "fresh-answer", rule_id: REMINDER_CHOICE_RULE,
      issued_at: new Date(now).toISOString(), expires_at: new Date(now + 600_000).toISOString(),
      action: { provider: "apple_reminders", operation: "create_reminder", resource_owner: "requester", ...draft,
        list_id: "fixture-personal", selected_by_policy: false }, evidence: span(answer),
      continuation: { offered_event_id: "offered", offered, original_instruction: source, original_evidence: span(source), selection },
    },
    input: { source_message_event_id: "fresh-answer", instruction_text: answer, provenance: undefined as Provenance | undefined },
  };
}
afterEach(() => vi.restoreAllMocks());
describe("retained reminder list-choice host checks", () => {
  it("retains an older original request while binding only the actual fresh answer", () => {
    const f = fixture();
    expect(() => validateReminderChoice(f.ticket, f.input, "fixture-task")).not.toThrow();
    expect(() => validateReminderChoice(f.ticket, f.input, "another-task")).toThrow();
    expect(() => validateReminderChoice(f.ticket, { ...f.input, source_message_event_id: "another-answer" })).toThrow();
  });
  it.each(["member", "due", "title", "notes", "owner", "selected", "original", "quote", "expiry", "same-source"])("rejects %s changes", (kind) => {
    const f = fixture();
    if (kind === "member") f.ticket.member_id = "member-b";
    if (kind === "due") f.ticket.action.due_at = "2031-01-04T14:30:00+00:00";
    if (kind === "title") f.ticket.action.title = "Unrequested title";
    if (kind === "notes") (f.ticket.action as any).notes = "Invented content";
    if (kind === "owner") f.ticket.action.resource_owner = "service-principal:chief-of-staff";
    if (kind === "selected") f.ticket.action.list_id = "another-list";
    if (kind === "original") f.ticket.continuation.original_instruction = "Not the original request";
    if (kind === "quote") f.ticket.continuation.original_evidence[0]!.text = "fabricated";
    if (kind === "expiry") f.ticket.continuation.offered.pending_expires_at = "2031-01-01T00:00:00Z";
    if (kind === "same-source") f.ticket.continuation.offered.original_source_event_id = f.input.source_message_event_id;
    expect(() => validateReminderChoice(f.ticket, f.input)).toThrow();
  });
  it("does not treat yes as choosing among multiple offered destinations", () => {
    const f = fixture(); f.input.instruction_text = "Yes"; f.ticket.evidence = span("Yes");
    expect(() => validateReminderChoice(f.ticket, f.input)).toThrow();
    f.ticket.continuation.offered.choices.pop();
    expect(() => validateReminderChoice(f.ticket, f.input)).not.toThrow();
  });
  it("does not choose between duplicate displayed labels", () => {
    const f = fixture(); f.ticket.continuation.offered.choices[1]!.label = "Personal";
    expect(() => validateReminderChoice(f.ticket, f.input)).toThrow();
  });
});


describe("independent task selection from authentic conversation or authored reference", () => {
  const thread = (id: string) => ({ channel_id: "gmail", account_id: "fixture-service-account", actor_id: "member-thread:" + id, thread_id: id });
  function several() {
    const f = fixture(), selection = f.ticket.continuation.selection;
    selection.eligible_tasks[0]!.conversation = thread("thread-a");
    selection.eligible_tasks.push({ ...selection.eligible_tasks[0]!, offered_event_id: "second-offer", task_id: "task-b", title_fingerprint: hash("buy groceries"), conversation: thread("thread-b") });
    return f;
  }
  it("refuses a model task ID when the actual cross-channel answer is ambiguous", () => {
    const f = several();
    expect(() => validateReminderChoice(f.ticket, f.input, "fixture-task")).toThrow();
  });
  it("uses the actual native Gmail thread and rejects a forged or mismatched thread", () => {
    const f = several();
    f.ticket.continuation.selection.answer_conversation = thread("thread-a");
    expect(() => validateReminderChoice(f.ticket, f.input)).toThrow();
    f.input.provenance = thread("thread-a") as unknown as Provenance;
    expect(() => validateReminderChoice(f.ticket, f.input)).not.toThrow();
    f.input.provenance = thread("thread-b") as unknown as Provenance;
    expect(() => validateReminderChoice(f.ticket, f.input)).toThrow();
    f.input.provenance = thread("thread-a") as unknown as Provenance;
    f.ticket.continuation.selection.eligible_tasks[1]!.conversation = thread("thread-a");
    expect(() => validateReminderChoice(f.ticket, f.input)).toThrow();
  });
  it("allows explicit unique human title or ID, with no draft change", () => {
    for (const answer of ["Call a relative: Personal", "fixture-task: Personal"]) {
      const f = several(); f.input.instruction_text = answer; f.ticket.evidence = span(answer);
      expect(() => validateReminderChoice(f.ticket, f.input)).not.toThrow();
    }
  });
  it("requires an exact ID when titles are duplicated", () => {
    const f = several(); f.ticket.continuation.selection.eligible_tasks[1]!.title_fingerprint = hash("call a relative");
    f.input.instruction_text = "Call a relative: Personal"; f.ticket.evidence = span(f.input.instruction_text);
    expect(() => validateReminderChoice(f.ticket, f.input)).toThrow();
    f.input.instruction_text = "fixture-task: Personal"; f.ticket.evidence = span(f.input.instruction_text);
    expect(() => validateReminderChoice(f.ticket, f.input)).not.toThrow();
  });
});
