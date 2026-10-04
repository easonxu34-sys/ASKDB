"use client";

import { ComposerSelect, type ComposerSelectOption } from "@/components/ui/composer-select";
import type { DataSourceSummary } from "@/lib/data-sources";

type Props = {
  availableSources: DataSourceSummary[];
  selectedId: string;
  selectedSourceName?: string;
  disabled: boolean;
  notice?: string;
  onChange: (sourceId: string) => void;
};

export function DataSourceSelector({
  availableSources,
  selectedId,
  selectedSourceName,
  disabled,
  notice,
  onChange,
}: Props) {
  const selected = availableSources.find((source) => source.id === selectedId);
  const options: ComposerSelectOption[] = availableSources.map((source) => ({
    value: source.id,
    label: source.display_name,
    badge: source.is_default ? "默认" : undefined,
  }));
  if (!selected && selectedId) {
    options.unshift({
      value: selectedId,
      label: selectedSourceName ?? "数据源",
      description: "当前不可用",
      disabled: true,
    });
  }
  return (
    <div className="flex min-h-8 min-w-0 max-w-full flex-wrap items-center gap-2 px-1 font-sans">
      <label htmlFor="askdb-data-source" className="text-[11px] text-[#89847a]">
        数据源
      </label>
      <ComposerSelect
        id="askdb-data-source"
        ariaLabel="选择当前会话的数据源"
        value={selectedId}
        disabled={disabled}
        options={options}
        placeholder={availableSources.length ? "选择数据源" : "请先配置数据源"}
        onValueChange={onChange}
      />
      {notice && (
        <span role="status" className="text-[11px] text-[#9c6046]">
          {notice}
        </span>
      )}
    </div>
  );
}
