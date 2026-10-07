import {
  memoryRecallKindLabel,
  memoryRecallSummary,
  type MemoryRecallKind,
  type MemoryRecallPayload,
} from "@/lib/memory-recall-notice";

const MEMORY_RECALL_GROUPS: readonly MemoryRecallKind[] = [
  "query_example",
  "business_rule",
  "schema",
];

export function MemoryRecallNotice({ payload }: { payload: MemoryRecallPayload }) {
  const summary = memoryRecallSummary(payload.counts);
  if (!summary) return null;

  return (
    <details
      data-slot="aui_memory-recall-notice"
      className="group my-1 ml-2 max-w-md font-sans text-xs text-muted-foreground"
    >
      <summary className="flex min-h-7 w-fit max-w-full cursor-pointer list-none items-center gap-1.5 whitespace-nowrap rounded-md bg-muted/35 px-2.5 py-1.5 text-left transition-colors hover:bg-muted/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
        <span className="min-w-0 truncate">{summary}</span>
        <span className="shrink-0 font-medium text-foreground/70">查看详情</span>
        <svg
          aria-hidden="true"
          viewBox="0 0 16 16"
          className="size-3 shrink-0 transition-transform group-open:rotate-180"
          fill="none"
        >
          <path
            d="m4 6 4 4 4-4"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="1.5"
          />
        </svg>
      </summary>
      <div className="mt-2 max-h-72 max-w-md overflow-y-auto rounded-md border border-border/70 bg-background px-3 py-2.5">
        {MEMORY_RECALL_GROUPS.map((kind) => {
          const items = payload.items.filter((item) => item.kind === kind);
          if (items.length === 0) return null;

          return (
            <section key={kind} className="mt-3 first:mt-0">
              <h3 className="text-[11px] font-medium text-muted-foreground">
                {memoryRecallKindLabel(kind)}
              </h3>
              <ul className="mt-1.5 space-y-2">
                {items.map((item, index) => (
                  <li key={`${kind}-${index}`} className="border-l-2 border-border/70 pl-2.5">
                    <p className="break-words text-xs font-medium leading-5 text-foreground">
                      {item.title}
                      {item.titleTruncated ? (
                        <span className="ml-1 font-normal text-muted-foreground">
                          （标题已截断）
                        </span>
                      ) : null}
                    </p>
                    {item.detail ? (
                      <p className="mt-0.5 whitespace-pre-wrap break-words text-xs leading-5 text-muted-foreground">
                        {item.detail}
                        {item.detailTruncated ? "（详情已截断）" : ""}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
        {payload.items.length === 0 ? (
          <p className="text-xs leading-5 text-muted-foreground">
            本轮已提供参考资料，但当前没有可展示的详情。
          </p>
        ) : null}
      </div>
    </details>
  );
}
