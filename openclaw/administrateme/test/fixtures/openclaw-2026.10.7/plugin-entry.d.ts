/**
 * Narrow synthetic fixture for the surfaces used from OpenClaw 2026.10.7.
 * The hosted target-SDK compile separately checks the real published declarations;
 * this unit fixture is not source-install or live-runtime compatibility proof.
 */

import type { TSchema } from "typebox";

export type JsonSchema = Record<string, unknown>;

export type ToolResult = {
  content: Array<{ type: "text"; text: string }>;
  details?: unknown;
};

export type AnyAgentTool = {
  name: string;
  label?: string;
  description: string;
  parameters: TSchema;
  outputSchema?: unknown;
  execute: (id: string, params: unknown) => Promise<ToolResult>;
};

export type OpenClawPluginToolContext = {
  agentId?: string;
  sessionKey?: string;
  sessionId?: string;
  messageChannel?: string;
  agentAccountId?: string;
  deliveryContext?: {
    channel?: string;
    to?: string;
    accountId?: string;
    threadId?: string | number;
  };
  nativeChannelId?: string;
  requesterSenderId?: string;
  senderIsOwner?: boolean;
};

export type PluginCommandContext = {
  senderId?: string;
  channel: string;
  channelId?: string;
  isAuthorizedSender: boolean;
  senderIsOwner?: boolean;
  gatewayClientScopes?: string[];
  agentId?: string;
  sessionKey?: string;
  sessionId?: string;
  args?: string;
  commandBody: string;
  from?: string;
  to?: string;
  accountId?: string;
  messageThreadId?: string | number;
  threadParentId?: string;
};

export type PluginCommandResult = {
  text?: string;
  content?: string;
  continueAgent?: boolean;
  suppressReply?: boolean;
};

export type OpenClawPluginCommandDefinition = {
  name: string;
  description: string;
  acceptsArgs?: boolean;
  requireAuth?: boolean;
  requiredScopes?: string[];
  exposeSenderIsOwner?: boolean;
  handler: (ctx: PluginCommandContext) => PluginCommandResult | Promise<PluginCommandResult>;
};

export type GatewayClient = {
  authenticatedUserId?: string;
  pairedClientId?: string;
  connect?: { client?: { id?: string }; scopes?: string[] };
};

export type GatewayHandlerOptions = {
  params: Record<string, unknown>;
  client: GatewayClient | null;
  respond: (ok: boolean, value?: unknown, error?: unknown) => void;
};

export type PluginHookMessageContext = {
  channelId: string;
  accountId?: string;
  conversationId?: string;
  sessionKey?: string;
  runId?: string;
  messageId?: string;
  senderId?: string;
};

export type PluginHookMessageReceivedEvent = {
  from: string;
  content: string;
  threadId?: string | number;
  messageId?: string;
  senderId?: string;
  sessionKey?: string;
  runId?: string;
  metadata?: Record<string, unknown>;
};

export type PluginHookMessageSendingEvent = {
  to: string;
  content: string;
  replyToId?: string;
  threadId?: string | number;
  metadata?: Record<string, unknown>;
};

export type PluginHookMessageSendingResult = {
  content?: string;
  cancel?: boolean;
  cancelReason?: string;
  metadata?: Record<string, unknown>;
};

export type PluginHookAgentContext = {
  runId?: string;
  agentId?: string;
  sessionKey?: string;
  sessionId?: string;
};

export type PluginHookModelCallEvent = {
  runId: string;
  callId: string;
  sessionKey?: string;
  sessionId?: string;
  provider: string;
  model: string;
};

export type PluginHookBeforeToolCallEvent = {
  toolName: string;
  params: Record<string, unknown>;
  toolCallId?: string;
};

export type PluginHookBeforeToolCallResult = {
  params?: Record<string, unknown>;
  block?: boolean;
  blockReason?: string;
  requireApproval?: {
    title: string;
    description: string;
    severity?: "info" | "warning" | "critical";
    timeoutMs?: number;
    allowedDecisions?: Array<"allow-once" | "allow-always" | "deny">;
    pluginId?: string;
    onResolution?: (
      decision: "allow-once" | "allow-always" | "deny" | "timeout" | "cancelled",
    ) => Promise<void> | void;
  };
};

export type OpenClawPluginService = {
  id: string;
  start: () => void | Promise<void>;
  stop: () => void | Promise<void>;
};

export type OpenClawPluginApi = {
  id: string;
  name: string;
  version?: string;
  source: string;
  rootDir?: string;
  registrationMode: "full" | "setup" | "snapshot";
  pluginConfig?: Record<string, unknown>;
  config: Record<string, unknown>;
  runtime: {
    config: { current: () => Record<string, unknown> };
    subagent?: {
      run: (params: { sessionKey: string; message: string; deliver?: boolean; lane?: string; idempotencyKey?: string }) => Promise<{ runId: string }>;
      waitForRun: (params: { runId: string; timeoutMs?: number }) => Promise<{ status: "ok" | "error" | "timeout"; error?: string }>;
    };
    channel: {
      outbound: {
        loadAdapter: (id: string) => Promise<{
          sendText?: (context: {
            cfg: Record<string, unknown>;
            to: string;
            text: string;
            accountId?: string;
          }) => Promise<unknown>;
        } | undefined>;
      };
    };
  };
  logger: {
    debug?: (message: string) => void;
    info: (message: string) => void;
    warn: (message: string) => void;
    error: (message: string) => void;
  };
  registerTool: (
    tool: AnyAgentTool | ((ctx: OpenClawPluginToolContext) => AnyAgentTool | AnyAgentTool[]),
    opts?: { name?: string; names?: string[]; optional?: boolean },
  ) => void;
  registerCommand: (command: OpenClawPluginCommandDefinition) => void;
  registerAgentToolResultMiddleware: (
    handler: (event: { toolCallId: string; toolName: string; args: Record<string, unknown>; isError?: boolean; result: ToolResult }, context: PluginHookAgentContext & { runtime: "openclaw" | "codex" }) => Promise<{ result: ToolResult } | void> | { result: ToolResult } | void,
    options?: { matcher?: string[]; runtimes?: Array<"openclaw" | "codex"> },
  ) => void;
  registerGatewayMethod: (
    method: string,
    handler: (opts: GatewayHandlerOptions) => void | Promise<void>,
    opts?: { scope?: "operator.read" | "operator.write" | "operator.admin" },
  ) => void;
  registerService: (service: OpenClawPluginService) => void;
  on: <K extends keyof FixtureHookMap>(
    hook: K, handler: FixtureHookMap[K],
    options?: { priority?: number; eligibleTriggers?: readonly ["user", ...Array<"user" | "cron" | "heartbeat">] },
  ) => void;
};

export type PluginEntry = {
  id: string;
  name: string;
  description: string;
  configSchema?: unknown;
  register: (api: OpenClawPluginApi) => void;
};

export declare function definePluginEntry(entry: PluginEntry): PluginEntry;

export type FixtureHookMap = {
  before_prompt_build: (event: { prompt: string; messages: unknown[] }, context: PluginHookAgentContext) => { toolsAllow?: string[] } | void | Promise<{ toolsAllow?: string[] } | void>;
  after_tool_call: (event: { toolName: string; params: Record<string, unknown>; runId?: string; toolCallId?: string; result?: unknown; error?: string; durationMs?: number }, context: Record<string, unknown>) => void | Promise<void>;
  before_agent_reply: (event: { cleanedBody: string }, context: PluginHookAgentContext) => void | Promise<void>;
  agent_end: (event: { runId?: string; messages: unknown[]; success: boolean; error?: string; durationMs?: number }, context: PluginHookAgentContext) => void | Promise<void>;
  message_sending: (event: PluginHookMessageSendingEvent, context: PluginHookMessageContext) => PluginHookMessageSendingResult | void | Promise<PluginHookMessageSendingResult | void>;
  before_tool_call: (event: PluginHookBeforeToolCallEvent, context: Record<string, unknown>) => PluginHookBeforeToolCallResult | void | Promise<PluginHookBeforeToolCallResult | void>;
  message_received: (event: PluginHookMessageReceivedEvent, context: PluginHookMessageContext) => void | Promise<void>;
  model_call_started: (event: PluginHookModelCallEvent, context: PluginHookAgentContext) => void | Promise<void>;
  model_call_ended: (event: PluginHookModelCallEvent & { durationMs: number; outcome: "completed" | "error" }, context: PluginHookAgentContext) => void | Promise<void>;
};
