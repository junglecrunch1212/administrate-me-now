/** Derive used types from public registration APIs, never private host modules. */
import type { AnyAgentTool, OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";

declare const nativeApi: OpenClawPluginApi;
type MessageHandler = Parameters<typeof nativeApi.on<"message_received">>[1];
type AgentEndHandler = Parameters<typeof nativeApi.on<"agent_end">>[1];
type ModelCallHandler = Parameters<typeof nativeApi.on<"model_call_started">>[1];
export type PluginHookMessageContext = Parameters<MessageHandler>[1];
export type PluginHookAgentContext = Parameters<AgentEndHandler>[1];
export type PluginHookModelCallEvent = Parameters<ModelCallHandler>[0];
export type GatewayHandlerOptions = Parameters<Parameters<OpenClawPluginApi["registerGatewayMethod"]>[1]>[0];
export type GatewayClient = GatewayHandlerOptions["client"];
export type ToolResult = Awaited<ReturnType<AnyAgentTool["execute"]>>;
