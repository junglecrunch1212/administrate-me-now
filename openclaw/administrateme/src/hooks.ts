import { prepareRequestedApproval } from "./request-policy.js";
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
import { rememberSelfReply, registerSelfReplies, bindReminderTool } from "./self-replies.js";
import { TransportError } from "./transport.js";
import { provenanceFromServiceTool, SERVICE_PRIVATE_TOOL_NAMES } from "./service-private.js";
import { APPLE_REMINDER_INPUT_TOOLS } from "./apple-reminders.js";

type BridgeMarker = {
  consent?: unknown;
  eventKind?: unknown;
  sensitivity?: unknown;
};

export function registerHooks(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  registerSelfReplies(api, runtime);
  api.on(
    "before_tool_call",
    async (event, context) => {
      if ((SERVICE_PRIVATE_TOOL_NAMES as readonly string[]).includes(event.toolName)) {
        try {
          provenanceFromServiceTool(context, runtime.transport.config.sharedSecretEnv, runtime.transport.config.serviceWorkEnabled);
        } catch {
          return { block: true, blockReason: "Service-private work requires its dedicated internal service session." };
        }
        // Standing service-self authority covers only private internal state.
        // All member and provider actions retain their independent approvals.
        return;
      }
      // Generic delivery tools cannot borrow CoS account authority. The
      // final-answer hook handles explicitly authorized self replies. Other
      // sends use the exact approval tool; the native wrapper guards both.
      if (runtime.transport.config.forwardIntakeAccounts.length > 0 &&
          (event.toolName === "message" || event.toolName === "sessions_send") &&
          (event.params.channel === undefined ||
           event.params.channel === "imessage" || event.params.channel === "bluebubbles")) {
        return { block: true, blockReason: "Reply normally for an authorized private self reply; other sends require exact AdministrateMe approval." };
      }
      if (event.toolName === "administrateme_service_follow_up" && !runtime.transport.config.serviceWorkEnabled) {
        return { block: true, blockReason: "CoS internal work is disabled." };
      }
      if (event.toolName === "administrateme_self_reminder" || event.toolName === "administrateme_service_follow_up" ||
          (APPLE_REMINDER_INPUT_TOOLS as readonly string[]).includes(event.toolName)) {
        if (!bindReminderTool(runtime, {
          runId: typeof context.runId === "string" ? context.runId : undefined,
          sessionKey: typeof context.sessionKey === "string" ? context.sessionKey : undefined,
          agentId: typeof context.agentId === "string" ? context.agentId : undefined,
        }, typeof context.toolCallId === "string" ? context.toolCallId : event.toolCallId)) {
          return { block: true, blockReason: "This action requires this turn's acknowledged private member request." };
        }
        return;
      }
      if (!SIDE_EFFECT_TOOL_NAMES.has(event.toolName)) return;
      if (event.toolName === "administrateme_review" && event.params.action === "inspect") return;
      if (event.toolName === "administrateme_policy_proposal" && event.params.action === "inspect") {
        return;
      }
      try {
        const requested = await prepareRequestedApproval(runtime, event.toolName, event.params, {
          runId: context.runId, sessionKey: context.sessionKey, agentId: context.agentId,
          toolCallId: context.toolCallId ?? event.toolCallId,
        });
        if (requested) return { params: requested };
      } catch {
        return { block: true, blockReason: "The exact current request could not authorize this action. Check its receipt; do not create a new-ID retry." };
      }
      const approval = prepareApproval(runtime, event.toolName, event.params);
      if (event.toolName === "administrateme_cos_response" ||
          event.toolName === "administrateme_execute_pending_provider_action") {
        runtime.cosApprovals?.remember(event.params, {
          ...context, toolCallId: context.toolCallId ?? event.toolCallId,
        }, approval.receiptId, event.toolName);
      }
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
    "message_sending",
    (_event, context) => {
      // Synchronous cancellation cannot time out on our policy server. Missing
      // account coordinates also cancel when this transport hosts a CoS inbox.
      // OpenClaw has direct-send paths outside this hook, so it is deliberately
      // only defense in depth; activation additionally requires the CLI guard.
      if (runtime.transport.config.forwardIntakeAccounts.some((binding) =>
        binding.channelId === context.channelId &&
        (context.accountId === undefined || binding.accountId === context.accountId),
      )) {
        return { cancel: true, cancelReason: "CoS replies require exact governed delivery." };
      }
      return;
    },
    { priority: 1000 },
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
          const sensitivityDeclarations = [marker?.sensitivity, event.metadata?.sensitivity];
          if (sensitivityDeclarations.some((value) => value !== undefined && value !== "normal" && value !== "sensitive")) {
            throw new Error("CoS intake cannot accept privileged or unknown sensitivity");
          }
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
            sensitivity: sensitivityDeclarations.includes("sensitive") ? "sensitive" : "normal",
          };
          const submitted = (async () => {
            for (let attempt = 0; ; attempt += 1) {
              try {
                const receipt = await runtime.transport.request<{ intent_event_id?: string }>("/api/openclaw/v1/submissions/forward", request);
                return receipt.intent_event_id;
              } catch (error) {
                if (attempt >= 2 || !(error instanceof TransportError) ||
                    (error.status !== undefined && error.status < 500)) throw error;
              }
            }
          })();
          // Capture the trusted native message before awaiting intake: this hook
          // is fire-and-forget in the pinned host, and another turn can be queued.
          rememberSelfReply(runtime, provenance, event.runId ?? context.runId, submitted, event.content);
          await submitted;
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
