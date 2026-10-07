"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ActivityIcon, LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ComposerSelect } from "@/components/ui/composer-select";
import { authMutation } from "@/lib/auth-api";
import {
  fetchModelCatalog,
  responseMessage,
  type ModelCatalog,
  type ModelProfile,
} from "@/lib/model-profiles";

function profileKind(profile: ModelProfile) {
  return profile.model_kind ?? "chat";
}

export function MemoryServiceSettingsPanel({ active }: { active: boolean }) {
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const hasLoaded = useRef(false);

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const nextCatalog = await fetchModelCatalog();
      setCatalog(nextCatalog);
      setSelectedProfileId(nextCatalog.service_references?.memory_chat ?? "");
    } catch (cause) {
      setCatalog(null);
      setError(cause instanceof Error ? cause.message : "读取记忆服务配置失败，请重试。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!active || hasLoaded.current) return;
    hasLoaded.current = true;
    void loadSettings();
  }, [active, loadSettings]);

  const currentProfileId = catalog?.service_references?.memory_chat ?? "";
  const selectedProfile = useMemo(
    () => catalog?.profiles.find((profile) => profile.id === selectedProfileId) ?? null,
    [catalog, selectedProfileId],
  );
  const currentProfile = useMemo(
    () => catalog?.profiles.find((profile) => profile.id === currentProfileId) ?? null,
    [catalog, currentProfileId],
  );
  const chatProfiles = useMemo(
    () =>
      catalog?.profiles.filter(
        (profile) =>
          profileKind(profile) === "chat" && (profile.available || profile.id === currentProfileId),
      ) ?? [],
    [catalog, currentProfileId],
  );
  const canSave = Boolean(
    !loading &&
    !saving &&
    selectedProfile &&
    selectedProfile.available &&
    selectedProfileId !== currentProfileId,
  );

  async function saveSelection() {
    if (!canSave || !selectedProfile) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const response = await authMutation(
        `/api/settings/models/${encodeURIComponent(selectedProfile.id)}/memory-processing`,
        "PUT",
        {},
      );
      if (!response.ok) throw new Error(await responseMessage(response));
      setCatalog((current) =>
        current
          ? {
              ...current,
              service_references: {
                ...current.service_references,
                memory_chat: selectedProfile.id,
              },
            }
          : current,
      );
      setNotice("记忆处理模型已更新；后续记忆操作将使用新配置。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存记忆服务配置失败，请重试。");
    } finally {
      setSaving(false);
    }
  }

  const embeddingProfileId = catalog?.service_references?.embedding;
  const embeddingProfile = catalog?.profiles.find((profile) => profile.id === embeddingProfileId);
  const indexStatus = catalog?.personal_index_status;

  return (
    <div className="space-y-6">
        <section className="space-y-5 rounded-2xl border border-[#e7e2d8] bg-white/70 p-5 sm:p-6">
          <div>
            <h2 className="font-medium text-[#393630]">记忆处理模型</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-[#77736b]">
              此模型负责理解个人记忆的保存、更新、忘记和澄清请求。它是管理员配置的服务模型，不会改变会话使用的聊天模型或用户的记忆开关。
            </p>
          </div>

          {loading ? (
            <div role="status" className="flex items-center gap-2 py-4 text-sm text-[#89847a]">
              <LoaderCircleIcon className="size-4 animate-spin" />
              正在读取记忆服务配置…
            </div>
          ) : catalog ? (
            <>
              <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
                <label className="block text-sm font-medium text-[#615b51]">
                  Chat 模型
                  <ComposerSelect
                    id="memory-processing-model"
                    value={selectedProfileId}
                    disabled={saving}
                    ariaLabel="记忆处理 Chat 模型"
                    placeholder="选择可用的 Chat 模型"
                    options={chatProfiles.map((profile) => ({
                      value: profile.id,
                      label: `${profile.name} · ${profile.provider} · ${profile.model}${
                        profile.available ? "" : "（当前配置未就绪）"
                      }`,
                    }))}
                    onValueChange={(value) => {
                      setSelectedProfileId(value);
                      setError("");
                      setNotice("");
                    }}
                  />
                </label>
                <Button disabled={!canSave} onClick={() => void saveSelection()}>
                  {saving ? "保存中…" : "保存配置"}
                </Button>
              </div>

              {!chatProfiles.some((profile) => profile.available) && (
                <p className="text-sm text-[#8c6149]">
                  暂无可用的 Chat 模型。请先添加模型配置并完成连接测试。{" "}
                  <Link
                    className="font-medium underline underline-offset-4"
                    href="/settings/models"
                  >
                    前往模型配置
                  </Link>
                </p>
              )}

              {!currentProfile &&
                !currentProfileId &&
                chatProfiles.some((profile) => profile.available) && (
                  <p role="status" className="text-sm text-[#8c6149]">
                    尚未指定记忆处理模型，聊天中的自然语言记忆操作暂不可用。
                  </p>
                )}
              {!currentProfile && currentProfileId && (
                <p role="alert" className="text-sm text-[#9c4037]">
                  当前记忆处理模型配置已找不到，请重新选择可用模型。
                </p>
              )}

              {currentProfile && (
                <div className="flex flex-wrap items-center gap-2 rounded-xl border border-[#e7e2d8] bg-[#faf9f6] px-4 py-3 text-sm">
                  <span className="text-[#77736b]">当前生效</span>
                  <span className="font-medium text-[#393630]">{currentProfile.name}</span>
                  <span className="text-[#89847a]">
                    {currentProfile.provider} · {currentProfile.model}
                  </span>
                  <span
                    className={`ml-auto rounded-full px-2 py-1 text-xs ${currentProfile.available ? "bg-[#e7eee2] text-[#527249]" : "bg-[#f8e9e4] text-[#9c4037]"}`}
                  >
                    {currentProfile.available ? "可用" : "未就绪"}
                  </span>
                </div>
              )}

              {error && (
                <p role="alert" className="text-sm text-[#9c4037]">
                  {error}
                </p>
              )}
              {notice && (
                <p role="status" className="text-sm text-[#54734d]">
                  {notice}
                </p>
              )}
            </>
          ) : (
            <div className="flex flex-col items-start gap-3" role="alert">
              <p className="text-sm text-[#9c4037]">{error || "记忆服务配置暂不可用。"}</p>
              <Button type="button" variant="outline" onClick={() => void loadSettings()}>
                <RefreshCwIcon aria-hidden="true" />
                重试
              </Button>
            </div>
          )}
        </section>

        {catalog && (
          <section className="rounded-2xl border border-[#e7e2d8] bg-white/70 p-5 sm:p-6">
            <div className="flex items-start gap-3">
              <ActivityIcon className="mt-0.5 size-4 shrink-0 text-[#a66a4c]" />
              <div className="min-w-0 flex-1">
                <h2 className="font-medium text-[#393630]">个人记忆索引</h2>
                <p className="mt-2 text-sm text-[#77736b]">
                  Embedding 模型：
                  {embeddingProfile ? (
                    <span className="font-medium text-[#514b42]">
                      {embeddingProfile.name}
                      {!embeddingProfile.available && "（未就绪）"}
                    </span>
                  ) : (
                    <span>未配置</span>
                  )}
                </p>
                {indexStatus ? (
                  <>
                    <p className="mt-2 text-sm text-[#77736b]">
                      {indexStatus.generations.length
                        ? indexStatus.generations
                            .map((generation) =>
                              generation.state === "active"
                                ? `使用中 ${generation.dimensions} 维`
                                : `重建中 ${generation.dimensions} 维`,
                            )
                            .join("；")
                        : "暂无可用索引代际"}
                      。待处理 {indexStatus.pending_jobs} 项，待重试 {indexStatus.failed_jobs} 项。
                    </p>
                    {(!embeddingProfile || !embeddingProfile.available) && (
                      <p className="mt-2 text-xs text-[#89847a]">
                        配置并设为默认 Embedding 模型后，个人记忆索引才能开始构建。{" "}
                        <Link
                          className="font-medium underline underline-offset-4"
                          href="/settings/models"
                        >
                          前往模型配置
                        </Link>
                      </p>
                    )}
                  </>
                ) : (
                  <p className="mt-2 text-sm text-[#89847a]">索引状态暂不可用。</p>
                )}
                <p className="mt-3 text-xs text-[#89847a]">
                  此处只显示索引运行状态，不展示个人记忆内容。
                </p>
              </div>
            </div>
          </section>
        )}
    </div>
  );
}
