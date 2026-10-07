"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { AdminGate } from "@/components/auth/admin-gate";
import { SettingsPageHeader } from "@/components/settings/settings-page-header";
import { MemoryServiceSettingsPanel } from "@/components/settings/memory-service-settings-page";
import { Button } from "@/components/ui/button";
import { ComposerSelect } from "@/components/ui/composer-select";
import { ArrowUpRightIcon, BookOpenIcon, BrainIcon } from "lucide-react";
import { fetchCurrentUser, type AuthUser } from "@/lib/auth-api";
import { fetchDataSourceCatalog, type DataSourceCatalog } from "@/lib/data-sources";
import {
  activateQueryCorpus,
  businessRuleAction,
  getMemoryOperation,
  listBusinessRules,
  listQueryCorpusRevisions,
  listQueryExamples,
  queryExampleAction,
  type BusinessRuleCandidate,
  type QueryCorpusRevision,
  type QueryExampleCandidate,
} from "@/lib/memory-api";
import { useCallback, useEffect, useMemo, useState } from "react";

export type MemoryManagementTab = "query-examples" | "business-rules" | "service";

const reviewLabels: Record<string, string> = {
  pending: "待审核",
  needs_clarification: "等你补充",
  needs_revalidation: "需要重新确认",
  approved: "已审核通过",
  rejected: "已拒绝",
  withdrawn: "已撤回",
  revoked: "已撤销",
  expired: "已过期",
};

const publicationLabels: Record<string, string> = {
  draft: "草稿",
  not_published: "尚未发布",
  queued: "排队发布",
  publishing: "发布中",
  prepared: "等待激活",
  active: "已生效",
  failed: "发布失败",
  superseded: "已被新版本替代",
  removal_pending: "正在移除",
  removed: "已移除",
};

function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "green" | "amber" | "red" }) {
  const style = tone === "green" ? "border-[#c9ddcc] bg-[#eef6ef] text-[#42684b]"
    : tone === "amber" ? "border-[#ead8b5] bg-[#fff8e8] text-[#8a6534]"
      : tone === "red" ? "border-[#edcfc4] bg-[#fff1ec] text-[#9f4f38]"
        : "border-[#e7e0d5] bg-[#f6f3ed] text-[#6b655c]";
  return <span className={`inline-flex items-center rounded-full border px-2.5 py-1 font-sans text-[10px] leading-none ${style}`}>{children}</span>;
}

function StatusPair({ review, publication }: { review: string; publication: string }) {
  const effective = publication === "active";
  const reviewTone = review === "rejected" || review === "revoked" ? "red"
    : review === "approved" ? "green" : review === "pending" ? "amber" : "neutral";
  const publishTone = effective ? "green"
    : publication === "failed" || publication === "removed" ? "red"
      : publication === "prepared" || publication === "queued" || publication === "publishing" || publication === "removal_pending" ? "amber" : "neutral";
  return <div className="flex flex-wrap gap-1.5"><Badge tone={reviewTone}>{reviewLabels[review] ?? review}</Badge><Badge tone={publishTone}>{publicationLabels[publication] ?? publication}</Badge></div>;
}

export function MemoryManagementPage({
  adminMode = false,
  initialTab = "query-examples",
  syncTabToUrl = false,
}: {
  adminMode?: boolean;
  initialTab?: MemoryManagementTab;
  syncTabToUrl?: boolean;
}) {
  const router = useRouter();
  const pageTitle = adminMode ? "记忆管理" : "我的提交";
  const pageDescription = adminMode
    ? "管理查询示例、业务规则与记忆处理服务配置。"
    : "查看你提交的查询示例和业务规则。审核通过后，还需要发布或激活才会用于回答。";
  const PageIcon = adminMode ? BrainIcon : BookOpenIcon;
  const [user, setUser] = useState<AuthUser | null>(null);
  const [catalog, setCatalog] = useState<DataSourceCatalog | null>(null);
  const [sourceId, setSourceId] = useState("");
  const [tab, setTab] = useState<MemoryManagementTab>(
    adminMode ? initialTab : initialTab === "service" ? "query-examples" : initialTab,
  );
  const [reviewFilter, setReviewFilter] = useState("");
  const [examples, setExamples] = useState<QueryExampleCandidate[]>([]);
  const [nextExampleCursor, setNextExampleCursor] = useState<string | null>(null);
  const [rules, setRules] = useState<BusinessRuleCandidate[]>([]);
  const [revisions, setRevisions] = useState<QueryCorpusRevision[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [working, setWorking] = useState("");

  const sources = useMemo(
    () => catalog?.data_sources.filter((source) => source.enabled) ?? [],
    [catalog],
  );
  const activeSource = sources.find((source) => source.id === sourceId);

  useEffect(() => {
    setTab(adminMode ? initialTab : initialTab === "service" ? "query-examples" : initialTab);
  }, [adminMode, initialTab]);

  function selectTab(nextTab: MemoryManagementTab) {
    const resolvedTab = !adminMode && nextTab === "service" ? "query-examples" : nextTab;
    setTab(resolvedTab);
    if (!syncTabToUrl) return;
    router.replace(
      resolvedTab === "query-examples" ? "/settings/memories" : `/settings/memories?tab=${resolvedTab}`,
      { scroll: false },
    );
  }

  useEffect(() => {
    let mounted = true;
    Promise.all([fetchCurrentUser(), fetchDataSourceCatalog()])
      .then(([currentUser, nextCatalog]) => {
        if (!mounted) return;
        setUser(currentUser);
        setCatalog(nextCatalog);
        const initialSource = nextCatalog.default_data_source_id ?? nextCatalog.data_sources[0]?.id ?? "";
        setSourceId(initialSource);
      })
      .catch((cause) => {
        if (mounted) setError(cause instanceof Error ? cause.message : "无法加载数据源列表。请刷新页面重试。");
      })
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, []);

  const load = useCallback(async (quiet = false) => {
    if (!sourceId) return;
    if (quiet) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const [exampleResult, ruleResult, revisionResult] = await Promise.all([
        listQueryExamples(sourceId),
        listBusinessRules(sourceId),
        user?.role === "admin" ? listQueryCorpusRevisions(sourceId) : Promise.resolve({ revisions: [] as QueryCorpusRevision[] }),
      ]);
      setExamples(exampleResult.candidates);
      setNextExampleCursor(exampleResult.next_cursor);
      setRules(ruleResult.candidates);
      setRevisions(revisionResult.revisions);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法读取记忆提交。请刷新后重试。");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [sourceId, user?.role]);

  useEffect(() => { void load(); }, [load]);

  async function runAction(key: string, action: () => Promise<unknown>, success: string) {
    if (working) return;
    setWorking(key);
    setError("");
    setNotice("");
    try {
      const result = await action();
      setNotice(typeof result === "string" ? result : success);
      await load(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "操作未完成。请保留当前页面并重试。");
    } finally {
      setWorking("");
    }
  }

  async function loadMoreExamples() {
    if (!sourceId || !nextExampleCursor || refreshing) return;
    setRefreshing(true);
    setError("");
    try {
      const result = await listQueryExamples(sourceId, nextExampleCursor);
      setExamples((current) => [...current, ...result.candidates]);
      setNextExampleCursor(result.next_cursor);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法读取更多查询示例。请重试。");
    } finally {
      setRefreshing(false);
    }
  }

  async function publishRule(candidate: BusinessRuleCandidate): Promise<string> {
    const result = await businessRuleAction(candidate.business_rule_id, "publish", { expected_version: candidate.version });
    const operation = result.operation && typeof result.operation === "object" ? result.operation as Record<string, unknown> : null;
    const operationId = operation && typeof operation.id === "string" ? operation.id : null;
    if (!operationId) return "已开始发布；记忆会在新版本完成后生效。";
    for (let attempt = 0; attempt < 10; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 1500));
      const status = await getMemoryOperation(operationId);
      if (status.status === "active" || status.status === "failed") {
        return status.status === "active" ? "发布完成，业务规则已生效。" : "发布失败；规则尚未生效，可查看状态后重试。";
      }
    }
    return "发布仍在进行，可刷新列表查看最新状态。";
  }

  if (loading && !catalog && !(adminMode && tab === "service")) {
    return (
      <>
        <SettingsPageHeader
          title={pageTitle}
          description={pageDescription}
          icon={PageIcon}
        />
        <main className="min-h-full bg-[#f7f5f0] px-5 py-12 text-sm text-[#77736b]">
          <p role="status">正在读取记忆提交…</p>
        </main>
      </>
    );
  }

  return (
    <>
      <SettingsPageHeader
        title={pageTitle}
        description={pageDescription}
        icon={PageIcon}
      />
      <main className="min-h-full bg-[#f7f5f0] px-5 py-8 text-[#393630] sm:px-8 lg:px-10">
        <div className="mx-auto max-w-5xl">
          <div className="mb-4 flex w-fit max-w-full flex-wrap gap-1 rounded-xl bg-[#f0ede6] p-1" role="tablist" aria-label="记忆管理区域">
            <button id="memory-tab-query-examples" type="button" role="tab" aria-selected={tab === "query-examples"} aria-controls="memory-management-panel" onClick={() => selectTab("query-examples")} className={`rounded-lg px-3 py-2 font-sans text-xs transition-colors focus-visible:ring-2 focus-visible:ring-[#c57650] ${tab === "query-examples" ? "bg-white text-[#514b42] shadow-sm" : "text-[#77736b] hover:text-[#514b42]"}`}>查询示例 <span className="ml-1 text-[10px] text-[#968f83]">{examples.length}</span></button>
            <button id="memory-tab-business-rules" type="button" role="tab" aria-selected={tab === "business-rules"} aria-controls="memory-management-panel" onClick={() => selectTab("business-rules")} className={`rounded-lg px-3 py-2 font-sans text-xs transition-colors focus-visible:ring-2 focus-visible:ring-[#c57650] ${tab === "business-rules" ? "bg-white text-[#514b42] shadow-sm" : "text-[#77736b] hover:text-[#514b42]"}`}>业务规则 <span className="ml-1 text-[10px] text-[#968f83]">{rules.length}</span></button>
            {adminMode && <button id="memory-tab-service" type="button" role="tab" aria-selected={tab === "service"} aria-controls="memory-service-panel" onClick={() => selectTab("service")} className={`rounded-lg px-3 py-2 font-sans text-xs transition-colors focus-visible:ring-2 focus-visible:ring-[#c57650] ${tab === "service" ? "bg-white text-[#514b42] shadow-sm" : "text-[#77736b] hover:text-[#514b42]"}`}>服务配置</button>}
          </div>
          <div id="memory-management-panel" role="tabpanel" aria-labelledby={tab === "business-rules" ? "memory-tab-business-rules" : "memory-tab-query-examples"} hidden={adminMode && tab === "service"}>
          <div className="mb-4 flex flex-wrap items-center justify-end gap-2 font-sans">
            <label htmlFor="memory-source" className="text-xs text-[#89847a]">数据源</label>
            <ComposerSelect
              id="memory-source"
              ariaLabel="数据源"
              value={sourceId}
              disabled={sources.length === 0}
              placeholder="选择数据源"
              options={[
                ...(sources.length === 0 ? [{ value: "", label: "没有可用数据源" }] : []),
                ...sources.map((source) => ({ value: source.id, label: source.display_name })),
              ]}
              onValueChange={setSourceId}
              triggerClassName="h-9 max-w-[min(22rem,70vw)] rounded-lg border border-[#e3dbcf] bg-[#fbfaf7] px-3 py-2 text-xs text-[#514b42] focus-visible:ring-[#c57650]/20"
            />
            <Button variant="outline" size="sm" disabled={refreshing || !sourceId} onClick={() => void load(true)}>{refreshing ? "刷新中…" : "刷新"}</Button>
          </div>
          <section className="rounded-2xl border border-[#e7e0d5] bg-[#fbfaf7] p-4 shadow-[0_2px_10px_rgba(66,53,37,0.025)] sm:p-5">
          <div className="flex flex-wrap items-center justify-end gap-3">
            <div className="flex items-center gap-2 font-sans">
              <label htmlFor="memory-review-filter" className="text-[11px] text-[#89847a]">审核状态</label>
              <ComposerSelect
                id="memory-review-filter"
                ariaLabel="审核状态"
                value={reviewFilter}
                placeholder="全部"
                options={[
                  { value: "", label: "全部" },
                  { value: "pending", label: "待审核" },
                  { value: "needs_clarification", label: "等你补充" },
                  { value: "needs_revalidation", label: "需要重新确认" },
                  { value: "approved", label: "已审核通过" },
                  { value: "rejected", label: "已拒绝" },
                  { value: "withdrawn", label: "已撤回" },
                  { value: "revoked", label: "已撤销" },
                  { value: "expired", label: "已过期" },
                ]}
                onValueChange={setReviewFilter}
                triggerClassName="h-8 rounded-lg border border-[#e7e0d5] bg-[#fbfaf7] px-2.5 py-1.5 text-xs focus-visible:border-[#c57650]"
              />
            </div>
          </div>

          {error && <p role="alert" className="mt-4 rounded-xl border border-[#edcfc4] bg-[#fff1ec] px-4 py-3 text-sm leading-6 text-[#9f4f38]">{error}</p>}
          {notice && <p role="status" className="mt-4 rounded-xl border border-[#d7e2d1] bg-[#f1f7ee] px-4 py-3 text-sm leading-6 text-[#4b6748]">{notice}</p>}

          {tab === "query-examples" ? (
            <div className="mt-5 space-y-3">
              {adminMode && <PreparedRevisions revisions={revisions} sourceId={sourceId} working={working} runAction={runAction} />}
              {loading ? <p role="status" className="px-2 py-8 text-center text-sm text-[#89847a]">正在加载查询示例…</p>
                : filterExamples(examples, reviewFilter).length === 0 ? <EmptyState title="这里还没有查询示例" body="在一条成功查询的回答下，打开“…”菜单即可提交；提交后会先等待管理员审核。" />
                  : filterExamples(examples, reviewFilter).map((candidate) => <QueryExampleCard key={candidate.query_example_id} candidate={candidate} user={user} isAdmin={adminMode} sourceName={activeSource?.display_name ?? sourceId} working={working} runAction={runAction} />)}
              {nextExampleCursor && !loading && <div className="pt-1 text-center"><Button variant="outline" size="sm" disabled={refreshing} onClick={() => void loadMoreExamples()}>{refreshing ? "正在加载…" : "加载更多查询示例"}</Button></div>}
            </div>
          ) : (
            <div className="mt-5 space-y-3">
              {loading ? <p role="status" className="px-2 py-8 text-center text-sm text-[#89847a]">正在加载业务规则…</p>
                : filterRules(rules, reviewFilter).length === 0 ? <EmptyState title="这里还没有业务规则" body="从一条对话的“…”菜单填写业务术语和口径；不会自动保存整段问答。" />
                  : filterRules(rules, reviewFilter).map((candidate) => <BusinessRuleCard key={candidate.business_rule_id} candidate={candidate} user={user} isAdmin={adminMode} sourceName={activeSource?.display_name ?? sourceId} working={working} runAction={runAction} publishRule={publishRule} />)}
            </div>
          )}
          <p className="mt-5 border-t border-[#eee8de] pt-4 font-sans text-[11px] leading-5 text-[#8d867b]">
            {adminMode ? "审核通过只代表内容审核完成。查询示例需激活语料版本；业务规则需发布到新的数据源版本。" : "提交内容由服务端按当前账号和数据源权限隔离。待审核、被拒绝或尚未发布的内容不会用于回答。"}
          </p>
        </section>
          </div>
          {adminMode && (
            <div id="memory-service-panel" role="tabpanel" aria-labelledby="memory-tab-service" hidden={tab !== "service"} className="mt-4">
              <MemoryServiceSettingsPanel active={tab === "service"} />
            </div>
          )}
      </div>
      </main>
    </>
  );
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return <div className="rounded-xl border border-dashed border-[#ddd4c6] bg-[#f8f6f1] px-5 py-10 text-center">
    <p className="font-serif text-lg text-[#514b42]">{title}</p><p className="mx-auto mt-2 max-w-md text-sm leading-6 text-[#89847a]">{body}</p>
  </div>;
}

function filterExamples(items: QueryExampleCandidate[], filter: string) {
  return filter ? items.filter((item) => item.review_status === filter) : items;
}

function filterRules(items: BusinessRuleCandidate[], filter: string) {
  return filter ? items.filter((item) => item.review_status === filter) : items;
}

function QueryExampleCard({ candidate, user, isAdmin, sourceName, working, runAction }: {
  candidate: QueryExampleCandidate;
  user: AuthUser | null;
  isAdmin: boolean;
  sourceName: string;
  working: string;
  runAction: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("incorrect_semantics");
  const id = candidate.query_example_id;
  const mine = candidate.submitted_by === user?.user_id;
  const transition = { expected_version: candidate.version };
  return <article className="grid gap-4 rounded-xl border border-[#eae4da] bg-white/75 p-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:p-5">
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-2"><Badge>{sourceName}</Badge><StatusPair review={candidate.review_status} publication={candidate.publication_status} /></div>
      <h2 className="mt-3 font-serif text-lg leading-6 text-[#403c35]">{candidate.normalized_question || "查询内容已移除"}</h2>
      {candidate.sql_template && <pre className="mt-3 max-h-40 overflow-auto rounded-lg bg-[#f5f2ec] p-3 font-mono text-[11px] leading-5 text-[#696257]">{candidate.sql_template}</pre>}
      <p className="mt-2 font-sans text-[10px] text-[#979084]">提交于 {new Date(candidate.created_at).toLocaleString("zh-CN")} · 到期 {new Date(candidate.expires_at).toLocaleDateString("zh-CN")}</p>
      {candidate.review_reason_code && <p className="mt-2 text-xs text-[#925841]">处理说明：{candidate.review_reason_code}</p>}
    </div>
    <div className="flex flex-wrap content-start gap-2 sm:max-w-48 sm:justify-end">
      {isAdmin && candidate.review_status === "pending" && <ActionButton busy={working === id} onClick={() => runAction(id, () => queryExampleAction(id, "approve", transition), "审核已通过，查询示例还没有生效；请在上方激活准备好的语料版本。")}>审核并准备</ActionButton>}
      {isAdmin && candidate.review_status === "needs_revalidation" && <ActionButton busy={working === id} onClick={() => runAction(id, () => queryExampleAction(id, "revalidate", transition), "已重新检查查询示例；如准备了新语料，请单独激活。")}>重新检查</ActionButton>}
      {isAdmin && ["pending", "needs_revalidation"].includes(candidate.review_status) && <>
        <ComposerSelect
          id={`query-example-reject-reason-${id}`}
          ariaLabel="拒绝原因"
          value={reason}
          placeholder="选择拒绝原因"
          options={[
            { value: "incorrect_semantics", label: "口径不正确" },
            { value: "incorrect_sql", label: "查询有误" },
            { value: "unsafe_template", label: "模板不安全" },
            { value: "duplicate", label: "重复提交" },
            { value: "other", label: "其他" },
          ]}
          onValueChange={setReason}
          triggerClassName="h-7 rounded-lg border border-[#e6ded2] bg-[#fbfaf7] px-2 font-sans text-[10px] text-[#655e54] focus-visible:border-[#c57650]"
        />
        <ActionButton variant="quiet" busy={working === id} onClick={() => runAction(id, () => queryExampleAction(id, "reject", { ...transition, reason_code: reason }), "已拒绝此查询示例。")}>拒绝</ActionButton>
      </>}
      {mine && candidate.review_status === "pending" && <ActionButton variant="quiet" busy={working === id} onClick={() => runAction(id, () => queryExampleAction(id, "withdraw", transition), "已撤回提交。")}>撤回</ActionButton>}
      {isAdmin && candidate.publication_status === "active" && candidate.review_status !== "revoked" && <ActionButton variant="quiet" busy={working === id} onClick={() => runAction(id, () => queryExampleAction(id, "revoke", { idempotency_key: makeActionKey() }), "已撤销；线上召回已立即停止。")}>撤销生效内容</ActionButton>}
    </div>
  </article>;
}

function BusinessRuleCard({ candidate, user, isAdmin, sourceName, working, runAction, publishRule }: {
  candidate: BusinessRuleCandidate;
  user: AuthUser | null;
  isAdmin: boolean;
  sourceName: string;
  working: string;
  runAction: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
  publishRule: (candidate: BusinessRuleCandidate) => Promise<string>;
}) {
  const [reason, setReason] = useState("ambiguous_definition");
  const [askingClarification, setAskingClarification] = useState(false);
  const [clarificationQuestion, setClarificationQuestion] = useState("请补充这条口径的适用范围和判断条件。");
  const id = candidate.business_rule_id;
  const mine = candidate.submitted_by === user?.user_id;
  const publishedContentRemoved =
    candidate.publication_status === "active" && !candidate.term && !candidate.definition;
  const transition = { expected_version: candidate.version };
  return <article className="rounded-xl border border-[#eae4da] bg-white/75 p-4 sm:p-5">
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-2"><Badge>{sourceName}</Badge><StatusPair review={candidate.review_status} publication={candidate.publication_status} /></div>
      <h2 className="mt-3 font-serif text-lg leading-6 text-[#403c35]">{candidate.term || (publishedContentRemoved ? "业务规则已发布" : "规则内容已移除")}</h2>
      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-[#6b655c]">{candidate.definition || (publishedContentRemoved ? "当前生效正文以数据源的活动版本为准；此提交记录保留来源和审核信息。" : "规则正文已移除。")}</p>
      {publishedContentRemoved && isAdmin && <Link
        href={`/settings/wren?data_source_id=${encodeURIComponent(candidate.data_source_id)}&revision=active#business-rules`}
        className="mt-2 inline-flex items-center gap-1 font-sans text-xs font-medium text-[#a45f40] underline underline-offset-4 transition-colors hover:text-[#82452f] focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:ring-offset-2"
      >
        查看当前活动版本
        <ArrowUpRightIcon aria-hidden="true" className="size-3.5" />
      </Link>}
      {candidate.mdl_references.length > 0 && <p className="mt-3 font-sans text-[11px] text-[#837b70]">关联：{candidate.mdl_references.join(" · ")}</p>}
      {candidate.has_exact_term_conflict && <p className="mt-3 rounded-lg bg-[#fff8e8] px-3 py-2 text-xs text-[#8a6534]">发现同名规则，需要管理员核对或要求补充说明。</p>}
      {candidate.clarification_question && <p className="mt-3 rounded-lg bg-[#f5f2ec] px-3 py-2 text-xs leading-5 text-[#615b51]">管理员请补充：{candidate.clarification_question}</p>}
      <p className="mt-2 font-sans text-[10px] text-[#979084]">提交于 {new Date(candidate.created_at).toLocaleString("zh-CN")} · 到期 {new Date(candidate.expires_at).toLocaleDateString("zh-CN")}</p>
      {candidate.review_reason_code && <p className="mt-2 text-xs text-[#925841]">审核说明：{candidate.review_reason_code === "admin_approved" ? "管理员已审核通过" : candidate.review_reason_code}</p>}
    </div>
    <div className="mt-5 flex flex-col gap-4 border-t border-[#eee8de] pt-4 sm:flex-row sm:items-center sm:justify-between">
      {mine && candidate.review_status === "pending" && <div className="flex shrink-0 flex-col items-start gap-1.5">
        <span className="font-sans text-[10px] text-[#89847a]">提交人操作</span>
        <ActionButton variant="quiet" className="h-10 px-4" busy={working === id} onClick={() => runAction(id, () => businessRuleAction(id, "withdraw", transition), "已撤回提交。")}>撤回</ActionButton>
      </div>}
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <div className="flex flex-wrap items-center justify-start gap-2 sm:justify-end">
          {isAdmin && candidate.review_status === "pending" && <button type="button" aria-expanded={askingClarification} aria-controls={`clarification-panel-${id}`} onClick={() => setAskingClarification((value) => !value)} className="inline-flex h-10 items-center rounded-lg px-3 font-sans text-xs font-medium text-[#a45f40] transition-colors hover:bg-[#fbf6f0] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:ring-offset-2">
            {askingClarification ? "取消补充请求" : "请求补充"}
          </button>}
          {isAdmin && candidate.review_status === "pending" && <>
            <ComposerSelect
              id={`business-rule-reject-reason-${id}`}
              ariaLabel="拒绝原因"
              value={reason}
              placeholder="选择拒绝原因"
              options={[
                { value: "ambiguous_definition", label: "口径不清楚" },
                { value: "duplicate_term", label: "已有同名规则" },
                { value: "unsupported_scope", label: "适用范围不支持" },
                { value: "invalid_reference", label: "引用无效" },
                { value: "conflicting_rule", label: "与现有规则冲突" },
                { value: "other", label: "其他" },
              ]}
              onValueChange={setReason}
              triggerClassName="h-10 min-w-36 rounded-lg border border-[#e6ded2] bg-[#fbfaf7] px-3 font-sans text-xs text-[#655e54] focus-visible:border-[#c57650]"
            />
            <ActionButton variant="quiet" className="h-10 px-4" busy={working === id} onClick={() => runAction(id, () => businessRuleAction(id, "reject", { ...transition, reason_code: reason }), "已拒绝此业务规则。")}>拒绝</ActionButton>
          </>}
          {isAdmin && candidate.review_status === "pending" && <ActionButton className="h-10 px-4" busy={working === id} onClick={() => runAction(id, () => businessRuleAction(id, "approve", transition), "审核已通过；还需要发布到新的数据源版本后才会生效。")}>审核通过</ActionButton>}
          {isAdmin && candidate.review_status === "approved" && candidate.publication_status !== "active" && <ActionButton className="h-10 px-4" busy={working === id} onClick={() => runAction(id, () => publishRule(candidate), "发布任务已提交。")}>发布到数据源</ActionButton>}
          {isAdmin && candidate.publication_status === "active" && <ActionButton variant="quiet" className="h-10 px-4" busy={working === id} onClick={() => runAction(id, () => businessRuleAction(id, "revoke", { idempotency_key: makeActionKey() }), "已撤销；线上召回已立即停止，正在从当前版本移除。")}>撤销生效内容</ActionButton>}
          {mine && candidate.review_status === "needs_clarification" && <ClarificationForm candidate={candidate} runAction={runAction} working={working} />}
        </div>
        {isAdmin && candidate.review_status === "pending" && <div id={`clarification-panel-${id}`} hidden={!askingClarification} className="w-full sm:ml-auto sm:max-w-xl">
          <textarea aria-label="需要提交人补充的问题" value={clarificationQuestion} onChange={(event) => setClarificationQuestion(event.target.value)} rows={3} className="w-full rounded-lg border border-[#e3dbcf] bg-white p-3 font-sans text-xs leading-5 outline-none transition-colors focus:border-[#c57650] focus:ring-2 focus:ring-[#c57650]/20" />
          <div className="mt-2 flex justify-end">
            <ActionButton className="h-10 px-4" busy={working === id} onClick={() => runAction(id, () => businessRuleAction(id, "clarification-request", { ...transition, question: clarificationQuestion.trim() }), "已请求提交人补充说明。")}>发送请求</ActionButton>
          </div>
        </div>}
      </div>
    </div>
  </article>;
}

function ClarificationForm({ candidate, runAction, working }: {
  candidate: BusinessRuleCandidate;
  runAction: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
  working: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [definition, setDefinition] = useState(candidate.definition ?? "");
  const id = candidate.business_rule_id;
  return <div className="w-full sm:text-right">
    <button type="button" className="text-xs text-[#a45f40] underline underline-offset-4" onClick={() => setExpanded((value) => !value)}>{expanded ? "收起补充" : "补充说明"}</button>
    {expanded && <form onSubmit={(event) => { event.preventDefault(); void runAction(id, () => businessRuleAction(id, "clarification-response", { expected_version: candidate.version, term: candidate.term ?? "", definition, mdl_references: candidate.mdl_references }), "补充说明已提交，等待管理员复核。"); }} className="mt-2 w-full space-y-2 text-left">
      <textarea required value={definition} onChange={(event) => setDefinition(event.target.value)} rows={4} className="w-full rounded-lg border border-[#e3dbcf] bg-white p-2 text-xs outline-none focus:border-[#c57650]" />
      <Button type="submit" size="sm" disabled={working === id}>提交补充</Button>
    </form>}
  </div>;
}

function PreparedRevisions({ revisions, sourceId, working, runAction }: {
  revisions: QueryCorpusRevision[];
  sourceId: string;
  working: string;
  runAction: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const prepared = revisions.filter((revision) => revision.status === "prepared");
  if (prepared.length === 0) return null;
  return <section className="rounded-xl border border-[#e5d8c3] bg-[#faf6ed] p-4">
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <h2 className="font-serif text-base text-[#514b42]">待激活的查询语料</h2>
      <p className="font-sans text-[10px] text-[#8d806c]">激活后才会用于回答；此操作会切换当前数据源的查询示例版本。</p>
    </div>
    <div className="mt-3 space-y-2">
      {prepared.map((revision) => <div key={revision.corpus_revision} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[#e9e1d5] bg-white/80 px-3 py-2.5">
        <div className="font-sans text-xs text-[#615b51]">语料版本 {revision.corpus_revision} <span className="ml-2 text-[10px] text-[#928a7d]">{revision.record_ids.length} 条 · {revision.content_hash.slice(0, 12)}</span></div>
        <ActionButton busy={working === `corpus-${revision.corpus_revision}`} onClick={() => runAction(`corpus-${revision.corpus_revision}`, () => activateQueryCorpus(sourceId, revision.corpus_revision), "查询语料已激活。")}>激活此版本</ActionButton>
      </div>)}
    </div>
  </section>;
}

function ActionButton({ children, onClick, busy, variant = "primary", className }: {
  children: React.ReactNode;
  onClick: () => void;
  busy: boolean;
  variant?: "primary" | "quiet";
  className?: string;
}) {
  return <Button type="button" size="sm" variant={variant === "quiet" ? "outline" : "default"} disabled={busy} onClick={onClick} className={`${variant === "primary" ? "bg-[#bd7551] text-white hover:bg-[#a96242]" : "border-[#e6ded2] bg-[#fbfaf7] text-[#655e54] hover:bg-[#f1eee7]"} ${className ?? ""}`}>
    {busy ? "处理中…" : children}
  </Button>;
}

function makeActionKey() {
  return crypto.randomUUID().replaceAll("-", "");
}

export function AdminMemoryManagementPageRoute({
  initialTab = "query-examples",
  syncTabToUrl = false,
}: {
  initialTab?: MemoryManagementTab;
  syncTabToUrl?: boolean;
}) {
  return <AdminGate><MemoryManagementPage adminMode initialTab={initialTab} syncTabToUrl={syncTabToUrl} /></AdminGate>;
}
