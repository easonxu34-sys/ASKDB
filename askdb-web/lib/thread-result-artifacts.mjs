const DEFAULT_MAX_RESULT_ARTIFACT_BYTES = 2 * 1024 * 1024;

/**
 * Append one artifact to a turn-scoped cache while evicting the oldest other
 * turns as needed. A failed or oversized write leaves the stored value intact.
 *
 * @param {{ getItem(key: string): string | null, setItem(key: string, value: string): void }} storage
 * @param {string} key
 * @param {string} turnId
 * @param {unknown} output
 * @param {number} maxBytes
 * @returns {boolean}
 */
export function saveResultArtifact(
  storage,
  key,
  turnId,
  output,
  maxBytes = DEFAULT_MAX_RESULT_ARTIFACT_BYTES,
) {
  if (!key || !turnId || !Number.isSafeInteger(maxBytes) || maxBytes < 0) return false;

  let artifacts = {};
  try {
    const stored = JSON.parse(storage.getItem(key) ?? "{}");
    if (typeof stored === "object" && stored !== null && !Array.isArray(stored)) {
      artifacts = Object.fromEntries(
        Object.entries(stored).filter(([, value]) => Array.isArray(value)),
      );
    }
  } catch {
    // Rebuild a corrupt local artifact index from the current result.
  }

  artifacts = {
    ...artifacts,
    [turnId]: [...(Array.isArray(artifacts[turnId]) ? artifacts[turnId] : []), output],
  };

  let serialized;
  try {
    serialized = JSON.stringify(artifacts);
    while (new TextEncoder().encode(serialized).byteLength > maxBytes) {
      const oldestOtherTurn = Object.keys(artifacts).find((id) => id !== turnId);
      if (!oldestOtherTurn) return false;
      const next = { ...artifacts };
      delete next[oldestOtherTurn];
      artifacts = next;
      serialized = JSON.stringify(artifacts);
    }
    storage.setItem(key, serialized);
    return true;
  } catch {
    // Quota limits and unserializable outputs must not interrupt the live stream.
    return false;
  }
}
