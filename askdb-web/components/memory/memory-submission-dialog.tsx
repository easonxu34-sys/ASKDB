"use client";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { ComposerSelect } from "@/components/ui/composer-select";
import { createIdempotencyKey, submitBusinessRule, submitQueryExample } from "@/lib/memory-api";
import type { AuthUser } from "@/lib/auth-api";
import { useEffect, useState } from "react";

export type MemorySubmissionContext = {
  threadId: string;
  dataSourceId: string;
  sourceTurnKey: string;
  question: string;
  sqlTemplate: string;
};

type MemoryMode = "query-example" | "business-rule";
type ParameterSpec = { name: string; value_type: string; nullable: boolean };

const fieldClass = "w-full rounded-xl border border-[#e3dbcf] bg-white/80 px-3 py-2.5 font-sans text-sm text-[#393630] outline-none placeholder:text-[#a19a8f] focus:border-[#c57650] focus:ring-2 focus:ring-[#c57650]/20";

export function MemorySubmissionDialog({
  open,
  onOpenChange,
  mode,
  context,
  user,
  onSubmitted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: MemoryMode;
  context: MemorySubmissionContext | null;
  user: AuthUser;
  onSubmitted: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [sqlTemplate, setSqlTemplate] = useState("");
  const [parameters, setParameters] = useState<ParameterSpec[]>([]);
  const [term, setTerm] = useState("");
  const [definition, setDefinition] = useState("");
  const [references, setReferences] = useState("");
  const [confirmedSafe, setConfirmedSafe] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    if (!open) return;
    setQuestion(context?.question ?? "");
    setSqlTemplate(context?.sqlTemplate ?? "");
    setParameters([]);
    setTerm("");
    setDefinition("");
    setReferences("");
    setConfirmedSafe(false);
    setError("");
    setSubmitted(false);
  }, [context, mode, open]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!context || saving) return;
    setSaving(true);
    setError("");
    try {
      if (mode === "query-example") {
        await submitQueryExample({
          thread_id: context.threadId,
          source_turn_key: context.sourceTurnKey,
          idempotency_key: createIdempotencyKey(),
          question: question.trim(),
          sql_template: sqlTemplate.trim(),
          parameter_specs: parameters.filter((item) => item.name.trim()).map((item) => ({
            ...item,
            name: item.name.trim(),
          })),
        });
      } else {
        await submitBusinessRule({
          data_source_id: context.dataSourceId,
          thread_id: context.threadId,
          idempotency_key: createIdempotencyKey(),
          term: term.trim(),
          definition: definition.trim(),
          mdl_references: references.split(/[,，\n]/).map((value) => value.trim()).filter(Boolean),
        });
      }
      setSubmitted(true);
      onSubmitted();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "提交失败，请保留内容后重试。");
    } finally {
      setSaving(false);
    }
  }

  const isQuery = mode === "query-example";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[min(90dvh,52rem)] max-w-2xl overflow-y-auto border-[#e7e0d5] bg-[#fbfaf7] p-0 text-[#393630] shadow-[0_24px_80px_rgba(43,37,28,0.22)]">
        <DialogHeader className="border-b border-[#eee8de] px-6 py-5 pr-12 sm:px-7">
          <p className="font-sans text-[10px] font-semibold tracking-[0.18em] text-[#a66a4d]">ASKDB · 记忆提交</p>
          <DialogTitle className="mt-2 font-serif text-xl font-medium">
            {submitted ? "提交已收到" : isQuery ? "提交为查询示例" : "提交为业务规则"}
          </DialogTitle>
          <DialogDescription className="leading-6 text-[#77736b]">
            {submitted
              ? "当前内容只进入待审核列表，尚未影响 AskDB 的回答。"
              : "请检查下面将要提交的内容。提交后不会立即影响回答；审核和发布/激活完成后才会生效。"}
          </DialogDescription>
        </DialogHeader>

        {submitted ? (
          <div className="px-6 py-8 text-sm leading-6 text-[#615b51] sm:px-7">
            <p>你可以在“我的提交”中查看审核和生效进度。</p>
            <a className="mt-4 inline-flex text-[#a45f40] underline underline-offset-4" href="/settings">
              查看我的提交
            </a>
          </div>
        ) : (
          <form onSubmit={(event) => void handleSubmit(event)} className="space-y-5 px-6 py-5 sm:px-7">
            {isQuery ? (
              <>
                <label className="block space-y-1.5">
                  <span className="font-sans text-xs font-medium text-[#625b51]">用户问题</span>
                  <textarea required maxLength={1000} rows={2} value={question} onChange={(event) => setQuestion(event.target.value)} className={fieldClass} />
                </label>
                <label className="block space-y-1.5">
                  <span className="font-sans text-xs font-medium text-[#625b51]">只读 SQL 模板</span>
                  <textarea required maxLength={20000} rows={8} spellCheck={false} value={sqlTemplate} onChange={(event) => setSqlTemplate(event.target.value)} className={`${fieldClass} font-mono text-xs leading-5`} />
                </label>
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="font-sans text-xs font-medium text-[#625b51]">命名参数类型</p>
                      <p className="mt-1 text-[11px] text-[#8a8378]">SQL 中的 :参数名 要与这里一致；WHERE / HAVING 的筛选值必须参数化，不能提交实际值。</p>
                    </div>
                    <Button type="button" variant="outline" size="sm" onClick={() => setParameters((items) => [...items, { name: "", value_type: "string", nullable: false }])}>
                      添加参数
                    </Button>
                  </div>
                  {parameters.map((item, index) => (
                    <div key={index} className="grid grid-cols-[minmax(0,1fr)_9rem_auto] gap-2">
                      <input aria-label={`参数 ${index + 1} 名称`} placeholder="参数名（不含冒号）" value={item.name} onChange={(event) => setParameters((items) => items.map((current, at) => at === index ? { ...current, name: event.target.value } : current))} className={fieldClass} />
                      <ComposerSelect
                        id={`parameter-type-${index}`}
                        ariaLabel={`参数 ${index + 1} 类型`}
                        value={item.value_type}
                        placeholder="选择参数类型"
                        options={[
                          { value: "string", label: "文本" },
                          { value: "integer", label: "整数" },
                          { value: "decimal", label: "小数" },
                          { value: "boolean", label: "是/否" },
                          { value: "date", label: "日期" },
                          { value: "datetime", label: "日期时间" },
                        ]}
                        onValueChange={(value) => setParameters((items) => items.map((current, at) => at === index ? { ...current, value_type: value } : current))}
                        triggerClassName={`h-11 max-w-full ${fieldClass}`}
                      />
                      <Button type="button" variant="ghost" size="sm" aria-label={`删除参数 ${index + 1}`} onClick={() => setParameters((items) => items.filter((_, at) => at !== index))}>移除</Button>
                    </div>
                  ))}
                </div>
                <label className="flex gap-2.5 rounded-xl border border-[#e9e1d5] bg-[#f5f1e9] px-3.5 py-3 font-sans text-xs leading-5 text-[#615b51]">
                  <input type="checkbox" checked={confirmedSafe} onChange={(event) => setConfirmedSafe(event.target.checked)} className="mt-1 accent-[#bd7551]" />
                  <span>我已检查模板：WHERE / HAVING 中的筛选值都改成命名参数；没有放入实际参数值、查询结果或整段对话。</span>
                </label>
              </>
            ) : (
              <>
                <label className="block space-y-1.5">
                  <span className="font-sans text-xs font-medium text-[#625b51]">业务术语</span>
                  <input required maxLength={512} value={term} onChange={(event) => setTerm(event.target.value)} placeholder="例如：活跃客户" className={fieldClass} />
                </label>
                <label className="block space-y-1.5">
                  <span className="font-sans text-xs font-medium text-[#625b51]">含义和使用口径</span>
                  <textarea required maxLength={10000} rows={5} value={definition} onChange={(event) => setDefinition(event.target.value)} placeholder="用业务人员能理解的话说明这个词代表什么。" className={fieldClass} />
                </label>
                <label className="block space-y-1.5">
                  <span className="font-sans text-xs font-medium text-[#625b51]">对应模型或字段（可选）</span>
                  <textarea maxLength={3000} rows={2} value={references} onChange={(event) => setReferences(event.target.value)} placeholder="每行或用逗号分隔，例如：Orders.status" className={fieldClass} />
                </label>
                <p className="rounded-xl border border-[#e9e1d5] bg-[#f5f1e9] px-3.5 py-3 font-sans text-xs leading-5 text-[#615b51]">
                  只提交上面填写的术语和口径，不会保存整段问答。当前账号：{user.username}
                </p>
              </>
            )}

            {error && <p role="alert" className="rounded-lg bg-[#fff1ec] px-3 py-2 text-sm text-[#9f442e]">{error}</p>}
            <DialogFooter className="border-t border-[#eee8de] pt-4">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>取消</Button>
              <Button type="submit" disabled={saving || (isQuery && !confirmedSafe)} className="bg-[#bd7551] text-white hover:bg-[#a96242]">
                {saving ? "正在提交…" : "提交审核"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
