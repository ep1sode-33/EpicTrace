import { useState } from "react";
import {
  ChevronRight,
  FolderClosed,
  Loader2,
  MoreHorizontal,
  PenLine,
  Plus,
  RefreshCw,
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
 * - 平铺为默认态:行无常驻背景/边框;灰底+细环只在 hover / 键盘聚焦时出现。
 *   选中项目本身不留任何常驻背景——「我在哪」交给主区标题。
 * - 唯一的常驻底纹留给「当前打开的对话」(active),作为安静的位置指示。
 * - 缩进表达层级:对话相对项目左缩进,并有一条安静的引导竖线。
 * - 行内操作(新对话 +、… 菜单)平时隐形,hover / 聚焦 / 菜单打开时显现。
 */
export function ProjectSidebar({
  projects,
  selectedProjectId,
  selectedConversationId,
  draftProjectId,
  expandedIds,
  conversationsByProject,
  loadingProjectIds,
  reindexingIds,
  onSelectProject,
  onToggleExpand,
  onSelectConversation,
  onCreateConversation,
  onDeleteConversation,
  onCreateProject,
  onDeleteProject,
  onReindexProject,
}: {
  projects: Project[];
  selectedProjectId: number | null;
  selectedConversationId: number | null;
  /** 正在撰写草稿(尚未落库的新对话)的项目 id;用于在树内显示一个瞬态指示。 */
  draftProjectId: number | null;
  /** 当前展开的项目 id 集合。 */
  expandedIds: ReadonlySet<number>;
  /** 已加载的对话缓存:project id → 该项目的对话列表。 */
  conversationsByProject: Readonly<Record<number, Conversation[]>>;
  /** 正在懒加载对话的项目 id 集合。 */
  loadingProjectIds: ReadonlySet<number>;
  /** 正在「重建索引」(后台索引 job 运行中)的项目 id 集合;用于行内进度指示 + 菜单项禁用。 */
  reindexingIds: ReadonlySet<number>;
  /** 点项目名/行:选中并展开该项目。 */
  onSelectProject: (project: Project) => void;
  /** 点 chevron:仅切换展开/折叠(不改变选中)。 */
  onToggleExpand: (project: Project) => void;
  onSelectConversation: (conversation: Conversation) => void;
  /** 新建对话:开一段草稿(不调后端);首次发送时才落库。 */
  onCreateConversation: (project: Project) => void;
  /** 用户在某个对话行选择「删除」时调用,由父级打开确认对话框。 */
  onDeleteConversation: (conversation: Conversation) => void;
  onCreateProject: () => void;
  /** 用户在某个项目行选择「删除项目」时调用,由父级打开确认对话框。 */
  onDeleteProject: (project: Project) => void;
  /** 用户在某个项目行选择「重建索引」时调用,由父级确认 + 调后端 + 轮询进度。 */
  onReindexProject: (project: Project) => void;
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
                hasDraft={p.id === draftProjectId}
                conversations={conversationsByProject[p.id]}
                conversationsLoading={loadingProjectIds.has(p.id)}
                reindexing={reindexingIds.has(p.id)}
                selectedConversationId={selectedConversationId}
                onSelectProject={onSelectProject}
                onToggleExpand={onToggleExpand}
                onSelectConversation={onSelectConversation}
                onCreateConversation={onCreateConversation}
                onDeleteConversation={onDeleteConversation}
                onDeleteProject={onDeleteProject}
                onReindexProject={onReindexProject}
              />
            ))}
          </ul>
        )}
      </nav>
    </aside>
  );
}

/**
 * 项目行样式:始终平铺。无论是否选中都不留常驻背景——
 * 灰底 + 细环只在 hover / 键盘聚焦时出现(灰背景是「正悬停的目标」而非「当前选中项」)。
 */
const PROJECT_ROW_CLASS = cn(
  "w-full rounded-lg text-left text-sm text-muted-foreground outline-none transition-colors",
  "ring-1 ring-transparent",
  "hover:bg-background hover:text-foreground hover:ring-border/70",
  "focus-within:bg-background focus-within:text-foreground focus-within:ring-2 focus-within:ring-ring/50",
);

/**
 * 对话行样式:平时平铺、hover 显灰底+细环;
 * active(当前打开的对话)保留一层安静的常驻底纹,作为「我在哪」的唯一常驻指示。
 */
function conversationRowClass(active: boolean) {
  return cn(
    "w-full rounded-lg text-left text-sm outline-none transition-colors",
    "ring-1 ring-transparent",
    "hover:bg-background hover:text-foreground hover:ring-border/70",
    "focus-within:bg-background focus-within:text-foreground focus-within:ring-2 focus-within:ring-ring/50",
    active ? "bg-muted text-foreground" : "text-muted-foreground",
  );
}

function ProjectNode({
  project,
  selected,
  expanded,
  hasDraft,
  conversations,
  conversationsLoading,
  reindexing,
  selectedConversationId,
  onSelectProject,
  onToggleExpand,
  onSelectConversation,
  onCreateConversation,
  onDeleteConversation,
  onDeleteProject,
  onReindexProject,
}: {
  project: Project;
  selected: boolean;
  expanded: boolean;
  hasDraft: boolean;
  conversations: Conversation[] | undefined;
  conversationsLoading: boolean;
  reindexing: boolean;
  selectedConversationId: number | null;
  onSelectProject: (project: Project) => void;
  onToggleExpand: (project: Project) => void;
  onSelectConversation: (conversation: Conversation) => void;
  onCreateConversation: (project: Project) => void;
  onDeleteConversation: (conversation: Conversation) => void;
  onDeleteProject: (project: Project) => void;
  onReindexProject: (project: Project) => void;
}) {
  // 菜单打开时让行内操作保持可见(否则鼠标移开行后会随 hover 消失)。
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <li>
      {/* 项目行:[chevron] [文件夹图标] [名称] …(hover: 新对话 + 与 … 菜单) */}
      <div className={cn("group/row relative flex items-center", PROJECT_ROW_CLASS)}>
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
          <span
            className={cn(
              "truncate font-medium",
              selected && "text-foreground",
            )}
          >
            {project.title}
          </span>
        </button>

        {/* 重建索引进行态:常驻一个安静的旋转指示(不随 hover 隐现),
            让用户即便鼠标移开也知道该项目后台还在索引。 */}
        {reindexing && (
          <span
            className="absolute top-1/2 right-1.5 flex size-6 -translate-y-1/2 items-center justify-center text-muted-foreground"
            role="status"
            aria-live="polite"
            title="正在重建索引…"
          >
            <Loader2 className="size-4 animate-spin" />
            <span className="sr-only">正在重建索引</span>
          </span>
        )}

        {/* 行内操作:与主按钮并列(绝对定位、不嵌套)。默认隐形,hover/聚焦/菜单打开时显现。
            重建索引进行中时隐藏整组行内操作,给旋转指示让位,并避免重复触发。 */}
        <div
          className={cn(
            "absolute top-1/2 right-1.5 flex -translate-y-1/2 items-center gap-0.5 transition-opacity",
            reindexing
              ? "pointer-events-none opacity-0"
              : menuOpen
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
            <PenLine className="size-4" />
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
                disabled={reindexing}
                onSelect={() => onReindexProject(project)}
              >
                <RefreshCw />
                重建索引
              </DropdownMenuItem>
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
            conversations={conversations}
            loading={conversationsLoading}
            hasDraft={hasDraft}
            selectedConversationId={selectedConversationId}
            onSelectConversation={onSelectConversation}
            onDeleteConversation={onDeleteConversation}
          />
        </div>
      )}
    </li>
  );
}

/**
 * 展开后的对话子列表:加载骨架 / 空态「暂无对话」/ 对话行(每行带 hover 删除菜单)。
 * 顶部可能出现一个瞬态的「新对话」草稿指示(未落库,纯前端状态)。
 * 不再提供底部「+ 新对话」入口——新建对话只走项目行 hover 的 + 按钮。
 */
function ChatChildren({
  conversations,
  loading,
  hasDraft,
  selectedConversationId,
  onSelectConversation,
  onDeleteConversation,
}: {
  conversations: Conversation[] | undefined;
  loading: boolean;
  hasDraft: boolean;
  selectedConversationId: number | null;
  onSelectConversation: (conversation: Conversation) => void;
  onDeleteConversation: (conversation: Conversation) => void;
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
      {/* 瞬态草稿指示:正在为该项目撰写一段尚未落库的新对话。 */}
      {hasDraft && (
        <li
          className="flex items-center gap-1.5 rounded-lg bg-muted px-2.5 py-1.5 text-sm text-foreground"
          aria-current="true"
        >
          <PenLine className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate italic text-muted-foreground">新对话…</span>
        </li>
      )}

      {items.length === 0 && !hasDraft ? (
        <li className="px-2.5 py-1.5 text-xs text-muted-foreground">暂无对话</li>
      ) : (
        items.map((c) => (
          <ChatRow
            key={c.id}
            conversation={c}
            active={c.id === selectedConversationId}
            onSelect={onSelectConversation}
            onDelete={onDeleteConversation}
          />
        ))
      )}
    </ul>
  );
}

/** 单个对话行:主点击区选中;hover/聚焦/菜单打开时右侧显现 … 菜单(删除)。 */
function ChatRow({
  conversation,
  active,
  onSelect,
  onDelete,
}: {
  conversation: Conversation;
  active: boolean;
  onSelect: (conversation: Conversation) => void;
  onDelete: (conversation: Conversation) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <li>
      <div
        className={cn(
          "group/chat relative flex items-center",
          conversationRowClass(active),
        )}
      >
        <button
          type="button"
          aria-current={active ? "true" : undefined}
          onClick={() => onSelect(conversation)}
          className="flex min-w-0 flex-1 items-center px-2.5 py-1.5 pr-8 text-left outline-none"
        >
          <span className="truncate">{conversation.title}</span>
        </button>

        <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              aria-label={`对话「${conversation.title}」的操作`}
              className={cn(
                "absolute top-1/2 right-1 flex size-6 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground outline-none transition-[opacity,color,background-color]",
                "hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50",
                "aria-expanded:bg-muted aria-expanded:text-foreground",
                menuOpen
                  ? "opacity-100"
                  : "opacity-0 group-hover/chat:opacity-100 group-focus-within/chat:opacity-100 focus-visible:opacity-100",
              )}
            >
              <MoreHorizontal className="size-3.5" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" sideOffset={4}>
            <DropdownMenuItem
              variant="destructive"
              onSelect={() => onDelete(conversation)}
            >
              <Trash2 />
              删除
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </li>
  );
}
