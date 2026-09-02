import { Type } from "typebox";
import type {
  AnyAgentTool,
  OpenClawPluginApi,
  OpenClawPluginToolContext,
} from "openclaw/plugin-sdk/plugin-entry";
import { provenanceFromTool } from "./provenance.js";
import { toolResult } from "./result.js";
import { trackSession, type PluginRuntime } from "./runtime.js";

const ItemKind = Type.Union([Type.Literal("task"), Type.Literal("commitment")]);
const LifecycleAction = Type.Union([
  Type.Literal("complete"),
  Type.Literal("defer"),
  Type.Literal("snooze"),
  Type.Literal("block"),
  Type.Literal("unblock"),
]);
const ServiceContextScope = Type.Union([
  Type.Literal("member_private"),
  Type.Literal("household_shared"),
]);
const ServiceAssignmentRole = Type.Union([
  Type.Literal("manages_member_obligation"),
  Type.Literal("manages_household_obligation"),
  Type.Literal("delegatee"),
  Type.Literal("internal"),
]);
const ServiceWorkType = Type.Union([
  Type.Literal("operational_task"),
  Type.Literal("waiting_for"),
  Type.Literal("follow_up"),
  Type.Literal("clarification"),
  Type.Literal("exception"),
  Type.Literal("preparation"),
  Type.Literal("held_action"),
  Type.Literal("provider_failure"),
  Type.Literal("reconciliation"),
  Type.Literal("operational_calendar"),
  Type.Literal("reminder_like"),
]);
const DelegatedActionClass = Type.Union([
  Type.Literal("google_calendar_create"),
  Type.Literal("google_calendar_update"),
  Type.Literal("google_calendar_move"),
  Type.Literal("google_calendar_withdraw"),
  Type.Literal("google_calendar_recurrence"),
  Type.Literal("google_tasks_create"),
  Type.Literal("google_tasks_update"),
  Type.Literal("google_tasks_complete"),
  Type.Literal("google_tasks_reopen"),
  Type.Literal("google_tasks_withdraw"),
  Type.Literal("google_tasks_list_create"),
  Type.Literal("google_tasks_list_update"),
  Type.Literal("google_tasks_list_withdraw"),
  Type.Literal("google_tasks_reminder_create"),
  Type.Literal("google_tasks_reminder_update"),
  Type.Literal("google_tasks_reminder_complete"),
  Type.Literal("google_tasks_reminder_reopen"),
  Type.Literal("google_tasks_reminder_withdraw"),
  Type.Literal("apple_calendar_create"),
  Type.Literal("apple_calendar_update"),
  Type.Literal("apple_calendar_move"),
  Type.Literal("apple_calendar_withdraw"),
  Type.Literal("apple_calendar_recurrence"),
  Type.Literal("apple_reminders_create"),
  Type.Literal("apple_reminders_update"),
  Type.Literal("apple_reminders_complete"),
  Type.Literal("apple_reminders_reopen"),
  Type.Literal("apple_reminders_withdraw"),
  Type.Literal("communication_draft"),
  Type.Literal("communication_hold"),
]);
const DelegatedRiskClass = Type.Union([
  Type.Literal("ordinary"),
  Type.Literal("money"),
  Type.Literal("nonrefundable_travel"),
  Type.Literal("legal_signature"),
  Type.Literal("clinical_decision"),
  Type.Literal("child_consent"),
  Type.Literal("life_safety_system"),
  Type.Literal("public_statement"),
  Type.Literal("another_adult_source"),
  Type.Literal("infrastructure_administration"),
]);
const RecurrenceStrategy = Type.Union([
  Type.Literal("none"),
  Type.Literal("provider_series"),
  Type.Literal("individual_occurrence"),
]);
const RepresentationKind = Type.Union([
  Type.Literal("provider_object"),
  Type.Literal("cos_operational"),
  Type.Literal("member_delegated"),
]);

function makeTool(
  context: OpenClawPluginToolContext,
  runtime: PluginRuntime,
  definition: {
    name: string;
    description: string;
    parameters: unknown;
    path: string | ((params: Record<string, unknown>) => string);
    pathParam?: string;
    standingOrder?: boolean;
  },
): AnyAgentTool {
  return {
    name: definition.name,
    label: definition.name,
    description: definition.description,
    parameters: definition.parameters,
    async execute(id, params) {
      const provenance = provenanceFromTool(
        context,
        definition.standingOrder ? "standing_order" : "tool",
        runtime.transport.config.sharedSecretEnv,
      );
      trackSession(runtime, provenance);
      const typedParams = params as Record<string, unknown>;
      const invocationParams: Record<string, unknown> = definition.standingOrder
        ? { ...typedParams, trigger_id: id }
        : { ...typedParams };
      const path =
        typeof definition.path === "function"
          ? definition.path(invocationParams)
          : definition.path;
      if (definition.pathParam) delete invocationParams[definition.pathParam];
      const result = await runtime.transport.request(path, {
        ...invocationParams,
        provenance,
      });
      return toolResult(result);
    },
  };
}

export function registerTools(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  const definitions = [
    {
      name: "administrateme_state",
      description: "Read the authorized member's AdministrateMe state, Triage queue, and commitments.",
      parameters: Type.Object({}, { additionalProperties: false }),
      path: "/api/openclaw/v1/tools/state",
    },
    {
      name: "administrateme_what_now",
      description: "Read the authorized member's deterministic What Now ranking.",
      parameters: Type.Object({}, { additionalProperties: false }),
      path: "/api/openclaw/v1/tools/what-now",
    },
    {
      name: "administrateme_add_for_review",
      description:
        "Save exact member text in Triage for deterministic interpretation and human confirmation.",
      parameters: Type.Object(
        {
          request_id: Type.String({ minLength: 8, maxLength: 120 }),
          text: Type.String({ minLength: 1, maxLength: 2000 }),
          kind_hint: Type.Optional(
            Type.Union([
              Type.Literal("task"),
              Type.Literal("commitment"),
              Type.Literal("calendar_event"),
              Type.Literal("reminder"),
              Type.Literal("list"),
              Type.Literal("list_item"),
              Type.Literal("recurrence"),
              Type.Literal("party_identifier"),
              Type.Literal("party_candidate"),
              Type.Literal("party_relationship"),
              Type.Literal("relationship_fact"),
            ]),
          ),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/add-for-review",
    },
    {
      name: "administrateme_review",
      description: "Inspect or decide an authorized communication commitment proposal.",
      parameters: Type.Object(
        {
          request_id: Type.String({ minLength: 1 }),
          action: Type.Union([
            Type.Literal("inspect"),
            Type.Literal("approve"),
            Type.Literal("dismiss"),
            Type.Literal("edit"),
          ]),
          commitment_id: Type.Optional(Type.String({ minLength: 1 })),
          expected_last_event_id: Type.Optional(Type.String({ minLength: 1 })),
          description: Type.Optional(Type.String({ minLength: 1, maxLength: 500 })),
          due_at: Type.Optional(Type.String()),
          reason: Type.Optional(Type.String({ maxLength: 500 })),
          send_acknowledgement: Type.Optional(Type.Boolean()),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/review",
    },
    {
      name: "administrateme_lifecycle",
      description: "Complete, defer, snooze, block, or unblock one authorized item.",
      parameters: Type.Object(
        {
          request_id: Type.String({ minLength: 1 }),
          item_kind: ItemKind,
          item_id: Type.String({ minLength: 1 }),
          action: LifecycleAction,
          expected_last_event_id: Type.String({ minLength: 1 }),
          until: Type.Optional(Type.String()),
          reason: Type.Optional(Type.String({ maxLength: 500 })),
          note: Type.Optional(Type.String({ maxLength: 500 })),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/lifecycle",
    },
    {
      name: "administrateme_correct",
      description: "Correct allowlisted fields on one authorized task or commitment.",
      parameters: Type.Object(
        {
          request_id: Type.String({ minLength: 1 }),
          item_kind: ItemKind,
          item_id: Type.String({ minLength: 1 }),
          expected_last_event_id: Type.String({ minLength: 1 }),
          fields: Type.Record(Type.String(), Type.Unknown()),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/correct",
    },
    {
      name: "administrateme_service_work",
      description:
        "Create exact-grant CoS-owned or CoS-managed work without invoking a provider or scheduler.",
      parameters: Type.Object(
        {
          request_id: Type.String({ minLength: 8, maxLength: 120 }),
          context_scope: Type.Optional(ServiceContextScope),
          service_assignment_role: Type.Optional(ServiceAssignmentRole),
          service_work_type: Type.Optional(ServiceWorkType),
          title: Type.String({ minLength: 1, maxLength: 500 }),
          description: Type.Optional(Type.String({ maxLength: 2000 })),
          due: Type.Optional(Type.String()),
          scheduled_start_at: Type.Optional(Type.String()),
          scheduled_end_at: Type.Optional(Type.String()),
          follow_up_at: Type.Optional(Type.String()),
          waiting_on_party_id: Type.Optional(Type.String({ minLength: 1, maxLength: 200 })),
          linked_item_kind: Type.Optional(
            Type.Union([
              Type.Literal("task"),
              Type.Literal("commitment"),
              Type.Literal("calendar_event"),
              Type.Literal("list_item"),
              Type.Literal("provider_failure"),
              Type.Literal("coordination"),
            ]),
          ),
          linked_item_id: Type.Optional(Type.String({ minLength: 1, maxLength: 200 })),
          coordination_id: Type.Optional(Type.String({ minLength: 1, maxLength: 200 })),
          source_event_id: Type.Optional(Type.String({ minLength: 1, maxLength: 240 })),
          recurrence_rrule: Type.Optional(Type.String({ minLength: 1, maxLength: 500 })),
          recurrence_next_occurrence: Type.Optional(Type.String()),
          provider_representation_owner_kind: Type.Optional(
            Type.Union([
              Type.Literal("none"),
              Type.Literal("member"),
              Type.Literal("service"),
            ]),
          ),
          provider_id: Type.Optional(Type.String({ minLength: 1, maxLength: 120 })),
          provider_account_reference: Type.Optional(
            Type.String({ minLength: 1, maxLength: 300 }),
          ),
          provider_workspace_reference: Type.Optional(
            Type.String({ minLength: 1, maxLength: 300 }),
          ),
          provider_external_reference_digest: Type.Optional(
            Type.String({ pattern: "^[0-9a-f]{64}$" }),
          ),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/service-work",
    },
    {
      name: "administrateme_service_work_state",
      description:
        "Advance one exact-grant CoS work item without impersonating a member or calling a provider.",
      parameters: Type.Object(
        {
          task_id: Type.String({ minLength: 1, maxLength: 240 }),
          request_id: Type.String({ minLength: 8, maxLength: 120 }),
          context_scope: Type.Optional(ServiceContextScope),
          expected_last_event_id: Type.String({ minLength: 1, maxLength: 240 }),
          action: Type.Union([
            Type.Literal("start"),
            Type.Literal("wait_on_member"),
            Type.Literal("wait_on_other"),
            Type.Literal("follow_up"),
            Type.Literal("hold"),
            Type.Literal("resume"),
            Type.Literal("complete"),
            Type.Literal("cancel"),
          ]),
          waiting_on_member_id: Type.Optional(
            Type.String({ minLength: 1, maxLength: 200 }),
          ),
          waiting_on_party_id: Type.Optional(
            Type.String({ minLength: 1, maxLength: 200 }),
          ),
          follow_up_at: Type.Optional(Type.String()),
          reason_code: Type.Union([
            Type.Literal("member_input"),
            Type.Literal("external_dependency"),
            Type.Literal("clarification"),
            Type.Literal("provider_failure"),
            Type.Literal("reconciliation"),
            Type.Literal("scheduled_hold"),
            Type.Literal("completed"),
            Type.Literal("cancelled"),
            Type.Literal("other"),
          ]),
        },
        { additionalProperties: false },
      ),
      path: (params: Record<string, unknown>) =>
        `/api/openclaw/v1/tools/service-work/${encodeURIComponent(String(params.task_id))}/state`,
      pathParam: "task_id",
    },
    {
      name: "administrateme_prepare_delegated_action",
      description:
        "Prepare or hold one exact fixture-only provider representation under the active CoS grant.",
      parameters: Type.Object(
        {
          request_id: Type.String({ minLength: 8, maxLength: 120 }),
          context_scope: Type.Optional(ServiceContextScope),
          action_class: DelegatedActionClass,
          canonical_object_kind: Type.String({ minLength: 1, maxLength: 80 }),
          canonical_object_id: Type.String({ minLength: 1, maxLength: 240 }),
          occurrence_id: Type.Optional(Type.String({ minLength: 1, maxLength: 240 })),
          series_id: Type.Optional(Type.String({ minLength: 1, maxLength: 240 })),
          recurrence_strategy: Type.Optional(RecurrenceStrategy),
          coordination_id: Type.Optional(Type.String({ minLength: 1, maxLength: 240 })),
          desired_representation_id: Type.String({ minLength: 1, maxLength: 240 }),
          operator_kind: Type.Optional(
            Type.Union([
              Type.Literal("openclaw_automation"),
              Type.Literal("simulated_human"),
            ]),
          ),
          provider_account_id: Type.String({ minLength: 1, maxLength: 500 }),
          provider_account_owner_principal_id: Type.String({ minLength: 1, maxLength: 200 }),
          destination_id: Type.String({ minLength: 1, maxLength: 500 }),
          audience_ids: Type.Array(Type.String({ minLength: 1, maxLength: 200 }), {
            minItems: 1,
            maxItems: 20,
          }),
          representation_kind: Type.Optional(RepresentationKind),
          target_external_id: Type.Optional(Type.String({ maxLength: 500 })),
          expected_provider_revision: Type.Optional(Type.String({ maxLength: 500 })),
          desired_payload: Type.Record(Type.String(), Type.Unknown()),
          fact_event_ids: Type.Array(Type.String({ minLength: 1, maxLength: 240 }), {
            minItems: 1,
            maxItems: 100,
          }),
          facts_observed_at: Type.String({ minLength: 1 }),
          facts_valid_until: Type.String({ minLength: 1 }),
          risk_class: Type.Optional(DelegatedRiskClass),
          execution_mode: Type.Optional(
            Type.Union([
              Type.Literal("execute"),
              Type.Literal("desired_only"),
              Type.Literal("shadow"),
            ]),
          ),
          expires_at: Type.String({ minLength: 1 }),
          expected_policy_digest: Type.String({ pattern: "^[0-9a-f]{64}$" }),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/delegated-actions/prepare",
    },
    {
      name: "administrateme_execute_delegated_action",
      description:
        "Execute one exact domain-approved fixture action after a separate OpenClaw allow-once approval.",
      parameters: Type.Object(
        {
          request_id: Type.String({ minLength: 8, maxLength: 120 }),
          context_scope: Type.Optional(ServiceContextScope),
          action_id: Type.String({ minLength: 1, maxLength: 240 }),
          expected_last_event_id: Type.String({ minLength: 1, maxLength: 240 }),
          expected_action_fingerprint: Type.String({ pattern: "^[0-9a-f]{64}$" }),
          expected_policy_digest: Type.String({ pattern: "^[0-9a-f]{64}$" }),
          operator_kind: Type.Optional(
            Type.Union([
              Type.Literal("openclaw_automation"),
              Type.Literal("simulated_human"),
            ]),
          ),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/delegated-actions/execute",
    },
    {
      name: "administrateme_observe_delegated_action",
      description:
        "Record a later fixture-provider observation and reconcile it to the exact desired representation.",
      parameters: Type.Object(
        {
          request_id: Type.String({ minLength: 8, maxLength: 120 }),
          context_scope: Type.Optional(ServiceContextScope),
          action_id: Type.String({ minLength: 1, maxLength: 240 }),
          expected_last_event_id: Type.String({ minLength: 1, maxLength: 240 }),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/delegated-actions/observe",
    },
    {
      name: "administrateme_execute_pending_provider_action",
      description:
        "Execute one member-confirmed provider action after this separate OpenClaw allow-once approval.",
      parameters: Type.Object(
        {
          domain_approval_event_id: Type.String({ minLength: 1, maxLength: 240 }),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/execute-pending-provider-action",
    },
    {
      name: "administrateme_skill",
      description: "Run one Phase 3 AdministrateMe skill through OpenClaw-owned model routing.",
      parameters: Type.Object(
        {
          skill_id: Type.String({ minLength: 1 }),
          inputs: Type.Record(Type.String(), Type.Unknown()),
          input_sensitivities: Type.Optional(
            Type.Record(
              Type.String(),
              Type.Union([
                Type.Literal("normal"),
                Type.Literal("sensitive"),
                Type.Literal("privileged"),
              ]),
            ),
          ),
          triggering_event_id: Type.Optional(Type.String()),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/skill",
    },
    {
      name: "administrateme_notification_result",
      description: "Record the typed outcome of an OpenClaw-owned notification attempt.",
      parameters: Type.Object(
        {
          attempt_id: Type.String({ minLength: 1, maxLength: 240 }),
          decision_id: Type.String({ minLength: 1, maxLength: 240 }),
          signal_id: Type.String({ minLength: 1, maxLength: 240 }),
          outcome: Type.Union([
            Type.Literal("delivered"),
            Type.Literal("failed"),
            Type.Literal("retrying"),
          ]),
          attempted_at: Type.String({ minLength: 1 }),
          provider_receipt_id: Type.Optional(Type.String({ maxLength: 500 })),
          error_code: Type.Optional(Type.String({ maxLength: 120 })),
          next_retry_at: Type.Optional(Type.String()),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/notifications/result",
    },
    {
      name: "administrateme_standing_order",
      description: "Record one allowlisted standing-order trigger for the authorized direct route.",
      parameters: Type.Object(
        {
          program_id: Type.String({ minLength: 1 }),
          automation_id: Type.String({ minLength: 1 }),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/standing-orders/trigger",
      standingOrder: true,
    },
  ];

  for (const definition of definitions) {
    api.registerTool((context) => makeTool(context, runtime, definition), {
      name: definition.name,
      optional: !["administrateme_state", "administrateme_what_now"].includes(definition.name),
    });
  }
}
