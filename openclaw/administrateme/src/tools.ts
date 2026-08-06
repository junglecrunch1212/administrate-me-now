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

function makeTool(
  context: OpenClawPluginToolContext,
  runtime: PluginRuntime,
  definition: {
    name: string;
    description: string;
    parameters: unknown;
    path: string;
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
      const approvedParams =
        definition.name === "administrateme_review"
          ? { ...params, openclaw_approved: true }
          : params;
      const invocationParams = definition.standingOrder
        ? { ...approvedParams, trigger_id: id }
        : approvedParams;
      const result = await runtime.transport.request(definition.path, {
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
      name: "administrateme_action",
      description: "Propose or capture one authorized household task or commitment.",
      parameters: Type.Object(
        {
          request_id: Type.String({ minLength: 1 }),
          mode: Type.Union([Type.Literal("propose"), Type.Literal("capture")]),
          kind: ItemKind,
          title: Type.String({ minLength: 1, maxLength: 500 }),
          due: Type.Optional(Type.String()),
          owed_to_party_id: Type.Optional(Type.String({ minLength: 1 })),
        },
        { additionalProperties: false },
      ),
      path: "/api/openclaw/v1/tools/action",
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
