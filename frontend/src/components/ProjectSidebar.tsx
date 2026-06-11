import { useState } from "react";
import { FolderClosed, MoreHorizontal, Plus, Trash2 } from "lucide-react";

import { type Project } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function ProjectSidebar({
  projects,
  selectedId,
  onSelect,
  onCreate,
  onDelete,
}: {
  projects: Project[];
  selectedId: number | null;
  onSelect: (project: Project) => void;
  onCreate: () => void;
  /** 用户在某个项目行选择「删除项目」时调用,由父级打开确认对话框。 */
  onDelete: (project: Project) => void;
}) {
  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-border/70 bg-sidebar">
      <div className="flex items-center justify-between px-4 pt-4 pb-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-foreground">项目</h2>
          {projects.length > 0 && (
            <span className="min-w-5 rounded-full bg-muted px-1.5 text-center text-xs leading-5 font-medium tabular-nums text-muted-foreground">
              {projects.length}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onCreate}
          aria-label="新建项目"
          title="新建项目"
          className="flex size-7 items-center justify-center rounded-md text-muted-foreground outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <Plus className="size-4" />
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 pb-2">
        {projects.length === 0 ? (
          <p className="px-2 py-6 text-xs leading-relaxed text-muted-foreground">
            还没有项目。点右上角的 + 创建第一个。
          </p>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {projects.map((p) => (
              <ProjectRow
                key={p.id}
                project={p}
                active={p.id === selectedId}
                onSelect={onSelect}
                onDelete={onDelete}
              />
            ))}
          </ul>
        )}
      </nav>
    </aside>
  );
}

function ProjectRow({
  project,
  active,
  onSelect,
  onDelete,
}: {
  project: Project;
  active: boolean;
  onSelect: (project: Project) => void;
  onDelete: (project: Project) => void;
}) {
  // 菜单打开时让「…」按钮保持可见(否则鼠标移开行后会随 hover 消失)。
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <li className="group/row relative">
      {/* 选择项目的主点击区。右侧留出空间避开「…」触发器,避免按钮套按钮。 */}
      <button
        type="button"
        aria-current={active ? "true" : undefined}
        onClick={() => onSelect(project)}
        className={cn(
          "flex w-full items-center gap-2.5 rounded-lg py-2 pr-9 pl-2.5 text-left text-sm outline-none transition-colors",
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
              : "text-muted-foreground group-hover/row:text-foreground",
          )}
          strokeWidth={active ? 2.25 : 2}
        />
        <span className="truncate font-medium">{project.title}</span>
      </button>

      {/* 行内操作:与主按钮并列(绝对定位、不嵌套)。默认隐形,
          悬停 / 选中 / 菜单打开时显现。 */}
      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label={`项目「${project.title}」的操作`}
            className={cn(
              "absolute top-1/2 right-1.5 flex size-6 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground outline-none transition-all",
              "hover:bg-muted hover:text-foreground focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-ring/50",
              "aria-expanded:bg-muted aria-expanded:text-foreground",
              menuOpen
                ? "opacity-100"
                : "opacity-0 group-hover/row:opacity-100 group-focus-within/row:opacity-100",
            )}
          >
            <MoreHorizontal className="size-4" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" sideOffset={4}>
          <DropdownMenuItem
            variant="destructive"
            onSelect={() => onDelete(project)}
          >
            <Trash2 />
            删除项目
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </li>
  );
}
