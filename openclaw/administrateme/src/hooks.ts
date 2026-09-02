import type {
  OpenClawPluginApi,
  PluginHookAgentContext,
  PluginHookMessageContext,
  PluginHookMessageReceivedEvent,
  PluginHookModelCallEvent,
} from "openclaw/plugin-sdk/plugin-entry";
import { PLUGIN_ID, SIDE_EFFECT_TOOL_NAMES, type BridgeEventKind } from "./contract.js";
import { provenanceFromMessage } from "./provenance.js";
import { prepareApproval } from "./approval.js";
import type { PluginRuntime } from "./runtime.js";

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
      if (!config.bridgeEnabled || !config.bridgeChannels.includes(context.channelId)) return;
      const marker = event.metadata?.administrateme as BridgeMarker | undefined;
      if (marker?.consent !== true || typeof marker.eventKind !== "string") return;
      if (!config.bridgeEventKinds.includes(marker.eventKind as BridgeEventKind)) return;
      if (marker.sensitivity !== "normal" && marker.sensitivity !== "sensitive") return;
      try {
        const provenance = provenanceFromMessage(event, context);
        await runtime.transport.request("/api/openclaw/v1/bridge/events", {
          provenance,
          event_kind: marker.eventKind,
          content: event.content,
          consent: true,
          sensitivity: marker.sensitivity,
        });
      } catch {
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
