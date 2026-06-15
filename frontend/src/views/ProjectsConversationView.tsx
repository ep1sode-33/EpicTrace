import { useCallback, useEffect, useRef, useState } from "react";
import {
  FolderGit2,
  FolderPlus,
  Loader2,
  PanelRight,
  Sparkles,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";

import {
  api,
  type Citation,
  type Conversation,
  type ConversationReference,
  type Project,
  type StreamHandlers,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { CreateProjectModal } from "@/components/CreateProjectModal";
import { DeleteProjectDialog } from "@/components/DeleteProjectDialog";
import { ProjectSidebar } from "@/components/ProjectSidebar";
import { Composer } from "@/components/Composer";
import { MessageList, type ViewMessage } from "@/components/MessageList";
import { ReferencePanel } from "@/components/ReferencePanel";
import { ProjectFilesZone } from "@/components/ProjectFilesZone";
import { SourceViewer } from "@/components/SourceViewer";

export function ProjectsConversationView({
  llmConfigured,
  onOpenSettings,
  onReindexStarted,
}: {
  llmConfigured: boolean;
  onOpenSettings: () => void;
  /** 重建索引已触发:由 App 切到「信息处理和入库」并聚焦该项目,在那儿看完整索引进度。 */
  onReindexStarted: (projectId: number) => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<Project | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  // 待删除确认的项目;为 null 时确认对话框关闭。
  const [pendingDelete, setPendingDelete] = useState<Project | null>(null);
  // 待删除确认的对话;为 null 时确认对话框关闭。
  const [pendingDeleteConversation, setPendingDeleteConversation] =
    useState<Conversation | null>(null);

  // —— 树状态:展开集合 + 每项目对话缓存 + 选中会话 ——
  // 展开的项目 id 集合(可多开);默认展开当前选中项目。
  const [expandedIds, setExpandedIds] = useState<ReadonlySet<number>>(new Set());
  // 已加载的对话缓存:project id → 对话列表。懒加载:首次展开时拉取并缓存。
  const [conversationsByProject, setConversationsByProject] = useState<
    Record<number, Conversation[]>
  >({});
  // 正在懒加载对话的项目 id 集合(用于树内骨架/新对话按钮的进行态)。
  const [loadingProjectIds, setLoadingProjectIds] = useState<ReadonlySet<number>>(
    new Set(),
  );
  // 当前选中会话(独立于项目;切换项目不强制清掉,但选项目即清会话以回到项目态)。
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null);
  // 草稿态:正在为该项目撰写一段尚未落库的新对话(点 + 进入)。
  // 与 activeConversationId 互斥:有草稿时主区显示空线程 + 就绪输入框,后端无任何记录。
  // 首次发送时才 createConversation;切换项目/会话/顶栏页签会丢弃草稿(纯前端瞬态)。
  const [draftProjectId, setDraftProjectId] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listProjects()
      .then((rows) => {
        if (cancelled) return;
        setProjects(rows);
        setSelected((cur) => cur ?? rows[0] ?? null);
      })
      .catch(() => {
        /* 列表加载失败时保持空态;创建流程仍可用。 */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 懒加载某项目的对话(若已缓存则跳过)。展开/新建/刷新时调用。
  const loadConversations = useCallback(
    (projectId: number, force = false) => {
      if (!force && conversationsByProject[projectId]) return;
      setLoadingProjectIds((prev) => {
        const next = new Set(prev);
        next.add(projectId);
        return next;
      });
      api
        .listConversations(projectId)
        .then((rows) => {
          setConversationsByProject((prev) => ({ ...prev, [projectId]: rows }));
        })
        .catch(() => {
          // 拉取失败:落一个空数组,树显示「暂无对话」,用户仍可新建。
          setConversationsByProject((prev) =>
            prev[projectId] ? prev : { ...prev, [projectId]: [] },
          );
        })
        .finally(() => {
          setLoadingProjectIds((prev) => {
            const next = new Set(prev);
            next.delete(projectId);
            return next;
          });
        });
    },
    [conversationsByProject],
  );

  // 默认:选中的项目展开,并懒加载其对话(覆盖初始加载、删除回退等程序化选中场景)。
  useEffect(() => {
    if (!selected) return;
    setExpandedIds((prev) => {
      if (prev.has(selected.id)) return prev;
      const next = new Set(prev);
      next.add(selected.id);
      return next;
    });
    loadConversations(selected.id);
    // loadConversations 自带缓存去重;依赖只取 selected.id 即可。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id]);

  // 选中项目(点项目名/行):选中、展开、清空会话回到项目态、懒加载其对话。
  // 同时丢弃任何在途草稿——切换导航即视为放弃未发送的草稿。
  const handleSelectProject = useCallback(
    (project: Project) => {
      setSelected(project);
      setActiveConversationId(null);
      setDraftProjectId(null);
      setExpandedIds((prev) => {
        if (prev.has(project.id)) return prev;
        const next = new Set(prev);
        next.add(project.id);
        return next;
      });
      loadConversations(project.id);
    },
    [loadConversations],
  );

  // chevron:仅切换展开/折叠;首次展开时懒加载对话。
  const handleToggleExpand = useCallback(
    (project: Project) => {
      setExpandedIds((prev) => {
        const next = new Set(prev);
        if (next.has(project.id)) {
          next.delete(project.id);
        } else {
          next.add(project.id);
          loadConversations(project.id);
        }
        return next;
      });
    },
    [loadConversations],
  );

  const handleSelectConversation = useCallback(
    (conversation: Conversation) => {
      const project = projects.find((p) => p.id === conversation.project_id);
      if (project) setSelected(project);
      setActiveConversationId(conversation.id);
      // 选中已有会话也丢弃草稿(导航离开 = 放弃未发送的草稿)。
      setDraftProjectId(null);
    },
    [projects],
  );

  // 新建对话(树上的每项目 +):不调后端、不建 DB 行——只在主区开一段草稿。
  // 选中并展开该项目、清空选中会话、把草稿挂到该项目。真正的 createConversation 推迟到首次发送。
  const handleStartDraft = useCallback((project: Project) => {
    setSelected(project);
    setActiveConversationId(null);
    setDraftProjectId(project.id);
    setExpandedIds((prev) => {
      if (prev.has(project.id)) return prev;
      const next = new Set(prev);
      next.add(project.id);
      return next;
    });
  }, []);

  // Workspace 内空态/Composer 用的「为当前项目开草稿」便捷封装。
  const handleStartDraftForSelected = useCallback(() => {
    if (selected) handleStartDraft(selected);
  }, [selected, handleStartDraft]);

  // 草稿首次发送时由 Conversation 调用:落库新建一段对话并返回 cid。
  // 这里只创建,不改 activeConversationId——流仍在同一个 Conversation 实例里跑,
  // 提前切 activeConversationId 会触发其重置 effect、打断在途流。
  const handleCreateConversation = useCallback(
    (projectId: number) => api.createConversation(projectId),
    [],
  );

  // Conversation 通知:草稿已落库为真实会话(已拿到首条回答的 cid)。
  // 缓存前插、清掉草稿态、把它设为选中会话——此时流早已结束,可安全切换。
  const handleConversationCreated = useCallback((c: Conversation) => {
    setConversationsByProject((prev) => ({
      ...prev,
      [c.project_id]: [c, ...(prev[c.project_id] ?? [])],
    }));
    setDraftProjectId((cur) => (cur === c.project_id ? null : cur));
    setActiveConversationId(c.id);
  }, []);

  // 助手回答完成后,标题可能已更新(后端按首条消息),刷新当前项目对话列表保持侧栏标题同步。
  const refreshConversations = useCallback(() => {
    if (selected) loadConversations(selected.id, true);
  }, [selected, loadConversations]);

  // 删除对话:调后端 → 从缓存移除;若删的是当前选中会话,清空主区回到项目态。
  const handleDeleteConversation = useCallback(
    async (conversation: Conversation) => {
      try {
        await api.deleteConversation(conversation.id);
      } catch {
        /* 删除失败(非 404):静默,用户可重试。 */
        return;
      }
      setConversationsByProject((prev) => {
        const list = prev[conversation.project_id];
        if (!list) return prev;
        return {
          ...prev,
          [conversation.project_id]: list.filter((c) => c.id !== conversation.id),
        };
      });
      setActiveConversationId((cur) => (cur === conversation.id ? null : cur));
    },
    [],
  );

  // 重建索引:确认 → 调 /reindex(后端清旧向量 + 把记录翻回待索引 + 跑同一条索引流水线)→
  // 切到「信息处理和入库」页并聚焦该项目。进度不再在侧栏行内显示——而是复用与「建立索引」
  // 完全相同的整套进度 UI(PendingList,轮询同一个 index/status 端点),用户在那儿看实时进度。
  const handleReindexProject = useCallback(
    async (project: Project) => {
      // 重建较慢且会清空现有索引,二次确认避免误触。
      const ok = window.confirm(
        "将清除该项目索引并用当前提取引擎重新索引所有文件,可能较慢,继续?",
      );
      if (!ok) return;
      try {
        // POST 同步把该项目记录翻回待索引并启动后台 job,立刻返回。
        // 必须先 await 再跳转:这样目标页拉到的列表里该项目已是待索引,PendingList 才能聚合出它。
        await api.reindexProject(project.id);
      } catch {
        // 触发失败:静默,用户可重试(不跳转,留在当前页)。
        return;
      }
      onReindexStarted(project.id);
    },
    [onReindexStarted],
  );

  // 重命名项目(仅显示名,后端不动磁盘):乐观更新列表 + 选中项,失败回滚到原标题。
  const handleRenameProject = useCallback(
    async (project: Project, title: string) => {
      const next = title.trim();
      if (!next || next === project.title) return;
      const prevTitle = project.title;
      setProjects((prev) =>
        prev.map((p) => (p.id === project.id ? { ...p, title: next } : p)),
      );
      setSelected((cur) => (cur && cur.id === project.id ? { ...cur, title: next } : cur));
      try {
        await api.renameProject(project.id, next);
      } catch {
        // 失败回滚到原标题(列表 + 选中项)。
        setProjects((prev) =>
          prev.map((p) => (p.id === project.id ? { ...p, title: prevTitle } : p)),
        );
        setSelected((cur) =>
          cur && cur.id === project.id ? { ...cur, title: prevTitle } : cur,
        );
      }
    },
    [],
  );

  // 重命名对话:乐观更新该项目的对话缓存,失败回滚到原标题。
  const handleRenameConversation = useCallback(
    async (conversation: Conversation, title: string) => {
      const next = title.trim();
      if (!next || next === conversation.title) return;
      const prevTitle = conversation.title;
      const patch = (t: string) =>
        setConversationsByProject((prev) => {
          const list = prev[conversation.project_id];
          if (!list) return prev;
          return {
            ...prev,
            [conversation.project_id]: list.map((c) =>
              c.id === conversation.id ? { ...c, title: t } : c,
            ),
          };
        });
      patch(next);
      try {
        await api.renameConversation(conversation.id, next);
      } catch {
        patch(prevTitle);
      }
    },
    [],
  );

  const handleCreated = async (project: Project) => {
    // 重新拉取权威列表,避免较慢的初始 listProjects 响应覆盖乐观插入的新项目;
    // 随后按 id 选中并展开新项目。
    let next = project;
    try {
      const rows = await api.listProjects();
      setProjects(rows);
      next = rows.find((p) => p.id === project.id) ?? project;
    } catch {
      // 列表刷新失败时退回乐观插入,至少保证新项目可见且被选中。
      setProjects((prev) => [project, ...prev]);
    }
    handleSelectProject(next);
  };

  const handleDeleted = (deleted: Project) => {
    // 删除成功:从列表移除;清理其展开态与对话缓存;
    // 若删的是当前选中项,退回首个剩余项目(没有则清空)并清空选中会话。
    const next = projects.filter((p) => p.id !== deleted.id);
    setProjects(next);
    setExpandedIds((prev) => {
      if (!prev.has(deleted.id)) return prev;
      const s = new Set(prev);
      s.delete(deleted.id);
      return s;
    });
    setConversationsByProject((prev) => {
      if (!(deleted.id in prev)) return prev;
      const { [deleted.id]: _, ...rest } = prev;
      return rest;
    });
    setDraftProjectId((cur) => (cur === deleted.id ? null : cur));
    setSelected((cur) => {
      if (cur && cur.id === deleted.id) {
        setActiveConversationId(null);
        return next[0] ?? null;
      }
      return cur;
    });
  };

  return (
    <div className="flex h-[calc(100vh-3.5rem)]">
      <ProjectSidebar
        projects={projects}
        selectedProjectId={selected?.id ?? null}
        selectedConversationId={activeConversationId}
        draftProjectId={draftProjectId}
        expandedIds={expandedIds}
        conversationsByProject={conversationsByProject}
        loadingProjectIds={loadingProjectIds}
        onSelectProject={handleSelectProject}
        onToggleExpand={handleToggleExpand}
        onSelectConversation={handleSelectConversation}
        onCreateConversation={handleStartDraft}
        onDeleteConversation={(c) => setPendingDeleteConversation(c)}
        onCreateProject={() => setCreateOpen(true)}
        onDeleteProject={setPendingDelete}
        onReindexProject={handleReindexProject}
        onRenameProject={handleRenameProject}
        onRenameConversation={handleRenameConversation}
      />

      <section className="flex min-w-0 flex-1 flex-col">
        {selected ? (
          <Workspace
            key={selected.id}
            project={selected}
            llmConfigured={llmConfigured}
            onOpenSettings={onOpenSettings}
            activeConversationId={activeConversationId}
            draft={draftProjectId === selected.id}
            onStartDraft={handleStartDraftForSelected}
            onCreateConversation={handleCreateConversation}
            onConversationCreated={handleConversationCreated}
            onConversationActivity={refreshConversations}
          />
        ) : (
          <EmptyState onCreate={() => setCreateOpen(true)} />
        )}
      </section>

      <CreateProjectModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={handleCreated}
      />

      <DeleteProjectDialog
        project={pendingDelete}
        onClose={() => setPendingDelete(null)}
        onDeleted={handleDeleted}
      />

      <DeleteConversationDialog
        conversation={pendingDeleteConversation}
        onClose={() => setPendingDeleteConversation(null)}
        onConfirm={handleDeleteConversation}
      />
    </div>
  );
}

/**
 * 删除对话的轻量确认。沿用项目删除对话框的视觉语言(破坏性图标 + 标题 + 底部双按钮),
 * 但更简单——对话只是一段问答记录,删除只清库内记录,不涉及磁盘文件。
 */
function DeleteConversationDialog({
  conversation,
  onClose,
  onConfirm,
}: {
  /** 待删除的对话;为 null 时对话框关闭。 */
  conversation: Conversation | null;
  onClose: () => void;
  /** 用户确认后调用;父级负责实际删除与列表/选中态清理。 */
  onConfirm: (conversation: Conversation) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const open = conversation !== null;

  useEffect(() => {
    if (open) setBusy(false);
  }, [open]);

  const confirm = async () => {
    if (!conversation) return;
    setBusy(true);
    await onConfirm(conversation);
    setBusy(false);
    onClose();
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && !busy && onClose()}>
      <DialogContent showCloseButton={!busy} className="gap-0 p-0">
        <DialogHeader className="gap-2 px-6 pt-6">
          <span
            aria-hidden
            className="flex size-9 items-center justify-center rounded-xl bg-destructive/10 text-destructive ring-1 ring-destructive/15"
          >
            <TriangleAlert className="size-[18px]" strokeWidth={2} />
          </span>
          <DialogTitle>删除对话「{conversation?.title}」?</DialogTitle>
          <DialogDescription>
            将从该项目移除这段对话及其全部消息。此操作不可撤销。
          </DialogDescription>
        </DialogHeader>

        <DialogFooter className="gap-2 border-t border-border/70 bg-muted/30 px-6 py-4">
          <Button
            type="button"
            variant="ghost"
            size="lg"
            disabled={busy}
            onClick={onClose}
          >
            取消
          </Button>
          <Button
            type="button"
            variant="destructive"
            size="lg"
            disabled={busy}
            onClick={confirm}
          >
            {busy ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                正在删除…
              </>
            ) : (
              <>
                <Trash2 className="size-4" />
                删除
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Workspace({
  project,
  llmConfigured,
  onOpenSettings,
  activeConversationId,
  draft,
  onStartDraft,
  onCreateConversation,
  onConversationCreated,
  onConversationActivity,
}: {
  project: Project;
  llmConfigured: boolean;
  onOpenSettings: () => void;
  activeConversationId: number | null;
  /** 本项目当前是否处于草稿态(主区显示空线程 + 就绪输入框)。 */
  draft: boolean;
  /** 开启一段新草稿(空态/“新建对话”按钮)。 */
  onStartDraft: () => void;
  /** 草稿首次发送:落库新建对话并返回其记录(含 cid)。 */
  onCreateConversation: (projectId: number) => Promise<Conversation>;
  /** 草稿已落库为真实会话后通知父级(入缓存、清草稿、设为选中)。 */
  onConversationCreated: (conversation: Conversation) => void;
  onConversationActivity: () => void;
}) {
  return (
    <div className="relative flex h-full min-w-0 flex-1">
      {/* 主区:对话为中心 */}
      <div className="flex h-full min-w-0 flex-1 flex-col">
        {/* Workspace header:标题 + 路径 */}
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-border/70 px-8 py-5">
          <div className="min-w-0">
            <h1 className="truncate text-xl font-semibold tracking-tight text-foreground">
              {project.title}
            </h1>
            <div className="mt-1.5 flex items-center gap-1.5 text-xs text-muted-foreground">
              <FolderGit2 className="size-3.5 shrink-0" strokeWidth={1.75} />
              <span className="truncate font-mono" title={project.folder_path}>
                {project.folder_path}
              </span>
            </div>
          </div>
        </header>

        {/* 对话主体:占据主纵向空间 */}
        <Conversation
          projectId={project.id}
          projectTitle={project.title}
          conversationId={activeConversationId}
          draft={draft}
          llmConfigured={llmConfigured}
          onOpenSettings={onOpenSettings}
          onStartDraft={onStartDraft}
          onCreateConversation={onCreateConversation}
          onConversationCreated={onConversationCreated}
          onConversationActivity={onConversationActivity}
        />
      </div>
    </div>
  );
}

function parseCitations(json: string | null): Citation[] {
  if (!json) return [];
  try {
    const arr = JSON.parse(json);
    return Array.isArray(arr) ? (arr as Citation[]) : [];
  } catch {
    return [];
  }
}

/**
 * 对话主体——本工作区的中心元素(Codex/ChatGPT 式)。
 * 选中会话后展示历史消息;Composer 发送走 SSE 流式渲染;assistant 的 [n] 引用可点开来源查看器。
 */
function Conversation({
  projectId,
  projectTitle,
  conversationId,
  draft,
  llmConfigured,
  onOpenSettings,
  onStartDraft,
  onCreateConversation,
  onConversationCreated,
  onConversationActivity,
}: {
  projectId: number;
  projectTitle: string;
  conversationId: number | null;
  draft: boolean;
  llmConfigured: boolean;
  onOpenSettings: () => void;
  onStartDraft: () => void;
  onCreateConversation: (projectId: number) => Promise<Conversation>;
  onConversationCreated: (conversation: Conversation) => void;
  onConversationActivity: () => void;
}) {
  const [messages, setMessages] = useState<ViewMessage[]>([]);
  const [loading, setLoading] = useState(false);
  // 流式状态文案(检索中/生成中);null 表示无进行中的请求。
  const [status, setStatus] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  // 点开来源查看器的当前引用;null 时关闭。
  const [viewing, setViewing] = useState<Citation | null>(null);
  // 本对话引用(外部附件 + 内部项目文件)。草稿态为空,落库后随会话拉取。
  const [references, setReferences] = useState<ConversationReference[]>([]);
  // 「库内文件」区的强制展开信号:自增即让侧栏内的 ProjectFilesZone 展开并滚入视野
  //(Composer 的「从项目」按钮触发)。
  const [filesZoneSignal, setFilesZoneSignal] = useState(0);
  // 附加外部文件失败时的可关闭内联提示;null 表示无错误。
  const [attachError, setAttachError] = useState<string | null>(null);
  // 正在附加(后端对大文件同步索引,较慢)时显示瞬态提示。
  const [attaching, setAttaching] = useState(false);
  // 附加进行中的实时进度文案(如「正在用 MinerU 解析 report.pdf:解析中 12/29」);null 表示暂无进度文案。
  const [attachProgress, setAttachProgress] = useState<string | null>(null);
  // 右侧「引用」侧栏开关(类 Claude Desktop 的 Context 面板)。默认收起。
  const [refSidebarOpen, setRefSidebarOpen] = useState(false);
  // 拖放覆盖层:在整个对话区拖动文件时显示半透明提示。
  const [dropOverlay, setDropOverlay] = useState(false);
  // 当前流的 abort 句柄(切换会话 / 停止 / 卸载时调用)。
  const abortRef = useRef<(() => void) | null>(null);
  // 草稿首次落库的在途去重:并发调用 ensureConversation 时共用同一个创建 promise,避免重复建会话。
  const creatingRef = useRef<Promise<number> | null>(null);
  // 在途 attachExternal 计数:并发附加(拖一批 + 紧接着再拖一个)时,只有全部归零才解除
  // attaching 锁。否则第一个 finally 抢先 setAttaching(false) 会在第二批仍在跑时放行发送。
  const attachInFlightRef = useRef(0);
  // 由本组件在草稿首次发送时落库出来的 cid。父级随后会把 conversationId 切到这个值,
  // 此时不应重置/重拉历史——流就在本实例里跑,内容已在屏。下面的重置 effect 据此跳过这一跳变。
  const selfCreatedCidRef = useRef<number | null>(null);
  // 上一次渲染的引用数量,用于「引用新增时自动展开右侧侧栏」(仅在数量上升时触发,避免解挂/切换也强开)。
  const prevRefCountRef = useRef(0);

  // 切换会话:中断在途流、拉取历史消息。
  // 例外:从草稿(null)跳到本组件刚落库的 cid——同一段对话、流仍在跑,跳过重置。
  useEffect(() => {
    if (conversationId != null && conversationId === selfCreatedCidRef.current) {
      // 草稿已被父级提升为选中会话;消化掉这一跳,后续若再切走才真正重置。
      selfCreatedCidRef.current = null;
      return;
    }
    abortRef.current?.();
    abortRef.current = null;
    selfCreatedCidRef.current = null;
    setStreaming(false);
    setStatus(null);
    setMessages([]);
    // 切到草稿(null)或别的会话:引用先清空,真实会话再拉取。
    setReferences([]);
    if (conversationId == null) return;
    let cancelled = false;
    setLoading(true);
    api
      .listMessages(conversationId)
      .then((rows) => {
        if (cancelled) return;
        setMessages(
          rows.map((m) => ({
            id: m.id,
            role: m.role,
            content: m.content,
            citations: parseCitations(m.citations_json),
          })),
        );
      })
      .catch(() => {
        /* 历史拉取失败:留空,用户仍可继续提问。 */
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    api
      .listReferences(conversationId)
      .then((rows) => {
        if (!cancelled) setReferences(rows);
      })
      .catch(() => {
        /* 引用拉取失败:留空,不阻塞对话。 */
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  // 组件卸载时中断在途流,避免回调写入已卸载组件。
  useEffect(() => () => abortRef.current?.(), []);

  // 把一个 SSE 流(发送 / 重生成)的事件接到指定 assistant 消息上。
  // start 接收 onToken/onCitations 等回调,返回 abort 句柄(api.sendMessage / api.regenerate)。
  // 该 assistant 消息须已就绪(发送:乐观插入的空消息;重生成:已存在并被重置为流式)。
  const runStream = useCallback(
    (cid: number, assistantId: number | string, start: (h: StreamHandlers) => () => void) => {
      const patch = (fn: (m: ViewMessage) => ViewMessage) =>
        setMessages((prev) => prev.map((m) => (m.id === assistantId ? fn(m) : m)));

      abortRef.current = start({
        onStatus: (s) => setStatus(s),
        onToken: (t) => patch((m) => ({ ...m, content: m.content + t })),
        onCitations: (c) => patch((m) => ({ ...m, citations: c })),
        onDone: () => {
          patch((m) => ({ ...m, streaming: false }));
          setStreaming(false);
          setStatus(null);
          abortRef.current = null;
          // 后端在首轮按 LLM 自动命名:刷新当前项目对话列表,让已命名的会话出现在侧栏。
          onConversationActivity();
          // 用真实(数字)消息 id 替换乐观字符串 id → 本轮立即可编辑/重试。
          api.listMessages(cid)
            .then((rows) =>
              setMessages(rows.map((m) => ({
                id: m.id, role: m.role, content: m.content,
                citations: parseCitations(m.citations_json),
              }))))
            .catch(() => { /* 刷新失败不致命:乐观消息仍在,切换会话后会校正 */ });
        },
        onError: (e) => {
          // SSE 可能发来 error 事件(base_url 错误 / 端点不可达 / 模型报错)。
          // 把错误挂到当前助手消息上,以安静的内联通知呈现;已流出的部分正文保留。
          patch((m) => ({ ...m, streaming: false, error: e.message }));
          setStreaming(false);
          setStatus(null);
          abortRef.current = null;
        },
      });
    },
    [onConversationActivity],
  );

  // 真正打开发送的 SSE 流。content 已经过乐观插入(用户消息 + 空助手消息)。
  const streamTo = useCallback(
    (cid: number, content: string, assistantId: string) =>
      runStream(cid, assistantId, (h) => api.sendMessage(cid, content, h)),
    [runStream],
  );

  // 确保存在真实会话并返回其 cid:已有则直接返回;否则此刻才 createConversation。
  // 草稿首次发送、附加外部/内部引用都经此创建,保证只创建一次(不重复建会话)。
  // 创建成功会通知父级(入缓存、清草稿、设为选中),并记到 selfCreatedCidRef,
  // 使切换会话的重置 effect 跳过这一跳变,不打断在途流/不清掉刚加的引用。
  const ensureConversation = useCallback(async (): Promise<number> => {
    if (conversationId != null) return conversationId;
    // 已有在途创建:复用同一个 promise,避免并发(拖文件 + 紧接着发送)各建一段会话。
    if (creatingRef.current) return creatingRef.current;
    const create = (async () => {
      const c = await onCreateConversation(projectId);
      selfCreatedCidRef.current = c.id;
      onConversationCreated(c);
      return c.id;
    })();
    creatingRef.current = create;
    try {
      return await create;
    } finally {
      creatingRef.current = null;
    }
  }, [conversationId, projectId, onCreateConversation, onConversationCreated]);

  const send = useCallback(
    async (content: string) => {
      // 草稿态需先落库;非草稿则要求已有 cid。流式进行中、或外部附件仍在处理(提取/下模型/索引)
      // 时都不接受新发送——附件未就绪就提问会答非所问。
      if (streaming || attaching) return;
      if (conversationId == null && !draft) return;

      const assistantId = `assistant-${Date.now()}`;
      // 乐观插入用户消息 + 一条空的流式助手消息。
      setMessages((prev) => [
        ...prev,
        { id: `user-${Date.now()}`, role: "user", content, citations: [] },
        { id: assistantId, role: "assistant", content: "", citations: [], streaming: true },
      ]);
      setStreaming(true);
      setStatus("思考中");

      let cid: number;
      try {
        // 草稿首次发送:此刻才真正 createConversation(经 ensureConversation 统一创建)。
        cid = await ensureConversation();
      } catch {
        // 落库失败:撤回乐观消息、回到草稿就绪态,用户可重试。
        setMessages((prev) => prev.filter((m) => m.id !== assistantId).slice(0, -1));
        setStreaming(false);
        setStatus(null);
        return;
      }

      streamTo(cid, content, assistantId);
    },
    [conversationId, draft, streaming, attaching, ensureConversation, streamTo],
  );

  // —— 本对话引用:附加(外部/内部)/解挂 ——
  // 引用按会话维护;附加任一前先确保会话已落库(ensureConversation,不重复建)。
  // 单文件失败不阻塞其余;增删后统一以服务端为准重拉。
  const refreshRefs = useCallback(async (cid: number) => {
    setReferences(await api.listReferences(cid));
  }, []);

  const attachExternal = useCallback(
    async (paths: string[]) => {
      // 锁要在 ensureConversation(await,会让出事件循环)之前就上,且用计数器跨并发累计:
      // 否则有「await 建会话」这段窗口让 send / 第二次 attach 抢进来;而若只用布尔标志,
      // 第一个 finally 会在第二批仍在跑时把锁解开。计数归零才真正解锁。
      attachInFlightRef.current += 1;
      setAttaching(true);
      const cid = await ensureConversation();
      const failures: string[] = [];
      try {
        // 顺序逐个解析:阻塞直到每个文件 done/error(MinerU 解析较慢),
        // 期间用 attachProgress 把后端的实时进度文案直出到指示器。
        for (const p of paths) {
          const name = p.split("/").pop() || p;
          let failed: string | null = null;
          try {
            await api.attachExternalStream(cid, p, {
              onStatus: (text) => setAttachProgress(`正在用 MinerU 解析 ${name}:${text}`),
              // done 由下面统一 refreshRefs 以服务端为准重拉,这里无需手动并入。
              onError: (message) => { failed = message; },
            });
          } catch (e) {
            // 流被 reject(HTTP / 网络 / SSE 解析错误,或 server error 事件经 onError 后仍 reject):
            // 也算单文件失败,收进 failures(而非整批抛出),否则会漏掉 finally 的进行态清理。
            failed = e instanceof Error ? e.message : String(e);
          }
          // 单文件失败不阻塞其余;收集后统一以内联提示呈现。
          if (failed) failures.push(`${name}(${failed})`);
        }
      } finally {
        // 只有所有在途 attach 都结束(计数归零)才解锁;清进度文案,避免按钮卡在加载态。
        attachInFlightRef.current -= 1;
        if (attachInFlightRef.current === 0) setAttaching(false);
        setAttachProgress(null);
      }
      // 呈现失败提示,最后刷新引用列表——即便 refreshRefs 抛错,用户仍看得到哪些文件没加上。
      setAttachError(failures.length ? `部分文件未能添加:${failures.join("；")}` : null);
      await refreshRefs(cid);
    },
    [ensureConversation, refreshRefs],
  );

  const detachRef = useCallback(
    async (rid: number) => {
      // 解挂只对已落库会话有意义;草稿态无引用,无可解挂。
      if (conversationId == null) return;
      await api.detachReference(conversationId, rid);
      await refreshRefs(conversationId);
    },
    [conversationId, refreshRefs],
  );

  const addInternal = useCallback(
    async (ingestRecordId: number) => {
      const cid = await ensureConversation();
      try {
        await api.addInternalReference(cid, ingestRecordId);
      } catch {
        /* ignore */
      }
      await refreshRefs(cid);
    },
    [ensureConversation, refreshRefs],
  );

  // 引用数量上升时自动展开右侧侧栏,让用户看到新引用落位。
  // 仅在「增加」时触发;解挂、切换会话清空等下降/持平不强开(尊重用户手动收起)。
  useEffect(() => {
    const prev = prevRefCountRef.current;
    if (references.length > prev) setRefSidebarOpen(true);
    prevRefCountRef.current = references.length;
  }, [references.length]);

  // 桌面外壳(pywebview)原生拖放:外壳能拿到真实绝对路径,通过该全局回调把路径交回前端。
  // 浏览器 drop 事件读不到路径(这正是旧 File.path 方案在打包态失效的原因),故路径走这条原生通道。
  useEffect(() => {
    (window as unknown as { __onNativeFilesDropped?: (paths: string[]) => void }).__onNativeFilesDropped =
      (paths: string[]) => {
        if (paths?.length) attachExternal(paths);
      };
    return () => {
      delete (window as unknown as { __onNativeFilesDropped?: unknown }).__onNativeFilesDropped;
    };
  }, [attachExternal]);

  const stop = useCallback(() => {
    abortRef.current?.();
    abortRef.current = null;
    setStreaming(false);
    setStatus(null);
    setMessages((prev) =>
      prev.map((m) => (m.streaming ? { ...m, streaming: false } : m)),
    );
  }, []);

  // 重新生成最后一轮:后端删最后一条 user 之后的消息、对同一提问重跑。前端把最后一条
  // assistant 消息复位为流式(清空旧正文/引用/错误),token 流进同一条;无 assistant 消息
  // (理论上不会)则补一条空的流式消息。复用与发送相同的流式/abort 机制。
  const regenerate = useCallback(() => {
    if (streaming || conversationId == null) return;

    let targetId: number | string | null = null;
    setMessages((prev) => {
      const lastAssistantIdx = prev.reduce(
        (acc, m, i) => (m.role === "assistant" ? i : acc),
        -1,
      );
      if (lastAssistantIdx >= 0) {
        targetId = prev[lastAssistantIdx].id;
        return prev.map((m, i) =>
          i === lastAssistantIdx
            ? { ...m, content: "", citations: [], error: undefined, streaming: true }
            : m,
        );
      }
      // 兜底:历史里没有 assistant 消息(失败轮次理应留有乐观空消息,这里以防万一)。
      targetId = `assistant-${Date.now()}`;
      return [
        ...prev,
        { id: targetId, role: "assistant", content: "", citations: [], streaming: true },
      ];
    });
    if (targetId == null) return;

    setStreaming(true);
    setStatus("检索中");
    runStream(conversationId, targetId, (h) => api.regenerate(conversationId, h));
  }, [streaming, conversationId, runStream]);

  // 编辑某条 user 消息并就地重生成:把该消息内容改为 content、删它之后的全部消息(本地截断)、
  // 追加一条空的流式 assistant 消息,token 流进去。后端做同样的事(改内容、删其后、重跑)。
  // 仅对已落库(数字 mid)的 user 消息可用;复用与发送/重生成相同的流式/abort 机制。
  const editMessage = useCallback(
    (messageId: number | string, content: string) => {
      if (streaming || conversationId == null || typeof messageId !== "number") return;
      const text = content.trim();
      if (!text) return;

      const assistantId = `assistant-${Date.now()}`;
      setMessages((prev) => {
        const idx = prev.findIndex((m) => m.id === messageId);
        if (idx < 0) return prev; // 该消息已不在(竞态):放弃。
        // 保留到被编辑消息(含),改其内容,删其后的全部,追加新的流式 assistant。
        const kept = prev.slice(0, idx + 1).map((m) =>
          m.id === messageId ? { ...m, content: text } : m,
        );
        return [
          ...kept,
          { id: assistantId, role: "assistant", content: "", citations: [], streaming: true },
        ];
      });

      setStreaming(true);
      setStatus("检索中");
      runStream(conversationId, assistantId, (h) => api.editMessage(conversationId, messageId, text, h));
    },
    [streaming, conversationId, runStream],
  );

  const hasMessages = messages.length > 0;
  // 已 pin 为内部引用的项目文件 id 集合:供「库内文件」区标记「已引用」并禁用点选。
  const pinnedRecordIds = new Set(
    references
      .filter((r) => r.kind === "internal" && r.ingest_record_id != null)
      .map((r) => r.ingest_record_id as number),
  );
  // 主区有「正在对话」上下文:已有选中会话,或正在撰写一段草稿。
  // 二者皆无时只展示项目空态与「新建对话」CTA,不挂输入框(发送无处可去)。
  const inConversation = conversationId != null || draft;

  // —— 对话区拖放(类 ChatGPT 网页:整个对话区都是拖放靶区,不只输入框)——
  // dragover 时显示半透明覆盖层;真正读路径走原生通道(__onNativeFilesDropped)。
  // 浏览器 drop 拿不到绝对路径,故 onDrop 仅做视觉收尾;开发态浏览器额外给出提示。
  const dragHasFiles = (e: React.DragEvent) =>
    Array.from(e.dataTransfer.types).includes("Files");

  const onAreaDragOver = (e: React.DragEvent) => {
    if (!inConversation || !dragHasFiles(e)) return;
    e.preventDefault();
    setDropOverlay(true);
  };
  const onAreaDragLeave = (e: React.DragEvent) => {
    // 仅当指针真正离开对话区(而非进入子元素)时才隐藏,避免覆盖层闪烁。
    if (e.relatedTarget && e.currentTarget.contains(e.relatedTarget as Node)) return;
    setDropOverlay(false);
  };
  const onAreaDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDropOverlay(false);
    // 路径由原生外壳经 __onNativeFilesDropped 交付;此处不读 File.path。
    // 开发态浏览器(无 pywebview)确实拿不到路径,给出与 + 选择文件一致的提示。
    const hasPywebview = "pywebview" in window;
    if (!hasPywebview && e.dataTransfer.files.length) {
      setAttachError("当前为开发态浏览器,无法读取拖放文件路径;请点 + 选择文件。");
    }
  };

  return (
    <div className="flex min-h-0 flex-1">
      <div className="relative flex min-w-0 flex-1 flex-col">
        {/* 右上角:引用侧栏开关(类 Claude Desktop Context 面板) */}
        {inConversation && (
          <div className="flex shrink-0 justify-end px-6 pt-3">
            <ReferenceSidebarToggle
              count={references.length}
              open={refSidebarOpen}
              onToggle={() => setRefSidebarOpen((o) => !o)}
            />
          </div>
        )}

        <div
          className="relative flex min-h-0 flex-1 flex-col"
          onDragOver={onAreaDragOver}
          onDragLeave={onAreaDragLeave}
          onDrop={onAreaDrop}
        >
          <div className="min-h-0 flex-1 overflow-y-auto">
        {!inConversation ? (
          <CenteredEmpty
            title={`与「${projectTitle}」对话`}
            body="为项目文件建立索引后,即可在此基于你的资料提问,并跳回答案引用的原始来源。"
            actionLabel="新建对话"
            onAction={onStartDraft}
          />
        ) : loading ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" />
            正在加载对话…
          </div>
        ) : hasMessages ? (
          <MessageList
            messages={messages}
            status={status}
            busy={streaming}
            onCitation={setViewing}
            onRegenerate={regenerate}
            onEdit={editMessage}
          />
        ) : (
          messages.length === 0 &&
          !streaming && (
            <div className="mx-auto flex h-full w-full max-w-2xl flex-col items-center justify-center gap-3 px-6 text-center">
              <p className="text-sm font-medium text-foreground">开始对话</p>
              <p className="max-w-sm text-xs leading-relaxed text-muted-foreground">
                基于项目资料提问,或用「+」附一个文件一起聊。回答会带可跳回原文的来源引用。
              </p>
              <div className="flex flex-wrap justify-center gap-2">
                {["这个项目主要讲了什么?", "帮我总结关键结论", "列出待办/风险点"].map(
                  (q) => (
                    <button
                      key={q}
                      type="button"
                      disabled={!llmConfigured}
                      onClick={() => send(q)}
                      className="rounded-full border border-border/70 bg-background px-3 py-1.5 text-xs text-foreground outline-none hover:bg-muted/50 disabled:opacity-50"
                    >
                      {q}
                    </button>
                  ),
                )}
              </div>
            </div>
          )
        )}
          </div>

          {/* 拖放覆盖层:覆盖整个对话区(含输入框),仅视觉提示。 */}
          {inConversation && dropOverlay && (
            <div
              aria-hidden
              className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center rounded-lg border-2 border-dashed border-ring/60 bg-background/80 backdrop-blur-[1px]"
            >
              <span className="rounded-full bg-foreground/90 px-4 py-2 text-sm font-medium text-background shadow-sm">
                拖放文件到此处添加引用
              </span>
            </div>
          )}

          {inConversation && attaching && (
            <p className="mx-auto w-full max-w-2xl px-6 text-xs text-muted-foreground">
              {attachProgress ?? "正在索引附件…"}
            </p>
          )}

          {inConversation && attachError && (
            <div className="mx-auto w-full max-w-2xl px-6">
              <div role="alert" className="flex items-start gap-2 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive">
                <span className="flex-1 break-words">{attachError}</span>
                <button type="button" onClick={() => setAttachError(null)} aria-label="关闭" className="shrink-0 underline-offset-2 hover:underline">关闭</button>
              </div>
            </div>
          )}

          {inConversation && (
            <Composer
              llmConfigured={llmConfigured}
              streaming={streaming}
              attaching={attaching}
              onSend={send}
              onStop={stop}
              onOpenSettings={onOpenSettings}
              onAttachPaths={attachExternal}
              onAddInternal={() => {
                // 「从项目」:开侧栏并让「库内文件」区展开+滚入视野(取代旧的选择对话框)。
                setRefSidebarOpen(true);
                setFilesZoneSignal((s) => s + 1);
              }}
              onAttachUnsupported={() =>
                setAttachError("当前环境无法读取拖拽/粘贴的文件路径,请点 + 选择文件。")
              }
            />
          )}
        </div>
      </div>

      {/* 右侧「引用」侧栏:可折叠(开启约 260px),收起则不占宽。 */}
      {inConversation && refSidebarOpen && (
        <aside
          aria-label="本对话引用"
          className="flex h-full w-[260px] shrink-0 flex-col border-l border-border/70 bg-sidebar"
        >
          <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border/70 px-4 py-3">
            <h2 className="flex items-center gap-1.5 text-sm font-medium text-foreground">
              引用
              {references.length > 0 && (
                <span className="rounded-full bg-muted px-1.5 py-0.5 text-[0.7rem] font-medium tabular-nums text-muted-foreground">
                  {references.length}
                </span>
              )}
            </h2>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => setRefSidebarOpen(false)}
              aria-label="关闭引用面板"
              className="text-muted-foreground"
            >
              <X className="size-4" />
            </Button>
          </div>
          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
            {/* 外部已引用 / 库内已引用 */}
            <ReferencePanel references={references} onDetach={detachRef} />
            {/* 库内文件:可折叠(默认收起),搜索 + 点选 pin + 右键在 Finder 查看 */}
            <ProjectFilesZone
              projectId={projectId}
              pinnedRecordIds={pinnedRecordIds}
              onPin={addInternal}
              openSignal={filesZoneSignal}
              refreshSignal={references.length}
            />
          </div>
        </aside>
      )}

      <SourceViewer citation={viewing} onClose={() => setViewing(null)} />
    </div>
  );
}

/**
 * 右侧「引用」侧栏的开关:一个安静的面板图标按钮(类 Claude Desktop Context 面板)。
 * 收起且存在引用时,在图标上叠一个小计数角标,提示「这里有引用」。
 */
function ReferenceSidebarToggle({
  count,
  open,
  onToggle,
}: {
  count: number;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      onClick={onToggle}
      aria-expanded={open}
      aria-pressed={open}
      aria-label="本对话引用"
      title="本对话引用"
      className="relative text-muted-foreground data-[active=true]:text-foreground"
      data-active={open}
    >
      <PanelRight className="size-4" strokeWidth={1.75} aria-hidden />
      {!open && count > 0 && (
        <span
          aria-hidden
          className="absolute -top-1 -right-1 flex min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[0.6rem] font-semibold leading-none text-primary-foreground tabular-nums"
        >
          {count}
        </span>
      )}
    </Button>
  );
}

function CenteredEmpty({
  title,
  body,
  actionLabel,
  onAction,
}: {
  title: string;
  body: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center">
      <span
        aria-hidden
        className="flex size-12 items-center justify-center rounded-2xl bg-muted text-foreground ring-1 ring-border/70"
      >
        <Sparkles className="size-5" strokeWidth={1.75} />
      </span>
      <h2 className="mt-5 text-lg font-semibold tracking-tight text-balance text-foreground">
        {title}
      </h2>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-pretty text-muted-foreground">
        {body}
      </p>
      {actionLabel && onAction && (
        <Button type="button" className="mt-6" onClick={onAction}>
          {actionLabel}
        </Button>
      )}
    </div>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-8 text-center">
      <span
        aria-hidden
        className="flex size-14 items-center justify-center rounded-2xl bg-muted text-foreground ring-1 ring-border/70"
      >
        <FolderPlus className="size-6" strokeWidth={1.5} />
      </span>
      <h1 className="mt-5 text-xl font-semibold tracking-tight text-foreground">
        创建你的第一个项目
      </h1>
      <p className="mt-2 max-w-sm text-sm leading-relaxed text-muted-foreground">
        选择一个本地文件夹作为项目根目录,EpicTrace 会就地登记其中的资料,
        随后即可浏览文件、建立索引并对话。
      </p>
      <Button type="button" size="lg" className="mt-6" onClick={onCreate}>
        新建项目
      </Button>
    </div>
  );
}
