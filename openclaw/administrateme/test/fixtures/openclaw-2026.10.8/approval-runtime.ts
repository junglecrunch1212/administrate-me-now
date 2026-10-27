/** Narrow public SDK fixture at ce67ffb; real runtime proof is hosted separately. */
export type PluginApprovalRequest = {
  id: string; createdAtMs: number; expiresAtMs: number;
  request: {
    pluginId?: string | null; toolName?: string | null; toolCallId?: string | null;
    agentId?: string | null; sessionKey?: string | null;
    allowedDecisions?: readonly string[] | null;
  };
};
export type PluginApprovalResolved = { id: string; decision: string; request?: PluginApprovalRequest["request"] };
export type NativeAdapter<TEntry, TTarget, TContent, TRequest, TResolved> = {
  label: string; clientDisplayName: string; cfg: Record<string, unknown>; eventKinds: string[];
  isConfigured(): boolean; shouldHandle(request: TRequest): boolean;
  nativeAdapter: {
    describeDeliveryCapabilities(): { enabled: boolean; preferredSurface: string; supportsOriginSurface: boolean; supportsApproverDmSurface: boolean };
    resolveOriginTarget(params: { request: TRequest }): { to: string };
  };
  buildPendingContent(params: { request: TRequest }): TContent;
  prepareTarget(params: { request: TRequest }): { dedupeKey: string; target: TTarget };
  deliverTarget(params: { request: TRequest }): Promise<TEntry | null>;
  finalizeResolved(params: { request: TRequest; resolved: TResolved }): Promise<void>;
  finalizeExpired(params: { request: TRequest }): Promise<void>;
  onStopped(): void;
};
export const fixtureApprovalRuntimes: Array<{
  adapter: NativeAdapter<string, string, string, PluginApprovalRequest, PluginApprovalResolved>;
  calls: Array<{ method: string; params: Record<string, unknown> }>;
}> = [];
export function createChannelNativeApprovalRuntime<E, T, C, R, S>(adapter: NativeAdapter<E, T, C, R, S>) {
  const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
  fixtureApprovalRuntimes.push({ adapter: adapter as unknown as NativeAdapter<string, string, string, PluginApprovalRequest, PluginApprovalResolved>, calls });
  return {
    async start() {},
    async stop() { adapter.onStopped(); },
    async request(method: string, params: Record<string, unknown>) { calls.push({ method, params }); return { ok: true }; },
  };
}
