"use client";

import { ArrowRightIcon, KeyRoundIcon, LoaderCircleIcon } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { authMutation, fetchCurrentUser, responseError } from "@/lib/auth-api";
import { PasswordField } from "@/components/auth/password-field";
import { SettingsPageHeader } from "@/components/settings/settings-page-header";
import { AskDbMark, AskDbWordmark } from "@/components/brand/askdb-logo";

export function PasswordForm({ embedded = false }: { embedded?: boolean }) {
  const router = useRouter();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [firstLogin, setFirstLogin] = useState(false);

  useEffect(() => {
    let active = true;
    fetchCurrentUser().then((user) => {
      if (!active) return;
      if (!user) {
        router.replace("/login");
        return;
      }
      setFirstLogin(user.must_change_password);
      setReady(true);
    }).catch(() => {
      if (active) setError("暂时无法确认登录状态，请刷新后重试。");
    });
    return () => { active = false; };
  }, [router]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    if (newPassword !== confirmation) {
      setError("两次输入的新密码不一致。");
      return;
    }
    if (newPassword.length < 8) {
      setError("新密码至少需要 8 个字符。");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await authMutation("/api/auth/change-password", "POST", {
        current_password: currentPassword,
        new_password: newPassword,
      });
      if (!response.ok) {
        setError(await responseError(response, "密码修改失败，请检查当前密码后重试。"));
        return;
      }
      setCurrentPassword("");
      setNewPassword("");
      setConfirmation("");
      if (embedded) {
        setNotice("密码修改成功。");
        return;
      }
      router.replace("/");
      router.refresh();
    } catch {
      setError("密码修改服务暂时不可用，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {embedded && (
        <SettingsPageHeader
          title={firstLogin ? "请设置新密码" : "修改密码"}
          description={firstLogin ? "为了保护账号，请先将临时密码更换为个人密码。" : "输入当前密码，再设置一个新的个人密码。"}
          icon={KeyRoundIcon}
        />
      )}
      <main className={embedded
        ? "min-h-[calc(100dvh-4rem)] bg-[#f7f5f0] px-5 py-8 text-[#30302e] sm:px-8 sm:py-10"
        : "flex min-h-dvh items-center justify-center bg-[#f7f5f0] px-5 py-10 text-[#30302e]"}
      >
      <section className={`w-full rounded-[1.75rem] border border-[#e7e2d8] bg-[#fbfaf7] p-7 shadow-[0_20px_70px_rgba(58,49,36,0.08)] sm:p-10 ${embedded ? "mx-auto max-w-xl" : "max-w-lg"}`}>
        {!embedded && (
          <>
            <div className="flex items-center gap-3">
              <span className="flex size-10 shrink-0 items-center justify-center">
                <AskDbMark className="size-6" />
              </span>
              <div><p><AskDbWordmark className="text-sm" /></p><p className="text-[11px] text-[#89847a]">账号安全</p></div>
            </div>
            <p className="mt-10 text-xs font-medium tracking-[0.15em] text-[#a1694b]">{firstLogin ? "首次登录" : "账户设置"}</p>
            <h1 className="mt-3 font-serif text-3xl tracking-tight">{firstLogin ? "请设置新密码" : "修改密码"}</h1>
            <p className="mt-3 text-sm leading-6 text-[#77736b]">
              {firstLogin ? "为了保护账号，请先将临时密码更换为个人密码。" : "输入当前密码，再设置一个新的个人密码。"}
            </p>
          </>
        )}
        {notice && <p role="status" className="mt-5 rounded-lg border border-[#d7e2d1] bg-[#f1f7ee] px-3 py-2.5 text-xs leading-5 text-[#4b6748]">{notice}</p>}
        <form className="mt-8 space-y-4" onSubmit={(event) => void submit(event)}>
          <PasswordField
            id="current-password"
            label={firstLogin ? "临时密码" : "当前密码"}
            autoComplete="current-password"
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            showVisibilityToggle={!firstLogin && ready}
          />
          <PasswordField
            id="new-password"
            label="新密码"
            autoComplete="new-password"
            minLength={8}
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
          />
          <p className="-mt-2 text-[11px] leading-5 text-[#89847a]">至少 8 个字符，不要求大小写、数字或符号组合。</p>
          <PasswordField
            id="confirm-password"
            label="再次输入新密码"
            autoComplete="new-password"
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
          />
          {error && <p role="alert" className="rounded-lg bg-[#f8e9e4] px-3 py-2.5 text-xs leading-5 text-[#9c4037]">{error}</p>}
          <button type="submit" disabled={!ready || busy || !currentPassword || !newPassword || !confirmation} className="flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#b96f4b] px-4 text-sm font-medium text-white transition hover:bg-[#a96040] focus-visible:ring-2 focus-visible:ring-[#30302e] focus-visible:ring-offset-2 disabled:cursor-wait disabled:opacity-60">
            {busy ? <><LoaderCircleIcon className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />正在保存</> : <>保存新密码<ArrowRightIcon className="size-4" aria-hidden="true" /></>}
          </button>
        </form>
      </section>
      </main>
    </>
  );
}
