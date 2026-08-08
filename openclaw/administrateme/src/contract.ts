export const OPENCLAW_VERSION = "2026.7.2" as const;
export const OPENCLAW_COMMIT = "ce67ffb70e83e5ebb56ea02e814e577153f84f61" as const;
export const PLUGIN_ID = "administrateme" as const;

export const BRIDGE_EVENT_KINDS = [
  "commitment_candidate",
  "household_action",
  "delivery_outcome",
] as const;

export type BridgeEventKind = (typeof BRIDGE_EVENT_KINDS)[number];
export type CallKind = "tool" | "dashboard" | "slash_command" | "standing_order" | "bridge";

export type Provenance = {
  actor_id: string;
  agent_id?: string;
  session_key?: string;
  session_id?: string;
  channel_id: string;
  account_id?: string;
  message_id?: string;
  thread_id?: string;
  correlation_id: string;
  call_kind: CallKind;
  openclaw_version: typeof OPENCLAW_VERSION;
  openclaw_commit: typeof OPENCLAW_COMMIT;
};

export type PluginConfig = {
  baseUrl: string;
  sharedSecretEnv: string;
  timeoutMs: number;
  bridgeEnabled: boolean;
  bridgeChannels: readonly string[];
  bridgeEventKinds: readonly BridgeEventKind[];
  modelSessionPrefix: string;
};

export type IntegrationState = "healthy" | "degraded" | "unavailable" | "incompatible";

export const TOOL_NAMES = [
  "administrateme_state",
  "administrateme_what_now",
  "administrateme_add_for_review",
  "administrateme_review",
  "administrateme_lifecycle",
  "administrateme_correct",
  "administrateme_execute_pending_provider_action",
  "administrateme_skill",
  "administrateme_notification_result",
  "administrateme_standing_order",
] as const;

export const SIDE_EFFECT_TOOL_NAMES = new Set<string>([
  "administrateme_add_for_review",
  "administrateme_review",
  "administrateme_lifecycle",
  "administrateme_correct",
  "administrateme_execute_pending_provider_action",
  "administrateme_skill",
  "administrateme_standing_order",
]);
