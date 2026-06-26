import { useEffect, useRef, useState } from "react";
import { ChevronRight, Sparkles } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * 思考过程折叠块(透明对话):展示推理模型的 reasoning。
 * 行为:思考进行中(答案尚未开始)自动展开、可实时看推理流;答案一开始自动收起为「已思考」,
 * 用户手动展开/收起后则尊重用户选择。空 thinking 不渲染。
 */
export function ThinkingBlock({
  thinking,
  streaming,
  hasContent,
}: {
  thinking: string;
  streaming: boolean;
  hasContent: boolean;
}) {
  const [open, setOpen] = useState(false);
  const userToggled = useRef(false);
  const active = streaming && !hasContent; // 还在思考(答案未开始)

  useEffect(() => {
    if (!userToggled.current) setOpen(active && thinking.length > 0);
  }, [active, thinking]);

  if (!thinking) return null;
  return (
    <div className="flex flex-col gap-1.5">
      <button
        type="button"
        onClick={() => {
          userToggled.current = true;
          setOpen((o) => !o);
        }}
        aria-expanded={open}
        className="inline-flex w-fit items-center gap-1.5 rounded-md py-0.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronRight
          className={cn("size-3 transition-transform", open && "rotate-90")}
          strokeWidth={2.5}
          aria-hidden
        />
        <Sparkles className={cn("size-3", active && "animate-pulse text-primary")} aria-hidden />
        {active ? "思考中…" : "已思考"}
      </button>
      {open && (
        <div className="max-h-64 overflow-y-auto rounded-md bg-muted/50 px-3 py-2 text-xs leading-relaxed break-words whitespace-pre-wrap text-muted-foreground">
          {thinking}
        </div>
      )}
    </div>
  );
}
