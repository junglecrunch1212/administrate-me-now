export declare function callGatewayTool<T = Record<string, unknown>>(method: string, opts: { timeoutMs?: number }, params?: unknown, extra?: { requireAgentRuntimeIdentity?: boolean }): Promise<T>;
