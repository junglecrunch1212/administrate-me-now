/**
 * Narrow official-contract fixture copied from the OpenClaw plugin SDK shape
 * at commit ce67ffb70e83e5ebb56ea02e814e577153f84f61.  It is intentionally
 * limited to the surfaces used by this plugin and is not a live-runtime claim.
 */

export type JsonSchema = Record<string, unknown>;

export type ToolResult = {
  content: Array<{ type: "text"; text: string }>;
  details?: unknown;
};

export type AnyAgentTool = {
  name: string;
  label?: string;
  description: string;
  parameters: unknown;
  outputSchema?: unknown;
  execute: (id: string, params: Record<string, unknown>) => Promise<ToolResult>;
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
  agentId?: string;
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
    subagent?: {
      run: (params: { sessionKey: string; message: string; deliver?: boolean; idempotencyKey?: string }) => Promise<{ runId: string }>;
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
    debug: (message: string) => void;
    info: (message: string) => void;
    warn: (message: string) => void;
    error: (message: string) => void;
  };
  registerTool: (
    tool: AnyAgentTool | ((ctx: OpenClawPluginToolContext) => AnyAgentTool | AnyAgentTool[]),
    opts?: { name?: string; names?: string[]; optional?: boolean },
  ) => void;
  registerCommand: (command: OpenClawPluginCommandDefinition) => void;
  registerGatewayMethod: (
    method: string,
    handler: (opts: GatewayHandlerOptions) => void | Promise<void>,
    opts?: { scope?: "operator.read" | "operator.write" | "operator.admin" },
  ) => void;
  registerService: (service: OpenClawPluginService) => void;
  on: {
    (hook: "before_agent_reply", handler: (event: { cleanedBody: string }, context: PluginHookAgentContext) => void | Promise<void>, options?: { eligibleTriggers?: readonly ["user", ...Array<"user" | "cron" | "heartbeat">] }): void;
    (
      hook: "agent_end",
      handler: (event: { runId?: string; messages: unknown[]; success: boolean; error?: string; durationMs?: number }, context: PluginHookAgentContext) => void | Promise<void>,
    ): void;
    (
      hook: "message_sending",
      handler: (
        event: PluginHookMessageSendingEvent,
        context: PluginHookMessageContext,
      ) => PluginHookMessageSendingResult | void | Promise<PluginHookMessageSendingResult | void>,
      opts?: { priority?: number },
    ): void;
    (
      hook: "before_tool_call",
      handler: (
        event: PluginHookBeforeToolCallEvent,
        context: Record<string, unknown>,
      ) => PluginHookBeforeToolCallResult | void | Promise<PluginHookBeforeToolCallResult | void>,
      opts?: { priority?: number },
    ): void;
    (
      hook: "message_received",
      handler: (
        event: PluginHookMessageReceivedEvent,
        context: PluginHookMessageContext,
      ) => void | Promise<void>,
    ): void;
    (
      hook: "model_call_started" | "model_call_ended",
      handler: (
        event: PluginHookModelCallEvent,
        context: PluginHookAgentContext,
      ) => void | Promise<void>,
    ): void;
  };
};

export type PluginEntry = {
  id: string;
  name: string;
  description: string;
  configSchema?: unknown;
  register: (api: OpenClawPluginApi) => void;
};

export declare function definePluginEntry(entry: PluginEntry): PluginEntry;
