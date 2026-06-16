import { useEffect, useRef, useState } from "react";
import {
  Camera,
  Clipboard,
  Loader2,
  Mic,
  Pause,
  Play,
  Square,
  StickyNote,
  Volume2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, type CaptureSessionDetail } from "@/lib/api";
import { native } from "@/lib/native";

/** 来源选项;笔记/剪贴板/截图 + 外录(麦克风)/内录(系统声音)均可勾选 */
const SOURCE_OPTIONS = [
  { id: "note", icon: StickyNote, label: "笔记", disabled: false, coming: false },
  { id: "clipboard", icon: Clipboard, label: "剪贴板", disabled: false, coming: false },
  { id: "screenshot", icon: Camera, label: "截图", disabled: false, coming: false },
  { id: "mic", icon: Mic, label: "🎤 外录(麦克风)", disabled: false, coming: false },
  { id: "system_audio", icon: Volume2, label: "🔊 内录(系统声音)", disabled: false, coming: false },
];

/** 将秒数格式化为 MM:SS */
function formatTime(secs: number): string {
  const m = Math.floor(secs / 60).toString().padStart(2, "0");
  const s = Math.floor(secs % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

/** transcription 事件的来源:meta.source 为 "mic"(外录)或 "device"(内录)。 */
function sourceLabel(meta: Record<string, unknown>): string {
  return meta?.source === "device" ? "内录" : "外录";
}
function sourceEmoji(meta: Record<string, unknown>): string {
  return meta?.source === "device" ? "🔊" : "🎤";
}

export function CaptureView({ onSessionStopped }: { onSessionStopped?: () => void } = {}) {
  const [selectedSources, setSelectedSources] = useState<Set<string>>(
    new Set(["note", "clipboard", "screenshot"]),
  );
  const [session, setSession] = useState<CaptureSessionDetail | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [paused, setPaused] = useState(false);
  const [noteText, setNoteText] = useState("");
  const [noteLoading, setNoteLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const elapsedRef = useRef(0); // 同步 ref 供 timer 使用

  // 检查启动时是否已有活动 session(页面刷新/重新进入时恢复状态)
  useEffect(() => {
    api
      .activeSession()
      .then((s) => {
        if (s && s.status === "recording") {
          // 恢复活动会话
          api
            .getSession(s.id)
            .then((detail) => {
              setSession(detail);
              const base = detail.elapsed_seconds;
              elapsedRef.current = base;
              setElapsed(base);
              startTimer();
              startPolling(s.id);
            })
            .catch(() => {});
        }
      })
      .catch(() => {});
    return () => {
      stopTimer();
      stopPolling();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  function startPolling(sid: number) {
    stopPolling();
    pollRef.current = setInterval(() => {
      api
        .getSession(sid)
        .then((detail) => {
          // 若 session 已被本窗、HUD 或别处停止 → 退出录制态(否则主窗会一直显示「录制中」)。
          if (detail.status !== "recording") {
            stopTimer();
            stopPolling();
            setSession(null);
            setElapsed(0);
            elapsedRef.current = 0;
            setPaused(false);
            onSessionStopped?.();
            return;
          }
          setSession(detail);
          // 定期用后端值校准计时
          elapsedRef.current = detail.elapsed_seconds;
          setElapsed(detail.elapsed_seconds);
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

  function toggleSource(id: string) {
    setSelectedSources((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleStart() {
    setStarting(true);
    setError(null);
    try {
      const sources = Array.from(selectedSources);
      const sess = await api.startSession(sources);
      // 拉取 detail（含 events 和 elapsed_seconds）
      const detail = await api.getSession(sess.id);
      setSession(detail);
      elapsedRef.current = detail.elapsed_seconds;
      setElapsed(detail.elapsed_seconds);
      setPaused(false);
      startTimer();
      startPolling(sess.id);
      // 通知原生层开启监听 + 显示 HUD
      await native.startMonitors(sess.id, sess.staging_dir, sources);
      await native.showHud(sess.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "开始 session 失败");
    } finally {
      setStarting(false);
    }
  }

  async function handleNote() {
    if (!session || !noteText.trim()) return;
    setNoteLoading(true);
    try {
      await api.appendEvent(session.id, "note", noteText.trim());
      setNoteText("");
      // 立即刷新 session 以显示新笔记
      const detail = await api.getSession(session.id);
      setSession(detail);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存笔记失败");
    } finally {
      setNoteLoading(false);
    }
  }

  async function handleScreenshot() {
    if (!native.available()) return;
    try {
      await native.screenshot();
      // shell 会自行 POST 截图事件;轮询会在下一次拉取时更新
    } catch (e) {
      setError(e instanceof Error ? e.message : "截图失败");
    }
  }

  async function handlePauseResume() {
    if (!session) return;
    try {
      if (!paused) {
        await api.pauseSession(session.id);
        await native.stopMonitors();
        stopTimer();
        setPaused(true);
      } else {
        await api.resumeSession(session.id);
        const sources = session.sources;
        await native.startMonitors(session.id, session.staging_dir, sources);
        startTimer();
        setPaused(false);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    }
  }

  async function handleStop() {
    if (!session) return;
    setStopping(true);
    setError(null);
    try {
      await native.stopMonitors();
      await native.hideHud();
      await api.stopSession(session.id);
      stopTimer();
      stopPolling();
      setSession(null);
      setElapsed(0);
      elapsedRef.current = 0;
      setPaused(false);
      onSessionStopped?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "停止 session 失败");
    } finally {
      setStopping(false);
    }
  }

  // —— 无活动 session —— 显示源开关 + 开始按钮
  if (!session) {
    return (
      <div className="flex min-h-[calc(100vh-3.5rem)] flex-col items-center justify-center px-8 py-16">
        <div className="flex w-full max-w-md flex-col items-center text-center">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">采集 session</h1>
          <p className="mt-2.5 text-sm leading-relaxed text-balance text-muted-foreground">
            开启一个 session,在工作过程中持续采集以下来源,稍后统一整理归类并入库。
          </p>

          {error && (
            <p className="mt-4 text-sm text-destructive">{error}</p>
          )}

          {/* 来源开关 */}
          <ul className="mt-8 w-full divide-y divide-border/60 overflow-hidden rounded-xl border border-border/70 bg-card text-left">
            {SOURCE_OPTIONS.map(({ id, icon: Icon, label, disabled, coming }) => (
              <li
                key={id}
                className={`flex items-center gap-3 px-4 py-2.5 ${disabled ? "opacity-50" : "cursor-pointer hover:bg-muted/40 transition-colors"}`}
                onClick={() => !disabled && toggleSource(id)}
              >
                <span
                  className={`flex size-4 shrink-0 items-center justify-center rounded border transition-colors ${
                    !disabled && selectedSources.has(id)
                      ? "border-primary bg-primary"
                      : "border-muted-foreground/40 bg-transparent"
                  }`}
                  aria-hidden
                >
                  {!disabled && selectedSources.has(id) && (
                    <svg
                      viewBox="0 0 12 12"
                      className="size-2.5 text-primary-foreground"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={2}
                    >
                      <polyline points="2,6 5,9 10,3" />
                    </svg>
                  )}
                </span>
                <Icon
                  aria-hidden
                  className="size-4 shrink-0 text-muted-foreground"
                  strokeWidth={1.75}
                />
                <span className="flex-1 text-sm font-medium text-foreground">{label}</span>
                {coming && (
                  <span className="rounded-full border border-amber-600/25 bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-800 dark:border-amber-400/20 dark:bg-amber-400/10 dark:text-amber-300">
                    即将到来
                  </span>
                )}
              </li>
            ))}
          </ul>

          <Button
            className="mt-8 gap-1.5"
            onClick={handleStart}
            disabled={starting || selectedSources.size === 0}
          >
            {starting ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
            开始 session
          </Button>
        </div>
      </div>
    );
  }

  // —— 有活动 session —— 显示计时 + live feed + 控件
  const textEvents = session.events.filter((e) =>
    ["note", "clipboard", "screenshot", "transcription"].includes(e.kind),
  );

  return (
    <div className="flex min-h-[calc(100vh-3.5rem)] flex-col px-6 py-8">
      <div className="mx-auto w-full max-w-2xl space-y-6">
        {/* 状态栏 */}
        <div className="flex items-center justify-between rounded-xl border border-border/70 bg-card px-5 py-4">
          <div className="flex items-center gap-3">
            <span
              className={`size-2.5 rounded-full ${paused ? "bg-amber-500" : "animate-pulse bg-red-500"}`}
              aria-hidden
            />
            <span className="font-mono text-xl font-semibold tabular-nums text-foreground">
              {formatTime(elapsed)}
            </span>
            <span className="text-xs text-muted-foreground">
              {paused ? "已暂停" : "录制中"}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5"
              onClick={handlePauseResume}
            >
              {paused ? (
                <>
                  <Play className="size-3.5" />
                  继续
                </>
              ) : (
                <>
                  <Pause className="size-3.5" />
                  暂停
                </>
              )}
            </Button>
            <Button
              variant="destructive"
              size="sm"
              className="gap-1.5"
              onClick={handleStop}
              disabled={stopping}
            >
              {stopping ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Square className="size-3.5" />
              )}
              停止
            </Button>
          </div>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}

        {/* 笔记输入 */}
        <div className="flex gap-2">
          <input
            type="text"
            value={noteText}
            onChange={(e) => setNoteText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void handleNote();
              }
            }}
            placeholder="快速记录笔记… (Enter 提交)"
            className="flex-1 rounded-lg border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          />
          <Button
            variant="outline"
            size="sm"
            onClick={handleNote}
            disabled={noteLoading || !noteText.trim()}
          >
            {noteLoading ? <Loader2 className="size-3.5 animate-spin" /> : <StickyNote className="size-3.5" />}
          </Button>
          {/* 截图按钮 */}
          <Button
            variant="outline"
            size="sm"
            onClick={handleScreenshot}
            disabled={!native.available()}
            title={native.available() ? "截取当前屏幕" : "需在桌面 app 内"}
          >
            <Camera className="size-3.5" />
          </Button>
        </div>

        {/* Live feed */}
        <div className="space-y-1">
          <p className="text-xs font-medium text-muted-foreground">
            采集 feed（{textEvents.length} 条）
          </p>
          {textEvents.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border/60 px-5 py-8 text-center text-sm text-muted-foreground">
              暂无采集内容
            </div>
          ) : (
            <ul className="divide-y divide-border/50 overflow-hidden rounded-xl border border-border/70 bg-card">
              {[...textEvents].reverse().map((ev) => (
                <li key={ev.id} className="flex items-start gap-3 px-4 py-3">
                  <span className="mt-0.5 text-base leading-none">
                    {ev.kind === "note" ? "✏️" : ev.kind === "clipboard" ? "📋" : ev.kind === "screenshot" ? "📷" : ev.kind === "transcription" ? sourceEmoji(ev.meta) : "•"}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <p className="truncate text-sm text-foreground">{ev.payload || "(无内容)"}</p>
                      {ev.kind === "transcription" && (
                        <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                          {sourceLabel(ev.meta)}
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {new Date(ev.ts).toLocaleTimeString()}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
