"use client";
import { useState } from "react";
import { useAui } from "@assistant-ui/react";
import { Button } from "@/components/ui/button";
export function PersonalMemoryNotice({ data }: { data: Record<string, unknown> }) {
  const { thread } = useAui();
  const [sent, setSent] = useState(false);
  if (typeof data.message !== "string") return null;
  const choices = Array.isArray(data.choices)
    ? data.choices
        .filter(
          (x): x is { choice_id: string; label: string } =>
            x && typeof x.choice_id === "string" && typeof x.label === "string",
        )
        .slice(0, 9)
    : [];
  return (
    <aside
      className="my-3 rounded-xl border border-[#e7e2d8] bg-[#f7f5f0] p-3 text-xs"
      aria-label="个人记忆状态"
    >
      <p className="whitespace-pre-wrap">{data.message}</p>
      {typeof data.request_id === "string" && choices.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {choices.map((choice) => (
            <Button
              key={choice.choice_id}
              variant="outline"
              disabled={sent}
              onClick={() => {
                setSent(true);
                thread.append({
                  role: "user",
                  content: [{ type: "text", text: choice.label }],
                  metadata: {
                    custom: {
                      personal_memory_response: {
                        request_id: data.request_id,
                        choice_id: choice.choice_id,
                      },
                    },
                  },
                });
              }}
            >
              {choice.label}
            </Button>
          ))}
        </div>
      )}
    </aside>
  );
}
