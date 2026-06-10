import { useEffect, useState } from "react";
import { FileText, Loader2 } from "lucide-react";

import { api, type IngestRecord } from "@/lib/api";
import { cn, formatBytes, ingestMethodLabel } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";

export function FileList({
  projectId,
  refreshKey = 0,
}: {
  projectId: number;
  refreshKey?: number;
}) {
  const [files, setFiles] = useState<IngestRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setFiles(null);
    setError(null);
    api
      .listFiles(projectId)
      .then((rows) => {
        if (!cancelled) setFiles(rows);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, refreshKey]);

  if (error) {
    return (
      <p className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive">
        加载文件失败:{error}
      </p>
    );
  }

  if (files === null) {
    return (
      <div className="flex items-center gap-2 px-1 py-6 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        正在加载文件…
      </div>
    );
  }

  if (files.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed border-border/70 bg-muted/20 px-6 py-10 text-center">
        <FileText className="size-5 text-muted-foreground" strokeWidth={1.5} />
        <p className="mt-1 text-sm font-medium text-foreground">该项目暂无文件</p>
        <p className="max-w-xs text-xs leading-relaxed text-muted-foreground">
          在「信息处理和入库」中重新扫描,或向项目文件夹添加文件。
        </p>
      </div>
    );
  }

  const indexedCount = files.filter((f) => f.indexed).length;

  return (
    <div className="overflow-hidden rounded-xl border border-border/70 bg-card">
      <div className="flex items-center justify-between border-b border-border/70 bg-muted/30 px-4 py-2.5">
        <span className="text-xs font-medium text-foreground">
          {files.length} 个文件
        </span>
        <span className="text-xs text-muted-foreground">
          已索引{" "}
          <span className="font-medium tabular-nums text-foreground">
            {indexedCount}
          </span>{" "}
          / {files.length}
        </span>
      </div>
      {/* 紧凑单行:文件名 + 状态徽章 + 大小;入库方式/描述移入悬停 title。 */}
      <ul className="max-h-[52vh] divide-y divide-border/60 overflow-y-auto">
        {files.map((f) => (
          <li
            key={f.id}
            className="group flex items-center gap-2.5 px-4 py-1.5 transition-colors hover:bg-muted/30"
            title={
              f.description
                ? `${ingestMethodLabel(f.ingest_method)} · ${f.description}`
                : ingestMethodLabel(f.ingest_method)
            }
          >
            <FileText
              aria-hidden
              className="size-4 shrink-0 text-muted-foreground"
              strokeWidth={1.75}
            />
            <span className="min-w-0 flex-1 truncate text-sm text-foreground">
              {f.original_filename}
            </span>
            <Badge
              variant={f.indexed ? "success" : "pending"}
              className="shrink-0"
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
            <span className="shrink-0 tabular-nums text-xs text-muted-foreground">
              {formatBytes(f.size_bytes)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
