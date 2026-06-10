import { useEffect, useMemo, useState } from "react";
import { ChevronRight, Database, Inbox, Loader2 } from "lucide-react";

import { api, type IngestRecord, type Project } from "@/lib/api";
import { cn, formatBytes } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

type PendingGroup = {
  projectId: number;
  projectTitle: string;
  files: IngestRecord[];
};

export function PendingList({
  projects,
  refreshKey,
}: {
  projects: Project[];
  /** 变更此值可触发重新聚合(例如重新扫描后)。 */
  refreshKey: number;
}) {
  const [groups, setGroups] = useState<PendingGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 默认全部折叠:文件多时避免「无尽平铺列表」。按项目 id 记录展开态。
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set());

  useEffect(() => {
    let cancelled = false;
    setGroups(null);
    setError(null);

    if (projects.length === 0) {
      setGroups([]);
      return;
    }

    Promise.all(
      // 逐项目拉取文件,过滤出 indexed === false 的记录,保留项目分组结构。
      projects.map((p) =>
        api.listFiles(p.id).then((rows) => ({
          projectId: p.id,
          projectTitle: p.title,
          files: rows.filter((r) => !r.indexed),
        })),
      ),
    )
      .then((all) => {
        if (cancelled) return;
        // 只展示有待索引文件的项目。
        setGroups(all.filter((g) => g.files.length > 0));
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });

    return () => {
      cancelled = true;
    };
  }, [projects, refreshKey]);

  const total = useMemo(
    () => (groups ?? []).reduce((n, g) => n + g.files.length, 0),
    [groups],
  );

  const toggle = (projectId: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });

  if (error) {
    return (
      <p className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive">
        加载待索引列表失败:{error}
      </p>
    );
  }

  if (groups === null) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-border/70 bg-card px-4 py-6 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        正在汇总待索引文件…
      </div>
    );
  }

  if (groups.length === 0) {
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
            {total}
          </span>{" "}
          个文件待索引
          <span aria-hidden className="mx-1.5 text-muted-foreground/40">
            ·
          </span>
          <span className="tabular-nums">{groups.length}</span> 个项目
        </span>
      </div>

      {/* 按项目分组,默认折叠;展开后才显示该项目的文件。 */}
      <div className="max-h-[52vh] divide-y divide-border/60 overflow-y-auto">
        {groups.map((g) => {
          const isOpen = expanded.has(g.projectId);
          return (
            <div key={g.projectId}>
              <button
                type="button"
                aria-expanded={isOpen}
                onClick={() => toggle(g.projectId)}
                className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
              >
                <ChevronRight
                  aria-hidden
                  className={cn(
                    "size-4 shrink-0 text-muted-foreground transition-transform",
                    isOpen && "rotate-90",
                  )}
                  strokeWidth={2}
                />
                <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                  {g.projectTitle}
                </span>
                <span className="shrink-0 text-xs text-muted-foreground">
                  <span className="font-medium tabular-nums text-foreground">
                    {g.files.length}
                  </span>{" "}
                  个待索引
                </span>
              </button>

              {isOpen && (
                <ul className="divide-y divide-border/50 border-t border-border/50 bg-muted/15">
                  {g.files.map((it) => (
                    <li
                      key={it.id}
                      className="flex items-center gap-2.5 py-1.5 pr-4 pl-10 transition-colors hover:bg-muted/40"
                      title={it.stored_path}
                    >
                      <span className="min-w-0 flex-1 truncate text-sm text-foreground">
                        {it.original_filename}
                      </span>
                      <Badge variant="pending" className="shrink-0">
                        <span
                          aria-hidden
                          className="size-1.5 rounded-full bg-amber-500 dark:bg-amber-400"
                        />
                        待索引
                      </Badge>
                      <span className="shrink-0 tabular-nums text-xs text-muted-foreground">
                        {formatBytes(it.size_bytes)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>

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
