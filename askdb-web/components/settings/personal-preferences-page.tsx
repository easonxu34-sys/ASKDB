"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { BrainIcon } from "lucide-react";
import { SettingsPageHeader } from "@/components/settings/settings-page-header";
import { ComposerSelect } from "@/components/ui/composer-select";
import { Button } from "@/components/ui/button";
import { fetchDataSourceCatalog, type DataSourceSummary } from "@/lib/data-sources";
import { authMutation, fetchCurrentUser, responseError } from "@/lib/auth-api";

type Memory = {
  id: string;
  kind: string;
  source_id: string | null;
  scenario: string;
  content: string;
  payload: Record<string, unknown>;
  version: number;
  expires_at: string | null;
  status: string;
};
type Settings = { enabled: boolean; revision: number };
const kinds = [
  { value: "expression", label: "表达偏好" },
  { value: "display", label: "展示偏好" },
  { value: "analysis_steps", label: "操作习惯" },
  { value: "default_filter", label: "常用范围" },
  { value: "metric_definition", label: "个人业务定义" },
  { value: "analysis_recipe", label: "分析方法" },
];
const input = "w-full rounded-lg border border-[#e1dbd0] bg-white px-3 py-2 text-sm";
export function PersonalPreferencesPage() {
  const owner = useRef<string | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [sources, setSources] = useState<DataSourceSummary[]>([]);
  const [records, setRecords] = useState<Memory[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [draft, setDraft] = useState<Memory | null>(null);
  const [preview, setPreview] = useState<{ request_id: string; message: string } | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const reset = useCallback(() => {
    setRecords([]);
    setSources([]);
    setSettings(null);
    setDraft(null);
    setPreview(null);
    setNotice("");
  }, []);
  const load = useCallback(
    async (next = "") => {
      setBusy(true);
      try {
        const user = await fetchCurrentUser();
        if (owner.current !== user?.user_id) {
          owner.current = user?.user_id ?? null;
          reset();
        }
        if (!user) throw new Error("请先登录。");
        const [s, m, availableSources] = await Promise.all([
          fetch("/api/me/memory-settings", { cache: "no-store" }),
          fetch(`/api/me/memories${next ? `?cursor=${encodeURIComponent(next)}` : ""}`, {
            cache: "no-store",
          }),
          fetchDataSourceCatalog().catch(() => null),
        ]);
        if (!s.ok || !m.ok) throw new Error("个人记忆读取失败，请重试。");
        if ((await fetchCurrentUser())?.user_id !== user.user_id) {
          owner.current = null;
          reset();
          return;
        }
        setSettings(await s.json());
        setSources(availableSources?.data_sources ?? []);
        const page = await m.json();
        setRecords((old) => (next ? [...old, ...page.memories] : page.memories));
        setCursor(page.next_cursor);
        setError("");
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "读取失败。");
      } finally {
        setBusy(false);
      }
    },
    [reset],
  );
  useEffect(() => {
    void load();
    const refresh = () => {
      void load();
    };
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [load]);
  async function mutate(path: string, method: "PATCH" | "POST" | "DELETE", body?: unknown) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const user = await fetchCurrentUser();
      if (!user || user.user_id !== owner.current) {
        reset();
        throw new Error("账号已变化，请重新读取。");
      }
      const response = await authMutation(path, method, body);
      if (!response.ok) throw new Error(await responseError(response, "操作失败，草稿已保留。"));
      const value = await response.json();
      if ((await fetchCurrentUser())?.user_id !== user.user_id) {
        reset();
        throw new Error("账号已变化。");
      }
      return value;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "操作失败。");
      return null;
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <SettingsPageHeader
        title="个人偏好记忆"
        description="只保存你明确要求长期记住的偏好，由本人管理。"
        icon={BrainIcon}
      />
      <main className="mx-auto max-w-5xl space-y-6 px-4 py-7 sm:px-8">
        <section className="rounded-2xl border border-[#e7e2d8] bg-white/70 p-5">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h2 className="font-medium">在后续会话中使用我的偏好</h2>
              <p className="mt-2 text-sm text-[#77736b]">
                默认关闭。开启后可说“记住”或“以后都这样”；关闭保留记录，仍可明确忘记。
              </p>
            </div>
            <Button
              disabled={!settings || busy}
              onClick={async () => {
                if (!settings) return;
                const value = await mutate("/api/me/memory-settings", "PATCH", {
                  enabled: !settings.enabled,
                  expected_revision: settings.revision,
                });
                if (value) {
                  setSettings(value);
                  setNotice("开关已更新。");
                }
              }}
            >
              {settings?.enabled ? "关闭记忆" : "开启记忆"}
            </Button>
          </div>
          <p className="mt-4 text-xs text-[#89847a]">
            当前回答继续使用开始时的快照，下一轮采用新状态。关闭会立即阻止尚未提交的聊天保存和更新。
          </p>
        </section>
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
        <div className="flex flex-wrap justify-between gap-3">
          <ComposerSelect
            id="memory-kind"
            value={filter}
            options={[{ value: "", label: "全部类型" }, ...kinds]}
            onValueChange={setFilter}
            ariaLabel="记忆类型"
            placeholder="全部类型"
          />
          <div className="flex gap-2">
            <Button variant="outline" disabled={busy} onClick={() => void load()}>
              刷新 / 重试
            </Button>
            <Button
              variant="outline"
              disabled={busy || !settings}
              onClick={async () => {
                const value = await mutate("/api/me/memories/clear-preview", "POST");
                if (value) setPreview(value);
              }}
            >
              清空全部
            </Button>
          </div>
        </div>
        {preview && (
          <section className="rounded-xl border border-[#d8c5b1] p-4">
            <p className="text-sm">{preview.message}</p>
            <div className="mt-3 flex gap-2">
              <Button
                disabled={busy}
                onClick={async () => {
                  const value = await mutate("/api/me/memories/clear", "POST", {
                    request_id: preview.request_id,
                    choice_id: "confirm",
                  });
                  if (value) {
                    setPreview(null);
                    setDraft(null);
                    await load();
                    setNotice("个人记忆已清空。");
                  }
                }}
              >
                确认清空
              </Button>
              <Button variant="outline" disabled={busy} onClick={() => setPreview(null)}>
                取消
              </Button>
            </div>
          </section>
        )}
        {records
          .filter((x) => !filter || x.kind === filter)
          .map((memory) => (
            <article
              key={memory.id}
              className="rounded-2xl border border-[#e7e2d8] bg-white/70 p-5"
            >
              <div className="flex flex-wrap justify-between gap-3">
                <div>
                  <p className="text-xs text-[#89847a]">
                    {kinds.find((x) => x.value === memory.kind)?.label} ·{" "}
                    {memory.source_id ?? "全局"} ·{" "}
                    {memory.expires_at
                      ? `期限 ${new Date(memory.expires_at).toLocaleString()}`
                      : "长期有效"}
                  </p>
                  <p className="mt-2 whitespace-pre-wrap text-sm">{memory.content}</p>
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" disabled={busy} onClick={() => setDraft({ ...memory })}>
                    编辑
                  </Button>
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={async () => {
                      const value = await mutate(
                        `/api/me/memories/${memory.id}?expected_version=${memory.version}`,
                        "DELETE",
                      );
                      if (value) {
                        await load();
                        setNotice("已忘记；下一轮生效。");
                      }
                    }}
                  >
                    忘记
                  </Button>
                </div>
              </div>
            </article>
          ))}
        {settings && !records.length && (
          <p className="text-sm text-[#89847a]">
            还没有保存个人偏好。在聊天中明确说“记住”即可保存。
          </p>
        )}
        {cursor && (
          <Button disabled={busy} variant="outline" onClick={() => void load(cursor)}>
            加载更多
          </Button>
        )}
        {draft && (
          <section className="space-y-4 rounded-2xl border border-[#d8c5b1] bg-white p-5">
            <h2 className="font-medium">编辑记忆</h2>
            <label className="block text-sm">
              内容
              <textarea
                className={`${input} mt-2 min-h-28`}
                maxLength={1200}
                value={draft.content}
                onChange={(e) => setDraft({ ...draft, content: e.target.value })}
              />
            </label>
            <label className="block text-sm">
              适用范围
              <ComposerSelect
                id="edit-memory-scope"
                value={draft.source_id ?? ""}
                options={[
                  { value: "", label: "全局" },
                  ...sources.map((source) => ({ value: source.id, label: source.display_name })),
                  ...(draft.source_id && !sources.some((source) => source.id === draft.source_id)
                    ? [{ value: draft.source_id, label: "原绑定数据源（当前不可用）" }]
                    : []),
                ]}
                ariaLabel="适用范围"
                placeholder="适用范围"
                onValueChange={(v) => setDraft({ ...draft, source_id: v || null })}
              />
            </label>
            <label className="block text-sm">
              期限（空白为长期）
              <input
                className={`${input} mt-2`}
                type="datetime-local"
                value={
                  draft.expires_at
                    ? new Date(
                        new Date(draft.expires_at).getTime() -
                          new Date(draft.expires_at).getTimezoneOffset() * 60000,
                      )
                        .toISOString()
                        .slice(0, 16)
                    : ""
                }
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    expires_at: e.target.value ? new Date(e.target.value).toISOString() : null,
                  })
                }
              />
            </label>
            <p className="text-xs text-[#89847a]">
              修改后下一轮采用新内容。范围、口径或步骤的改写将在使用前重新校验。
            </p>
            <div className="flex gap-2">
              <Button
                disabled={busy || !draft.content.trim()}
                onClick={async () => {
                  const value = await mutate(`/api/me/memories/${draft.id}`, "PATCH", {
                    kind: draft.kind,
                    content: draft.content,
                    scenario: draft.scenario,
                    source_id: draft.source_id,
                    expires_at: draft.expires_at,
                    payload: draft.payload,
                    expected_version: draft.version,
                  });
                  if (value) {
                    setDraft(null);
                    await load();
                    setNotice("记忆已更新；下一轮生效。");
                  }
                }}
              >
                保存修改
              </Button>
              <Button disabled={busy} variant="outline" onClick={() => setDraft(null)}>
                取消
              </Button>
            </div>
          </section>
        )}
      </main>
    </>
  );
}
