import { useEffect, useRef, useState } from "react";
import { ChevronRight, Search } from "lucide-react";

import { type ToolStep } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 检索活动时间线(透明对话):展示本轮 agent 调用知识库的每一步(查询词 + 命中段数)。
 * 行为:检索时展开看步骤;答案一出现就自动收起为「检索 N 次 · 命中 M 段」摘要;
 * 用户手动展开/收起后尊重用户选择。空步骤不渲染。
 */
export function ActivityTimeline({ steps, hasContent }: { steps: ToolStep[]; hasContent: boolean }) {
  const [open, setOpen] = useState(true);
  const userToggled = useRef(false);

  useEffect(() => {
    if (!userToggled.current) setOpen(!hasContent); // 答案出现前展开、出现后自动收起
  }, [hasContent]);

  if (!steps.length) return null;
  const total = steps.reduce((s, x) => s + (x.count || 0), 0);
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
        <Search className="size-3" aria-hidden />
        检索 {steps.length} 次 · 命中 {total} 段
      </button>
      {open && (
        <ul className="ml-1.5 flex flex-col gap-1 pl-3">
          {steps.map((s, i) => (
            <li key={i} className="flex items-center gap-2 text-xs text-muted-foreground">
              <Search className="size-3 shrink-0 text-muted-foreground/70" aria-hidden />
              <span className="min-w-0 truncate">
                知识库「{s.query || "—"}」
              </span>
              <span className="ml-auto shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[0.65rem] tabular-nums text-muted-foreground">
                {s.count} 段
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
