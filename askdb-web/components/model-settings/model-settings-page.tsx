"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIcon,
  CheckCircle2Icon,
  CirclePlusIcon,
  CpuIcon,
  LoaderCircleIcon,
  ServerIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { SettingsPageHeader } from "@/components/settings/settings-page-header";
import { authMutation } from "@/lib/auth-api";
import {
  fetchModelCatalog,
  responseMessage,
  type ModelCatalog,
  type ModelProfile,
  type ModelProvider,
} from "@/lib/model-profiles";

type ProfileForm = {
  name: string;
  provider: ModelProvider;
  model: string;
  base_url: string;
  api_key: string;
  context_window_tokens: string;
  max_output_tokens: string;
  tokenizer_id: "" | "tiktoken:cl100k_base" | "tiktoken:o200k_base";
};

const providerDefaults: Record<ModelProvider, string> = {
  openai: "https://api.openai.com/v1",
  deepseek: "https://api.deepseek.com",
  custom: "",
};

const inputClass =
  "mt-1.5 h-10 w-full rounded-lg border border-[#e1dbd0] bg-white px-3 text-sm text-[#393630] outline-none transition focus:border-[#c57650] focus:ring-2 focus:ring-[#c57650]/20 disabled:opacity-60";

const emptyForm = (): ProfileForm => ({
  name: "",
  provider: "deepseek",
  model: "",
  base_url: providerDefaults.deepseek,
  api_key: "",
  context_window_tokens: "",
  max_output_tokens: "",
  tokenizer_id: "",
});

function budgetPayload(form: ProfileForm) {
  const contextWindow = Number(form.context_window_tokens);
  const outputTokens = Number(form.max_output_tokens);
  if (
    !Number.isInteger(contextWindow) || contextWindow < 1024 || contextWindow > 2_000_000 ||
    !Number.isInteger(outputTokens) || outputTokens < 1 || outputTokens >= contextWindow ||
    !form.tokenizer_id
  ) return null;
  return {
    context_window_tokens: contextWindow,
    max_output_tokens: outputTokens,
    tokenizer_id: form.tokenizer_id,
  };
}

export function ModelSettingsPage() {
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [loading, setLoading] = useState(false);
  const [working, setWorking] = useState<"test" | "save" | "delete" | "default" | "clear" | null>(
    null,
  );
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<ProfileForm>(emptyForm);
  const [keyConfigured, setKeyConfigured] = useState(false);
  const [tested, setTested] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const canSave = useMemo(
    () =>
      Boolean(
        catalog &&
        tested &&
        !working &&
        form.name.trim() &&
        form.model.trim() &&
        form.base_url.trim() &&
        (form.api_key.trim() || keyConfigured),
        budgetPayload(form),
      ),
    [catalog, tested, working, form, keyConfigured],
  );

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setCatalog(await fetchModelCatalog());
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取模型配置失败，请重试。");
      setCatalog(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setEditingId(null);
    setNotice("");
    void loadSettings();
  }, [loadSettings]);

  function notifyModelCatalogUpdated() {
    window.dispatchEvent(new Event("askdb:model-catalog-updated"));
  }

  function updateField<K extends keyof ProfileForm>(field: K, value: ProfileForm[K]) {
    setForm((current) => ({ ...current, [field]: value }));
    setTested(false);
    setError("");
    setNotice("");
  }

  function startCreate() {
    setEditingId(null);
    setForm(emptyForm());
    setKeyConfigured(false);
    setTested(false);
    setError("");
    setNotice("");
  }

  function startEdit(profile: ModelProfile) {
    setEditingId(profile.id);
    setForm({
      name: profile.name,
      provider: profile.provider,
      model: profile.model,
      base_url: profile.base_url,
      api_key: "",
      context_window_tokens: profile.context_window_tokens == null
        ? ""
        : String(profile.context_window_tokens),
      max_output_tokens: profile.max_output_tokens == null
        ? ""
        : String(profile.max_output_tokens),
      tokenizer_id: profile.tokenizer_id ?? "",
    });
    setKeyConfigured(profile.api_key_configured);
    setTested(false);
    setError("");
    setNotice("");
  }

  async function testConnection() {
    setWorking("test");
    setError("");
    setNotice("");
    try {
      const response = await authMutation("/api/settings/models/test", "POST", {
        ...form,
        ...budgetPayload(form),
        profile_id: editingId,
      });
      if (!response.ok) throw new Error(await responseMessage(response));
      setTested(true);
      setNotice("连接测试成功，可以保存配置。");
    } catch (cause) {
      setTested(false);
      setError(cause instanceof Error ? cause.message : "连接测试失败，请重试。");
    } finally {
      setWorking(null);
    }
  }

  async function saveProfile() {
    setWorking("save");
    setError("");
    try {
      const response = await authMutation(
        editingId
          ? `/api/settings/models/${encodeURIComponent(editingId)}`
          : "/api/settings/models",
        editingId ? "PUT" : "POST",
        { ...form, ...budgetPayload(form) },
      );
      if (!response.ok) throw new Error(await responseMessage(response));
      setForm(emptyForm());
      setEditingId(null);
      setKeyConfigured(false);
      setTested(false);
      setNotice("模型配置已保存。");
      await loadSettings();
      notifyModelCatalogUpdated();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存配置失败，请重试。");
    } finally {
      setWorking(null);
    }
  }

  async function setDefault(profile: ModelProfile) {
    setWorking("default");
    setError("");
    try {
      const response = await authMutation(
        `/api/settings/models/${encodeURIComponent(profile.id)}/default`,
        "PUT",
        {},
      );
      if (!response.ok) throw new Error(await responseMessage(response));
      await loadSettings();
      setNotice(`已将“${profile.name}”设为默认模型。`);
      notifyModelCatalogUpdated();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "设置默认模型失败。");
    } finally {
      setWorking(null);
    }
  }

  async function deleteProfile(profile: ModelProfile) {
    const replacement = catalog?.profiles.find((item) => item.id !== profile.id && item.available);
    if (profile.id === catalog?.default_profile_id && !replacement) {
      setError("请先添加另一项可用模型，再删除当前默认模型。");
      return;
    }
    const message =
      profile.id === catalog?.default_profile_id
        ? `删除后默认模型将切换为“${replacement?.name}”。继续吗？`
        : `确定删除模型配置“${profile.name}”吗？`;
    if (!window.confirm(message)) return;

    setWorking("delete");
    setError("");
    try {
      const response = await authMutation(
        `/api/settings/models/${encodeURIComponent(profile.id)}`,
        "DELETE",
        profile.id === catalog?.default_profile_id
          ? { new_default_profile_id: replacement?.id }
          : undefined,
      );
      if (!response.ok) throw new Error(await responseMessage(response));
      if (editingId === profile.id) startCreate();
      await loadSettings();
      setNotice("模型配置已删除。");
      notifyModelCatalogUpdated();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "删除模型配置失败。");
    } finally {
      setWorking(null);
    }
  }

  async function clearCredential(profile: ModelProfile) {
    if (!window.confirm(`清除“${profile.name}”的 API Key 后，该模型将不能用于新会话。继续吗？`))
      return;
    setWorking("clear");
    setError("");
    try {
      const response = await authMutation(
        `/api/settings/models/${encodeURIComponent(profile.id)}/credential`,
        "DELETE",
      );
      if (!response.ok) throw new Error(await responseMessage(response));
      if (editingId === profile.id) setKeyConfigured(false);
      await loadSettings();
      setNotice("API Key 已清除。重新录入密钥并测试后可恢复使用。");
      notifyModelCatalogUpdated();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "清除 API Key 失败。");
    } finally {
      setWorking(null);
    }
  }

  function selectProvider(provider: ModelProvider) {
    setForm((current) => ({
      ...current,
      provider,
      base_url: provider === "custom" ? current.base_url : providerDefaults[provider],
    }));
    setTested(false);
    setError("");
  }

  return (
    <main className="min-h-dvh bg-[#f5f2eb] text-[#393630]">
      <SettingsPageHeader
        title="模型配置"
        description="管理可用模型；每个会话可以单独选择模型"
        icon={CpuIcon}
        rightSlot={editingId && catalog && (
            <span
              className={`hidden items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] sm:inline-flex ${catalog.profiles.find((profile) => profile.id === editingId)?.available ? "bg-[#e7eee2] text-[#527249]" : "bg-[#f2e7dc] text-[#8c6149]"}`}
            >
              {catalog.profiles.find((profile) => profile.id === editingId)?.available ? (
                <CheckCircle2Icon className="size-3.5" />
              ) : (
                <ActivityIcon className="size-3.5" />
              )}
              {catalog.profiles.find((profile) => profile.id === editingId)?.available
                ? "可用"
                : "未就绪"}
            </span>
        )}
      />

      <div className="mx-auto grid max-w-[1440px] gap-5 px-4 py-5 sm:px-8 lg:grid-cols-[280px_minmax(0,1fr)] lg:gap-7 lg:py-8">
        <aside className="h-fit rounded-2xl border border-[#e7e2d8] bg-[#f9f7f2] p-3 lg:sticky lg:top-24">
          <div className="flex items-center justify-between px-2 pb-2 pt-1">
            <div>
              <p className="text-[10px] font-semibold tracking-[0.13em] text-[#9b9589]">
                已配置模型
              </p>
              <p className="mt-1 text-xs text-[#89847a]">
                {loading ? "正在读取…" : `${catalog?.profiles.length ?? 0} 项配置`}
              </p>
            </div>
            <button
              type="button"
              onClick={startCreate}
              disabled={Boolean(working) || loading || !catalog}
              className="flex size-8 items-center justify-center rounded-lg text-[#a66443] hover:bg-[#eee8dc] focus-visible:ring-2 focus-visible:ring-[#c57650] disabled:cursor-not-allowed disabled:opacity-50"
              aria-label="添加模型"
              title="添加模型"
            >
              <CirclePlusIcon className="size-4" />
            </button>
          </div>

          {loading ? (
            <div role="status" className="flex items-center gap-2 px-2 py-5 text-xs text-[#89847a]">
              <LoaderCircleIcon className="size-4 animate-spin" />
              正在读取模型配置…
            </div>
          ) : catalog?.profiles.length === 0 ? (
            <p className="rounded-xl border border-dashed border-[#ddd5c8] px-3 py-4 text-xs leading-5 text-[#89847a]">
              还没有模型配置。添加并测试连接后，就能在聊天框中选择。
            </p>
          ) : catalog ? (
            <nav
              aria-label="模型配置列表"
              className="flex gap-2 overflow-x-auto lg:flex-col lg:overflow-visible"
            >
              {catalog.profiles.map((profile) => {
                const selected = editingId === profile.id;
                const isDefault = profile.id === catalog.default_profile_id;
                return (
                  <article
                    key={profile.id}
                    className={`min-w-56 flex-1 rounded-xl border border-[#e7e2d8] p-2 transition lg:min-w-0 ${selected ? "bg-[#ebe5d9]" : "bg-white/70 hover:bg-white"}`}
                  >
                    <button
                      type="button"
                      onClick={() => startEdit(profile)}
                      disabled={Boolean(working)}
                      aria-current={selected ? "true" : undefined}
                      className="w-full rounded-lg px-1.5 py-1 text-left focus-visible:ring-2 focus-visible:ring-[#c57650] disabled:cursor-not-allowed"
                    >
                      <span className="flex min-w-0 items-center gap-2 text-xs font-medium text-[#4a463f]">
                        <ServerIcon className="size-3.5 shrink-0 text-[#a66a4c]" />
                        <span className="min-w-0 flex-1 truncate">{profile.name}</span>
                        {isDefault && (
                          <span className="shrink-0 rounded-full bg-[#eee8dc] px-1.5 py-0.5 text-[9px] text-[#8c6149]">
                            默认
                          </span>
                        )}
                      </span>
                      <span className="mt-1.5 block truncate pl-[22px] text-[10px] text-[#89847a]">
                        {profile.provider} · {profile.model}
                      </span>
                      {!profile.available && (
                        <span className="mt-1.5 ml-[22px] inline-flex rounded-full bg-[#f8e9e4] px-1.5 py-0.5 text-[9px] text-[#9c4037]">
                          未就绪
                        </span>
                      )}
                    </button>
                    <div className="mt-1 flex flex-wrap gap-1 px-1">
                      {profile.available && !isDefault && (
                        <Button
                          type="button"
                          variant="ghost"
                          className="h-7 px-2 text-[10px]"
                          disabled={Boolean(working)}
                          onClick={() => void setDefault(profile)}
                        >
                          设为默认
                        </Button>
                      )}
                      {profile.api_key_configured && (
                        <Button
                          type="button"
                          variant="ghost"
                          className="h-7 px-2 text-[10px] text-[#9c4037]"
                          disabled={Boolean(working)}
                          onClick={() => void clearCredential(profile)}
                        >
                          清除密钥
                        </Button>
                      )}
                      <Button
                        type="button"
                        variant="ghost"
                        className="h-7 px-2 text-[10px] text-[#9c4037]"
                        disabled={Boolean(working)}
                        onClick={() => void deleteProfile(profile)}
                      >
                        删除
                      </Button>
                    </div>
                  </article>
                );
              })}
            </nav>
          ) : (
            <p role="status" className="px-2 py-4 text-xs leading-5 text-[#89847a]">
              模型列表暂不可用，请在右侧重试。
            </p>
          )}
        </aside>

        <section
          className="min-w-0 rounded-2xl border border-[#e7e2d8] bg-[#fbfaf7] p-4 sm:p-6"
          aria-label="模型配置表单"
        >
          {loading ? (
            <div
              role="status"
              className="flex min-h-48 items-center justify-center gap-2 text-sm text-[#89847a]"
            >
              <LoaderCircleIcon className="size-4 animate-spin" />
              正在读取模型配置…
            </div>
          ) : !catalog ? (
            <div className="flex min-h-48 flex-col items-start justify-center gap-3" role="alert">
              <p className="text-sm text-[#9c4037]">{error || "模型配置暂不可用。"}</p>
              <Button type="button" variant="outline" onClick={() => void loadSettings()}>
                重试
              </Button>
            </div>
          ) : (
            <form
              className="space-y-6"
              onSubmit={(event) => {
                event.preventDefault();
                if (canSave) void saveProfile();
              }}
            >
              <div className="flex flex-wrap items-end justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-[10px] font-semibold tracking-[0.13em] text-[#9b9589]">
                    {editingId ? "模型配置" : "新增配置"}
                  </p>
                  <h2 className="mt-1 text-xl font-semibold tracking-tight text-[#393630]">
                    {editingId ? "编辑模型" : "添加模型"}
                  </h2>
                </div>
                {editingId && (
                  <Button
                    type="button"
                    variant="outline"
                    disabled={Boolean(working)}
                    onClick={startCreate}
                  >
                    添加模型
                  </Button>
                )}
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <label
                  className="block text-xs font-medium text-[#615b51]"
                  htmlFor="model-profile-name"
                >
                  配置名称
                  <input
                    id="model-profile-name"
                    value={form.name}
                    disabled={Boolean(working)}
                    onChange={(event) => updateField("name", event.target.value)}
                    placeholder="例如 DeepSeek 日常"
                    className={inputClass}
                  />
                </label>
                <label
                  className="block text-xs font-medium text-[#615b51]"
                  htmlFor="model-profile-provider"
                >
                  供应商
                  <select
                    id="model-profile-provider"
                    value={form.provider}
                    disabled={Boolean(working)}
                    onChange={(event) => selectProvider(event.target.value as ModelProvider)}
                    className={inputClass}
                  >
                    <option value="openai">OpenAI</option>
                    <option value="deepseek">DeepSeek</option>
                    <option value="custom">自定义 OpenAI 兼容服务</option>
                  </select>
                </label>
                <label
                  className="block text-xs font-medium text-[#615b51] sm:col-span-2"
                  htmlFor="model-profile-model"
                >
                  模型名称
                  <input
                    id="model-profile-model"
                    required
                    value={form.model}
                    disabled={Boolean(working)}
                    onChange={(event) => updateField("model", event.target.value)}
                    placeholder="例如 deepseek-v4-flash"
                    className={inputClass}
                  />
                </label>
                <label
                  className="block text-xs font-medium text-[#615b51] sm:col-span-2"
                  htmlFor="model-profile-base-url"
                >
                  API 地址
                  <input
                    id="model-profile-base-url"
                    type="url"
                    required
                    value={form.base_url}
                    disabled={Boolean(working)}
                    onChange={(event) => updateField("base_url", event.target.value)}
                    placeholder="https://api.example.com/v1"
                    className={inputClass}
                  />
                </label>
                <label
                  className="block text-xs font-medium text-[#615b51] sm:col-span-2"
                  htmlFor="model-profile-api-key"
                >
                  API Key
                  <input
                    id="model-profile-api-key"
                    type="password"
                    autoComplete="new-password"
                    value={form.api_key}
                    disabled={Boolean(working)}
                    onChange={(event) => updateField("api_key", event.target.value)}
                    placeholder={keyConfigured ? "已配置；留空则保留当前密钥" : "输入 API Key"}
                    className={inputClass}
                  />
                </label>
                <p className="-mt-2 text-xs text-[#89847a] sm:col-span-2">
                  {keyConfigured
                    ? "服务端已保存密钥；读取配置时不会回显。"
                    : "API Key 仅发送至 Agent 加密保存。"}
                </p>
                <div className="grid gap-4 rounded-xl border border-[#e7e2d8] bg-white/60 p-3 sm:col-span-2 sm:grid-cols-3">
                  <label className="block text-xs font-medium text-[#615b51]" htmlFor="model-context-window">
                    上下文窗口（token）
                    <input
                      id="model-context-window"
                      type="number"
                      min={1024}
                      max={2_000_000}
                      step={1}
                      required
                      value={form.context_window_tokens}
                      disabled={Boolean(working)}
                      onChange={(event) => updateField("context_window_tokens", event.target.value)}
                      placeholder="例如 128000"
                      className={inputClass}
                    />
                  </label>
                  <label className="block text-xs font-medium text-[#615b51]" htmlFor="model-output-reserve">
                    输出预留（token）
                    <input
                      id="model-output-reserve"
                      type="number"
                      min={1}
                      max={2_000_000}
                      step={1}
                      required
                      value={form.max_output_tokens}
                      disabled={Boolean(working)}
                      onChange={(event) => updateField("max_output_tokens", event.target.value)}
                      placeholder="例如 8192"
                      className={inputClass}
                    />
                  </label>
                  <label className="block text-xs font-medium text-[#615b51]" htmlFor="model-tokenizer">
                    Tokenizer 编码
                    <select
                      id="model-tokenizer"
                      required
                      value={form.tokenizer_id}
                      disabled={Boolean(working)}
                      onChange={(event) => updateField(
                        "tokenizer_id",
                        event.target.value as ProfileForm["tokenizer_id"],
                      )}
                      className={inputClass}
                    >
                      <option value="">选择明确编码</option>
                      <option value="tiktoken:cl100k_base">cl100k_base</option>
                      <option value="tiktoken:o200k_base">o200k_base</option>
                    </select>
                  </label>
                  <p className="text-xs leading-5 text-[#89847a] sm:col-span-3">
                    新版会话按此预算裁剪上下文。模型名称不会自动推断编码；上下文窗口需大于输出预留。
                  </p>
                </div>
              </div>

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
              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[#ebe6dd] pt-4">
                <Button
                  type="button"
                  variant="outline"
                  disabled={
                    Boolean(working) ||
                    !form.model.trim() ||
                    !form.base_url.trim() ||
                    (!form.api_key.trim() && !keyConfigured) ||
                    !budgetPayload(form)
                  }
                  onClick={() => void testConnection()}
                >
                  {working === "test" ? "正在测试…" : "测试连接"}
                </Button>
                <div className="flex gap-2">
                  {editingId && (
                    <Button
                      type="button"
                      variant="ghost"
                      disabled={Boolean(working)}
                      onClick={startCreate}
                    >
                      取消编辑
                    </Button>
                  )}
                  <Button type="submit" disabled={!canSave}>
                    {working === "save" ? "正在保存…" : "保存配置"}
                  </Button>
                </div>
              </div>
            </form>
          )}
        </section>
      </div>
    </main>
  );
}
