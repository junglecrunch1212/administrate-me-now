/** Pinned SDK process-map semantics; the hosted native proof uses OpenClaw itself. */
export function resolveGlobalMap<TKey, TValue>(key: symbol): Map<TKey, TValue> {
  const store = globalThis as Record<PropertyKey, unknown>;
  if (!Object.hasOwn(store, key)) store[key] = new Map<TKey, TValue>();
  return store[key] as Map<TKey, TValue>;
}
