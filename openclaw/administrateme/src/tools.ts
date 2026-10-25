import { requestExecutionProvenance } from "./request-policy.js";
import { Type } from "typebox";
import type {
  AnyAgentTool,
  OpenClawPluginApi,
  OpenClawPluginToolContext,
} from "openclaw/plugin-sdk/plugin-entry";
import { provenanceFromTool } from "./provenance.js";
import { toolResult } from "./result.js";
import { assertToolInput } from "./tool-input.js";
import { trackSession, type PluginRuntime } from "./runtime.js";
import { waitForApprovalPublication } from "./approval.js";

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
  api: OpenClawPluginApi,
  context: OpenClawPluginToolContext,
  runtime: PluginRuntime,
  definition: {
    name: string;
    description: string;
    parameters: AnyAgentTool["parameters"];
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
      assertToolInput(params);
      const provenance = requestExecutionProvenance(runtime, id, params, context) ?? provenanceFromTool(
        context,
        definition.standingOrder ? "standing_order" : "tool",
        runtime.transport.config.sharedSecretEnv,
        runtime,
      );
      trackSession(runtime, provenance);
      const invocationParams: Record<string, unknown> = definition.standingOrder
        ? { ...params, trigger_id: id }
        : { ...params };
      const path =
        typeof definition.path === "function"
          ? definition.path(invocationParams)
          : definition.path;
      if (definition.pathParam) delete invocationParams[definition.pathParam];
      if (definition.name === "administrateme_cos_response" ||
          definition.name === "administrateme_execute_pending_provider_action") {
        await waitForApprovalPublication(runtime, invocationParams.openclaw_approval_receipt_id);
      }
      const result = await runtime.transport.request(path, {
        ...invocationParams,
        provenance,
      });
      if (definition.name === "administrateme_cos_response" &&
          result && typeof result === "object" && "transport_dispatch" in result) {
        const dispatch = (result as Record<string, unknown>).transport_dispatch;
        if (!dispatch || typeof dispatch !== "object") throw new Error("Invalid CoS transport dispatch");
        const row = dispatch as Record<string, unknown>;
        if (row.channel !== "imessage" || typeof row.account_id !== "string" ||
            typeof row.recipient !== "string" || typeof row.body !== "string" ||
            typeof row.approval_event_id !== "string" ||
            !runtime.transport.config.forwardIntakeAccounts.some((binding) =>
              binding.channelId === "imessage" && binding.accountId === row.account_id,
            )) {
          throw new Error("CoS transport dispatch is outside the enrolled account");
        }
        try {
          const adapter = await api.runtime.channel.outbound.loadAdapter("imessage");
          if (!adapter?.sendText) throw new Error("CoS iMessage transport is unavailable");
          // One invocation only. The configured CLI guard independently claims
          // the exact approved text and audits the native result. Never expose
          // this private dispatch body or raw transport result to the model.
          await adapter.sendText({
            cfg: api.config,
            to: row.recipient,
            text: row.body,
            accountId: row.account_id,
          });
        } catch {
          // Native timeouts are ambiguous. The backend returns the durable
          // terminal if present, otherwise records unknown; no automatic retry.
        }
        return toolResult(await runtime.transport.request(
          "/api/openclaw/v1/cos-responses/result",
          { provenance, approval_event_id: row.approval_event_id },
        ));
      }
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
      name: "administrateme_capabilities",
      description:
        "Read the reviewed capability catalog and the authorized member's effective status.",
      parameters: Type.Object(
        {
          capability_id: Type.Optional(Type.String({ minLength: 1, maxLength: 240 })),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/capabilities",
    },
    {
      name: "administrateme_settings_context",
      description:
        "Read the minimum typed CoS context compiled from agreements, source policy, and exact grants.",
      parameters: Type.Object({}, { additionalProperties: false }),
      path: "/api/openclaw/v1/tools/settings-context",
    },
    {
      name: "administrateme_capability_request",
      description:
        "Record or cancel one inert, secret-free member capability request; never install or enroll anything.",
      parameters: Type.Object(
        {
          request_id: Type.String({ minLength: 8, maxLength: 120 }),
          expected_revision: Type.Integer({ minimum: 0 }),
          capability_id: Type.String({ minLength: 1, maxLength: 240 }),
          action: Type.Union([Type.Literal("request"), Type.Literal("cancel")]),
          requested_scope: Type.Optional(ServiceContextScope),
          provider_option: Type.Optional(Type.String({ minLength: 1, maxLength: 200 })),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/capability-request",
    },
    {
      name: "administrateme_policy_proposal",
      description:
        "Inspect or decide one member-owned Triage learning proposal; inspection is read-only.",
      parameters: Type.Object(
        {
          action: Type.Union([Type.Literal("inspect"), Type.Literal("decide")]),
          policy_proposal_id: Type.Optional(Type.String({ minLength: 1, maxLength: 240 })),
          request_id: Type.Optional(Type.String({ minLength: 8, maxLength: 120 })),
          decision: Type.Optional(
            Type.Union([
              Type.Literal("approve"),
              Type.Literal("edit"),
              Type.Literal("trial"),
              Type.Literal("reject"),
              Type.Literal("suppress"),
            ]),
          ),
          instruction: Type.Optional(
            Type.Union([
              Type.Literal("always"),
              Type.Literal("usually"),
              Type.Literal("ask"),
              Type.Literal("never"),
            ]),
          ),
          preview_digest: Type.Optional(Type.String({ pattern: "^[0-9a-f]{64}$" })),
          trial_proposal_ids: Type.Optional(
            Type.Array(Type.String({ minLength: 1, maxLength: 240 }), { maxItems: 100 }),
          ),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/policy-proposal",
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
        "Execute one domain-confirmed provider action after independent OpenClaw authorization. For a current private Apple Reminder request, first use administrateme_confirm_apple_reminder for the member account or administrateme_confirm_cos_apple_reminder for an explicitly requested CoS-owned work list. The exact current request is evaluated independently; older pending actions keep their original allow-once path. Accepted only queues execution. Inspect the matching member or CoS reminder status tool and claim creation only from terminal succeeded. Never retry an unknown or timed-out outcome with a new request ID.",
      parameters: Type.Object(
        {
          domain_approval_event_id: Type.String({ minLength: 1, maxLength: 240 }),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/execute-pending-provider-action",
    },
    {
      name: "administrateme_prepare_google_tasks_create_once",
      description:
        "Prepare only the exact member-approved production-v2 Google Tasks creation envelope.",
      parameters: Type.Object(
        {
          request_id: Type.String({ minLength: 8, maxLength: 120 }),
          context_scope: Type.Optional(ServiceContextScope),
          command_id: Type.String({ minLength: 1, maxLength: 240 }),
          domain_approval_event_id: Type.String({ minLength: 1, maxLength: 240 }),
          expected_approval_fingerprint: Type.String({ pattern: "^[0-9a-f]{64}$" }),
          task_content: Type.Object(
            {
              title: Type.String({ minLength: 1, maxLength: 1024 }),
              notes: Type.Optional(Type.String()),
              due: Type.Optional(Type.String()),
              status: Type.Optional(Type.Literal("needsAction")),
              parent: Type.Optional(Type.String()),
              previous: Type.Optional(Type.String()),
            },
            { additionalProperties: false },
          ),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/google-tasks-create/prepare",
    },
    {
      name: "administrateme_execute_google_tasks_create_once",
      description:
        "Execute the exact production-v2 Google Tasks create command once after independent allow-once approval.",
      parameters: Type.Object(
        {
          request_id: Type.String({ minLength: 8, maxLength: 120 }),
          context_scope: Type.Optional(ServiceContextScope),
          command_id: Type.String({ minLength: 1, maxLength: 240 }),
          expected_last_event_id: Type.String({ minLength: 1, maxLength: 240 }),
          expected_approval_fingerprint: Type.String({ pattern: "^[0-9a-f]{64}$" }),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/google-tasks-create/execute",
    },
    {
      name: "administrateme_google_tasks_create_state",
      description:
        "Read secret-safe terminal, later-observation, and reconciliation state for one B03 command.",
      parameters: Type.Object(
        {
          context_scope: Type.Optional(ServiceContextScope),
          command_id: Type.String({ minLength: 1, maxLength: 240 }),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/google-tasks-create/state",
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
      name: "administrateme_cos_response",
      description: "Execute one exact plain-text CoS response. Use the approval_event_id from administrateme_confirm_requested_email for a current explicit email-to-requester instruction; OpenClaw and the domain independently authorize that source-bound action without another permission question. Legacy exact member approvals keep their own approval path. Recipient listing alone never authorizes disclosure or sending. Only terminal sent proves delivery; unknown delivery is never retried.",
      parameters: Type.Object({
        request_id: Type.String({ minLength: 8, maxLength: 120 }),
        approval_event_id: Type.String({ minLength: 1, maxLength: 240 }),
      }, { additionalProperties: false }),
      path: "/api/openclaw/v1/tools/cos-response",
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
    api.registerTool((context) => makeTool(api, context, runtime, definition), {
      name: definition.name,
      optional: !["administrateme_state", "administrateme_what_now"].includes(definition.name),
    });
  }
}
