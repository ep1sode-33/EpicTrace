import { Fragment, useRef, useState } from "react";
import {
  ChevronRight,
  FolderClosed,
  MoreHorizontal,
  Pencil,
  PenLine,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";

import { type CoworkSession, type Project } from "@/lib/api";
import { sessionTitle } from "@/lib/coworkMeta";
import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

/**
 * 行内重命名输入框:受控、挂载即聚焦并全选;Enter / 失焦提交,Esc 取消。
 * 是否真正发请求(空 / 未变 → 不发)由父级回调决定;本组件只负责编辑交互。
 */
function InlineRename({
  initial,
  onSubmit,
  onCancel,
  className,
}: {
  initial: string;
  onSubmit: (value: string) => void;
  onCancel: () => void;
  className?: string;
}) {
  const [value, setValue] = useState(initial);
  // 用 ref 防止「失焦提交」与「Enter/Esc 已结束编辑」重复触发。
  const doneRef = useRef(false);
  const submit = () => {
    if (doneRef.current) return;
    doneRef.current = true;
    onSubmit(value);
  };
  const cancel = () => {
    if (doneRef.current) return;
    doneRef.current = true;
    onCancel();
  };
  return (
    <input
      autoFocus
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onFocus={(e) => e.currentTarget.select()}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          submit();
        } else if (e.key === "Escape") {
          e.preventDefault();
          cancel();
        }
      }}
      onBlur={submit}
      onClick={(e) => e.stopPropagation()}
      className={cn(
        "min-w-0 flex-1 rounded-md border border-ring/60 bg-background px-1.5 py-0.5 text-sm text-foreground outline-none",
        className,
      )}
    />
  );
}

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
  expandedIds,
  conversationsByProject,
  loadingProjectIds,
  onSelectProject,
  onToggleExpand,
  onSelectConversation,
  onCreateConversation,
  onDeleteConversation,
  onCreateProject,
  onDeleteProject,
  onReindexProject,
  onRenameProject,
  onRenameConversation,
}: {
  projects: Project[];
  selectedProjectId: number | null;
  selectedConversationId: number | null;
  /** 当前展开的项目 id 集合。 */
  expandedIds: ReadonlySet<number>;
  /** 已加载的对话缓存:project id → 该项目的 cowork 会话列表。 */
  conversationsByProject: Readonly<Record<number, CoworkSession[]>>;
  /** 正在懒加载对话的项目 id 集合。 */
  loadingProjectIds: ReadonlySet<number>;
  /** 点项目名/行:选中并展开该项目。 */
  onSelectProject: (project: Project) => void;
  /** 点 chevron:仅切换展开/折叠(不改变选中)。 */
  onToggleExpand: (project: Project) => void;
  onSelectConversation: (conversation: CoworkSession) => void;
  /** 新建对话:立即创建 cowork 会话(创建即落库,首轮自动标题)。 */
  onCreateConversation: (project: Project) => void;
  /** 用户在某个对话行选择「删除」时调用,由父级打开确认对话框。 */
  onDeleteConversation: (conversation: CoworkSession) => void;
  onCreateProject: () => void;
  /** 用户在某个项目行选择「删除项目」时调用,由父级打开确认对话框。 */
  onDeleteProject: (project: Project) => void;
  /** 用户在某个项目行选择「重建索引」时调用:父级确认 + 触发重建,并跳到入库页看进度。 */
  onReindexProject: (project: Project) => void;
  /** 重命名项目(仅显示名):行内编辑提交时调用。 */
  onRenameProject: (project: Project, title: string) => void;
  /** 重命名对话:行内编辑提交时调用。 */
  onRenameConversation: (conversation: CoworkSession, title: string) => void;
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
                onDeleteConversation={onDeleteConversation}
                onDeleteProject={onDeleteProject}
                onReindexProject={onReindexProject}
                onRenameProject={onRenameProject}
                onRenameConversation={onRenameConversation}
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
  conversations,
  conversationsLoading,
  selectedConversationId,
  onSelectProject,
  onToggleExpand,
  onSelectConversation,
  onCreateConversation,
  onDeleteConversation,
  onDeleteProject,
  onReindexProject,
  onRenameProject,
  onRenameConversation,
}: {
  project: Project;
  selected: boolean;
  expanded: boolean;
  conversations: CoworkSession[] | undefined;
  conversationsLoading: boolean;
  selectedConversationId: number | null;
  onSelectProject: (project: Project) => void;
  onToggleExpand: (project: Project) => void;
  onSelectConversation: (conversation: CoworkSession) => void;
  onCreateConversation: (project: Project) => void;
  onDeleteConversation: (conversation: CoworkSession) => void;
  onDeleteProject: (project: Project) => void;
  onReindexProject: (project: Project) => void;
  onRenameProject: (project: Project, title: string) => void;
  onRenameConversation: (conversation: CoworkSession, title: string) => void;
}) {
  // 菜单打开时让行内操作保持可见(否则鼠标移开行后会随 hover 消失)。
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);

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
        {editing ? (
          <div className="flex min-w-0 flex-1 items-center gap-2 py-2 pr-16">
            <FolderClosed className="size-4 shrink-0 text-foreground" strokeWidth={2.25} />
            <InlineRename
              initial={project.title}
              onSubmit={(v) => {
                setEditing(false);
                onRenameProject(project, v);
              }}
              onCancel={() => setEditing(false)}
            />
          </div>
        ) : (
          <button
            type="button"
            aria-current={selected ? "true" : undefined}
            onClick={() => onSelectProject(project)}
            onDoubleClick={() => setEditing(true)}
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
        )}

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
              <DropdownMenuItem onSelect={() => setEditing(true)}>
                <Pencil />
                重命名
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => onReindexProject(project)}>
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
            selectedConversationId={selectedConversationId}
            onSelectConversation={onSelectConversation}
            onDeleteConversation={onDeleteConversation}
            onRenameConversation={onRenameConversation}
          />
        </div>
      )}
    </li>
  );
}

/**
 * 展开后的对话子列表:加载骨架 / 空态「暂无对话」/ 对话行(每行带 hover 删除菜单)。
 * 不再提供底部「+ 新对话」入口——新建对话只走项目行 hover 的 + 按钮。
 */
function ChatChildren({
  conversations,
  loading,
  selectedConversationId,
  onSelectConversation,
  onDeleteConversation,
  onRenameConversation,
}: {
  conversations: CoworkSession[] | undefined;
  loading: boolean;
  selectedConversationId: number | null;
  onSelectConversation: (conversation: CoworkSession) => void;
  onDeleteConversation: (conversation: CoworkSession) => void;
  onRenameConversation: (conversation: CoworkSession, title: string) => void;
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
  // 子 agent(dispatch_child)嵌在父会话行下,父行显示「子任务 N/M」(需求:进度在项目树可见)。
  const tops = items.filter((c) => c.parent_id == null);
  const childrenOf = new Map<number, CoworkSession[]>();
  for (const c of items) {
    if (c.parent_id != null) {
      const arr = childrenOf.get(c.parent_id) ?? [];
      arr.push(c);
      childrenOf.set(c.parent_id, arr);
    }
  }

  return (
    <ul className="flex flex-col gap-0.5">
      {tops.length === 0 ? (
        <li className="px-2.5 py-1.5 text-xs text-muted-foreground">暂无对话</li>
      ) : (
        tops.map((c) => {
          const kids = childrenOf.get(c.id) ?? [];
          const kidsDone = kids.filter(
            (k) => k.status === "done" || k.status === "error",
          ).length;
          return (
            <Fragment key={c.id}>
              <ChatRow
                conversation={c}
                active={c.id === selectedConversationId}
                progress={kids.length > 0 ? `${kidsDone}/${kids.length}` : null}
                onSelect={onSelectConversation}
                onDelete={onDeleteConversation}
                onRename={onRenameConversation}
              />
              {kids.map((k) => (
                <ChatRow
                  key={k.id}
                  conversation={k}
                  child
                  active={k.id === selectedConversationId}
                  onSelect={onSelectConversation}
                  onDelete={onDeleteConversation}
                  onRename={onRenameConversation}
                />
              ))}
            </Fragment>
          );
        })
      )}
    </ul>
  );
}

/** 会话状态点:运行中琥珀脉冲 / 待确认琥珀 / 错误红;其余状态不占位(保持树行干净)。 */
function SessionStatusDot({ status }: { status: string }) {
  if (status === "thinking" || status === "executing") {
    return (
      <span className="relative mr-1.5 flex size-1.5 shrink-0" title="运行中">
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-amber-500/60" />
        <span className="relative inline-flex size-1.5 rounded-full bg-amber-500" />
      </span>
    );
  }
  if (status === "waiting_approval") {
    return (
      <span
        className="mr-1.5 inline-flex size-1.5 shrink-0 rounded-full bg-amber-600"
        title="待确认"
      />
    );
  }
  if (status === "error") {
    return (
      <span
        className="mr-1.5 inline-flex size-1.5 shrink-0 rounded-full bg-destructive"
        title="错误"
      />
    );
  }
  return null;
}

/** 单个对话行:主点击区选中;hover/聚焦/菜单打开时右侧显现 … 菜单(删除)。
 * child=true 渲染为子 agent 行(缩进 + 更弱字号);progress 非空时标题旁显示子任务进度。 */
function ChatRow({
  conversation,
  active,
  child = false,
  progress = null,
  onSelect,
  onDelete,
  onRename,
}: {
  conversation: CoworkSession;
  active: boolean;
  child?: boolean;
  /** 子任务进度文本(如 "2/3");仅父会话行传。 */
  progress?: string | null;
  onSelect: (conversation: CoworkSession) => void;
  onDelete: (conversation: CoworkSession) => void;
  onRename: (conversation: CoworkSession, title: string) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const title = sessionTitle(conversation);

  return (
    <li>
      <div
        className={cn(
          "group/chat relative flex items-center",
          conversationRowClass(active),
        )}
      >
        {editing ? (
          <div className="flex min-w-0 flex-1 items-center px-2.5 py-1.5 pr-8">
            <InlineRename
              initial={title}
              onSubmit={(v) => {
                setEditing(false);
                onRename(conversation, v);
              }}
              onCancel={() => setEditing(false)}
            />
          </div>
        ) : (
          <button
            type="button"
            aria-current={active ? "true" : undefined}
            onClick={() => onSelect(conversation)}
            onDoubleClick={() => setEditing(true)}
            className={cn(
              "flex min-w-0 flex-1 items-center px-2.5 py-1.5 pr-8 text-left outline-none",
              child && "pl-6",
            )}
          >
            {child && <span className="mr-1 shrink-0 text-muted-foreground/60">└</span>}
            <SessionStatusDot status={conversation.status} />
            <span className={cn("truncate", child && "text-muted-foreground")}>{title}</span>
            {progress && (
              <span className="ml-1.5 shrink-0 text-[10px] text-muted-foreground/70">
                子任务 {progress}
              </span>
            )}
          </button>
        )}

        <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              aria-label={`对话「${title}」的操作`}
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
            <DropdownMenuItem onSelect={() => setEditing(true)}>
              <Pencil />
              重命名
            </DropdownMenuItem>
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
