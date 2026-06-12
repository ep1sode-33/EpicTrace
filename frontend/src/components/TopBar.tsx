import { Radio, Inbox, MessagesSquare, Settings, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export type TabKey = "capture" | "process" | "projects";

type TabDef = { key: TabKey; label: string; icon: LucideIcon };

const TABS: TabDef[] = [
  { key: "capture", label: "采集", icon: Radio },
  { key: "process", label: "信息处理和入库", icon: Inbox },
  { key: "projects", label: "项目与对话", icon: MessagesSquare },
];

export function TopBar({
  active,
  onChange,
  onOpenSettings,
  inSettings = false,
}: {
  active: TabKey;
  onChange: (tab: TabKey) => void;
  /** 点齿轮进入「模型配置」整页设置。 */
  onOpenSettings: () => void;
  /** 处于设置页时高亮齿轮(镜像活动 Tab 的视觉)。 */
  inSettings?: boolean;
}) {
  return (
    <header className="sticky top-0 z-30 border-b border-border/70 bg-background/85 backdrop-blur-xl supports-[backdrop-filter]:bg-background/70">
      <div className="relative flex h-14 items-center px-4">
        {/* Wordmark */}
        <div className="flex min-w-0 items-center gap-2.5 pr-4">
          <span
            aria-hidden
            className="flex size-6 items-center justify-center rounded-md bg-foreground text-[11px] font-semibold text-background shadow-sm"
          >
            E
          </span>
          <span className="truncate text-sm font-semibold tracking-tight text-foreground">
            EpicTrace
          </span>
        </div>

        {/* Centered segmented tabs */}
        <nav
          aria-label="主导航"
          className="absolute left-1/2 -translate-x-1/2"
        >
          <div className="flex items-center gap-1 rounded-xl border border-border/70 bg-muted/60 p-1">
            {TABS.map(({ key, label, icon: Icon }) => {
              const isActive = key === active;
              return (
                <button
                  key={key}
                  type="button"
                  aria-current={isActive ? "page" : undefined}
                  onClick={() => onChange(key)}
                  className={cn(
                    "group relative flex items-center gap-2 rounded-lg px-3.5 py-1.5 text-sm font-medium",
                    "outline-none transition-[color,background-color,box-shadow] duration-200 ease-out",
                    "focus-visible:ring-2 focus-visible:ring-ring/50",
                    isActive
                      ? "bg-background text-foreground shadow-sm ring-1 ring-foreground/[0.06]"
                      : "text-muted-foreground hover:bg-background/50 hover:text-foreground",
                  )}
                >
                  <Icon
                    className={cn(
                      "size-4 shrink-0 transition-colors duration-200",
                      isActive
                        ? "text-foreground"
                        : "text-muted-foreground group-hover:text-foreground",
                    )}
                    strokeWidth={isActive ? 2.25 : 2}
                  />
                  <span className="whitespace-nowrap">{label}</span>
                </button>
              );
            })}
          </div>
        </nav>

        {/* Right: settings gear (also keeps the segmented group optically centered) */}
        <div className="ml-auto flex items-center">
          <button
            type="button"
            onClick={onOpenSettings}
            aria-label="设置"
            aria-pressed={inSettings}
            title="设置"
            className={cn(
              "flex size-8 items-center justify-center rounded-lg outline-none transition-colors",
              "focus-visible:ring-2 focus-visible:ring-ring/50",
              inSettings
                ? "bg-muted text-foreground ring-1 ring-foreground/[0.06]"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            <Settings className="size-[18px]" strokeWidth={inSettings ? 2.25 : 2} />
          </button>
        </div>
      </div>
    </header>
  );
}
