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

// 单个项目分组的索引状态:idle 时无;运行中带进度;完成后保留失败数用于提示。
type IndexState =
  | { phase: "indexing"; done: number; total: number }
  | { phase: "failed"; count: number };

export function PendingList({
  projects,
  refreshKey,
  onIndexed,
}: {
  projects: Project[];
  /** 变更此值可触发重新聚合(例如重新扫描后)。 */
  refreshKey: number;
  /** 某个项目索引完成后回调,供父级重新聚合(已索引文件离开待索引队列)。 */
  onIndexed?: () => void;
}) {
  const [groups, setGroups] = useState<PendingGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 默认全部折叠:文件多时避免「无尽平铺列表」。按项目 id 记录展开态。
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set());
  // 按项目 id 记录索引状态(运行中进度 / 完成后的失败提示)。
  const [indexState, setIndexState] = useState<Record<number, IndexState>>({});

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

  const runIndex = async (group: PendingGroup) => {
    const { projectId } = group;
    // 进入运行态:先按该组的待索引文件数估个进度上限,拿到响应后再校正。
    setIndexState((prev) => ({
      ...prev,
      [projectId]: { phase: "indexing", done: 0, total: group.files.length },
    }));
    try {
      const job = await api.indexProject(projectId);
      // 同步返回最终 job:done/total 即终态。失败文件用小字提示,成功则清除该组状态。
      if (job.errors.length > 0) {
        setIndexState((prev) => ({
          ...prev,
          [projectId]: { phase: "failed", count: job.errors.length },
        }));
      } else {
        setIndexState((prev) => {
          const next = { ...prev };
          delete next[projectId];
          return next;
        });
      }
    } catch {
      // 整体请求失败也归为「失败」提示;具体文件数未知,按该组全部计。
      setIndexState((prev) => ({
        ...prev,
        [projectId]: { phase: "failed", count: group.files.length },
      }));
    } finally {
      // 无论成败都让父级重新聚合:成功的文件翻「已索引」后会离开本队列。
      onIndexed?.();
    }
  };

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
          const state = indexState[g.projectId];
          const indexing = state?.phase === "indexing";
          return (
            <div key={g.projectId}>
              {/* 折叠头:左侧整块是展开/收起的点击区,右侧是该项目的「建立索引」动作。
                  两者并列、互不嵌套,避免按钮套按钮。 */}
              <div className="flex items-center gap-2 pr-3">
                <button
                  type="button"
                  aria-expanded={isOpen}
                  onClick={() => toggle(g.projectId)}
                  className="flex min-w-0 flex-1 items-center gap-2.5 py-2.5 pl-4 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
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

                {indexing ? (
                  <span
                    className="inline-flex shrink-0 items-center gap-1.5 px-1.5 text-xs text-muted-foreground tabular-nums"
                    role="status"
                    aria-live="polite"
                  >
                    <Loader2 className="size-3.5 animate-spin" />
                    索引中 {state.done}/{state.total}
                  </span>
                ) : (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="shrink-0"
                    onClick={() => runIndex(g)}
                  >
                    <Database className="size-3.5" />
                    建立索引
                  </Button>
                )}
              </div>

              {/* 失败提示:索引完成但有文件失败时,在该组下方留一行小字。 */}
              {state?.phase === "failed" && (
                <p className="px-4 pb-2 pl-10 text-xs text-destructive">
                  {state.count} 个文件索引失败
                </p>
              )}

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
    </div>
  );
}
