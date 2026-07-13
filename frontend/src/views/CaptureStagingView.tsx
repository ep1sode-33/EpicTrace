import { useEffect, useRef, useState } from "react";
import { ChevronDown, Clock, FolderOpen, Loader2, Trash2 } from "lucide-react";
import { api, type CaptureSession, type CaptureSessionDetail, type Project } from "@/lib/api";
import { findTimelineTargetIndex, groupTimelineItems } from "@/lib/transcript";
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

/** 时间线圆点颜色(按事件类型) */
function dotColor(kind: string): string {
  switch (kind) {
    case "note": return "bg-sky-500";
    case "clipboard": return "bg-zinc-400";
    case "screenshot": return "bg-violet-500";
    case "transcription": return "bg-teal-500";
    case "pause": return "bg-amber-500";
    case "resume": return "bg-emerald-500";
    case "source": return "bg-orange-500";
    default: return "bg-muted-foreground";
  }
}

/** 事件类型中文标签 */
function kindLabel(kind: string, meta?: Record<string, unknown>): string {
  switch (kind) {
    case "note": return "笔记";
    case "clipboard": return "剪贴板";
    case "screenshot": return "截图";
    case "transcription": return meta?.source === "device" ? "系统声音采集" : "麦克风";
    case "pause": return "暂停";
    case "resume": return "继续";
    case "source": {  // 音源开/停:何时开始/停止了哪个源的录音
      const src = meta?.source === "system_audio" ? "系统声音" : "麦克风";
      return `${src} ${meta?.action === "stop" ? "停止采集" : "开始采集"}`;
    }
    default: return kind;
  }
}

interface Props {
  onOrganized: (projectId: number) => void;
  /** 「跳回会话时刻」导航(镜像 App 的 processFocus):展开该 session 并把时间线滚动/高亮到 ts
   *  对应的转写段。key 自增支持对同一引用反复跳转。null/缺省 = 无跳转,行为零变化。 */
  focus?: { sessionId: number; ts: string; key: number } | null;
  /** 焦点被消费(getSession 成功、pendingFocus 已交接)后回调 App 清空 sessionFocus。
   *  本视图是条件渲染,会随切页卸载;若不在重挂载边界之上清掉焦点,每次切回都会重放跳转。 */
  onFocusConsumed?: () => void;
}

export function CaptureStagingView({ onOrganized, focus, onFocusConsumed }: Props) {
  const [sessions, setSessions] = useState<CaptureSession[]>([]);
  const [selected, setSelected] = useState<CaptureSessionDetail | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<number | "">("");
  const [loading, setLoading] = useState(true);
  const [organizing, setOrganizing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  // 「跳回」待定位的时刻:focus 拉到 session 详情、setSelected 后暂存于此,待 selected 就位(DOM 已提交)
  // 的 effect 消费——用它定位时间线段并滚动/高亮。
  const [pendingFocus, setPendingFocus] = useState<
    { sessionId: number; ts: string } | null
  >(null);
  // 临时高亮的时间线条目({会话 id, 段下标});~2.5s 后清。带会话 id 防切换 session 后错高亮。
  const [highlight, setHighlight] = useState<{ sessionId: number; index: number } | null>(
    null,
  );
  // 高亮清除定时器句柄:新高亮/卸载时清,防泄漏。
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  // 选中的 session 正在重转时,轮询刷新详情,等权威转录到达(retranscribing 转 false)即停。
  useEffect(() => {
    if (!selected?.retranscribing) return;
    const sid = selected.id;
    const t = setInterval(() => {
      api
        .getSession(sid)
        .then((d) => setSelected((cur) => (cur?.id === sid ? d : cur)))
        .catch(() => {});
    }, 2000);
    return () => clearInterval(t);
  }, [selected?.id, selected?.retranscribing]);

  // 「跳回会话时刻」:focus 变(每次跳转都是新对象,含 key 自增 → 反复跳同一引用也重触发)时,
  // 拉取目标 session 详情并展开为 selected;记下待定位时刻交给下面的 effect。
  // 404(会话已删)→ 复用现有错误展示,不崩。焦点为 null 时不做任何事。
  useEffect(() => {
    if (!focus) return;
    let cancelled = false;
    api
      .getSession(focus.sessionId)
      .then((detail) => {
        if (cancelled) return;
        // 会话可能不在已加载列表里(列表陈旧/组件早已挂载):补进去,保证其展开详情能渲染。
        setSessions((prev) =>
          prev.some((s) => s.id === detail.id) ? prev : [detail, ...prev],
        );
        setSelected(detail);
        setSelectedProjectId("");
        setError(null);
        setPendingFocus({ sessionId: focus.sessionId, ts: focus.ts });
        // 消费即清:通知 App 清空 sessionFocus,使本视图重挂载时 focus 为 null、不再重放本次跳转。
        // 定位所需的时刻已存进 pendingFocus(组件内 state),清 App 焦点不影响后续滚动/高亮。
        onFocusConsumed?.();
      })
      .catch(() => {
        if (cancelled) return;
        setError("会话不存在或已删除");
        // 失败路径也要消费焦点:否则这条死 focus 会跨本视图重挂载存活,每次切回都重放
        // 同一失败请求(且 App 侧 sessionFocus 永不清空)。
        onFocusConsumed?.();
      });
    return () => {
      cancelled = true;
    };
  }, [focus, onFocusConsumed]);

  // selected 就位(DOM 已提交,时间线已渲染)后,定位并滚动/高亮目标转写段。
  // !loading 门:从对话页跳来会重新挂载本组件,初始 loading=true 时渲染的是加载态、时间线尚不在 DOM;
  // 待列表加载完(loading→false,时间线渲染)effect 重跑再定位。
  // 所有 setState 都在 rAF 回调里(非 effect 体内同步调用),避免 set-state-in-effect 级联渲染。
  useEffect(() => {
    if (loading || !pendingFocus || !selected || selected.id !== pendingFocus.sessionId) return;
    const sid = selected.id;
    const events = selected.events;
    const ts = pendingFocus.ts;
    const raf = requestAnimationFrame(() => {
      setPendingFocus(null); // 消费掉本次待定位。
      const idx = findTimelineTargetIndex(groupTimelineItems(events), ts);
      if (idx < 0) return; // 无匹配转写段:仅展开会话,不滚动/高亮(优雅降级)。
      document
        .getElementById(`tl-${sid}-${idx}`)
        ?.scrollIntoView({ block: "center" });
      setHighlight({ sessionId: sid, index: idx });
      if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
      highlightTimerRef.current = setTimeout(() => setHighlight(null), 2500);
    });
    return () => cancelAnimationFrame(raf);
  }, [pendingFocus, selected, loading]);

  // 卸载时清高亮定时器,防泄漏。
  useEffect(
    () => () => {
      if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
    },
    [],
  );

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
                      <div className="mb-2 flex items-center gap-2">
                        <p className="text-xs font-medium text-muted-foreground">
                          时间线（{selected.events.length} 条事件）
                        </p>
                        {selected.retranscribing && (
                          <span className="inline-flex items-center gap-1 text-[11px] text-teal-700 dark:text-teal-300">
                            <Loader2 className="size-3 animate-spin" />
                            重新转写中…(生成权威转录,完成后自动替换)
                          </span>
                        )}
                      </div>
                      {selected.events.length === 0 ? (
                        <p className="text-xs text-muted-foreground">无事件</p>
                      ) : (
                        <div className="relative space-y-2">
                          {/* 竖线 */}
                          <div className="absolute left-[5.5rem] top-0 h-full w-px bg-border/50" aria-hidden />
                          {/* 连续同源转写合并成段落(FIX 2):一段只显示一次来源标签 + 一个时间/区间,
                              整段文本换行展示,不再逐句一行一时间戳。 */}
                          {groupTimelineItems(selected.events).map((item, index) => {
                            // 稳定 DOM id(含会话 id + 段下标)供「跳回」滚动定位;命中段临时加高亮环。
                            const domId = `tl-${selected.id}-${index}`;
                            const hit =
                              highlight?.sessionId === selected.id && highlight.index === index;
                            const rowCls = `flex items-start gap-3 rounded-lg transition ${
                              hit ? "ring-2 ring-primary/50 bg-primary/5" : ""
                            }`;
                            if (item.kind === "transcription") {
                              const startRel = fmtRel(relSec(selected.started_at, item.start_ts));
                              const endRel = fmtRel(relSec(selected.started_at, item.end_ts));
                              const timeLabel = startRel === endRel ? startRel : `${startRel}–${endRel}`;
                              return (
                                <div key={`tr-${item.ids[0]}`} id={domId} className={rowCls}>
                                  <span className="w-20 shrink-0 text-right text-[11px] tabular-nums text-muted-foreground pt-0.5">
                                    {timeLabel}
                                  </span>
                                  <span className="relative z-10 mt-1 flex size-3 shrink-0 items-center justify-center">
                                    <span className={`size-2 rounded-full ${dotColor("transcription")}`} aria-hidden />
                                  </span>
                                  <div className="min-w-0 flex-1">
                                    <span className="text-xs font-medium text-foreground">
                                      {kindLabel("transcription", { source: item.source })}
                                    </span>
                                    {item.text && (
                                      <p className="mt-0.5 whitespace-pre-wrap break-words text-xs text-muted-foreground">
                                        {item.text}
                                      </p>
                                    )}
                                  </div>
                                </div>
                              );
                            }
                            const ev = item.event;
                            return (
                              <div key={ev.id} id={domId} className={rowCls}>
                                {/* 相对时间刻度 */}
                                <span className="w-20 shrink-0 text-right text-[11px] tabular-nums text-muted-foreground pt-0.5">
                                  {fmtRel(relSec(selected.started_at, ev.ts))}
                                </span>
                                {/* 时间线节点 */}
                                <span className="relative z-10 mt-1 flex size-3 shrink-0 items-center justify-center">
                                  <span className={`size-2 rounded-full ${dotColor(ev.kind)}`} aria-hidden />
                                </span>
                                {/* 事件内容 */}
                                <div className="min-w-0 flex-1">
                                  <div className="flex items-center gap-1.5">
                                    <span className="text-xs font-medium text-foreground">
                                      {kindLabel(ev.kind, ev.meta)}
                                    </span>
                                  </div>
                                  {ev.payload && (
                                    <p className="mt-0.5 truncate text-xs text-muted-foreground">
                                      {ev.payload}
                                    </p>
                                  )}
                                </div>
                              </div>
                            );
                          })}
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
                          disabled={!selectedProjectId || organizing || selected?.retranscribing}
                          title={selected?.retranscribing ? "正在生成权威转录,完成后才能入库" : undefined}
                          className="gap-1.5 shrink-0"
                        >
                          {organizing ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : null}
                          指派并入库
                        </Button>
                      </div>
                    )}
                    {sess.status === "staged" && selected?.retranscribing && (
                      <p className="text-xs text-muted-foreground -mt-2">
                        正在生成权威转录,完成后即可入库。
                      </p>
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
