import type {
  OpenClawPluginApi,
  PluginHookAgentContext,
  PluginHookMessageContext,
  PluginHookMessageReceivedEvent,
  PluginHookModelCallEvent,
} from "openclaw/plugin-sdk/plugin-entry";
import { createHash } from "node:crypto";
import { PLUGIN_ID, SIDE_EFFECT_TOOL_NAMES, type BridgeEventKind } from "./contract.js";
import { provenanceFromMessage } from "./provenance.js";
import { prepareApproval } from "./approval.js";
import type { PluginRuntime } from "./runtime.js";
import { TransportError } from "./transport.js";

type BridgeMarker = {
  consent?: unknown;
  eventKind?: unknown;
  sensitivity?: unknown;
};

export function registerHooks(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  api.on(
    "before_tool_call",
    async (event) => {
      if (!SIDE_EFFECT_TOOL_NAMES.has(event.toolName)) return;
      if (event.toolName === "administrateme_review" && event.params.action === "inspect") return;
      if (event.toolName === "administrateme_policy_proposal" && event.params.action === "inspect") {
        return;
      }
      const approval = prepareApproval(runtime, event.toolName, event.params);
      return {
        params: approval.params,
        requireApproval: {
          title: "Run AdministrateMe action",
          description: `Allow one ${event.toolName} call. AdministrateMe will apply its own household authorization separately.`,
          severity: "warning",
          allowedDecisions: ["allow-once", "deny"],
          timeoutMs: 120_000,
          pluginId: PLUGIN_ID,
          onResolution: approval.resolve,
        },
      };
    },
    { priority: 50 },
  );

  api.on(
    "message_received",
    async (event: PluginHookMessageReceivedEvent, context: PluginHookMessageContext) => {
      const config = runtime.transport.config;
      const marker = event.metadata?.administrateme as BridgeMarker | undefined;
      const dedicatedIntake = config.forwardIntakeAccounts.some((binding) =>
        binding.channelId === context.channelId && binding.accountId === context.accountId,
      );
      const explicitBridge = config.bridgeEnabled && config.bridgeChannels.includes(context.channelId) &&
        marker?.consent === true && typeof marker.eventKind === "string" &&
        config.bridgeEventKinds.includes(marker.eventKind as BridgeEventKind) &&
        (marker.sensitivity === "normal" || marker.sensitivity === "sensitive");
      if (!dedicatedIntake && !explicitBridge) return;
      const submission = dedicatedIntake || marker?.eventKind === "member_submission";
      try {
        const provenance = provenanceFromMessage(event, context);
        if (submission) {
          if (!provenance.message_id || !event.content.trim() || event.content.length > 32_000) {
            throw new Error("CoS intake requires a bounded text selection and native message identity");
          }
          const attachments = event.metadata?.attachments;
          const attachmentCount = Array.isArray(attachments) ? attachments.length :
            (typeof event.metadata?.attachmentCount === "number" ? event.metadata.attachmentCount : 0);
          const request = {
            provenance,
            request_id: createHash("sha256").update(JSON.stringify([
              provenance.channel_id, provenance.account_id, provenance.actor_id, provenance.message_id,
            ])).digest("hex"),
            text: event.content,
            consent: true,
            submission_gesture: dedicatedIntake ? "dedicated_intake" : "explicit",
            unsupported_attachment_count: attachmentCount,
            sensitivity: marker?.sensitivity === "sensitive" ? "sensitive" : "normal",
          };
          for (let attempt = 0; ; attempt += 1) {
            try {
              await runtime.transport.request("/api/openclaw/v1/submissions/forward", request);
              break;
            } catch (error) {
              if (attempt >= 2 || !(error instanceof TransportError) ||
                  (error.status !== undefined && error.status < 500)) throw error;
            }
          }
          return;
        }
        await runtime.transport.request("/api/openclaw/v1/bridge/events", {
          provenance,
          event_kind: marker?.eventKind,
          content: event.content,
          consent: true,
          sensitivity: marker?.sensitivity,
        });
      } catch (error) {
        if (submission) {
          api.logger.error("AdministrateMe forward was not acknowledged; retry the same source receipt after restoring intake.");
          throw error;
        }
        // Observation hooks must not break normal OpenClaw message delivery.
      }
    },
  );

  const modelCall =
    (phase: "started" | "ended") =>
    async (event: PluginHookModelCallEvent, context: PluginHookAgentContext): Promise<void> => {
      const sessionKey = event.sessionKey ?? context.sessionKey;
      if (!sessionKey || !sessionKey.startsWith(runtime.transport.config.modelSessionPrefix)) return;
      const provenance = runtime.trackedSessions.get(sessionKey);
      if (!provenance) return;
      try {
        await runtime.transport.request("/api/openclaw/v1/model-calls", {
          provenance,
          phase,
          run_id: event.runId,
          provider: event.provider,
          model: event.model,
        });
      } catch {
        // Metadata observation is best-effort and never captures prompt/output content.
      }
    };
  api.on("model_call_started", modelCall("started"));
  api.on("model_call_ended", modelCall("ended"));
}
