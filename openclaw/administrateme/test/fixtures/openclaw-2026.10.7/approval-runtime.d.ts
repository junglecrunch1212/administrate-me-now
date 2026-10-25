/** Synthetic unit fixture; hosted target-SDK compilation verifies the public contract. */
export type PluginApprovalRequest = {
  id: string; createdAtMs: number; expiresAtMs: number;
  request: {
    pluginId?: string | null; toolName?: string | null; toolCallId?: string | null;
    agentId?: string | null; sessionKey?: string | null; allowedDecisions?: readonly string[] | null;
  };
};
export type PluginApprovalResolved = { id: string; decision: string; request?: PluginApprovalRequest["request"] };
export declare function createChannelNativeApprovalRuntime<E, T, C, R extends PluginApprovalRequest, S extends PluginApprovalResolved>(adapter: {
  label: string; clientDisplayName: string; cfg: Record<string, unknown>; eventKinds: string[];
  isConfigured(): boolean; shouldHandle(request: R): boolean;
  nativeAdapter: {
    describeDeliveryCapabilities(): { enabled: boolean; preferredSurface: string; supportsOriginSurface: boolean; supportsApproverDmSurface: boolean };
    resolveOriginTarget(params: { request: R }): { to: string };
  };
  buildPendingContent(params: { request: R }): C;
  prepareTarget(params: { request: R }): { dedupeKey: string; target: T };
  deliverTarget(params: { request: R }): Promise<E | null>;
  finalizeResolved(params: { request: R; resolved: S }): Promise<void>;
  finalizeExpired(params: { request: R }): Promise<void>;
  onStopped(): void;
}): {
  start(): Promise<void>; stop(): Promise<void>;
  request(method: string, params: Record<string, unknown>): Promise<unknown>;
};
