"use client";

import { type FC } from "react";
import { CheckIcon, ChevronDownIcon, CopyIcon } from "lucide-react";

import { TooltipIconButton } from "@/components/assistant-ui/elements/tooltip-icon-button";
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard";

type SqlDisclosureProps = {
  code: string;
};

export const SqlDisclosure: FC<SqlDisclosureProps> = ({ code }) => {
  const { isCopied, copyToClipboard } = useCopyToClipboard();
  const sqlPreview = code.trim().replace(/\s+/g, " ");

  return (
    <details className="aui-sql-disclosure group my-4 overflow-hidden rounded-xl border border-border/70 bg-background">
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-medium text-foreground [&::-webkit-details-marker]:hidden">
        <span className="min-w-0">
          <span className="block">SQL 语句</span>
          <span className="mt-1 block truncate font-mono text-xs font-normal text-muted-foreground">
            {sqlPreview}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-2 text-xs font-normal text-muted-foreground">
          <span className="group-open:hidden">展开 SQL</span>
          <span className="hidden group-open:inline">收起 SQL</span>
          <ChevronDownIcon
            aria-hidden="true"
            className="size-4 transition-transform duration-200 group-open:rotate-180"
          />
        </span>
      </summary>
      <div className="border-t border-border/70 bg-muted/20">
        <div className="flex items-center justify-between px-4 pt-2.5">
          <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            SQL
          </span>
          <TooltipIconButton
            type="button"
            tooltip={isCopied ? "已复制" : "复制 SQL"}
            disabled={!code || isCopied}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              if (code && !isCopied) copyToClipboard(code);
            }}
          >
            {!isCopied && <CopyIcon aria-hidden="true" />}
            {isCopied && <CheckIcon aria-hidden="true" />}
          </TooltipIconButton>
        </div>
        <pre className="max-h-72 overflow-auto px-4 pb-4 pt-2 font-mono text-xs leading-relaxed">
          <code>{code}</code>
        </pre>
      </div>
    </details>
  );
};
