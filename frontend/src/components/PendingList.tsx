import { useEffect, useState } from "react";
import { Database, Inbox, Loader2 } from "lucide-react";

import { api, type IngestRecord, type Project } from "@/lib/api";
import { formatBytes } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

type PendingItem = IngestRecord & { projectTitle: string };

export function PendingList({
  projects,
  refreshKey,
}: {
  projects: Project[];
  /** 变更此值可触发重新聚合(例如重新扫描后)。 */
  refreshKey: number;
}) {
  const [items, setItems] = useState<PendingItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setItems(null);
    setError(null);

    if (projects.length === 0) {
      setItems([]);
      return;
    }

    Promise.all(
      // 逐项目拉取文件,过滤出 indexed === false 的记录并附带项目标题。
      projects.map((p) =>
        api.listFiles(p.id).then((rows) =>
          rows
            .filter((r) => !r.indexed)
            .map((r) => ({ ...r, projectTitle: p.title })),
        ),
      ),
    )
      .then((groups) => {
        if (cancelled) return;
        setItems(groups.flat());
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });

    return () => {
      cancelled = true;
    };
  }, [projects, refreshKey]);

  if (error) {
    return (
      <p className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive">
        加载待索引列表失败:{error}
      </p>
    );
  }

  if (items === null) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-border/70 bg-card px-4 py-6 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        正在汇总待索引文件…
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-1.5 rounded-2xl border border-dashed border-border/70 bg-muted/15 px-6 py-12 text-center">
        <Inbox className="size-6 text-muted-foreground" strokeWidth={1.5} />
        <p className="mt-1 text-sm font-medium text-foreground">
          没有待索引的文件
        </p>
        <p className="max-w-xs text-xs leading-relaxed text-muted-foreground">
          创建项目或重新扫描后,新发现的文件会出现在这里。
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-border/70 bg-card">
      <div className="flex items-center justify-between border-b border-border/70 bg-muted/30 px-4 py-2.5">
        <span className="text-xs text-muted-foreground">
          <span className="font-medium tabular-nums text-foreground">
            {items.length}
          </span>{" "}
          个文件待索引
        </span>
      </div>

      <ul className="max-h-[52vh] divide-y divide-border/60 overflow-y-auto">
        {items.map((it) => (
          <li
            key={it.id}
            className="flex items-start gap-3 px-4 py-3 transition-colors hover:bg-muted/30"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate text-sm font-medium text-foreground">
                  {it.original_filename}
                </span>
                <Badge variant="pending">
                  <span
                    aria-hidden
                    className="size-1.5 rounded-full bg-amber-500 dark:bg-amber-400"
                  />
                  待索引
                </Badge>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-muted-foreground">
                <span className="rounded bg-muted px-1.5 py-0.5 font-medium text-foreground">
                  {it.projectTitle}
                </span>
                <span aria-hidden className="text-muted-foreground/40">·</span>
                <span className="tabular-nums">{formatBytes(it.size_bytes)}</span>
              </div>
              <p
                className="mt-1 truncate font-mono text-[11px] text-muted-foreground"
                title={it.stored_path}
              >
                {it.stored_path}
              </p>
            </div>
          </li>
        ))}
      </ul>

      <div className="flex items-center justify-between gap-3 border-t border-border/70 bg-muted/30 px-4 py-3">
        <span className="text-xs text-muted-foreground">
          索引功能开发中(Plan 2)
        </span>
        <Button
          type="button"
          size="lg"
          disabled
          title="索引功能开发中(Plan 2)"
        >
          <Database className="size-4" />
          建立索引
        </Button>
      </div>
    </div>
  );
}
