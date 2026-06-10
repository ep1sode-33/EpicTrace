import { FolderClosed, Plus } from "lucide-react";

import { type Project } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export function ProjectSidebar({
  projects,
  selectedId,
  onSelect,
  onCreate,
}: {
  projects: Project[];
  selectedId: number | null;
  onSelect: (project: Project) => void;
  onCreate: () => void;
}) {
  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-border/70 bg-sidebar">
      <div className="flex items-center justify-between px-4 pt-4 pb-2">
        <h2 className="text-sm font-semibold text-foreground">项目</h2>
        {projects.length > 0 && (
          <span className="min-w-5 rounded-full bg-muted px-1.5 text-center text-xs leading-5 font-medium tabular-nums text-muted-foreground">
            {projects.length}
          </span>
        )}
      </div>

      <nav className="flex-1 overflow-y-auto px-2 pb-2">
        {projects.length === 0 ? (
          <p className="px-2 py-6 text-xs leading-relaxed text-muted-foreground">
            还没有项目。点击下方按钮创建第一个。
          </p>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {projects.map((p) => {
              const active = p.id === selectedId;
              return (
                <li key={p.id}>
                  <button
                    type="button"
                    aria-current={active ? "true" : undefined}
                    onClick={() => onSelect(p)}
                    className={cn(
                      "group flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm outline-none transition-colors",
                      "focus-visible:ring-2 focus-visible:ring-ring/50",
                      active
                        ? "bg-background text-foreground shadow-sm ring-1 ring-border/70"
                        : "text-muted-foreground hover:bg-background/60 hover:text-foreground",
                    )}
                  >
                    <FolderClosed
                      className={cn(
                        "size-4 shrink-0 transition-colors",
                        active
                          ? "text-foreground"
                          : "text-muted-foreground group-hover:text-foreground",
                      )}
                      strokeWidth={active ? 2.25 : 2}
                    />
                    <span className="truncate font-medium">{p.title}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </nav>

      <div className="border-t border-border/70 p-2">
        <Button
          type="button"
          variant="ghost"
          size="lg"
          className="w-full justify-start text-muted-foreground hover:text-foreground"
          onClick={onCreate}
        >
          <Plus className="size-4" />
          新建项目
        </Button>
      </div>
    </aside>
  );
}
