import Link from "next/link";
import { ChevronLeftIcon, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

type SettingsPageHeaderProps = {
  title: string;
  description: string;
  icon: LucideIcon;
  rightSlot?: ReactNode;
};

export function SettingsPageHeader({
  title,
  description,
  icon: Icon,
  rightSlot,
}: SettingsPageHeaderProps) {
  return (
    <header className="relative z-10 border-b border-[#e7e2d8] bg-[#f8f6f1]/95 backdrop-blur md:sticky md:top-0">
      <div className="mx-auto flex min-h-16 max-w-[1440px] items-center gap-3 px-4 py-3 sm:px-8">
        <Link
          href="/"
          aria-label="返回对话"
          title="返回对话"
          className="flex size-9 shrink-0 items-center justify-center rounded-lg text-[#77736b] hover:bg-[#ece8df] focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none"
        >
          <ChevronLeftIcon className="size-4" aria-hidden="true" />
        </Link>
        <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-[#e8e3d8] text-[#9c6046]">
          <Icon className="size-[18px]" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h1 className="text-sm font-semibold tracking-tight text-[#393630]">{title}</h1>
          <p className="hidden text-[11px] text-[#89847a] sm:block">{description}</p>
        </div>
        {rightSlot && <div className="ml-auto flex shrink-0 items-center gap-2">{rightSlot}</div>}
      </div>
    </header>
  );
}
