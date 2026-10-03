"use client";

import Link from "next/link";
import { useEffect, useState, type PropsWithChildren } from "react";
import { useRouter } from "next/navigation";
import { fetchCurrentUser } from "@/lib/auth-api";

export function AdminGate({ children }: PropsWithChildren) {
  const router = useRouter();
  const [state, setState] = useState<"loading" | "admin" | "denied" | "error">("loading");

  useEffect(() => {
    let active = true;
    fetchCurrentUser().then((user) => {
      if (!active) return;
      if (!user) {
        router.replace("/login");
        return;
      }
      if (user.must_change_password) {
        router.replace("/change-password");
        return;
      }
      setState(user.role === "admin" ? "admin" : "denied");
    }).catch(() => {
      if (active) setState("error");
    });
    return () => { active = false; };
  }, [router]);

  if (state === "loading") {
    return <main className="flex min-h-dvh items-center justify-center bg-[#f7f5f0] text-sm text-[#77736b]" role="status">正在确认管理员权限…</main>;
  }
  if (state === "denied" || state === "error") {
    return (
      <main className="flex min-h-dvh items-center justify-center bg-[#f7f5f0] px-6 text-[#30302e]">
        <section className="max-w-md rounded-2xl border border-[#e7e2d8] bg-[#fbfaf7] p-7 text-center">
          <h1 className="text-lg font-semibold">{state === "error" ? "暂时无法确认登录状态" : "此页面仅供管理员使用"}</h1>
          <p className="mt-2 text-sm text-[#77736b]">{state === "error" ? "请检查网络后刷新页面重试。" : "你的账号没有管理模型或数据源的权限。"}</p>
          <Link href="/" className="mt-5 inline-flex rounded-lg bg-[#b96f4b] px-4 py-2 text-sm text-white focus-visible:ring-2 focus-visible:ring-[#30302e]">返回对话</Link>
        </section>
      </main>
    );
  }
  return children;
}
