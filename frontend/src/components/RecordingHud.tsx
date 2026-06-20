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
    case "source":
      return "bg-orange-500";
    default:
      return "bg-muted-foreground";
  }
}

/** 音源开/停事件的标签:meta.source(mic/system_audio)+ meta.action(start/stop)。 */
function sourceEventLabel(meta?: Record<string, unknown>): { text: string; stop: boolean } {
  const src = meta?.source === "system_audio" ? "系统声音" : "麦克风";
  const stop = meta?.action === "stop";
  return { text: `${src} ${stop ? "停止采集" : "开始采集"}`, stop };
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
 * 音频源开关(HUD 专用)。两态,均可随时点击(中途也能开开始没勾的源):
 * - 开启:点亮(teal)+ 实心图标 = 正在采集;点击关闭(停采集,mic 灯灭)。
 * - 关闭:灰 + 斜杠图标(MicOff/VolumeX);点击开启(开始采集)。
 */
function AudioToggle({
  on,
  onIcon,
  offIcon,
  labelOn,
  labelOff,
  onClick,
}: {
  on: boolean;
  onIcon: ReactNode;
  offIcon: ReactNode;
  labelOn: string;
  labelOff: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={on ? labelOn : labelOff}
      className={`flex size-7 shrink-0 items-center justify-center rounded-md transition-colors ${
        on
          ? "text-teal-500 hover:bg-teal-500/10"
          : "text-muted-foreground/60 hover:bg-muted hover:text-foreground"
      }`}
    >
      {on ? onIcon : offIcon}
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
  // 期望开启的音频源 id 集:服务端权威态,挂载 + 每次轮询时刷新;点击乐观切换。
  // 开关随时可点——开启会(必要时懒启动 worker 并)开始采集,中途也能开开始没勾的源。
  const [enabled, setEnabled] = useState<string[]>([]);

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
    // 挂载即拉一次期望开启集(失败/无 ASR 时静默,留空 = 全未开启)。
    api
      .getAsrSources(sessionId)
      .then((m) => setEnabled(m.enabled))
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
      // 同频刷新期望开启集(服务端权威态;反映 worker 自退/别处改动)。
      api
        .getAsrSources(sessionId)
        .then((m) => setEnabled(m.enabled))
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
    stopTimer();
    stopPolling();
    // 已暂停时 monitors 早已停(handlePauseResume),再 stopMonitors 会抛 → 必须与 stopSession 分开
    // try,否则异常吞掉后 stopSession 永不执行,「暂停中点结束」就没反应(必须先继续再结束的 bug)。
    try {
      await native.stopMonitors();
    } catch {
      /* 已停 / 无 monitors → 忽略 */
    }
    try {
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

  // 启停某路音频源(乐观更新 + 失败回滚)。开关随时可点——中途也能开开始没勾的源。
  // 启用时模型未就绪后端会 409 → 回滚(开关弹回关闭态作为反馈)。
  async function toggleSource(source: string) {
    const willEnable = !enabled.includes(source);
    const prev = enabled;
    setEnabled(willEnable ? [...enabled, source] : enabled.filter((s) => s !== source));
    try {
      await api.setAsrSource(sessionId, source, willEnable);
    } catch {
      setEnabled(prev); // 回滚
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
      ["note", "clipboard", "screenshot", "transcription", "pause", "resume", "source"].includes(e.kind),
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
    if (ev.kind === "source") {
      const { text, stop } = sourceEventLabel(ev.meta);
      return <span className={stop ? "text-rose-600 dark:text-rose-400" : "text-emerald-600 dark:text-emerald-400"}>{text}</span>;
    }
    // 剪贴板可能很长 → 单行截断(title 存全文),避免霸占实时时间线。
    if (ev.kind === "clipboard")
      return <span className="text-foreground line-clamp-1 break-all" title={ev.payload}>{ev.payload || "(空)"}</span>;
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
      {/* 麦克风/系统声音:随时可点的启停开关。点亮(teal)= 正在采集;灰+斜杠 = 已关闭。
          中途也能开开始没勾的源(必要时后端懒启动 worker);全关一段时间 worker 自退省内存。 */}
      <AudioToggle
        on={enabled.includes("mic")}
        onIcon={<Mic className="size-3.5" />}
        offIcon={<MicOff className="size-3.5" />}
        labelOn="麦克风采集中(点击关闭)"
        labelOff="麦克风已关闭(点击开启)"
        onClick={() => void toggleSource("mic")}
      />
      <AudioToggle
        on={enabled.includes("system_audio")}
        onIcon={<Volume2 className="size-3.5" />}
        offIcon={<VolumeX className="size-3.5" />}
        labelOn="系统声音采集中(点击关闭)"
        labelOff="系统声音已关闭(点击开启)"
        onClick={() => void toggleSource("system_audio")}
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
