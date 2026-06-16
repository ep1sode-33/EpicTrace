import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ChevronDown,
  ChevronUp,
  Crop,
  Mic,
  MicOff,
  Pause,
  Pencil,
  Play,
  Square,
  Volume2,
  VolumeX,
} from "lucide-react";
import { api, type CaptureEvent, type CapturePartial } from "@/lib/api";
import { groupTimelineItems, type TimelineItem } from "@/lib/transcript";
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
    case "transcription":
      return "bg-teal-500";
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

/**
 * 音频源软静音开关(HUD 专用)。三态:
 * - 未启用(本 session 没选这个源):灰、不可点。
 * - 已启用 + 未静音:点亮(teal)+ 实心图标 = 正在采集;点击静音。
 * - 已启用 + 已静音:灰 + 斜杠图标(MicOff/VolumeX);点击恢复。
 */
function AudioToggle({
  enabled,
  muted,
  onLit,
  onMuted,
  labelOn,
  labelMuted,
  labelDisabled,
  onClick,
}: {
  enabled: boolean;
  muted: boolean;
  onLit: ReactNode;
  onMuted: ReactNode;
  labelOn: string;
  labelMuted: string;
  labelDisabled: string;
  onClick: () => void;
}) {
  const active = enabled && !muted;
  return (
    <button
      type="button"
      disabled={!enabled}
      onClick={onClick}
      title={!enabled ? labelDisabled : muted ? labelMuted : labelOn}
      className={`flex size-7 shrink-0 items-center justify-center rounded-md transition-colors disabled:cursor-not-allowed ${
        !enabled
          ? "text-muted-foreground/30"
          : active
            ? "text-teal-500 hover:bg-teal-500/10"
            : "text-muted-foreground/60 hover:bg-muted hover:text-foreground"
      }`}
    >
      {active ? onLit : onMuted}
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
  // 实时暂定段(ASR partial):与 events 分开存,不混进持久事件列表;末尾淡显「暂定」行。
  const [partials, setPartials] = useState<CapturePartial>({});
  // 软静音集(被静音的音频源 id):服务端权威态,挂载 + 每次轮询时刷新;点击乐观切换。
  const [muted, setMuted] = useState<string[]>([]);

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
    // 挂载即拉一次软静音集(失败/无 ASR 时静默,留空 = 全未静音)。
    api
      .getAsrMute(sessionId)
      .then((m) => setMuted(m.muted))
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
      // 与会话事件同频拉一份 partial 快照(内存态;失败/无 ASR 时静默,partial 留空)。
      api
        .getSessionPartial(sessionId)
        .then((p) => setPartials(p))
        .catch(() => {});
      // 同频刷新软静音集(服务端权威态;反映在别处/上次会话的改动)。
      api
        .getAsrMute(sessionId)
        .then((m) => setMuted(m.muted))
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

  // 软静音切换某路音频源(乐观更新 + 失败回滚)。仅对本 session 启用的源可点。
  async function toggleMute(source: string) {
    if (!sources.includes(source)) return;
    const willMute = !muted.includes(source);
    const prev = muted;
    setMuted(willMute ? [...muted, source] : muted.filter((s) => s !== source));
    try {
      await api.setAsrMute(sessionId, source, willMute);
    } catch {
      setMuted(prev); // 回滚
    }
  }

  if (stopped) {
    return (
      <div className="flex h-screen items-center justify-center bg-background text-[11px] text-muted-foreground">
        已停止
      </div>
    );
  }

  // 连续同源转写合并成段落(FIX 2):时间线不再逐句一行,整段一个气泡 + 一个时间戳。
  const timeline = groupTimelineItems(
    events.filter((e) =>
      ["note", "clipboard", "screenshot", "transcription", "pause", "resume"].includes(e.kind),
    ),
  );
  // partial 行(每源一条暂定文本);空文本不显示。
  const partialEntries = Object.entries(partials).filter(([, text]) => text.trim());

  // 合并条目的圆点 kind(transcription 段 → transcription;透传 → 原事件 kind)。
  function itemKind(item: TimelineItem): string {
    return item.kind === "transcription" ? "transcription" : item.event.kind;
  }
  // 合并条目代表时间(段落取首句时间;透传取事件时间)。
  function itemTs(item: TimelineItem): string {
    return item.kind === "transcription" ? item.start_ts : item.event.ts;
  }

  function renderItem(item: TimelineItem): ReactNode {
    if (item.kind === "transcription")
      return (
        <span className="text-foreground">
          <span className="mr-1 rounded bg-teal-500/15 px-1 py-px text-[9px] font-medium text-teal-700 dark:text-teal-300">
            {item.source === "device" ? "系统声音采集" : "麦克风"}
          </span>
          {item.text || "(无内容)"}
        </span>
      );
    const ev = item.event;
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
      {/* 麦克风/系统声音:本 session 启用的源 = 可点的软静音开关(不重启 worker)。
          点亮(teal)= 正在采集(未静音);灰+斜杠图标 = 已软静音;未启用的源灰且不可点。 */}
      <AudioToggle
        enabled={sources.includes("mic")}
        muted={muted.includes("mic")}
        onLit={<Mic className="size-3.5" />}
        onMuted={<MicOff className="size-3.5" />}
        labelOn="麦克风采集中(点击静音)"
        labelMuted="麦克风已静音(点击恢复)"
        labelDisabled="本 session 未启用麦克风"
        onClick={() => void toggleMute("mic")}
      />
      <AudioToggle
        enabled={sources.includes("system_audio")}
        muted={muted.includes("system_audio")}
        onLit={<Volume2 className="size-3.5" />}
        onMuted={<VolumeX className="size-3.5" />}
        labelOn="系统声音采集中(点击静音)"
        labelMuted="系统声音已静音(点击恢复)"
        labelDisabled="本 session 未启用系统声音采集"
        onClick={() => void toggleMute("system_audio")}
      />
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
            {timeline.length === 0 && partialEntries.length === 0 ? (
              <p className="py-8 text-center text-xs text-muted-foreground">暂无采集内容</p>
            ) : (
              <div className="relative pl-1">
                <div className="absolute bottom-1 left-[5px] top-1 w-px bg-border" aria-hidden />
                <ul className="space-y-2.5">
                  {timeline.map((item) => {
                    const key =
                      item.kind === "transcription" ? `tr-${item.ids[0]}` : `ev-${item.event.id}`;
                    return (
                      <li key={key} className="relative flex gap-2 pl-4">
                        <span
                          className={`absolute left-[1px] top-1 size-2 rounded-full ring-2 ring-background ${dotColor(itemKind(item))}`}
                          aria-hidden
                        />
                        <div className="min-w-0 flex-1">
                          <div className="break-words text-xs leading-snug">{renderItem(item)}</div>
                          <div className="mt-0.5 text-[10px] tabular-nums text-muted-foreground">
                            {new Date(itemTs(item)).toLocaleTimeString([], {
                              hour: "2-digit",
                              minute: "2-digit",
                              second: "2-digit",
                            })}
                          </div>
                        </div>
                      </li>
                    );
                  })}
                  {/* 实时暂定段(partial):淡显、空心点,不落库——只是当前正在转写的尾段。 */}
                  {partialEntries.map(([source, text]) => (
                    <li key={`partial-${source}`} className="relative flex gap-2 pl-4">
                      <span
                        className="absolute left-[1px] top-1 size-2 animate-pulse rounded-full border border-teal-500/60 ring-2 ring-background"
                        aria-hidden
                      />
                      <div className="min-w-0 flex-1">
                        <div className="break-words text-xs leading-snug text-muted-foreground/80">
                          <span className="mr-1 rounded bg-teal-500/10 px-1 py-px text-[9px] font-medium text-teal-700/80 dark:text-teal-300/80">
                            {source === "device" ? "系统声音采集" : "麦克风"}
                          </span>
                          {text}
                        </div>
                        <div className="mt-0.5 text-[10px] text-muted-foreground/70">暂定</div>
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
