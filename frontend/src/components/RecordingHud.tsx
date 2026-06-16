import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ChevronDown,
  ChevronUp,
  Crop,
  Mic,
  Pause,
  Pencil,
  Play,
  Square,
  Volume2,
} from "lucide-react";
import { api, type CaptureEvent } from "@/lib/api";
import { native } from "@/lib/native";

// HUD 窗口尺寸(配合 shell 的 resize_recording_hud)。
// 紧凑条 / 带笔记行 / 展开成正经面板(工具条 + 时间线 + 底部输入)。
const BAR_W = 300;
const BAR_H = 40;
const NOTE_H = 78;
const EXPANDED_W = 380;
const EXPANDED_H = 430;

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

/** 紧凑图标按钮(HUD 专用) */
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
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
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

  // 据 expanded / noteOpen 调整窗口尺寸(展开切到大面板,收起回紧凑条)。
  function syncSize(nextExpanded: boolean, nextNoteOpen: boolean) {
    if (nextExpanded) void native.resizeHud(EXPANDED_W, EXPANDED_H);
    else void native.resizeHud(BAR_W, nextNoteOpen ? NOTE_H : BAR_H);
  }

  function toggleExpand() {
    const next = !expanded;
    setExpanded(next);
    expandedRef.current = next;
    syncSize(next, noteOpenRef.current);
  }
  function toggleNote() {
    const next = !noteOpen;
    setNoteOpen(next);
    noteOpenRef.current = next;
    if (!expandedRef.current) syncSize(false, next);
  }
  function notePencil() {
    // 展开态笔记输入常驻底部,铅笔聚焦它;紧凑态切换输入行。
    if (expandedRef.current) inputRef.current?.focus();
    else toggleNote();
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

  const timeline = events.filter((e) =>
    ["note", "clipboard", "screenshot", "pause", "resume"].includes(e.kind),
  );

  function renderContent(ev: CaptureEvent): ReactNode {
    if (ev.kind === "screenshot") return <span className="text-foreground">截图</span>;
    if (ev.kind === "pause")
      return <span className="text-amber-600 dark:text-amber-400">暂停</span>;
    if (ev.kind === "resume")
      return <span className="text-emerald-600 dark:text-emerald-400">继续</span>;
    return <span className="text-foreground">{ev.payload || "(无内容)"}</span>;
  }

  const noteInput = (
    <input
      ref={inputRef}
      type="text"
      value={noteText}
      onChange={(e) => setNoteText(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          void handleNote();
        }
        if (e.key === "Escape" && !expanded) toggleNote();
      }}
      placeholder="记笔记… Enter 提交"
      className="w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-xs placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
    />
  );

  // 工具条(紧凑条与展开面板顶部共用)
  const toolbar = (
    <div className="flex h-10 shrink-0 items-center gap-0.5 px-2">
      <span
        className={`size-2 shrink-0 rounded-full ${paused ? "bg-amber-500" : "animate-pulse bg-red-500"}`}
        aria-hidden
      />
      <span className="mx-1.5 w-10 font-mono text-xs font-semibold tabular-nums text-foreground">
        {formatTime(elapsed)}
      </span>
      <span className="mr-0.5 h-4 w-px bg-border" aria-hidden />
      <IconBtn onClick={notePencil} title="记笔记">
        <Pencil className="size-3.5" />
      </IconBtn>
      <IconBtn
        onClick={handleScreenshot}
        disabled={!native.available()}
        title={native.available() ? "截图" : "需在桌面 app 内"}
      >
        <Crop className="size-3.5" />
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
      {/* 展开态把收起箭头推到面板右缘;紧凑态紧贴 */}
      {expanded && <div className="flex-1" aria-hidden />}
      <span className="mx-0.5 h-4 w-px bg-border" aria-hidden />
      <IconBtn onClick={toggleExpand} title={expanded ? "收起" : "展开时间线"}>
        {expanded ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
      </IconBtn>
    </div>
  );

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background/95 backdrop-blur select-none">
      {toolbar}

      {/* 紧凑态:笔记输入行 */}
      {!expanded && noteOpen && <div className="px-2 pb-2">{noteInput}</div>}

      {/* 展开态:时间线(填充)+ 底部常驻笔记输入 */}
      {expanded && (
        <>
          <div className="min-h-0 flex-1 overflow-y-auto border-t border-border/60 px-3 py-2">
            {timeline.length === 0 ? (
              <p className="py-8 text-center text-xs text-muted-foreground">暂无采集内容</p>
            ) : (
              <div className="relative pl-1">
                <div className="absolute bottom-1 left-[5px] top-1 w-px bg-border" aria-hidden />
                <ul className="space-y-2.5">
                  {timeline.map((ev) => (
                    <li key={ev.id} className="relative flex gap-2 pl-4">
                      <span
                        className={`absolute left-[1px] top-1 size-2 rounded-full ring-2 ring-background ${dotColor(ev.kind)}`}
                        aria-hidden
                      />
                      <div className="min-w-0 flex-1">
                        <div className="break-words text-xs leading-snug">{renderContent(ev)}</div>
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
          <div className="shrink-0 border-t border-border/60 p-2">{noteInput}</div>
        </>
      )}
    </div>
  );
}
