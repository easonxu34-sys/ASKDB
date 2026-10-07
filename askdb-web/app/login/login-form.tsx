"use client";

import { ArrowRightIcon, LoaderCircleIcon } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { authMutation, fetchCurrentUser, isAuthUser, responseError } from "@/lib/auth-api";
import { PasswordField } from "@/components/auth/password-field";
import { AskDbMark, AskDbWordmark } from "@/components/brand/askdb-logo";

export function LoginForm() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    fetchCurrentUser().then((user) => {
      if (!active || !user) return;
      router.replace(user.must_change_password ? "/change-password" : "/");
    }).catch(() => {});
    return () => { active = false; };
  }, [router]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await authMutation("/api/auth/login", "POST", { username, password });
      setPassword("");
      if (!response.ok) {
        setError(await responseError(response, "用户名或密码错误。"));
        return;
      }
      const payload: unknown = await response.json();
      if (!payload || typeof payload !== "object" || !("user" in payload) || !isAuthUser(payload.user)) {
        setError("登录服务返回了无效响应，请重试。");
        return;
      }
      router.replace(payload.user.must_change_password ? "/change-password" : "/");
    } catch {
      setError("登录服务暂时不可用，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="min-h-dvh bg-[#f7f5f0] px-5 py-8 text-[#30302e] sm:px-8 lg:flex lg:items-center lg:justify-center">
      <div className="mx-auto grid min-h-[min(44rem,calc(100dvh-4rem))] w-full max-w-5xl overflow-hidden rounded-[2rem] border border-[#e7e2d8] bg-[#fbfaf7] shadow-[0_24px_80px_rgba(58,49,36,0.08)] lg:grid-cols-[1.08fr_0.92fr]">
        <section className="relative hidden flex-col justify-between overflow-hidden bg-[#eee8dc] p-10 lg:flex xl:p-14">
          <div className="absolute -right-20 -top-24 size-80 rounded-full border border-[#d9c9b3]" />
          <div className="absolute -right-8 -top-12 size-56 rounded-full border border-[#d9c9b3]/80" />
          <div className="relative flex items-center gap-3">
            <span className="flex size-10 shrink-0 items-center justify-center">
              <AskDbMark className="size-6" />
            </span>
            <div>
              <p><AskDbWordmark className="text-sm" /></p>
              <p className="mt-0.5 text-[11px] tracking-[0.12em] text-[#89847a]">DATA, IN CONTEXT</p>
            </div>
          </div>
          <div className="relative max-w-md pb-8">
            <p className="mb-5 text-xs font-medium tracking-[0.18em] text-[#a1694b]">从问题开始</p>
            <h1 className="font-serif text-4xl leading-[1.2] tracking-tight text-[#35332e] xl:text-5xl">
              让业务数据，<br />
              更容易被理解。
            </h1>
            <p className="mt-6 max-w-sm text-sm leading-7 text-[#77736b]">
              登录 AskDB，与组织授权的数据对话。你的数据访问范围由管理员分配。
            </p>
            <div className="mt-10 flex items-center gap-3 text-xs text-[#89847a]">
              <span className="h-px w-10 bg-[#c57650]" />
              仅限组织账号访问
            </div>
          </div>
          <p className="relative text-[11px] text-[#9b9589]">AskDB · 数据查询助手</p>
        </section>

        <section className="flex min-h-[38rem] flex-col justify-between p-6 sm:p-10 lg:p-12 xl:p-14">
          <div className="flex items-center gap-3 lg:hidden">
            <span className="flex size-9 shrink-0 items-center justify-center">
              <AskDbMark className="size-6" />
            </span>
            <AskDbWordmark className="text-sm" />
          </div>
          <div className="my-auto w-full max-w-sm self-center py-12 lg:py-0">
            <p className="text-xs font-medium tracking-[0.16em] text-[#a1694b]">欢迎回来</p>
            <h2 className="mt-3 font-serif text-3xl tracking-tight text-[#35332e]">登录你的账号</h2>
            <p className="mt-2 text-sm leading-6 text-[#89847a]">使用管理员分配的用户名或工号继续。</p>

            <form className="mt-9 space-y-5" onSubmit={(event) => void submit(event)}>
              <label className="block space-y-2">
                <span className="text-xs font-medium text-[#625d54]">用户名 / 工号</span>
                <input
                  autoComplete="username"
                  autoCapitalize="none"
                  required
                  maxLength={128}
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  className="h-12 w-full rounded-xl border border-[#e3ddd2] bg-white px-3.5 text-sm outline-none transition focus:border-[#c57650] focus:ring-4 focus:ring-[#c57650]/10"
                  placeholder="输入用户名或工号"
                />
              </label>
              <PasswordField
                id="login-password"
                label="密码"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="输入密码"
              />
              {error && <p role="alert" className="rounded-lg bg-[#f8e9e4] px-3 py-2.5 text-xs leading-5 text-[#9c4037]">{error}</p>}
              <button
                type="submit"
                disabled={busy || !username || !password}
                className="flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#b96f4b] px-4 text-sm font-medium text-white shadow-[0_4px_12px_rgba(185,111,75,0.18)] transition hover:bg-[#a96040] focus-visible:ring-2 focus-visible:ring-[#30302e] focus-visible:ring-offset-2 disabled:cursor-wait disabled:opacity-60"
              >
                {busy ? <><LoaderCircleIcon className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />正在登录</> : <>登录<ArrowRightIcon className="size-4" aria-hidden="true" /></>}
              </button>
            </form>
            <p className="mt-6 text-center text-[11px] leading-5 text-[#9b9589]">
              需要账号或临时密码？请联系组织管理员。
            </p>
          </div>
          <footer className="flex items-center justify-between pt-6 text-[10px] text-[#aaa397]">
            <AskDbWordmark className="text-[10px]" />
            <span>安全登录 · 仅限组织成员</span>
          </footer>
        </section>
      </div>
    </main>
  );
}
