import { useCallback, useEffect, useRef, useState } from "react";
import {
  FolderGit2,
  FolderPlus,
  Loader2,
  Sparkles,
  Trash2,
  TriangleAlert,
} from "lucide-react";

import { api, type CoworkSession, type Project } from "@/lib/api";
import { sessionTitle } from "@/lib/coworkMeta";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { CoworkConversation } from "@/components/CoworkConversation";
import { CreateProjectModal } from "@/components/CreateProjectModal";
import { DeleteProjectDialog } from "@/components/DeleteProjectDialog";
import { ProjectSidebar } from "@/components/ProjectSidebar";

export function ProjectsConversationView({
  onReindexStarted,
  onJumpToSession,
}: {
  // 保留在契约里(App 的设置门禁仍按旧约定传入);cowork 会话与 Cowork tab 一致,不在前端做配置门禁。
  llmConfigured: boolean;
  onOpenSettings: () => void;
  /** 重建索引已触发:由 App 切到「信息处理和入库」并聚焦该项目,在那儿看完整索引进度。 */
  onReindexStarted: (projectId: number) => void;
  /** 引用「跳回会话时刻」:由 App 切到采集/暂存区并定位该 session 时刻(透传给 SourceViewer)。 */
  onJumpToSession: (sessionId: number, ts: string) => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<Project | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  // 待删除确认的项目;为 null 时确认对话框关闭。
  const [pendingDelete, setPendingDelete] = useState<Project | null>(null);
  // 待删除确认的对话(cowork 会话);为 null 时确认对话框关闭。
  const [pendingDeleteConversation, setPendingDeleteConversation] =
    useState<CoworkSession | null>(null);

  // —— 树状态:展开集合 + 每项目会话缓存 + 选中会话 ——
  // 展开的项目 id 集合(可多开);默认展开当前选中项目。
  const [expandedIds, setExpandedIds] = useState<ReadonlySet<number>>(new Set());
  // 已加载的会话缓存:project id → 该项目绑定的 cowork 会话列表。懒加载:首次展开时拉取并缓存。
  const [conversationsByProject, setConversationsByProject] = useState<
    Record<number, CoworkSession[]>
  >({});
  // 正在懒加载会话的项目 id 集合(用于树内骨架/新对话按钮的进行态)。
  const [loadingProjectIds, setLoadingProjectIds] = useState<ReadonlySet<number>>(
    new Set(),
  );
  // 当前选中会话(独立于项目;切换项目不强制清掉,但选项目即清会话以回到项目态)。
  const [activeConversationId, setActiveConversationId] = useState<number | null>(null);
  // 「新建对话」在途去重:cowork 会话创建即落库(异步),连点防重。
  const creatingRef = useRef<Set<number>>(new Set());

  // 把某项目并入展开集合(选中即展开的各处共用)。
  const expandProject = useCallback((projectId: number) => {
    setExpandedIds((prev) => {
      if (prev.has(projectId)) return prev;
      const next = new Set(prev);
      next.add(projectId);
      return next;
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .listProjects()
      .then((rows) => {
        if (cancelled) return;
        setProjects(rows);
        setSelected((cur) => cur ?? rows[0] ?? null);
        // 程序化选中(初始加载)的项目同步展开,与点选行为一致。
        if (rows[0]) expandProject(rows[0].id);
      })
      .catch(() => {
        /* 列表加载失败时保持空态;创建流程仍可用。 */
      });
    return () => {
      cancelled = true;
    };
  }, [expandProject]);

  // 懒加载某项目的 cowork 会话(若已缓存则跳过)。展开/新建/刷新时调用。
  const loadConversations = useCallback(
    (projectId: number, force = false) => {
      if (!force && conversationsByProject[projectId]) return;
      setLoadingProjectIds((prev) => {
        const next = new Set(prev);
        next.add(projectId);
        return next;
      });
      api
        .listCoworkSessions({ project_id: projectId })
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

  // 默认:懒加载选中项目的会话(展开由选中来源处处理:点选/新建/初始加载/删除回退)。
  useEffect(() => {
    if (!selected) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 数据拉取 effect:同步置 loading 再发请求
    loadConversations(selected.id);
    // loadConversations 自带缓存去重;依赖只取 selected.id 即可。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id]);

  // 选中项目(点项目名/行):选中、展开、清空会话回到项目态、懒加载其会话。
  const handleSelectProject = useCallback(
    (project: Project) => {
      setSelected(project);
      setActiveConversationId(null);
      expandProject(project.id);
      loadConversations(project.id);
    },
    [loadConversations, expandProject],
  );

  // chevron:仅切换展开/折叠;首次展开时懒加载会话。
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
    (conversation: CoworkSession) => {
      const project = projects.find((p) => p.id === conversation.project_id);
      if (project) setSelected(project);
      setActiveConversationId(conversation.id);
    },
    [projects],
  );

  // 新建对话(树上的每项目 +):立即创建绑定该项目的 cowork 会话(创建即落库、首轮自动标题),
  // 前插入缓存并选中。失败静默,用户可重试。
  const handleCreateConversation = useCallback(async (project: Project) => {
    if (creatingRef.current.has(project.id)) return;
    creatingRef.current.add(project.id);
    try {
      const s = await api.createCoworkSession({ type: "agent", project_id: project.id });
      setConversationsByProject((prev) => ({
        ...prev,
        [project.id]: [s, ...(prev[project.id] ?? [])],
      }));
      setSelected(project);
      expandProject(project.id);
      setActiveConversationId(s.id);
    } catch {
      /* 创建失败:静默,用户可重试。 */
    } finally {
      creatingRef.current.delete(project.id);
    }
  }, [expandProject]);

  // Workspace 空态的「新建对话」便捷封装。
  const handleCreateForSelected = useCallback(() => {
    if (selected) void handleCreateConversation(selected);
  }, [selected, handleCreateConversation]);

  // 助手回答完成 / 首轮自动标题(session_renamed)后,标题与状态可能已变:刷新当前项目会话列表。
  const refreshConversations = useCallback(() => {
    if (selected) loadConversations(selected.id, true);
  }, [selected, loadConversations]);

  // 流式 session_state 事件:就地更新缓存里该会话的状态(对话头徽标即时刷新,不等下次列表拉取)。
  const handleSessionState = useCallback((sid: number, status: string) => {
    setConversationsByProject((prev) => {
      for (const pid of Object.keys(prev)) {
        const list = prev[Number(pid)];
        if (list.some((s) => s.id === sid)) {
          return {
            ...prev,
            [Number(pid)]: list.map((s) => (s.id === sid ? { ...s, status } : s)),
          };
        }
      }
      return prev;
    });
  }, []);

  // 删除对话:调后端 → 从缓存移除;若删的是当前选中会话,清空主区回到项目态。
  const handleDeleteConversation = useCallback(
    async (conversation: CoworkSession) => {
      try {
        await api.deleteCoworkSession(conversation.id);
      } catch {
        /* 删除失败(非 404):静默,用户可重试。 */
        return;
      }
      setConversationsByProject((prev) => {
        const list = conversation.project_id != null ? prev[conversation.project_id] : undefined;
        if (!list || conversation.project_id == null) return prev;
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

  // 重命名对话(cowork 会话):乐观更新该项目的会话缓存,失败回滚到原标题。
  const handleRenameConversation = useCallback(
    async (conversation: CoworkSession, title: string) => {
      const next = title.trim();
      const prevName = sessionTitle(conversation);
      if (!next || next === prevName) return;
      const pid = conversation.project_id;
      if (pid == null) return;
      const patch = (name: string) =>
        setConversationsByProject((prev) => {
          const list = prev[pid];
          if (!list) return prev;
          return {
            ...prev,
            [pid]: list.map((c) => (c.id === conversation.id ? { ...c, name } : c)),
          };
        });
      patch(next);
      try {
        await api.updateCoworkSession(conversation.id, { name: next });
      } catch {
        patch(prevName);
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
    // 删除成功:从列表移除;清理其展开态与会话缓存;
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
      const rest = { ...prev };
      delete rest[deleted.id];
      return rest;
    });
    // 程序化回退选中时也同步展开,与点选行为一致。
    if (selected?.id === deleted.id && next[0]) expandProject(next[0].id);
    setSelected((cur) => {
      if (cur && cur.id === deleted.id) {
        setActiveConversationId(null);
        return next[0] ?? null;
      }
      return cur;
    });
  };

  // 当前选中会话对象:取自缓存(选中会话时其项目即 selected,缓存已加载)。
  const activeConversation =
    selected != null && activeConversationId != null
      ? (conversationsByProject[selected.id]?.find((c) => c.id === activeConversationId) ?? null)
      : null;

  return (
    <div className="flex h-[calc(100vh-3.5rem)]">
      <ProjectSidebar
        projects={projects}
        selectedProjectId={selected?.id ?? null}
        selectedConversationId={activeConversationId}
        expandedIds={expandedIds}
        conversationsByProject={conversationsByProject}
        loadingProjectIds={loadingProjectIds}
        onSelectProject={handleSelectProject}
        onToggleExpand={handleToggleExpand}
        onSelectConversation={handleSelectConversation}
        onCreateConversation={(p) => void handleCreateConversation(p)}
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
            activeConversation={activeConversation}
            onCreateConversation={handleCreateForSelected}
            onSessionState={handleSessionState}
            onConversationActivity={refreshConversations}
            onJumpToSession={onJumpToSession}
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
  conversation: CoworkSession | null;
  onClose: () => void;
  /** 用户确认后调用;父级负责实际删除与列表/选中态清理。 */
  onConfirm: (conversation: CoworkSession) => Promise<void>;
}) {
  return (
    <Dialog open={conversation !== null} onOpenChange={(o) => !o && onClose()}>
      {/* key=会话 id:每条待删会话重挂载,busy 瞬态自然归零,无需重置 effect。 */}
      {conversation && (
        <DeleteConversationBody
          key={conversation.id}
          conversation={conversation}
          onClose={onClose}
          onConfirm={onConfirm}
        />
      )}
    </Dialog>
  );
}

function DeleteConversationBody({
  conversation,
  onClose,
  onConfirm,
}: {
  conversation: CoworkSession;
  onClose: () => void;
  onConfirm: (conversation: CoworkSession) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);

  const confirm = async () => {
    setBusy(true);
    await onConfirm(conversation);
    setBusy(false);
    onClose();
  };

  return (
    // busy(删除中)时拦截 ESC/遮罩关闭,与旧行为一致。
    <DialogContent
      showCloseButton={!busy}
      className="gap-0 p-0"
      onEscapeKeyDown={(e) => busy && e.preventDefault()}
      onPointerDownOutside={(e) => busy && e.preventDefault()}
    >
      <DialogHeader className="gap-2 px-6 pt-6">
        <span
          aria-hidden
          className="flex size-9 items-center justify-center rounded-xl bg-destructive/10 text-destructive ring-1 ring-destructive/15"
        >
          <TriangleAlert className="size-[18px]" strokeWidth={2} />
        </span>
        <DialogTitle>删除对话「{sessionTitle(conversation)}」?</DialogTitle>
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
  );
}

function Workspace({
  project,
  activeConversation,
  onCreateConversation,
  onSessionState,
  onConversationActivity,
  onJumpToSession,
}: {
  project: Project;
  /** 当前选中的 cowork 会话;null 时显示项目空态 + 「新建对话」。 */
  activeConversation: CoworkSession | null;
  onCreateConversation: () => void;
  onSessionState: (sid: number, status: string) => void;
  onConversationActivity: () => void;
  /** 引用「跳回会话时刻」:透传给对话区内的 SourceViewer。 */
  onJumpToSession: (sessionId: number, ts: string) => void;
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

        {/* 对话主体:选中会话渲染共享 CoworkConversation(按会话 key 重挂载,切换即中止在途流) */}
        {activeConversation ? (
          <CoworkConversation
            key={activeConversation.id}
            session={activeConversation}
            onSessionState={onSessionState}
            onActivity={onConversationActivity}
            onSessionRenamed={onConversationActivity}
            onJumpToSession={onJumpToSession}
          />
        ) : (
          <CenteredEmpty
            title={`与「${project.title}」对话`}
            body="为项目文件建立索引后,即可在此基于你的资料提问,并跳回答案引用的原始来源。"
            actionLabel="新建对话"
            onAction={onCreateConversation}
          />
        )}
      </div>
    </div>
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
