"use client";

import { useEffect, useState } from "react";

type ConversationTimeValue = Date | string | null | undefined;
type ConversationTimeVariant = "activity" | "clock";

function parseConversationTime(value: ConversationTimeValue): Date | undefined {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? undefined : value;
  }
  if (typeof value !== "string" || !value.trim()) return undefined;

  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date;
}

function formatClock(date: Date): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

function formatFullDateTime(date: Date): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "long",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

function formatActivity(date: Date): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

function formatDateLabel(date: Date): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(date);
}

function sameLocalDate(first: Date, second: Date): boolean {
  return (
    first.getFullYear() === second.getFullYear() &&
    first.getMonth() === second.getMonth() &&
    first.getDate() === second.getDate()
  );
}

export function ConversationTimestamp({
  value,
  variant,
  accessibleLabel,
  className,
}: {
  value: ConversationTimeValue;
  variant: ConversationTimeVariant;
  accessibleLabel: string;
  className?: string;
}) {
  const date = parseConversationTime(value);
  const timestamp = date?.getTime();
  const [formatted, setFormatted] = useState<{
    timestamp: number;
    label: string;
    fullLabel: string;
  }>();

  useEffect(() => {
    if (timestamp === undefined) return;

    const localDate = new Date(timestamp);
    setFormatted({
      timestamp,
      label: variant === "activity" ? formatActivity(localDate) : formatClock(localDate),
      fullLabel: formatFullDateTime(localDate),
    });
  }, [timestamp, variant]);

  if (!date) return null;
  const current = formatted?.timestamp === timestamp ? formatted : undefined;

  return (
    <time
      dateTime={date.toISOString()}
      title={current?.fullLabel}
      aria-label={current?.fullLabel ? `${accessibleLabel}：${current.fullLabel}` : undefined}
      className={className}
    >
      {current?.label ?? (
        <span aria-hidden="true" className="invisible">
          00:00
        </span>
      )}
    </time>
  );
}

export function MessageDateSeparator({
  current,
  previous,
  isFirst,
}: {
  current: Date;
  previous?: Date;
  isFirst: boolean;
}) {
  const currentTime = parseConversationTime(current)?.getTime();
  const previousTime = parseConversationTime(previous)?.getTime();
  const [result, setResult] = useState<{
    currentTime: number;
    previousTime: number | undefined;
    isFirst: boolean;
    show: boolean;
  }>();

  useEffect(() => {
    if (currentTime === undefined) {
      return;
    }
    if (isFirst || previousTime === undefined) {
      setResult({ currentTime, previousTime, isFirst, show: true });
      return;
    }

    setResult({
      currentTime,
      previousTime,
      isFirst,
      show: !sameLocalDate(new Date(currentTime), new Date(previousTime)),
    });
  }, [currentTime, isFirst, previousTime]);

  const date = currentTime === undefined ? undefined : new Date(currentTime);
  if (
    !result ||
    result.currentTime !== currentTime ||
    result.previousTime !== previousTime ||
    result.isFirst !== isFirst ||
    !result.show ||
    !date
  )
    return null;

  const label = formatDateLabel(date);
  return (
    <div
      role="separator"
      aria-label={label}
      className="my-1 flex items-center gap-3 px-2 font-sans"
    >
      <span aria-hidden="true" className="h-px flex-1 bg-[#e7e2d8]" />
      <time dateTime={date.toISOString()} className="text-[10px] tabular-nums text-[#777166]">
        {label}
      </time>
      <span aria-hidden="true" className="h-px flex-1 bg-[#e7e2d8]" />
    </div>
  );
}
