"use client";

import { usePathname } from "next/navigation";
import { Assistant } from "./assistant";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const keepAssistantMounted = pathname === "/" || pathname.startsWith("/settings");

  return (
    <>
      {keepAssistantMounted && (
        <div className={pathname === "/" ? "" : "hidden"}>
          <Assistant />
        </div>
      )}
      {pathname === "/" ? null : children}
    </>
  );
}
