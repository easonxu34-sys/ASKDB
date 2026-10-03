export type ModelSelectionCatalog = {
  default_profile_id: string | null;
  profiles: Array<{ id: string; available: boolean }>;
};

export type ThreadModelSelectionStore = {
  get(threadId: string): string | undefined;
  set(threadId: string, profileId: string): void;
  clear(threadId: string): void;
};

export type ModelSelectionResolution = {
  selectedId: string;
  notice: string;
};

export function reconcileThreadModelSelection(
  threadId: string | undefined,
  catalog: ModelSelectionCatalog,
  store: ThreadModelSelectionStore,
): ModelSelectionResolution {
  const storedId = threadId ? store.get(threadId) : undefined;
  const availableIds = new Set(
    catalog.profiles.filter((profile) => profile.available).map((profile) => profile.id),
  );
  const selectedDefaultId = catalog.default_profile_id &&
    availableIds.has(catalog.default_profile_id)
    ? catalog.default_profile_id
    : undefined;
  const defaultAvailable = selectedDefaultId !== undefined;
  const storedSelectionAvailable = storedId !== undefined && availableIds.has(storedId);
  const selectedId = (storedSelectionAvailable ? storedId : undefined) ??
    selectedDefaultId ?? catalog.profiles.find((profile) => profile.available)?.id ?? "";

  if (threadId) {
    if (storedId && !availableIds.has(storedId)) {
      if (selectedId) store.set(threadId, selectedId);
      else store.clear(threadId);
    } else if (!storedId && selectedId && selectedId !== catalog.default_profile_id) {
      // Legacy sessions follow the current default unless that default cannot be used.
      store.set(threadId, selectedId);
    }
  }

  let notice = "";
  if (storedId && !availableIds.has(storedId)) {
    notice = selectedId
      ? "此会话原模型已不可用，已切换到可用模型。"
      : "此会话所选模型已不可用，已清除选择。请在模型设置中完成配置。";
  } else if (!storedSelectionAvailable && !defaultAvailable && selectedId) {
    notice = "默认模型不可用，当前会话已切换到可用模型。";
  }

  return { selectedId, notice };
}

export function shouldRefreshModelSelection(errorCode: unknown): boolean {
  return errorCode === "MODEL_PROFILE_NOT_FOUND" || errorCode === "MODEL_NOT_CONFIGURED";
}

export function isSameActiveThread(expectedThreadId: string, currentThreadId: string): boolean {
  return expectedThreadId === currentThreadId;
}

export function bindModelSelectionToActiveThread(
  expectedThreadItemId: string,
  activeThreadItemId: string,
  remoteThreadId: string,
  profileId: string,
  store: Pick<ThreadModelSelectionStore, "set">,
): boolean {
  if (!isSameActiveThread(expectedThreadItemId, activeThreadItemId)) return false;
  store.set(remoteThreadId, profileId);
  return true;
}
