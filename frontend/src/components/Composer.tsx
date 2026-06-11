import { useRef, useState } from "react";
import { Settings2, SendHorizontal, Square } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

/**
 * 对话输入框。三态:
 * - 未配置 LLM(llmConfigured=false):禁用,提示先去设置配置对话模型(按钮跳设置)。
 * - 生成中(streaming=true):输入禁用,发送按钮变「停止」。
 * - 就绪:可输入、Enter 发送、Shift+Enter 换行。
 */
export function Composer({
  llmConfigured,
  streaming,
  onSend,
  onStop,
  onOpenSettings,
}: {
  llmConfigured: boolean;
  streaming: boolean;
  onSend: (content: string) => void;
  onStop: () => void;
  onOpenSettings: () => void;
}) {
  const [value, setValue] = useState("");
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  const grow = (el: HTMLTextAreaElement) => {
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  };

  const submit = () => {
    const text = value.trim();
    if (!text || streaming || !llmConfigured) return;
    onSend(text);
    setValue("");
    if (taRef.current) taRef.current.style.height = "auto";
  };

  return (
    <div className="shrink-0 px-6 pb-7">
      <div className="mx-auto w-full max-w-2xl">
        <div
          className={cn(
            "flex items-end gap-2 rounded-2xl border bg-background p-2 shadow-sm transition-colors",
            llmConfigured
              ? "border-border focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/40"
              : "border-border/70 bg-muted/30",
          )}
        >
          <textarea
            ref={taRef}
            rows={1}
            value={value}
            disabled={!llmConfigured || streaming}
            placeholder={
              llmConfigured ? "基于项目资料提问…" : "先在设置里配置对话模型后即可提问"
            }
            aria-label="对话输入"
            className={cn(
              "max-h-40 min-h-9 w-full flex-1 resize-none bg-transparent px-2.5 py-2 text-sm text-foreground outline-none placeholder:text-muted-foreground",
              (!llmConfigured || streaming) && "cursor-not-allowed",
            )}
            onChange={(e) => {
              setValue(e.target.value);
              grow(e.target);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
          />
          {streaming ? (
            <Button
              type="button"
              size="icon"
              variant="outline"
              onClick={onStop}
              aria-label="停止生成"
              className="mb-px"
            >
              <Square className="size-3.5 fill-current" />
            </Button>
          ) : (
            <Button
              type="button"
              size="icon"
              disabled={!llmConfigured || !value.trim()}
              onClick={submit}
              aria-label="发送"
              className="mb-px"
            >
              <SendHorizontal className="size-4" />
            </Button>
          )}
        </div>

        {llmConfigured ? (
          <p className="mt-2 text-center text-xs text-muted-foreground">
            回答会带来源引用,点引用编号可跳回原始片段。
          </p>
        ) : (
          <p className="mt-2 flex items-center justify-center gap-1.5 text-center text-xs text-muted-foreground">
            尚未配置对话模型。
            <button
              type="button"
              onClick={onOpenSettings}
              className="inline-flex items-center gap-1 font-medium text-foreground underline-offset-2 outline-none hover:underline focus-visible:underline"
            >
              <Settings2 className="size-3.5" />
              去设置
            </button>
          </p>
        )}
      </div>
    </div>
  );
}
