"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  deleteThreadWithImpact,
  getThreadDeletionImpact,
  ThreadApiError,
  type ThreadDeletionImpact,
} from "@/lib/thread-api";

type ThreadDeleteConfirmationDialogProps = {
  open: boolean;
  threadId: string | null;
  onOpenChange: (open: boolean) => void;
  onDeleted: (threadId: string) => void;
};

function impactDescription(impact: ThreadDeletionImpact | null) {
  const lines = [
    "删除后会话正文和摘要会立即从在线会话库清除；当前没有会话恢复入口。",
  ];
  if (impact) {
    const rules = impact.linked_business_rules;
    lines.push(rules.count > 0
      ? `关联业务规则：${rules.count} 条${rules.labels.length ? `（${rules.labels.join("、")}）` : ""}。删除后会从在线召回中屏蔽，活动 Wren 版本的移除随后完成，可能影响同一数据源的其他会话。`
      : "关联业务规则：0 条。");
    const candidates = impact.linked_query_example_candidates.count;
    lines.push(`关联的未发布查询示例候选：${candidates} 条，删除后清除；已发布查询示例保留。`);
  }
  lines.push("AskDB不提供按会话查看或恢复聊天正文备份的功能。");
  return lines.join("\n\n");
}

export function ThreadDeleteConfirmationDialog({
  open,
  threadId,
  onOpenChange,
  onDeleted,
}: ThreadDeleteConfirmationDialogProps) {
  const [impact, setImpact] = useState<ThreadDeletionImpact | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const requestId = useRef(0);
  const idempotencyKey = useRef<string | null>(null);
  const callbacks = useRef({ onOpenChange, onDeleted });

  useEffect(() => {
    callbacks.current = { onOpenChange, onDeleted };
  }, [onDeleted, onOpenChange]);

  const loadImpact = useCallback(async (targetId: string, notice = "") => {
    const currentRequest = ++requestId.current;
    setLoading(true);
    setImpact(null);
    setError("");
    try {
      const nextImpact = await getThreadDeletionImpact(targetId);
      if (requestId.current !== currentRequest) return;
      if (!nextImpact) {
        callbacks.current.onDeleted(targetId);
        return;
      }
      setImpact(nextImpact);
      setError(notice);
      idempotencyKey.current = crypto.randomUUID().replaceAll("-", "");
    } catch (reason) {
      if (requestId.current !== currentRequest) return;
      setError(reason instanceof Error ? reason.message : "读取删除影响失败，请重试。");
    } finally {
      if (requestId.current === currentRequest) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open && threadId) {
      void loadImpact(threadId);
      return () => { requestId.current += 1; };
    }
    requestId.current += 1;
    setImpact(null);
    setError("");
    setLoading(false);
    setSubmitting(false);
    idempotencyKey.current = null;
  }, [loadImpact, open, threadId]);

  async function confirmDelete() {
    if (!threadId) return;
    if (!impact) {
      await loadImpact(threadId);
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await deleteThreadWithImpact(
        threadId,
        impact.impact_version,
        idempotencyKey.current ?? crypto.randomUUID().replaceAll("-", ""),
      );
      requestId.current += 1;
      callbacks.current.onDeleted(threadId);
    } catch (reason) {
      if (reason instanceof ThreadApiError && reason.code === "THREAD_DELETE_CONFIRMATION_STALE") {
        await loadImpact(threadId, "删除影响已变化。请核对更新后的摘要，再次确认删除。");
      } else {
        setError(reason instanceof Error ? reason.message : "删除会话失败，请重试。");
      }
    } finally {
      setSubmitting(false);
    }
  }

  const description = error
    ? `${error}\n\n${impactDescription(impact)}`
    : loading && !impact
      ? "正在读取删除影响…"
      : impactDescription(impact);

  return (
    <ConfirmDialog
      open={open}
      title="删除会话"
      description={description}
      descriptionClassName="whitespace-pre-line"
      confirmLabel={submitting ? "正在删除…" : loading ? "正在读取…" : impact ? "删除会话" : "重新读取影响"}
      confirmVariant="destructive"
      confirmDisabled={submitting || loading || (!impact && !error)}
      cancelDisabled={submitting}
      onConfirm={() => void confirmDelete()}
      onCancel={() => {
        if (!submitting) callbacks.current.onOpenChange(false);
      }}
    />
  );
}
