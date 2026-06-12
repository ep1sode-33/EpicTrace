import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronRight, FileText, FolderOpen, Loader2, Search } from "lucide-react";

import { api, type IngestRecord } from "@/lib/api";
import { revealInFinder } from "@/lib/pickers";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";

/**
 * 「库内文件」区:右侧引用侧栏底部的可折叠区(默认收起)。
 * 展开后:搜索框(按文件名过滤)+ 项目文件列表(已索引/待索引 徽章)。
 * 左键点未引用的文件 → pin 为内部引用;已引用的显示「已引用」灰标且不可点。
 * 右键某行 →「在 Finder 中查看」(轻量单项上下文菜单)。
 *
 * 文件懒加载:首次展开才拉取;references 变化(refreshSignal)时重拉,保持「已引用」标记同步。
 * openSignal 自增时强制展开(供 Composer 的「从项目」按钮触发)。
 */
export function ProjectFilesZone({
  projectId,
  pinnedRecordIds,
  onPin,
  defaultOpen = false,
  openSignal = 0,
  refreshSignal = 0,
}: {
  projectId: number;
  /** 已 pin 为内部引用的 ingest_record id 集合(来自 references)。 */
  pinnedRecordIds: Set<number>;
  /** 左键点未引用文件:把该文件 pin 为本对话的内部引用。 */
  onPin: (ingestRecordId: number) => void;
  defaultOpen?: boolean;
  /** 自增时强制展开本区(并滚入视野)。 */
  openSignal?: number;
  /** 自增时(references 变化)重拉文件列表,保持「已引用」标记同步。 */
  refreshSignal?: number;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [files, setFiles] = useState<IngestRecord[] | null>(null);
  const [query, setQuery] = useState("");
  // 单项上下文菜单:目标文件 + 光标坐标;null 表示关闭。
  const [menu, setMenu] = useState<{ file: IngestRecord; x: number; y: number } | null>(
    null,
  );
  const sectionRef = useRef<HTMLDivElement>(null);

  // 拉取文件列表。展开时(懒加载)与 refreshSignal 变化时调用。
  const fetchFiles = useCallback(() => {
    let cancelled = false;
    api
      .listFiles(projectId)
      .then((rows) => {
        if (!cancelled) setFiles(rows);
      })
      .catch(() => {
        if (!cancelled) setFiles([]);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // openSignal 自增:展开并滚入视野(供「从项目」按钮触发)。
  const prevOpenSignal = useRef(openSignal);
  useEffect(() => {
    if (openSignal !== prevOpenSignal.current) {
      prevOpenSignal.current = openSignal;
      setOpen(true);
      sectionRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [openSignal]);

  // 展开时拉取(首次=懒加载);此后 references 变化(refreshSignal)或换项目时重拉,
  // 使「已引用」标记跟上。收起时不拉。
  useEffect(() => {
    if (!open) return;
    return fetchFiles();
  }, [open, refreshSignal, fetchFiles]);

  // 关闭菜单:点击/失焦/滚动时统一收起。
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener("click", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("blur", close);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("blur", close);
    };
  }, [menu]);

  const filtered = (files ?? []).filter((f) =>
    f.original_filename.toLowerCase().includes(query.trim().toLowerCase()),
  );

  return (
    <div ref={sectionRef} className="flex flex-col gap-1.5 border-t border-border/60 pt-3">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex items-center gap-1 rounded px-1 py-0.5 text-[0.7rem] font-medium uppercase tracking-wide text-muted-foreground/80 outline-none hover:text-foreground"
      >
        <ChevronRight
          className={cn("size-3 transition-transform", open && "rotate-90")}
          strokeWidth={2}
          aria-hidden
        />
        库内文件
        {files !== null && (
          <span className="rounded-full bg-muted px-1.5 py-0.5 text-[0.65rem] font-medium tabular-nums text-muted-foreground">
            {files.length}
          </span>
        )}
      </button>

      {open && (
        <div className="flex flex-col gap-2">
          <div className="relative">
            <Search
              className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索文件名…"
              aria-label="搜索库内文件"
              className="h-7 pl-7 text-xs"
            />
          </div>

          {files === null ? (
            <div className="flex items-center gap-2 px-1 py-4 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              正在加载文件…
            </div>
          ) : files.length === 0 ? (
            <p className="px-1 py-3 text-xs text-muted-foreground/70">该项目暂无文件。</p>
          ) : filtered.length === 0 ? (
            <p className="px-1 py-3 text-xs text-muted-foreground/70">无匹配文件。</p>
          ) : (
            <ul className="flex max-h-[40vh] flex-col gap-1 overflow-y-auto">
              {filtered.map((f) => {
                const pinned = f.id != null && pinnedRecordIds.has(f.id);
                return (
                  <li key={f.id}>
                    <button
                      type="button"
                      disabled={pinned}
                      onClick={() => !pinned && onPin(f.id)}
                      onContextMenu={(e) => {
                        e.preventDefault();
                        setMenu({ file: f, x: e.clientX, y: e.clientY });
                      }}
                      title={f.original_filename}
                      className={cn(
                        "flex w-full items-center gap-2 rounded-lg border border-transparent px-2 py-1.5 text-left outline-none",
                        pinned
                          ? "cursor-default opacity-60"
                          : "hover:border-border/60 hover:bg-muted/50",
                      )}
                    >
                      <FileText className="size-3.5 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 flex-1 truncate text-xs text-foreground">
                        {f.original_filename}
                      </span>
                      {pinned ? (
                        <span className="shrink-0 text-[0.65rem] text-muted-foreground">
                          已引用
                        </span>
                      ) : (
                        <Badge
                          variant={f.indexed ? "success" : "pending"}
                          className="shrink-0 px-1.5 py-0 text-[0.65rem]"
                        >
                          <span
                            aria-hidden
                            className={cn(
                              "size-1.5 rounded-full",
                              f.indexed
                                ? "bg-emerald-600 dark:bg-emerald-400"
                                : "bg-amber-500 dark:bg-amber-400",
                            )}
                          />
                          {f.indexed ? "已索引" : "待索引"}
                        </Badge>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}

      {menu && (
        <div
          role="menu"
          // 阻止 mousedown 冒泡到 window 的关闭监听,避免点项前就被关掉。
          onMouseDown={(e) => e.stopPropagation()}
          style={{ top: menu.y, left: menu.x }}
          className="fixed z-50 min-w-40 rounded-lg border border-border/70 bg-popover p-1 shadow-md"
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              void revealInFinder(menu.file.stored_path);
              setMenu(null);
            }}
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-foreground outline-none hover:bg-muted"
          >
            <FolderOpen className="size-3.5 text-muted-foreground" />
            在 Finder 中查看
          </button>
        </div>
      )}
    </div>
  );
}
