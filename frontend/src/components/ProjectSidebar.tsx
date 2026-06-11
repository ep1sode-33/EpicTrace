import { useState } from "react";
import {
  ChevronRight,
  FolderClosed,
  Loader2,
  MessageSquarePlus,
  MoreHorizontal,
  Plus,
  Trash2,
} from "lucide-react";

import { type Conversation, type Project } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

/**
 * 统一的「项目 → 对话」树(Codex/ChatGPT 式)。
 * 一行一个项目;展开后其对话缩进列在下方,折叠则隐藏。
 *
 * 视觉约定(贯穿全树):
 * - 平时平铺:无常驻边框/卡片;边框只在 hover / 键盘聚焦时出现。
 * - 选中:仅以淡色背景表示(不画边框——边框留给 hover)。
 * - 缩进表达层级:对话相对项目左缩进,并有一条安静的引导竖线。
 * - 行内操作(新对话 +、… 菜单)平时隐形,hover / 聚焦 / 菜单打开时显现。
 */
export function ProjectSidebar({
  projects,
  selectedProjectId,
  selectedConversationId,
  expandedIds,
  conversationsByProject,
  loadingProjectIds,
  onSelectProject,
  onToggleExpand,
  onSelectConversation,
  onCreateConversation,
  onCreateProject,
  onDeleteProject,
}: {
  projects: Project[];
  selectedProjectId: number | null;
  selectedConversationId: number | null;
  /** 当前展开的项目 id 集合。 */
  expandedIds: ReadonlySet<number>;
  /** 已加载的对话缓存:project id → 该项目的对话列表。 */
  conversationsByProject: Readonly<Record<number, Conversation[]>>;
  /** 正在懒加载对话的项目 id 集合。 */
  loadingProjectIds: ReadonlySet<number>;
  /** 点项目名/行:选中并展开该项目。 */
  onSelectProject: (project: Project) => void;
  /** 点 chevron:仅切换展开/折叠(不改变选中)。 */
  onToggleExpand: (project: Project) => void;
  onSelectConversation: (conversation: Conversation) => void;
  onCreateConversation: (project: Project) => void;
  onCreateProject: () => void;
  /** 用户在某个项目行选择「删除项目」时调用,由父级打开确认对话框。 */
  onDeleteProject: (project: Project) => void;
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
          onClick={onCreateProject}
          aria-label="新建项目"
          title="新建项目"
          className="flex size-7 items-center justify-center rounded-md text-muted-foreground outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <Plus className="size-4" />
        </button>
      </div>

      <nav
        aria-label="项目与对话"
        className="min-h-0 flex-1 overflow-y-auto px-2 pb-3"
      >
        {projects.length === 0 ? (
          <p className="px-2 py-6 text-xs leading-relaxed text-muted-foreground">
            还没有项目。点右上角的 + 创建第一个。
          </p>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {projects.map((p) => (
              <ProjectNode
                key={p.id}
                project={p}
                selected={p.id === selectedProjectId}
                expanded={expandedIds.has(p.id)}
                conversations={conversationsByProject[p.id]}
                conversationsLoading={loadingProjectIds.has(p.id)}
                selectedConversationId={selectedConversationId}
                onSelectProject={onSelectProject}
                onToggleExpand={onToggleExpand}
                onSelectConversation={onSelectConversation}
                onCreateConversation={onCreateConversation}
                onDeleteProject={onDeleteProject}
              />
            ))}
          </ul>
        )}
      </nav>
    </aside>
  );
}

/** 平铺 → hover 才显边框;选中只用淡背景的共享行样式。 */
function rowClass(selected: boolean) {
  return cn(
    "w-full rounded-lg text-left text-sm outline-none transition-colors",
    "ring-1 ring-transparent hover:bg-background hover:text-foreground hover:ring-border/70",
    "focus-visible:bg-background focus-visible:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50",
    selected ? "bg-muted text-foreground" : "text-muted-foreground",
  );
}

function ProjectNode({
  project,
  selected,
  expanded,
  conversations,
  conversationsLoading,
  selectedConversationId,
  onSelectProject,
  onToggleExpand,
  onSelectConversation,
  onCreateConversation,
  onDeleteProject,
}: {
  project: Project;
  selected: boolean;
  expanded: boolean;
  conversations: Conversation[] | undefined;
  conversationsLoading: boolean;
  selectedConversationId: number | null;
  onSelectProject: (project: Project) => void;
  onToggleExpand: (project: Project) => void;
  onSelectConversation: (conversation: Conversation) => void;
  onCreateConversation: (project: Project) => void;
  onDeleteProject: (project: Project) => void;
}) {
  // 菜单打开时让行内操作保持可见(否则鼠标移开行后会随 hover 消失)。
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <li>
      {/* 项目行:[chevron] [文件夹图标] [名称] …(hover: 新对话 + 与 … 菜单) */}
      <div className={cn("group/row relative flex items-center", rowClass(selected))}>
        {/* chevron:独立切换展开/折叠,不改变选中。 */}
        <button
          type="button"
          onClick={() => onToggleExpand(project)}
          aria-label={expanded ? "折叠" : "展开"}
          aria-expanded={expanded}
          className="flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <ChevronRight
            className={cn(
              "size-4 transition-transform duration-200",
              expanded && "rotate-90",
            )}
            strokeWidth={2}
          />
        </button>

        {/* 主点击区:选中并展开该项目。右侧留出空间给行内操作,避免按钮套按钮。 */}
        <button
          type="button"
          aria-current={selected ? "true" : undefined}
          onClick={() => onSelectProject(project)}
          className="flex min-w-0 flex-1 items-center gap-2 py-2 pr-16 text-left outline-none"
        >
          <FolderClosed
            className={cn(
              "size-4 shrink-0 transition-colors",
              selected
                ? "text-foreground"
                : "text-muted-foreground group-hover/row:text-foreground",
            )}
            strokeWidth={selected ? 2.25 : 2}
          />
          <span className="truncate font-medium">{project.title}</span>
        </button>

        {/* 行内操作:与主按钮并列(绝对定位、不嵌套)。默认隐形,hover/聚焦/菜单打开时显现。 */}
        <div
          className={cn(
            "absolute top-1/2 right-1.5 flex -translate-y-1/2 items-center gap-0.5 transition-opacity",
            menuOpen
              ? "opacity-100"
              : "opacity-0 group-hover/row:opacity-100 group-focus-within/row:opacity-100",
          )}
        >
          <button
            type="button"
            onClick={() => onCreateConversation(project)}
            aria-label={`在「${project.title}」新建对话`}
            title="新对话"
            className="flex size-6 items-center justify-center rounded-md text-muted-foreground outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-ring/50"
          >
            <MessageSquarePlus className="size-4" />
          </button>

          <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                aria-label={`项目「${project.title}」的操作`}
                className={cn(
                  "flex size-6 items-center justify-center rounded-md text-muted-foreground outline-none transition-colors",
                  "hover:bg-muted hover:text-foreground focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-ring/50",
                  "aria-expanded:bg-muted aria-expanded:text-foreground",
                )}
              >
                <MoreHorizontal className="size-4" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" sideOffset={4}>
              <DropdownMenuItem
                variant="destructive"
                onSelect={() => onDeleteProject(project)}
              >
                <Trash2 />
                删除项目
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {/* 展开:对话缩进列在项目下方,带一条安静的引导竖线。 */}
      {expanded && (
        <div className="mt-0.5 mb-1 ml-[1.4375rem] border-l border-border/60 pl-1.5">
          <ChatChildren
            project={project}
            conversations={conversations}
            loading={conversationsLoading}
            selectedConversationId={selectedConversationId}
            onSelectConversation={onSelectConversation}
            onCreateConversation={onCreateConversation}
          />
        </div>
      )}
    </li>
  );
}

/** 展开后的对话子列表:加载骨架 / 空态「暂无对话」/ 对话行 + 「新对话」入口。 */
function ChatChildren({
  project,
  conversations,
  loading,
  selectedConversationId,
  onSelectConversation,
  onCreateConversation,
}: {
  project: Project;
  conversations: Conversation[] | undefined;
  loading: boolean;
  selectedConversationId: number | null;
  onSelectConversation: (conversation: Conversation) => void;
  onCreateConversation: (project: Project) => void;
}) {
  // 首次展开尚未拉到数据(且在加载):骨架。
  if (loading && conversations === undefined) {
    return (
      <ul className="flex flex-col gap-0.5 py-0.5" aria-hidden>
        {[0, 1].map((i) => (
          <li key={i} className="px-2.5 py-1.5">
            <span className="block h-3 w-2/3 animate-pulse rounded bg-muted" />
          </li>
        ))}
      </ul>
    );
  }

  const items = conversations ?? [];

  return (
    <ul className="flex flex-col gap-0.5">
      {items.length === 0 ? (
        <li className="px-2.5 py-1.5 text-xs text-muted-foreground">暂无对话</li>
      ) : (
        items.map((c) => {
          const active = c.id === selectedConversationId;
          return (
            <li key={c.id}>
              <button
                type="button"
                aria-current={active ? "true" : undefined}
                onClick={() => onSelectConversation(c)}
                className={cn(
                  "flex w-full items-center px-2.5 py-1.5",
                  rowClass(active),
                )}
              >
                <span className="truncate">{c.title}</span>
              </button>
            </li>
          );
        })
      )}

      {/* 每项目一个「+ 新对话」入口(行内 + 也可)。 */}
      <li>
        <button
          type="button"
          onClick={() => onCreateConversation(project)}
          className="flex w-full items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-left text-xs text-muted-foreground outline-none transition-colors ring-1 ring-transparent hover:bg-background hover:text-foreground hover:ring-border/70 focus-visible:bg-background focus-visible:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          {loading && conversations !== undefined ? (
            <Loader2 className="size-3.5 shrink-0 animate-spin" />
          ) : (
            <Plus className="size-3.5 shrink-0" />
          )}
          新对话
        </button>
      </li>
    </ul>
  );
}
