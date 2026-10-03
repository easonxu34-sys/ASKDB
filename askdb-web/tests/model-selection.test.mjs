import assert from "node:assert/strict";
import test from "node:test";

import {
  bindModelSelectionToActiveThread,
  isSameActiveThread,
  reconcileThreadModelSelection,
  shouldRefreshModelSelection,
} from "../lib/model-selection.ts";

function createStore(initial = {}) {
  const selections = new Map(Object.entries(initial));
  return {
    get(threadId) {
      return selections.get(threadId);
    },
    set(threadId, profileId) {
      selections.set(threadId, profileId);
    },
    clear(threadId) {
      selections.delete(threadId);
    },
  };
}

function profile(id, available = true) {
  return { id, available };
}

test("persists a fallback when the default profile is unavailable", () => {
  const store = createStore();
  const result = reconcileThreadModelSelection(
    "thread-synthetic-fallback-01",
    {
      default_profile_id: "default-unavailable",
      profiles: [profile("default-unavailable", false), profile("fallback-ready")],
    },
    store,
  );

  assert.equal(result.selectedId, "fallback-ready");
  assert.equal(store.get("thread-synthetic-fallback-01"), "fallback-ready");
  assert.match(result.notice, /默认模型不可用/);
});

test("keeps an unselected session following an available current default", () => {
  const store = createStore();
  const result = reconcileThreadModelSelection(
    "thread-synthetic-legacy-02",
    {
      default_profile_id: "current-default",
      profiles: [profile("current-default"), profile("other-ready")],
    },
    store,
  );

  assert.equal(result.selectedId, "current-default");
  assert.equal(store.get("thread-synthetic-legacy-02"), undefined);
  assert.equal(result.notice, "");
});

test("replaces a deleted profile for the selected thread only", () => {
  const threadId = "thread-synthetic-deleted-03";
  const otherThreadId = "thread-synthetic-other-04";
  const store = createStore({
    [threadId]: "deleted-profile",
    [otherThreadId]: "other-profile",
  });
  const result = reconcileThreadModelSelection(
    threadId,
    {
      default_profile_id: "current-default",
      profiles: [profile("current-default"), profile("other-profile")],
    },
    store,
  );

  assert.equal(result.selectedId, "current-default");
  assert.equal(store.get(threadId), "current-default");
  assert.equal(store.get(otherThreadId), "other-profile");
  assert.match(result.notice, /已切换到可用模型/);
});

test("clears stale metadata and explains when no profile is available", () => {
  const threadId = "thread-synthetic-unavailable-05";
  const store = createStore({ [threadId]: "cleared-profile" });
  const result = reconcileThreadModelSelection(
    threadId,
    {
      default_profile_id: "cleared-profile",
      profiles: [profile("cleared-profile", false)],
    },
    store,
  );

  assert.equal(result.selectedId, "");
  assert.equal(store.get(threadId), undefined);
  assert.match(result.notice, /已清除选择/);
});

test("refreshes the catalog after deleted or newly unavailable profile errors", () => {
  assert.equal(shouldRefreshModelSelection("MODEL_PROFILE_NOT_FOUND"), true);
  assert.equal(shouldRefreshModelSelection("MODEL_NOT_CONFIGURED"), true);
  assert.equal(shouldRefreshModelSelection("MODEL_SETTINGS_UNAVAILABLE"), false);
});

test("does not apply an async selection after the active thread changes", () => {
  assert.equal(isSameActiveThread("thread-local-synthetic-06", "thread-local-synthetic-06"), true);
  assert.equal(isSameActiveThread("thread-local-synthetic-06", "thread-local-synthetic-07"), false);
  const store = createStore();
  assert.equal(
    bindModelSelectionToActiveThread(
      "thread-local-synthetic-06",
      "thread-local-synthetic-07",
      "thread-remote-synthetic-07",
      "fallback-ready",
      store,
    ),
    false,
  );
  assert.equal(store.get("thread-remote-synthetic-07"), undefined);
});

test("keeps the fallback when initialization fills remoteId before the effect continues", async () => {
  const threadItemId = "thread-local-synthetic-init-08";
  const remoteId = "thread-remote-synthetic-init-08";
  const store = createStore();
  const activeThread = { id: threadItemId, remoteId: undefined };

  const initializedId = await Promise.resolve().then(() => {
    activeThread.remoteId = remoteId;
    return remoteId;
  });
  assert.equal(activeThread.remoteId, initializedId);
  const saved = bindModelSelectionToActiveThread(
    threadItemId,
    activeThread.id,
    activeThread.remoteId,
    "fallback-ready",
    store,
  );

  assert.equal(saved, true);
  assert.equal(store.get(remoteId), "fallback-ready");
});
