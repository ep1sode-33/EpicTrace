import { useEffect, useState } from "react";
import { FileText, Loader2 } from "lucide-react";

import { api, type IngestRecord } from "@/lib/api";
import { cn, formatBytes, ingestMethodLabel } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";

export function FileList({ projectId }: { projectId: number }) {
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
  }, [projectId]);

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
      <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border/70 bg-muted/20 px-6 py-10 text-center">
        <FileText className="size-5 text-muted-foreground/60" strokeWidth={1.75} />
        <p className="text-sm font-medium text-foreground">该项目暂无文件</p>
        <p className="text-xs text-muted-foreground">
          在「信息处理和入库」中重新扫描,或向项目文件夹添加文件。
        </p>
      </div>
    );
  }

  const indexedCount = files.filter((f) => f.indexed).length;

  return (
    <div className="overflow-hidden rounded-xl border border-border/70 bg-card">
      <div className="flex items-center justify-between border-b border-border/70 bg-muted/30 px-4 py-2.5">
        <span className="text-xs font-medium text-muted-foreground">
          {files.length} 个文件
        </span>
        <span className="text-xs text-muted-foreground/80">
          已索引 {indexedCount} / {files.length}
        </span>
      </div>
      <ul className="divide-y divide-border/60">
        {files.map((f) => (
          <li
            key={f.id}
            className="group flex items-start gap-3 px-4 py-3 transition-colors hover:bg-muted/30"
          >
            <span
              aria-hidden
              className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground ring-1 ring-border/60"
            >
              <FileText className="size-4" strokeWidth={1.75} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate text-sm font-medium text-foreground">
                  {f.original_filename}
                </span>
                <Badge variant={f.indexed ? "success" : "pending"}>
                  {f.indexed ? "已索引" : "待索引"}
                </Badge>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-muted-foreground">
                <span>{formatBytes(f.size_bytes)}</span>
                <span aria-hidden className="text-border">·</span>
                <span>{ingestMethodLabel(f.ingest_method)}</span>
              </div>
              {f.description && (
                <p
                  className={cn(
                    "mt-1.5 line-clamp-2 text-xs leading-relaxed text-muted-foreground/90",
                  )}
                >
                  {f.description}
                </p>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
