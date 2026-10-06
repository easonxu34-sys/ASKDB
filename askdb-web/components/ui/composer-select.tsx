"use client";

import { cn } from "@/lib/utils";
import { CheckIcon, ChevronDownIcon } from "lucide-react";
import { Select } from "@base-ui/react/select";

const EMPTY_OPTION_VALUE = "__composer_select_empty_option__";

export type ComposerSelectOption = {
  value: string;
  label: string;
  description?: string;
  badge?: string;
  disabled?: boolean;
};

type ComposerSelectProps = {
  value: string;
  options: ComposerSelectOption[];
  placeholder: string;
  ariaLabel: string;
  id: string;
  disabled?: boolean;
  ariaInvalid?: boolean;
  ariaDescribedBy?: string;
  triggerClassName?: string;
  onValueChange: (value: string) => void;
};

export function ComposerSelect({
  value,
  options,
  placeholder,
  ariaLabel,
  id,
  disabled = false,
  ariaInvalid,
  ariaDescribedBy,
  triggerClassName,
  onValueChange,
}: ComposerSelectProps) {
  const selected = options.find((option) => option.value === value);
  const selectValue = value === "" && selected ? EMPTY_OPTION_VALUE : value || null;

  return (
    <Select.Root
      value={selectValue}
      disabled={disabled}
      onValueChange={(nextValue) => {
        if (typeof nextValue === "string") {
          onValueChange(nextValue === EMPTY_OPTION_VALUE ? "" : nextValue);
        }
      }}
    >
      <Select.Trigger
        id={id}
        aria-label={ariaLabel}
        aria-invalid={ariaInvalid}
        aria-describedby={ariaDescribedBy}
        className={cn(
          "inline-flex h-8 min-w-0 max-w-[min(19rem,65vw)] items-center gap-2 rounded-lg border border-[#e5ded3] bg-white/70 px-3 text-left font-sans text-xs text-[#615b51] transition-colors hover:border-[#d8c5b1] hover:bg-[#f8f5ef] focus-visible:border-[#c57650] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#c57650]/30 disabled:cursor-not-allowed disabled:opacity-55",
          triggerClassName,
        )}
      >
        <Select.Value className="min-w-0 flex-1">
          {selected ? (
            <span className="flex min-w-0 items-center gap-1.5">
              <span className="truncate">{selected.label}</span>
              {selected.description && (
                <>
                  <span aria-hidden="true" className="shrink-0 text-[#b3aa9d]">·</span>
                  <span className="truncate text-[#89847a]">{selected.description}</span>
                </>
              )}
            </span>
          ) : (
            <span className="truncate text-[#89847a]">{placeholder}</span>
          )}
        </Select.Value>
        {selected?.badge && (
          <span className="shrink-0 rounded-full bg-[#f2eee7] px-1.5 py-0.5 text-[10px] leading-none text-[#82786b]">
            {selected.badge}
          </span>
        )}
        <Select.Icon className="shrink-0 text-[#8a8277]">
          <ChevronDownIcon className="size-3.5" aria-hidden="true" />
        </Select.Icon>
      </Select.Trigger>
      <Select.Portal>
        <Select.Positioner
          align="end"
          alignItemWithTrigger={false}
          side="bottom"
          sideOffset={7}
          className="z-50 outline-none"
        >
          <Select.Popup className="max-h-[min(20rem,calc(100vh-2rem))] min-w-[min(18rem,calc(100vw-2rem))] overflow-y-auto rounded-xl border border-[#e8dfd3] bg-[#fffdfa] p-1.5 text-[#514b42] shadow-[0_16px_40px_rgba(50,39,28,0.16)] outline-none">
            <Select.List className="flex flex-col gap-0.5">
              {options.map((option) => (
                <Select.Item
                  key={option.value}
                  value={option.value === "" ? EMPTY_OPTION_VALUE : option.value}
                  label={[option.label, option.description].filter(Boolean).join(" ")}
                  disabled={option.disabled}
                  className="flex min-h-10 cursor-pointer items-center gap-3 rounded-lg px-2.5 py-2 text-left outline-none data-[highlighted]:bg-[#f5efe7] data-[selected]:bg-[#fbf5ee] data-[disabled]:cursor-not-allowed data-[disabled]:opacity-55"
                >
                  <Select.ItemText className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium text-[#514b42]">
                      {option.label}
                    </span>
                    {option.description && (
                      <span className="mt-0.5 block truncate text-[11px] text-[#918a7f]">
                        {option.description}
                      </span>
                    )}
                  </Select.ItemText>
                  {option.badge && (
                    <span className="shrink-0 rounded-full bg-white px-2 py-1 text-[10px] leading-none text-[#81796d] ring-1 ring-[#eee6db]">
                      {option.badge}
                    </span>
                  )}
                  <Select.ItemIndicator className="flex size-4 shrink-0 items-center justify-center text-[#bd704d]">
                    <CheckIcon className="size-3.5" aria-hidden="true" />
                  </Select.ItemIndicator>
                </Select.Item>
              ))}
            </Select.List>
          </Select.Popup>
        </Select.Positioner>
      </Select.Portal>
    </Select.Root>
  );
}
