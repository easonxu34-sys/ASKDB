/**
 * @param {string | null | undefined} dataSourceId
 * @param {string | null | undefined} sourceName
 */
export function getThreadGroupKey(dataSourceId, sourceName) {
  const normalizedName = sourceName?.trim() || "未命名数据源";
  return `${dataSourceId ?? ""}\u0000${normalizedName}`;
}

/**
 * @param {string} groupKey
 * @param {string | undefined} previousGroupKey
 * @param {ReadonlySet<string>} collapsedGroups
 */
export function getThreadGroupRenderState(groupKey, previousGroupKey, collapsedGroups) {
  const collapsed = collapsedGroups.has(groupKey);
  return {
    showHeading: groupKey !== previousGroupKey,
    collapsed,
    renderThread: !collapsed,
  };
}

/**
 * @param {Array<{ data_source_id?: string | null; data_source_name?: string | null }>} threads
 * @param {string | undefined} previousGroupKey
 */
export function getThreadGroupPageState(threads, previousGroupKey) {
  let previous = previousGroupKey;
  return threads.map((thread) => {
    const groupKey = getThreadGroupKey(thread.data_source_id, thread.data_source_name);
    const showHeading = groupKey !== previous;
    const showDivider = showHeading && previous !== undefined;
    previous = groupKey;
    return { groupKey, showHeading, showDivider };
  });
}
