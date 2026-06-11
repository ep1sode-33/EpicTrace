import { useRef, useState } from "react";
import { FolderInput, Plus, Settings2, SendHorizontal, Square } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { pickFiles } from "@/lib/pickers";

/**
 * 对话输入框。三态:
 * - 未配置 LLM(llmConfigured=false):禁用,提示先去设置配置对话模型(按钮跳设置)。
 * - 生成中(streaming=true):输入框仍可编辑(可起草下一条),发送按钮变「停止」;
 *   此时 Enter/发送被忽略,草稿保留在框里,待流结束后再发。
 * - 就绪:可输入、Enter 发送、Shift+Enter 换行。
 * 输入法(IME)合成期间的 Enter 用于确认候选词,不触发发送。
 */
export function Composer({
  llmConfigured,
  streaming,
  onSend,
  onStop,
  onOpenSettings,
  onAttachPaths,
  onAddInternal,
  onAttachUnsupported,
}: {
  llmConfigured: boolean;
  streaming: boolean;
  onSend: (content: string) => void;
  onStop: () => void;
  onOpenSettings: () => void;
  /** 用户通过「+」/拖拽/粘贴选了外部文件(绝对路径列表)。拖拽/粘贴在浏览器拿不到绝对路径时为空。 */
  onAttachPaths: (paths: string[]) => void;
  /** 始终可用的「从项目引用文件」入口(打开内部文件选择器)。 */
  onAddInternal: () => void;
  /** 拖拽/粘贴带了文件但当前环境拿不到绝对路径时调用(用于提示改用「+」)。 */
  onAttachUnsupported?: () => void;
}) {
  const [value, setValue] = useState("");
  const [dragging, setDragging] = useState(false);
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

  const pick = async () => {
    const paths = await pickFiles();
    if (paths.length) onAttachPaths(paths);
  };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const paths = Array.from(e.dataTransfer.files)
      .map((f) => (f as File & { path?: string }).path)
      .filter((p): p is string => Boolean(p));
    if (paths.length) onAttachPaths(paths);
    else if (e.dataTransfer.files.length) onAttachUnsupported?.();
  };

  return (
    <div className="shrink-0 px-6 pb-7">
      <div className="mx-auto w-full max-w-2xl">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            if (llmConfigured) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={llmConfigured ? onDrop : undefined}
          className={cn(
            "flex items-end gap-2 rounded-2xl border bg-background p-2 shadow-sm transition-colors",
            llmConfigured
              ? "border-border focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/40"
              : "border-border/70 bg-muted/30",
            dragging && "border-ring ring-3 ring-ring/40",
          )}
        >
          <Button
            type="button"
            size="icon"
            variant="ghost"
            disabled={!llmConfigured}
            onClick={pick}
            aria-label="添加文件"
            className="mb-px"
          >
            <Plus className="size-4" />
          </Button>
          <Button type="button" size="icon" variant="ghost" disabled={!llmConfigured}
                  onClick={onAddInternal} aria-label="从项目引用文件" className="mb-px">
            <FolderInput className="size-4" />
          </Button>
          <textarea
            ref={taRef}
            rows={1}
            value={value}
            disabled={!llmConfigured}
            placeholder={
              llmConfigured ? "基于项目资料提问…" : "先在设置里配置对话模型后即可提问"
            }
            aria-label="对话输入"
            className={cn(
              "max-h-40 min-h-9 w-full flex-1 resize-none bg-transparent px-2.5 py-2 text-sm text-foreground outline-none placeholder:text-muted-foreground",
              !llmConfigured && "cursor-not-allowed",
            )}
            onChange={(e) => {
              setValue(e.target.value);
              grow(e.target);
            }}
            onKeyDown={(e) => {
              // 输入法(IME)合成期间按 Enter 是确认候选词,不应触发发送。
              if (e.nativeEvent.isComposing || e.keyCode === 229) return;
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            onPaste={(e) => {
              const paths = Array.from(e.clipboardData.files)
                .map((f) => (f as File & { path?: string }).path)
                .filter((p): p is string => Boolean(p));
              if (paths.length) {
                e.preventDefault();
                onAttachPaths(paths);
              } else if (e.clipboardData.files.length) {
                onAttachUnsupported?.();
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
