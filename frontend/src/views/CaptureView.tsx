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
      <div className="flex w-full max-w-2xl flex-col items-center text-center">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-600/15 bg-amber-500/12 px-2.5 py-0.5 text-xs font-medium text-amber-700">
          开发中(Plan 4)
        </span>

        <h1 className="mt-5 text-2xl font-semibold tracking-tight text-foreground">
          采集 session
        </h1>
        <p className="mt-2.5 max-w-md text-sm leading-relaxed text-muted-foreground">
          开启一个 session,在工作过程中持续采集声音、截图、剪贴板、笔记与文件,
          稍后统一整理归类并入库。
        </p>

        <ul className="mt-9 grid w-full grid-cols-2 gap-3 text-left sm:grid-cols-3">
          {SOURCES.map(({ icon: Icon, label, desc }) => (
            <li
              key={label}
              className="flex flex-col gap-2 rounded-xl border border-border/70 bg-card px-3.5 py-3.5 ring-1 ring-foreground/[0.02]"
            >
              <span
                aria-hidden
                className="flex size-8 items-center justify-center rounded-lg bg-muted text-muted-foreground ring-1 ring-border/60"
              >
                <Icon className="size-4" strokeWidth={1.75} />
              </span>
              <div>
                <p className="text-sm font-medium text-foreground">{label}</p>
                <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                  {desc}
                </p>
              </div>
            </li>
          ))}
        </ul>

        <button
          type="button"
          disabled
          title="采集功能开发中(Plan 4)"
          className="mt-9 inline-flex h-10 cursor-not-allowed items-center justify-center gap-2 rounded-xl bg-primary px-5 text-sm font-medium text-primary-foreground opacity-50 select-none"
        >
          <Play className="size-4" />
          开始 session
        </button>
      </div>
    </div>
  );
}
