import { useEffect, useMemo, useRef, useState } from "react";
import { FileText, FolderOpen, Loader2 } from "lucide-react";

import { api, type Citation, type SourceText } from "@/lib/api";
import { revealInFinder } from "@/lib/pickers";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/**
 * 来源查看器:点引用 chip 打开。拉取整段来源文本,高亮 [char_start, char_end) 区间并滚动到该处。
 * 顶部「在 Finder 中显示」走 pywebview js_api(打包态)。
 */
export function SourceViewer({
  citation,
  onClose,
}: {
  /** 当前要查看的引用;为 null 时关闭。 */
  citation: Citation | null;
  onClose: () => void;
}) {
  const [source, setSource] = useState<SourceText | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const markRef = useRef<HTMLElement | null>(null);

  const open = citation !== null;

  useEffect(() => {
    if (!citation) return;
    setSource(null);
    setError(null);
    setLoading(true);
    let cancelled = false;
    const fetcher =
      citation.source_kind === "attachment" && citation.reference_id != null
        ? api.getAttachmentSource(citation.reference_id)
        : api.getSource(citation.ingest_record_id);
    fetcher
      .then((s) => {
        if (!cancelled) setSource(s);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [citation]);

  // 文本就位后把高亮段滚到可视区中部。
  useEffect(() => {
    if (source && markRef.current) {
      markRef.current.scrollIntoView({ block: "center", behavior: "auto" });
    }
  }, [source]);

  // 把整段文本按 [start, end) 切成 前/高亮/后 三段;越界则做安全收敛。
  // 后端的 char_start/char_end 是 Python 码点偏移;JS 字符串 .slice() 按 UTF-16 码元计数,
  // 遇到非 BMP 字符(emoji 等)会错位。故先 Array.from 拆成码点数组再按码点切片。
  const segments = useMemo(() => {
    if (!source || !citation) return null;
    const cp = Array.from(source.text);
    const len = cp.length;
    const start = Math.max(0, Math.min(citation.char_start, len));
    const end = Math.max(start, Math.min(citation.char_end, len));
    return {
      before: cp.slice(0, start).join(""),
      hit: cp.slice(start, end).join(""),
      after: cp.slice(end).join(""),
      empty: start === end,
    };
  }, [source, citation]);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        showCloseButton
        className="flex max-h-[min(80vh,46rem)] w-full max-w-2xl flex-col gap-0 overflow-hidden p-0 sm:max-w-2xl"
      >
        <DialogHeader className="flex-row items-start gap-3 border-b border-border/70 px-5 py-4 pr-12">
          <span
            aria-hidden
            className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-foreground ring-1 ring-border/70"
          >
            <FileText className="size-4" strokeWidth={2} />
          </span>
          <div className="min-w-0 flex-1">
            <DialogTitle className="truncate text-sm font-semibold" title={source?.filename}>
              {source?.filename ?? "来源"}
            </DialogTitle>
            {source?.path && (
              <p className="mt-1 truncate font-mono text-xs text-muted-foreground" title={source.path}>
                {source.path}
              </p>
            )}
          </div>
          {source?.path && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="shrink-0"
              onClick={() => revealInFinder(source.path)}
            >
              <FolderOpen className="size-3.5" />
              在 Finder 中显示
            </Button>
          )}
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {loading && (
            <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              正在加载来源…
            </div>
          )}

          {error && !loading && (
            <p className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive">
              加载来源失败:{error}
            </p>
          )}

          {segments && !loading && !error && (
            <pre className="font-mono text-[13px] leading-relaxed whitespace-pre-wrap break-words text-foreground/90">
              {segments.before}
              {segments.empty ? (
                // 零长度区间:用一个细标记表示位置而非高亮一片空白。
                <mark
                  ref={markRef}
                  className="rounded-sm bg-primary/15 px-px text-foreground ring-1 ring-primary/30"
                >
                  ‹此处›
                </mark>
              ) : (
                <mark
                  ref={markRef}
                  className="rounded-sm bg-primary/15 px-0.5 py-px text-foreground ring-1 ring-primary/25 [box-decoration-break:clone] [-webkit-box-decoration-break:clone]"
                >
                  {segments.hit}
                </mark>
              )}
              {segments.after}
            </pre>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
