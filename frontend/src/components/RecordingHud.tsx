import { useEffect, useRef, useState } from "react";
import { Camera, ChevronRight, Mic, Pause, Play, Square, StickyNote, Volume2 } from "lucide-react";
import { api } from "@/lib/api";
import { native } from "@/lib/native";

/** 将秒数格式化为 MM:SS */
function formatTime(secs: number): string {
  const m = Math.floor(secs / 60).toString().padStart(2, "0");
  const s = Math.floor(secs % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

export function RecordingHud({ sessionId }: { sessionId: number }) {
  const [elapsed, setElapsed] = useState(0);
  const [paused, setPaused] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [noteText, setNoteText] = useState("");
  const [stopped, setStopped] = useState(false);
  const [sources, setSources] = useState<string[]>([]);
  const [stagingDir, setStagingDir] = useState("");

  const elapsedRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 拉取初始 session 信息
  useEffect(() => {
    if (!sessionId) return;
    api
      .getSession(sessionId)
      .then((detail) => {
        elapsedRef.current = detail.elapsed_seconds;
        setElapsed(detail.elapsed_seconds);
        setSources(detail.sources);
        setStagingDir(detail.staging_dir);
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
        .then((detail) => {
          // 定期校准计时
          elapsedRef.current = detail.elapsed_seconds;
          setElapsed(detail.elapsed_seconds);
          if (detail.status !== "recording") {
            stopTimer();
            stopPolling();
          }
        })
        .catch(() => {});
    }, 2000);
  }

  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
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
      // HUD 窗口由 shell 的 hide_recording_hud 销毁;这里先标记已停止
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
      setNoteOpen(false);
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
      <div className="flex h-screen items-center justify-center bg-background/80 text-xs text-muted-foreground">
        已停止
      </div>
    );
  }

  // 收起态:只显示红点 + 计时 + 展开按钮
  if (collapsed) {
    return (
      <div className="flex h-screen items-center gap-2 bg-background/90 px-3 backdrop-blur">
        <span
          className={`size-2 rounded-full ${paused ? "bg-amber-500" : "animate-pulse bg-red-500"}`}
          aria-hidden
        />
        <span className="font-mono text-xs font-semibold tabular-nums text-foreground">
          {formatTime(elapsed)}
        </span>
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          className="ml-1 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          title="展开"
        >
          <ChevronRight className="size-3.5 rotate-180" />
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col justify-center gap-1 bg-background/90 backdrop-blur">
      {/* 主控制行 */}
      <div className="flex items-center gap-1.5 px-2">
        {/* 录制状态指示 */}
        <span
          className={`size-2 shrink-0 rounded-full ${paused ? "bg-amber-500" : "animate-pulse bg-red-500"}`}
          aria-hidden
        />
        {/* 计时 */}
        <span className="w-12 font-mono text-xs font-semibold tabular-nums text-foreground">
          {formatTime(elapsed)}
        </span>

        {/* 笔记按钮 */}
        <button
          type="button"
          onClick={() => setNoteOpen((o) => !o)}
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          title="记笔记"
        >
          <StickyNote className="size-3.5" />
        </button>

        {/* 截图按钮 */}
        <button
          type="button"
          onClick={handleScreenshot}
          disabled={!native.available()}
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40 disabled:cursor-not-allowed"
          title={native.available() ? "截取屏幕" : "需在桌面 app 内"}
        >
          <Camera className="size-3.5" />
        </button>

        {/* 外录(禁用) */}
        <button
          type="button"
          disabled
          className="rounded p-1 text-muted-foreground opacity-40 cursor-not-allowed"
          title="即将到来"
        >
          <Mic className="size-3.5" />
        </button>

        {/* 内录(禁用) */}
        <button
          type="button"
          disabled
          className="rounded p-1 text-muted-foreground opacity-40 cursor-not-allowed"
          title="即将到来"
        >
          <Volume2 className="size-3.5" />
        </button>

        {/* 暂停/继续 */}
        <button
          type="button"
          onClick={handlePauseResume}
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          title={paused ? "继续" : "暂停"}
        >
          {paused ? <Play className="size-3.5" /> : <Pause className="size-3.5" />}
        </button>

        {/* 停止 */}
        <button
          type="button"
          onClick={handleStop}
          className="rounded p-1 text-red-500 hover:bg-red-500/10"
          title="停止录制"
        >
          <Square className="size-3.5" />
        </button>

        {/* 收起 */}
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          title="收起"
        >
          <ChevronRight className="size-3.5" />
        </button>
      </div>

      {/* 笔记内联输入行(展开时显示) */}
      {noteOpen && (
        <div className="flex items-center gap-1 px-2">
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
              if (e.key === "Escape") {
                setNoteOpen(false);
                setNoteText("");
              }
            }}
            placeholder="记笔记… Enter 提交"
            className="flex-1 rounded border border-input bg-background px-2 py-1 text-xs placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          />
        </div>
      )}
    </div>
  );
}
