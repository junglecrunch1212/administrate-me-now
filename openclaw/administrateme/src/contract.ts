export const OPENCLAW_VERSION = "2026.7.2" as const;
export const OPENCLAW_COMMIT = "ce67ffb70e83e5ebb56ea02e814e577153f84f61" as const;
export const PLUGIN_ID = "administrateme" as const;

export const BRIDGE_EVENT_KINDS = [
  "commitment_candidate",
  "household_action",
  "delivery_outcome",
  "member_submission",
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
  serviceWorkEnabled: boolean;
  bridgeChannels: readonly string[];
  bridgeEventKinds: readonly BridgeEventKind[];
  forwardIntakeAccounts: readonly { channelId: "imessage" | "bluebubbles"; accountId: string }[];
  modelSessionPrefix: string;
  dmScope: "per_account_channel_peer";
  standingSelfReplyBindings: readonly string[];
  scheduledSelfReminderBindings: readonly string[];
  memberAgentIds: readonly string[];
  bindingFingerprints: readonly string[];
  uniqueAgents: boolean;
  uniqueAccounts: boolean;
};

export type IntegrationState = "healthy" | "degraded" | "unavailable" | "incompatible";

export const TOOL_NAMES = [
  "administrateme_state",
  "administrateme_what_now",
  "administrateme_capabilities",
  "administrateme_settings_context",
  "administrateme_capability_request",
  "administrateme_policy_proposal",
  "administrateme_add_for_review",
  "administrateme_review",
  "administrateme_lifecycle",
  "administrateme_correct",
  "administrateme_service_work",
  "administrateme_service_work_state",
  "administrateme_prepare_delegated_action",
  "administrateme_execute_delegated_action",
  "administrateme_observe_delegated_action",
  "administrateme_execute_pending_provider_action",
  "administrateme_prepare_google_tasks_create_once",
  "administrateme_execute_google_tasks_create_once",
  "administrateme_google_tasks_create_state",
  "administrateme_skill",
  "administrateme_notification_result",
  "administrateme_cos_response",
  "administrateme_standing_order",
  "administrateme_service_follow_up",
  "administrateme_confirm_apple_reminder",
  "administrateme_apple_reminder_status",
  "administrateme_self_reminder",
  "administrateme_self_reminder_fire",
  "administrateme_service_private_work",
  "administrateme_service_private_work_state",
  "administrateme_service_private_state",
  "administrateme_service_context",
  "administrateme_workstation_inventory",
] as const;

export const SIDE_EFFECT_TOOL_NAMES = new Set<string>([
  "administrateme_add_for_review",
  "administrateme_review",
  "administrateme_capability_request",
  "administrateme_policy_proposal",
  "administrateme_lifecycle",
  "administrateme_correct",
  "administrateme_service_work",
  "administrateme_service_work_state",
  "administrateme_prepare_delegated_action",
  "administrateme_execute_delegated_action",
  "administrateme_observe_delegated_action",
  "administrateme_prepare_google_tasks_create_once",
  "administrateme_execute_google_tasks_create_once",
  "administrateme_execute_pending_provider_action",
  "administrateme_skill",
  "administrateme_cos_response",
  "administrateme_standing_order",
]);
