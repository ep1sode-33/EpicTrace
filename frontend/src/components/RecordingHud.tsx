import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Camera,
  ChevronDown,
  ChevronUp,
  Mic,
  Pause,
  Play,
  Square,
  StickyNote,
  Volume2,
} from "lucide-react";
import { api, type CaptureEvent } from "@/lib/api";
import { native } from "@/lib/native";

// HUD 窗口尺寸(配合 shell 的 resize_recording_hud):紧凑条 / 带笔记行 / 展开时间线。
const BAR_W = 280;
const BAR_H = 40;
const NOTE_H = 78;
const EXPANDED_H = 300;

/** 将秒数格式化为 MM:SS */
function formatTime(secs: number): string {
  const m = Math.floor(secs / 60).toString().padStart(2, "0");
  const s = Math.floor(secs % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

/** 时间线圆点颜色(按事件类型) */
function dotColor(kind: string): string {
  switch (kind) {
    case "note":
      return "bg-sky-500";
    case "clipboard":
      return "bg-zinc-400";
    case "screenshot":
      return "bg-violet-500";
    case "pause":
      return "bg-amber-500";
    case "resume":
      return "bg-emerald-500";
    default:
      return "bg-muted-foreground";
  }
}

/** 紧凑图标按钮(HUD 专用,小尺寸) */
function IconBtn({
  onClick,
  disabled,
  title,
  danger,
  children,
}: {
  onClick?: () => void;
  disabled?: boolean;
  title?: string;
  danger?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`flex size-6 shrink-0 items-center justify-center rounded-md transition-colors disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent ${
        danger
          ? "text-red-500 hover:bg-red-500/10"
          : "text-muted-foreground hover:bg-muted hover:text-foreground"
      }`}
    >
      {children}
    </button>
  );
}

export function RecordingHud({ sessionId }: { sessionId: number }) {
  const [elapsed, setElapsed] = useState(0);
  const [paused, setPaused] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [noteText, setNoteText] = useState("");
  const [stopped, setStopped] = useState(false);
  const [sources, setSources] = useState<string[]>([]);
  const [stagingDir, setStagingDir] = useState("");
  const [events, setEvents] = useState<CaptureEvent[]>([]);

  const elapsedRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  // 供 syncHeight 取最新值(避免闭包旧值)
  const expandedRef = useRef(false);
  const noteOpenRef = useRef(false);

  useEffect(() => {
    if (!sessionId) return;
    api
      .getSession(sessionId)
      .then((d) => {
        elapsedRef.current = d.elapsed_seconds;
        setElapsed(d.elapsed_seconds);
        setSources(d.sources);
        setStagingDir(d.staging_dir);
        setEvents(d.events);
        startTimer();
        startPolling();
      })
      .catch(() => {});
    return () => {
      stopTimer();
      stopPolling();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // 时间线随新事件滚到底
  useEffect(() => {
    if (expanded) bottomRef.current?.scrollIntoView({ block: "end" });
  }, [events.length, expanded]);

  function startTimer() {
    stopTimer();
    timerRef.current = setInterval(() => {
      elapsedRef.current += 1;
      setElapsed(elapsedRef.current);
    }, 1000);
  }
  function stopTimer() {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }
  function startPolling() {
    stopPolling();
    pollRef.current = setInterval(() => {
      api
        .getSession(sessionId)
        .then((d) => {
          elapsedRef.current = d.elapsed_seconds;
          setElapsed(d.elapsed_seconds);
          setEvents(d.events);
          if (d.status !== "recording") {
            stopTimer();
            stopPolling();
            setStopped(true);
            void native.hideHud();
          }
        })
        .catch(() => {});
    }, 1500);
  }
  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  // 据 expanded / noteOpen 计算窗口高度并通知 shell resize。
  function syncHeight(nextExpanded: boolean, nextNoteOpen: boolean) {
    const h = nextExpanded ? EXPANDED_H : nextNoteOpen ? NOTE_H : BAR_H;
    void native.resizeHud(BAR_W, h);
  }

  function toggleExpand() {
    const next = !expanded;
    setExpanded(next);
    expandedRef.current = next;
    syncHeight(next, noteOpenRef.current);
  }
  function toggleNote() {
    const next = !noteOpen;
    setNoteOpen(next);
    noteOpenRef.current = next;
    syncHeight(expandedRef.current, next);
  }

  async function handlePauseResume() {
    try {
      if (!paused) {
        await api.pauseSession(sessionId);
        await native.stopMonitors();
        stopTimer();
        setPaused(true);
      } else {
        await api.resumeSession(sessionId);
        await native.startMonitors(sessionId, stagingDir, sources);
        startTimer();
        setPaused(false);
      }
    } catch {
      /* 静默降级 */
    }
  }

  async function handleStop() {
    try {
      stopTimer();
      stopPolling();
      await native.stopMonitors();
      await api.stopSession(sessionId);
      setStopped(true);
      await native.hideHud();
    } catch {
      /* 静默降级 */
    }
  }

  async function handleNote() {
    if (!noteText.trim()) return;
    try {
      await api.appendEvent(sessionId, "note", noteText.trim());
      setNoteText("");
      const d = await api.getSession(sessionId);
      setEvents(d.events);
    } catch {
      /* 静默降级 */
    }
  }

  async function handleScreenshot() {
    try {
      await native.screenshot();
    } catch {
      /* 静默降级 */
    }
  }

  if (stopped) {
    return (
      <div className="flex h-screen items-center justify-center bg-background text-[11px] text-muted-foreground">
        已停止
      </div>
    );
  }

  // 时间线:按时间顺序(旧→新),含暂停/继续标记
  const timeline = events.filter((e) =>
    ["note", "clipboard", "screenshot", "pause", "resume"].includes(e.kind),
  );

  function renderContent(ev: CaptureEvent): ReactNode {
    if (ev.kind === "screenshot")
      return <span className="text-foreground">📷 截图</span>;
    if (ev.kind === "pause")
      return <span className="text-amber-600 dark:text-amber-400">⏸ 暂停</span>;
    if (ev.kind === "resume")
      return <span className="text-emerald-600 dark:text-emerald-400">▶ 继续</span>;
    return <span className="text-foreground">{ev.payload || "(无内容)"}</span>;
  }

  const noteInput = (
    <input
      autoFocus
      type="text"
      value={noteText}
      onChange={(e) => setNoteText(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          void handleNote();
        }
        if (e.key === "Escape") toggleNote();
      }}
      placeholder="记笔记… Enter 提交"
      className="w-full rounded-md border border-input bg-background px-2 py-1 text-xs placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
    />
  );

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background/95 backdrop-blur select-none">
      {/* 紧凑控制条 */}
      <div className="flex h-10 shrink-0 items-center gap-0.5 px-1.5">
        <span
          className={`ml-0.5 size-2 shrink-0 rounded-full ${paused ? "bg-amber-500" : "animate-pulse bg-red-500"}`}
          aria-hidden
        />
        <span className="mx-1 w-9 font-mono text-[11px] font-semibold tabular-nums text-foreground">
          {formatTime(elapsed)}
        </span>
        <span className="mr-0.5 h-3.5 w-px bg-border" aria-hidden />
        <IconBtn onClick={toggleNote} title="记笔记">
          <StickyNote className="size-3" />
        </IconBtn>
        <IconBtn
          onClick={handleScreenshot}
          disabled={!native.available()}
          title={native.available() ? "截图" : "需在桌面 app 内"}
        >
          <Camera className="size-3" />
        </IconBtn>
        <IconBtn disabled title="外录 · 即将到来">
          <Mic className="size-3" />
        </IconBtn>
        <IconBtn disabled title="内录 · 即将到来">
          <Volume2 className="size-3" />
        </IconBtn>
        <IconBtn onClick={handlePauseResume} title={paused ? "继续" : "暂停"}>
          {paused ? <Play className="size-3" /> : <Pause className="size-3" />}
        </IconBtn>
        <IconBtn onClick={handleStop} danger title="停止">
          <Square className="size-3 fill-current" />
        </IconBtn>
        <span className="mx-0.5 h-3.5 w-px bg-border" aria-hidden />
        <IconBtn onClick={toggleExpand} title={expanded ? "收起" : "展开时间线"}>
          {expanded ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
        </IconBtn>
      </div>

      {/* 未展开时的笔记输入行 */}
      {noteOpen && !expanded && <div className="px-2 pb-2">{noteInput}</div>}

      {/* 展开:向下的垂直时间线 */}
      {expanded && (
        <div className="flex min-h-0 flex-1 flex-col border-t border-border/60">
          {noteOpen && <div className="shrink-0 px-2 py-1.5">{noteInput}</div>}
          <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
            {timeline.length === 0 ? (
              <p className="py-6 text-center text-xs text-muted-foreground">暂无采集内容</p>
            ) : (
              <div className="relative pl-1">
                {/* 贯穿竖线 */}
                <div
                  className="absolute bottom-1 left-[5px] top-1 w-px bg-border"
                  aria-hidden
                />
                <ul className="space-y-2">
                  {timeline.map((ev) => (
                    <li key={ev.id} className="relative flex gap-2 pl-3.5">
                      {/* 圆点(压在竖线上) */}
                      <span
                        className={`absolute left-[2px] top-1 size-2 rounded-full ring-2 ring-background ${dotColor(ev.kind)}`}
                        aria-hidden
                      />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-xs leading-snug">{renderContent(ev)}</div>
                        <div className="mt-0.5 text-[10px] tabular-nums text-muted-foreground">
                          {new Date(ev.ts).toLocaleTimeString([], {
                            hour: "2-digit",
                            minute: "2-digit",
                            second: "2-digit",
                          })}
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
                <div ref={bottomRef} className="h-px" />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
