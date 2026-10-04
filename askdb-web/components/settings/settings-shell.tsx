"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BookOpenIcon,
  ArchiveIcon,
  BrainIcon,
  CpuIcon,
  DatabaseIcon,
  KeyRoundIcon,
  UsersIcon,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { fetchCurrentUser, type AuthUser } from "@/lib/auth-api";

type SettingsItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  aliases?: string[];
};

const personalItems: SettingsItem[] = [
  { href: "/settings", label: "我的提交", icon: BookOpenIcon, aliases: ["/memories"] },
  { href: "/settings/archived-threads", label: "归档会话", icon: ArchiveIcon },
  { href: "/settings/password", label: "修改密码", icon: KeyRoundIcon },
];

const adminItems: SettingsItem[] = [
  { href: "/settings/memories", label: "记忆管理", icon: BrainIcon, aliases: ["/admin/memories"] },
  { href: "/settings/models", label: "模型配置", icon: CpuIcon },
  { href: "/settings/wren", label: "数据配置", icon: DatabaseIcon },
  { href: "/settings/users", label: "用户与权限", icon: UsersIcon, aliases: ["/admin/users"] },
];

function isCurrentItem(pathname: string, item: SettingsItem) {
  return pathname === item.href || item.aliases?.includes(pathname) === true;
}

function SettingsLink({ item, pathname }: { item: SettingsItem; pathname: string }) {
  const active = isCurrentItem(pathname, item);
  const Icon = item.icon;

  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={`group flex shrink-0 items-center gap-2.5 rounded-xl px-3 py-2.5 text-xs transition-colors focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none ${
        active
          ? "bg-[#e8e3d8] font-medium text-[#514b42]"
          : "text-[#77736b] hover:bg-[#e9e5dc] hover:text-[#514b42]"
      }`}
    >
      <Icon
        className={`size-4 ${active ? "text-[#b76d4b]" : "text-[#938a7b]"}`}
        aria-hidden="true"
      />
      {item.label}
    </Link>
  );
}

export function SettingsShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [user, setUser] = useState<AuthUser | null>(null);

  useEffect(() => {
    let active = true;
    fetchCurrentUser()
      .then((current) => {
        if (active) setUser(current);
      })
      .catch(() => {
        if (active) setUser(null);
      });
    return () => {
      active = false;
    };
  }, []);

  const isAdmin = user?.role === "admin";

  return (
    <div className="min-h-dvh bg-[#f7f5f0] text-[#30302e]">
      <div className="mx-auto flex min-h-dvh max-w-[1600px]">
        <aside className="sticky top-0 hidden h-dvh w-64 shrink-0 flex-col border-r border-[#e5ded3] bg-[#f1eee7] px-4 py-6 md:flex">
          <Link
            href="/"
            className="flex items-center gap-3 rounded-xl px-2 py-2 focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none"
          >
            <span className="flex size-9 items-center justify-center rounded-xl bg-[#e8e3d8] text-[#5c554c]">
              <DatabaseIcon className="size-[18px]" aria-hidden="true" />
            </span>
            <span>
              <span className="block text-sm font-semibold tracking-tight text-[#35332e]">
                AskDB
              </span>
              <span className="mt-0.5 block text-[11px] text-[#89847a]">个人设置</span>
            </span>
          </Link>

          <nav aria-label="设置导航" className="mt-8 flex-1 space-y-7">
            <div>
              <p className="px-3 text-[10px] font-medium tracking-[0.13em] text-[#9b9589]">个人</p>
              <div className="mt-2 space-y-1">
                {personalItems.map((item) => (
                  <SettingsLink key={item.href} item={item} pathname={pathname} />
                ))}
              </div>
            </div>
            {isAdmin && (
              <div>
                <p className="px-3 text-[10px] font-medium tracking-[0.13em] text-[#9b9589]">
                  管理员
                </p>
                <div className="mt-2 space-y-1">
                  {adminItems.map((item) => (
                    <SettingsLink key={item.href} item={item} pathname={pathname} />
                  ))}
                </div>
              </div>
            )}
          </nav>

          <Link
            href="/"
            className="mt-6 rounded-lg px-3 py-2 text-xs text-[#89847a] transition-colors hover:bg-[#e9e5dc] hover:text-[#514b42] focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none"
          >
            返回对话
          </Link>
        </aside>

        <div className="min-w-0 flex-1">
          <div className="sticky top-0 z-20 border-b border-[#e5ded3] bg-[#f1eee7]/95 px-4 py-3 backdrop-blur md:hidden">
            <nav aria-label="设置导航" className="-mx-4 flex gap-5 overflow-x-auto px-4 pb-1">
              <div className="shrink-0">
                <p className="mb-1 px-2 text-[9px] font-medium tracking-[0.1em] text-[#9b9589]">
                  个人
                </p>
                <div className="flex gap-1">
                  {personalItems.map((item) => (
                    <SettingsLink key={item.href} item={item} pathname={pathname} />
                  ))}
                </div>
              </div>
              {isAdmin && (
                <div className="shrink-0">
                  <p className="mb-1 px-2 text-[9px] font-medium tracking-[0.1em] text-[#9b9589]">
                    管理员
                  </p>
                  <div className="flex gap-1">
                    {adminItems.map((item) => (
                      <SettingsLink key={item.href} item={item} pathname={pathname} />
                    ))}
                  </div>
                </div>
              )}
            </nav>
          </div>
          {children}
        </div>
      </div>
    </div>
  );
}
