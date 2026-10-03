"use client";

import { EyeIcon, EyeOffIcon } from "lucide-react";
import { useEffect, useState, type ChangeEventHandler } from "react";

type PasswordFieldProps = {
  id: string;
  label: string;
  value: string;
  onChange: ChangeEventHandler<HTMLInputElement>;
  autoComplete: "current-password" | "new-password";
  required?: boolean;
  minLength?: number;
  maxLength?: number;
  placeholder?: string;
  showVisibilityToggle?: boolean;
};

export function PasswordField({
  id,
  label,
  value,
  onChange,
  autoComplete,
  required = true,
  minLength,
  maxLength = 1024,
  placeholder,
  showVisibilityToggle = true,
}: PasswordFieldProps) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!value || !showVisibilityToggle) setVisible(false);
  }, [showVisibilityToggle, value]);

  return (
    <div className="block space-y-2">
      <label htmlFor={id} className="block text-xs font-medium text-[#625d54]">
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          type={visible && showVisibilityToggle ? "text" : "password"}
          autoComplete={autoComplete}
          required={required}
          minLength={minLength}
          maxLength={maxLength}
          value={value}
          onChange={onChange}
          placeholder={placeholder}
          className="h-12 w-full rounded-xl border border-[#e3ddd2] bg-white px-3.5 pr-12 text-sm outline-none transition focus:border-[#c57650] focus:ring-4 focus:ring-[#c57650]/10"
        />
        {showVisibilityToggle && (
          <button
            type="button"
            aria-label={visible ? "隐藏密码" : "显示密码"}
            aria-pressed={visible}
            onClick={() => setVisible((current) => !current)}
            className="absolute inset-y-1 right-1 flex w-10 items-center justify-center rounded-lg text-[#77736b] hover:bg-[#f1eee7] focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none"
          >
            {visible ? (
              <EyeOffIcon className="size-4" aria-hidden="true" />
            ) : (
              <EyeIcon className="size-4" aria-hidden="true" />
            )}
          </button>
        )}
      </div>
    </div>
  );
}
