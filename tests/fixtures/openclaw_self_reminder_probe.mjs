// Hosted-only fixed tool used to exercise the production timer and callback.
import { readFile, writeFile } from "node:fs/promises";
import { confirmReminderTimer, registerSelfReminderTools } from "./self-reminders.js";
import { toolResult } from "./result.js";

const marker = process.env.ADMINME_REMINDER_PROBE_MARKER;
const reminder = "01JREMINDERSYNTHETIC";
export default {
  id: "administrateme",
  register(api) {
    const runtime = { transport: {
      config: { forwardIntakeAccounts: [], scheduledSelfReminderBindings: [] },
      async request(endpoint, body) {
        if (!endpoint.endsWith("/fire") || body.reminder_event_id !== reminder) throw new Error("Unexpected probe request");
        const saved = JSON.parse(await readFile(marker, "utf8"));
        if (body.job_id !== saved.job_id || body.session_key !== `agent:${body.agent_id}:cron:${saved.job_id}:trigger`) throw new Error("Native timer context mismatch");
        await writeFile(marker, JSON.stringify({ ...saved, callback_count: saved.callback_count + 1, session_verified: true }), { mode: 0o600 });
        // Simulates an already-terminal domain receipt; never constructs a provider.
        return { status: "sent" };
      },
    } };
    registerSelfReminderTools(api, runtime);
    api.registerTool((context) => ({
      name: "administrateme_probe_schedule", label: "Isolated timer probe",
      description: "Hosted fixture only", parameters: { type: "object", properties: {}, additionalProperties: false },
      async execute() {
        const due = new Date(Date.now() + 70_000).toISOString();
        await confirmReminderTimer({ reminder_event_id: reminder, due_at: due, job_id: null }, context.agentId,
          async (jobId) => writeFile(marker, JSON.stringify({ job_id: jobId, due_at: due, callback_count: 0 }), { mode: 0o600 }));
        return toolResult({ status: "scheduled" });
      },
    }), { name: "administrateme_probe_schedule", optional: true });
  },
};
