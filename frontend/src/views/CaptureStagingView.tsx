import { useEffect, useState } from "react";
import { ChevronDown, Clock, FolderOpen, Loader2, Trash2 } from "lucide-react";
import { api, type CaptureSession, type CaptureSessionDetail, type Project } from "@/lib/api";
import { Button } from "@/components/ui/button";

/** 状态徽标样式 */
function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    recording:
      "border-red-500/25 bg-red-500/10 text-red-700 dark:border-red-400/20 dark:bg-red-400/10 dark:text-red-400",
    staged:
      "border-amber-600/25 bg-amber-500/15 text-amber-800 dark:border-amber-400/20 dark:bg-amber-400/10 dark:text-amber-300",
    organized:
      "border-green-600/25 bg-green-500/15 text-green-800 dark:border-green-400/20 dark:bg-green-400/10 dark:text-green-300",
  };
  const labels: Record<string, string> = {
    recording: "录制中",
    staged: "已暂存",
    organized: "已归档",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${styles[status] ?? "border-border bg-muted text-muted-foreground"}`}
    >
      {status === "recording" && (
        <span className="size-1.5 animate-pulse rounded-full bg-red-500" aria-hidden />
      )}
      {labels[status] ?? status}
    </span>
  );
}

/** 将 ISO 时间戳转为相对秒数(相对 started_at) */
function relSec(base: string, ts: string): number {
  return Math.max(0, (new Date(ts).getTime() - new Date(base).getTime()) / 1000);
}

/** 格式化秒数为 +MM:SS */
function fmtRel(secs: number): string {
  const m = Math.floor(secs / 60).toString().padStart(2, "0");
  const s = Math.floor(secs % 60).toString().padStart(2, "0");
  return `+${m}:${s}`;
}

/** 事件图标 */
function kindIcon(kind: string): string {
  switch (kind) {
    case "note": return "✏️";
    case "clipboard": return "📋";
    case "screenshot": return "📷";
    case "pause": return "⏸";
    case "resume": return "▶️";
    default: return "•";
  }
}

interface Props {
  onOrganized: (projectId: number) => void;
}

export function CaptureStagingView({ onOrganized }: Props) {
  const [sessions, setSessions] = useState<CaptureSession[]>([]);
  const [selected, setSelected] = useState<CaptureSessionDetail | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<number | "">("");
  const [loading, setLoading] = useState(true);
  const [organizing, setOrganizing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.listSessions(), api.listProjects()])
      .then(([sess, projs]) => {
        if (cancelled) return;
        setSessions(sess);
        setProjects(projs);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSelect(sess: CaptureSession) {
    if (selected?.id === sess.id) {
      setSelected(null);
      return;
    }
    try {
      const detail = await api.getSession(sess.id);
      setSelected(detail);
      setSelectedProjectId("");
      setError(null);
    } catch {
      setError("加载 session 详情失败");
    }
  }

  async function handleOrganize() {
    if (!selected || !selectedProjectId) return;
    const pid = Number(selectedProjectId);
    setOrganizing(true);
    setError(null);
    try {
      await api.organizeSession(selected.id, pid);
      // 刷新列表
      const sess = await api.listSessions();
      setSessions(sess);
      setSelected(null);
      onOrganized(pid);
    } catch (e) {
      setError(e instanceof Error ? e.message : "归类失败");
    } finally {
      setOrganizing(false);
    }
  }

  async function handleDelete(sessId: number, e: React.MouseEvent) {
    e.stopPropagation();
    setDeleting(sessId);
    try {
      await api.deleteSession(sessId);
      setSessions((prev) => prev.filter((s) => s.id !== sessId));
      if (selected?.id === sessId) setSelected(null);
    } catch {
      setError("删除失败");
    } finally {
      setDeleting(null);
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-[calc(100vh-8rem)] items-center justify-center">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex min-h-[calc(100vh-8rem)] flex-col px-6 py-6">
      <div className="mx-auto w-full max-w-2xl space-y-4">
        <h2 className="text-base font-semibold text-foreground">采集暂存区</h2>

        {error && <p className="text-sm text-destructive">{error}</p>}

        {sessions.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border/60 px-5 py-12 text-center text-sm text-muted-foreground">
            暂无采集 session
          </div>
        ) : (
          <ul className="divide-y divide-border/50 overflow-hidden rounded-xl border border-border/70 bg-card">
            {sessions.map((sess) => (
              <li key={sess.id}>
                {/* session 列表行 */}
                <button
                  type="button"
                  className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-muted/40 transition-colors"
                  onClick={() => handleSelect(sess)}
                >
                  <StatusBadge status={sess.status} />
                  <span className="flex-1 min-w-0 truncate text-sm font-medium text-foreground">
                    {sess.title}
                  </span>
                  <span className="flex items-center gap-1 text-xs text-muted-foreground">
                    <Clock className="size-3" />
                    {new Date(sess.started_at).toLocaleDateString()} {new Date(sess.started_at).toLocaleTimeString()}
                  </span>
                  <button
                    type="button"
                    className="ml-1 rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    title="删除"
                    onClick={(e) => handleDelete(sess.id, e)}
                    disabled={deleting === sess.id}
                  >
                    {deleting === sess.id ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <Trash2 className="size-3.5" />
                    )}
                  </button>
                  <ChevronDown
                    className={`size-3.5 text-muted-foreground transition-transform ${selected?.id === sess.id ? "rotate-180" : ""}`}
                  />
                </button>

                {/* 展开的详情 + 时间线 */}
                {selected?.id === sess.id && (
                  <div className="border-t border-border/50 bg-muted/20 px-4 py-4 space-y-4">
                    {/* 图形时间线 v1 */}
                    <div>
                      <p className="mb-2 text-xs font-medium text-muted-foreground">
                        时间线（{selected.events.length} 条事件）
                      </p>
                      {selected.events.length === 0 ? (
                        <p className="text-xs text-muted-foreground">无事件</p>
                      ) : (
                        <div className="relative space-y-2">
                          {/* 竖线 */}
                          <div className="absolute left-[5.5rem] top-0 h-full w-px bg-border/50" aria-hidden />
                          {selected.events.map((ev) => (
                            <div key={ev.id} className="flex items-start gap-3">
                              {/* 相对时间刻度 */}
                              <span className="w-20 shrink-0 text-right text-[11px] tabular-nums text-muted-foreground pt-0.5">
                                {fmtRel(relSec(selected.started_at, ev.ts))}
                              </span>
                              {/* 时间线节点 */}
                              <span className="relative z-10 mt-1 flex size-3 shrink-0 items-center justify-center">
                                <span className="size-2 rounded-full bg-border" aria-hidden />
                              </span>
                              {/* 事件内容 */}
                              <div className="min-w-0 flex-1">
                                <div className="flex items-center gap-1.5">
                                  <span className="text-sm">{kindIcon(ev.kind)}</span>
                                  <span className="text-xs font-medium text-foreground capitalize">
                                    {ev.kind}
                                  </span>
                                </div>
                                {ev.payload && (
                                  <p className="mt-0.5 truncate text-xs text-muted-foreground">
                                    {ev.payload}
                                  </p>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* 指派到 Project（仅 staged 状态可操作） */}
                    {sess.status === "staged" && (
                      <div className="flex items-center gap-2 border-t border-border/50 pt-4">
                        <FolderOpen className="size-4 shrink-0 text-muted-foreground" />
                        <select
                          className="flex-1 rounded-lg border border-input bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                          value={selectedProjectId}
                          onChange={(e) =>
                            setSelectedProjectId(e.target.value === "" ? "" : Number(e.target.value))
                          }
                        >
                          <option value="">选择 Project…</option>
                          {projects.map((p) => (
                            <option key={p.id} value={p.id}>
                              {p.title}
                            </option>
                          ))}
                        </select>
                        <Button
                          size="sm"
                          onClick={handleOrganize}
                          disabled={!selectedProjectId || organizing}
                          className="gap-1.5 shrink-0"
                        >
                          {organizing ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : null}
                          指派并入库
                        </Button>
                      </div>
                    )}
                    {sess.status === "recording" && (
                      <p className="text-xs text-muted-foreground border-t border-border/50 pt-3">
                        录制中,停止后可指派到 Project。
                      </p>
                    )}
                    {sess.status === "organized" && (
                      <p className="text-xs text-green-700 dark:text-green-400 border-t border-border/50 pt-3">
                        已归档到 Project。
                      </p>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
