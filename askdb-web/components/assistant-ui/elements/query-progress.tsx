import type { QueryProgressStep } from "@/lib/query-progress";

type QueryProgressProps = {
  steps: QueryProgressStep[];
};

export function QueryProgress({ steps }: QueryProgressProps) {
  if (steps.length === 0) return null;
  const current = [...steps].reverse().find((step) => step.status === "running");

  return (
    <div
      data-slot="aui_query-progress"
      role="status"
      aria-live="polite"
      className="my-2 max-w-md rounded-xl border border-border/70 bg-muted/25 px-3 py-2.5 font-sans text-xs"
    >
      <ol className="flex flex-col gap-2">
        {steps.map((step) => {
          const active = step.status === "running";
          const failed = step.status === "failed";
          return (
            <li key={step.stepId} className="flex items-center gap-2.5">
              <span
                aria-hidden="true"
                className={
                  active
                    ? "text-primary animate-pulse motion-reduce:animate-none"
                    : failed
                      ? "text-destructive"
                      : "text-muted-foreground"
                }
              >
                {active ? "●" : failed ? "×" : "✓"}
              </span>
              <span className={active ? "text-foreground" : "text-muted-foreground"}>
                {step.label}
              </span>
              {active && <span className="sr-only">进行中</span>}
              {failed && <span className="sr-only">未完成</span>}
            </li>
          );
        })}
      </ol>
      {current && <span className="sr-only">当前步骤：{current.label}</span>}
    </div>
  );
}
