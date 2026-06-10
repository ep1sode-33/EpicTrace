import {
  Camera,
  Clipboard,
  FileStack,
  Mic,
  Play,
  StickyNote,
  type LucideIcon,
} from "lucide-react";

type Source = { icon: LucideIcon; label: string; desc: string };

const SOURCES: Source[] = [
  { icon: Mic, label: "声音", desc: "录制会议或讲解的音频" },
  { icon: Camera, label: "截图", desc: "定时或手动捕获屏幕" },
  { icon: Clipboard, label: "剪贴板", desc: "记录复制的文本与链接" },
  { icon: StickyNote, label: "笔记", desc: "随手记录想法与要点" },
  { icon: FileStack, label: "文件", desc: "归集过程中产生的文件" },
];

export function CaptureView() {
  return (
    <div className="flex min-h-[calc(100vh-3.5rem)] flex-col items-center justify-center px-8 py-16">
      <div className="flex w-full max-w-md flex-col items-center text-center">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-600/25 bg-amber-500/15 px-2.5 py-0.5 text-xs font-medium text-amber-800 dark:border-amber-400/20 dark:bg-amber-400/10 dark:text-amber-300">
          <span
            aria-hidden
            className="size-1.5 rounded-full bg-amber-500 dark:bg-amber-400"
          />
          开发中 · Plan 4
        </span>

        <h1 className="mt-5 text-2xl font-semibold tracking-tight text-foreground">
          采集 session
        </h1>
        <p className="mt-2.5 text-sm leading-relaxed text-balance text-muted-foreground">
          开启一个 session,在工作过程中持续采集以下来源,稍后统一整理归类并入库。
        </p>

        {/* 来源预览:一段被动的「将会采集什么」清单,而非可点的功能卡片。 */}
        <ul className="mt-8 w-full divide-y divide-border/60 overflow-hidden rounded-xl border border-border/70 bg-card text-left">
          {SOURCES.map(({ icon: Icon, label, desc }) => (
            <li key={label} className="flex items-center gap-3 px-4 py-2.5">
              <Icon
                aria-hidden
                className="size-4 shrink-0 text-muted-foreground"
                strokeWidth={1.75}
              />
              <span className="w-12 shrink-0 text-sm font-medium text-foreground">
                {label}
              </span>
              <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                {desc}
              </span>
            </li>
          ))}
        </ul>

        <button
          type="button"
          disabled
          aria-disabled
          title="采集功能开发中(Plan 4)"
          className="mt-8 inline-flex h-9 cursor-not-allowed items-center justify-center gap-1.5 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground opacity-50 select-none"
        >
          <Play className="size-4" />
          开始 session
        </button>
        <p className="mt-2.5 text-xs text-muted-foreground">
          采集功能开发完成后将在此开启。
        </p>
      </div>
    </div>
  );
}
