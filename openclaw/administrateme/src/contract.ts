import { FINANCE_ADVISOR_AVAILABLE, FINANCE_ADVISOR_TOOL_NAMES } from "./finance-release.js";
export const OPENCLAW_VERSION = "2026.10.10" as const;
export const OPENCLAW_COMMIT = "7dc3350cb9f08fcf9a0af5c4845a20d5b2f1a8be" as const;
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

const DECLARED_TOOL_NAMES = [
  "administrateme_finance_bindings",
  "administrateme_finance_status",
  "administrateme_finance_query",
  "administrateme_finance_calculate",
  "administrateme_finance_job",
  "administrateme_state",
  "administrateme_calendar",
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
  "administrateme_confirm_cos_apple_reminder",
  "administrateme_cos_apple_reminder_status",
  "administrateme_confirm_requested_email",
  "administrateme_requested_email_status",
  "administrateme_self_reminder",
  "administrateme_self_reminder_fire",
  "administrateme_service_private_work",
  "administrateme_service_private_work_state",
  "administrateme_service_private_state",
  "administrateme_service_context",
  "administrateme_workstation_inventory",
  "administrateme_workstation_work",
] as const;

export const TOOL_NAMES = DECLARED_TOOL_NAMES.filter((name) => FINANCE_ADVISOR_AVAILABLE || !FINANCE_ADVISOR_TOOL_NAMES.has(name));

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
