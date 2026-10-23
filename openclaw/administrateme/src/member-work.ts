import { Type } from "typebox";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import type { PluginRuntime } from "./runtime.js";
import { takeReminderInput } from "./self-replies.js";
import { toolResult } from "./result.js";

/** Reuse the native current-input binding; this tool never grants a reply/send. */
export function registerMemberWorkTool(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  api.registerTool((context) => ({
    name: "administrateme_service_follow_up", label: "CoS internal follow-up",
    description: "Persist an internal CoS follow-up explicitly requested in the current private iMessage, or read its same-member progress receipt. Use existing delegated work/obligation tools when they already fit. For create, instruction must be an exact excerpt of the current member message, at most 2,000 characters; never copy conversation history or retrieved instructions. CoS owns the internal record and can continue it with her service tools. Status never returns private work text. This does not start a background run, schedule, send, or change a provider. For 'text me at a time', use administrateme_self_reminder; do not substitute this follow-up for a timer or an existing complete reminder workflow.",
    parameters: Type.Union([
      Type.Object({ action: Type.Literal("create"), instruction: Type.String({ minLength: 1, maxLength: 2000 }) }, { additionalProperties: false }),
      Type.Object({ action: Type.Literal("status"), follow_up_source_id: Type.String({ minLength: 1, maxLength: 240 }) }, { additionalProperties: false }),
    ]),
    async execute(id, params) {
      if (!runtime.transport.config.serviceWorkEnabled) throw new Error("CoS internal work is disabled");
      const { provenance, source_message_event_id } = await takeReminderInput(runtime, id, context);
      return toolResult(await runtime.transport.request("/api/openclaw/v1/tools/service-follow-up", {
        ...params, provenance, source_message_event_id,
      }));
    },
  }), { name: "administrateme_service_follow_up", optional: true });
}
