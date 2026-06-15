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
const BAR_W = 300;
const BAR_H = 44;
const NOTE_H = 84;
const EXPANDED_H = 300;

/** 将秒数格式化为 MM:SS */
function formatTime(secs: number): string {
  const m = Math.floor(secs / 60).toString().padStart(2, "0");
  const s = Math.floor(secs % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
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
      className={`flex size-7 shrink-0 items-center justify-center rounded-md transition-colors disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent ${
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
          // session 已停止 → 收尾并销毁 HUD 窗口
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

  const feed = [...events]
    .filter((e) => ["note", "clipboard", "screenshot"].includes(e.kind))
    .reverse();

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
      <div className="flex h-11 shrink-0 items-center gap-0.5 px-1.5">
        <span
          className={`ml-1 size-2 shrink-0 rounded-full ${paused ? "bg-amber-500" : "animate-pulse bg-red-500"}`}
          aria-hidden
        />
        <span className="mx-1 w-10 font-mono text-xs font-semibold tabular-nums text-foreground">
          {formatTime(elapsed)}
        </span>
        <span className="mr-0.5 h-4 w-px bg-border" aria-hidden />
        <IconBtn onClick={toggleNote} title="记笔记">
          <StickyNote className="size-3.5" />
        </IconBtn>
        <IconBtn
          onClick={handleScreenshot}
          disabled={!native.available()}
          title={native.available() ? "截图" : "需在桌面 app 内"}
        >
          <Camera className="size-3.5" />
        </IconBtn>
        <IconBtn disabled title="外录 · 即将到来">
          <Mic className="size-3.5" />
        </IconBtn>
        <IconBtn disabled title="内录 · 即将到来">
          <Volume2 className="size-3.5" />
        </IconBtn>
        <IconBtn onClick={handlePauseResume} title={paused ? "继续" : "暂停"}>
          {paused ? <Play className="size-3.5" /> : <Pause className="size-3.5" />}
        </IconBtn>
        <IconBtn onClick={handleStop} danger title="停止">
          <Square className="size-3.5 fill-current" />
        </IconBtn>
        <span className="mx-0.5 h-4 w-px bg-border" aria-hidden />
        <IconBtn onClick={toggleExpand} title={expanded ? "收起" : "展开时间线"}>
          {expanded ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
        </IconBtn>
      </div>

      {/* 未展开时的笔记输入行 */}
      {noteOpen && !expanded && <div className="px-2 pb-2">{noteInput}</div>}

      {/* 展开:向下预览时间线 */}
      {expanded && (
        <div className="flex min-h-0 flex-1 flex-col gap-1.5 border-t border-border/60 px-2 py-1.5">
          {noteOpen && noteInput}
          <div className="min-h-0 flex-1 overflow-y-auto">
            {feed.length === 0 ? (
              <p className="py-4 text-center text-xs text-muted-foreground">暂无采集内容</p>
            ) : (
              <ul className="space-y-0.5">
                {feed.map((ev) => (
                  <li
                    key={ev.id}
                    className="flex items-start gap-1.5 rounded px-1 py-0.5 text-xs hover:bg-muted/40"
                  >
                    <span className="leading-tight">
                      {ev.kind === "note" ? "✏️" : ev.kind === "clipboard" ? "📋" : "📷"}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-foreground">
                      {ev.payload || "(截图)"}
                    </span>
                    <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
                      {new Date(ev.ts).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
