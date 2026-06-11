import { useCallback, useEffect, useRef, useState } from "react";
import {
  FolderGit2,
  FolderPlus,
  Loader2,
  PanelRight,
  Sparkles,
  X,
} from "lucide-react";

import {
  api,
  type Citation,
  type Conversation,
  type IngestRecord,
  type Project,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { CreateProjectModal } from "@/components/CreateProjectModal";
import { DeleteProjectDialog } from "@/components/DeleteProjectDialog";
import { FileList } from "@/components/FileList";
import { ProjectSidebar } from "@/components/ProjectSidebar";
import { Composer } from "@/components/Composer";
import { MessageList, type ViewMessage } from "@/components/MessageList";
import { SourceViewer } from "@/components/SourceViewer";

export function ProjectsConversationView({
  llmConfigured,
  onOpenSettings,
}: {
  llmConfigured: boolean;
  onOpenSettings: () => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<Project | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  // 待删除确认的项目;为 null 时确认对话框关闭。
  const [pendingDelete, setPendingDelete] = useState<Project | null>(null);
  // 创建后的自动扫描完成时自增,触发当前项目文件列表重新拉取(扫描晚于 onCreated)。
  const [scanTick, setScanTick] = useState(0);
  // 刚创建项目的自动扫描在途时为 true(扫描异步,完成晚于 onCreated)。
  const [scanning, setScanning] = useState(false);

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
  const handleSelectProject = useCallback(
    (project: Project) => {
      setSelected(project);
      setActiveConversationId(null);
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
    },
    [projects],
  );

  // 新建对话(树上的每项目 +):创建 → 选中该项目 → 缓存前插 → 选中新会话 → 确保展开。
  const handleCreateConversation = useCallback(
    async (project: Project) => {
      setSelected(project);
      setExpandedIds((prev) => {
        if (prev.has(project.id)) return prev;
        const next = new Set(prev);
        next.add(project.id);
        return next;
      });
      try {
        const c = await api.createConversation(project.id);
        setConversationsByProject((prev) => ({
          ...prev,
          [project.id]: [c, ...(prev[project.id] ?? [])],
        }));
        setActiveConversationId(c.id);
      } catch {
        /* 新建失败:静默,用户可重试。 */
      }
    },
    [],
  );

  // Workspace 内空态/Composer 用的「为当前项目新建对话」便捷封装。
  const handleCreateConversationForSelected = useCallback(() => {
    if (selected) handleCreateConversation(selected);
  }, [selected, handleCreateConversation]);

  // 助手回答完成后,标题可能已更新(后端按首条消息),刷新当前项目对话列表保持侧栏标题同步。
  const refreshConversations = useCallback(() => {
    if (selected) loadConversations(selected.id, true);
  }, [selected, loadConversations]);

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
        expandedIds={expandedIds}
        conversationsByProject={conversationsByProject}
        loadingProjectIds={loadingProjectIds}
        onSelectProject={handleSelectProject}
        onToggleExpand={handleToggleExpand}
        onSelectConversation={handleSelectConversation}
        onCreateConversation={handleCreateConversation}
        onCreateProject={() => setCreateOpen(true)}
        onDeleteProject={setPendingDelete}
      />

      <section className="flex min-w-0 flex-1 flex-col">
        {selected ? (
          <Workspace
            key={selected.id}
            project={selected}
            refreshKey={scanTick}
            scanning={scanning}
            llmConfigured={llmConfigured}
            onOpenSettings={onOpenSettings}
            activeConversationId={activeConversationId}
            onCreateConversation={handleCreateConversationForSelected}
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
        onScanStart={() => setScanning(true)}
        onScanComplete={() => {
          setScanning(false);
          setScanTick((t) => t + 1);
        }}
        onScanError={() => setScanning(false)}
      />

      <DeleteProjectDialog
        project={pendingDelete}
        onClose={() => setPendingDelete(null)}
        onDeleted={handleDeleted}
      />
    </div>
  );
}

function Workspace({
  project,
  refreshKey,
  scanning,
  llmConfigured,
  onOpenSettings,
  activeConversationId,
  onCreateConversation,
  onConversationActivity,
}: {
  project: Project;
  refreshKey: number;
  scanning: boolean;
  llmConfigured: boolean;
  onOpenSettings: () => void;
  activeConversationId: number | null;
  onCreateConversation: () => void;
  onConversationActivity: () => void;
}) {
  // 来源(文件)抽屉默认关闭——对话是中心,文件是安静的次要资料。
  const [sourcesOpen, setSourcesOpen] = useState(false);
  // 头部紧凑摘要用的文件计数;FileList 自己也会拉一次,但本视图需要独立的轻量计数。
  const [counts, setCounts] = useState<{ total: number; indexed: number } | null>(
    null,
  );

  useEffect(() => {
    let cancelled = false;
    setCounts(null);
    api
      .listFiles(project.id)
      .then((rows: IngestRecord[]) => {
        if (cancelled) return;
        setCounts({
          total: rows.length,
          indexed: rows.filter((f) => f.indexed).length,
        });
      })
      .catch(() => {
        /* 计数拉取失败时静默:抽屉里的 FileList 仍会展示完整错误。 */
      });
    return () => {
      cancelled = true;
    };
  }, [project.id, refreshKey]);

  return (
    <div className="relative flex h-full min-w-0 flex-1">
      {/* 主区:对话为中心 */}
      <div className="flex h-full min-w-0 flex-1 flex-col">
        {/* Workspace header:标题 + 路径,右侧是安静的「来源」摘要/开关 */}
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

          <SourcesToggle
            counts={counts}
            scanning={scanning}
            open={sourcesOpen}
            onToggle={() => setSourcesOpen((v) => !v)}
          />
        </header>

        {/* 对话主体:占据主纵向空间 */}
        <Conversation
          projectTitle={project.title}
          conversationId={activeConversationId}
          llmConfigured={llmConfigured}
          onOpenSettings={onOpenSettings}
          onCreateConversation={onCreateConversation}
          onConversationActivity={onConversationActivity}
        />
      </div>

      {/* 来源抽屉:默认关闭;开启时贴主区右侧,复用现有 FileList */}
      {sourcesOpen && (
        <SourcesDrawer
          projectId={project.id}
          refreshKey={refreshKey}
          scanning={scanning}
          onClose={() => setSourcesOpen(false)}
        />
      )}
    </div>
  );
}

/**
 * 头部的「来源」开关:把文件清单降级为一行紧凑摘要 + 一个安静的按钮。
 * 文本形如「N 个文件 · M 已索引」,几乎不占注意力。
 */
function SourcesToggle({
  counts,
  scanning,
  open,
  onToggle,
}: {
  counts: { total: number; indexed: number } | null;
  scanning: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={onToggle}
      aria-expanded={open}
      aria-pressed={open}
      className="shrink-0 gap-2 text-muted-foreground"
    >
      <PanelRight className="size-3.5" strokeWidth={1.75} aria-hidden />
      <span className="font-medium text-foreground">来源</span>
      <span aria-hidden className="text-border">·</span>
      {scanning ? (
        <span className="inline-flex items-center gap-1 tabular-nums">
          <Loader2 className="size-3 animate-spin" aria-hidden />
          扫描中
        </span>
      ) : counts ? (
        <span className="tabular-nums">
          {counts.total} 个文件 · {counts.indexed} 已索引
        </span>
      ) : (
        <span className="tabular-nums text-muted-foreground/70">…</span>
      )}
    </Button>
  );
}

/**
 * 来源抽屉:贴在工作区右侧的次要面板,默认关闭。
 * 内容直接复用 FileList(已带 已索引/待索引 徽章),不做改动。
 */
function SourcesDrawer({
  projectId,
  refreshKey,
  scanning,
  onClose,
}: {
  projectId: number;
  refreshKey: number;
  scanning: boolean;
  onClose: () => void;
}) {
  return (
    <aside
      aria-label="项目来源文件"
      className="flex h-full w-80 shrink-0 flex-col border-l border-border/70 bg-sidebar"
    >
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border/70 px-4 py-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-medium text-foreground">来源</h2>
          {scanning && (
            <span
              className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"
              role="status"
              aria-live="polite"
            >
              <Loader2 className="size-3.5 animate-spin" />
              正在扫描…
            </span>
          )}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          onClick={onClose}
          aria-label="关闭来源面板"
          className="text-muted-foreground"
        >
          <X className="size-4" />
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <FileList projectId={projectId} refreshKey={refreshKey} />
      </div>
    </aside>
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
  projectTitle,
  conversationId,
  llmConfigured,
  onOpenSettings,
  onCreateConversation,
  onConversationActivity,
}: {
  projectTitle: string;
  conversationId: number | null;
  llmConfigured: boolean;
  onOpenSettings: () => void;
  onCreateConversation: () => void;
  onConversationActivity: () => void;
}) {
  const [messages, setMessages] = useState<ViewMessage[]>([]);
  const [loading, setLoading] = useState(false);
  // 流式状态文案(检索中/生成中);null 表示无进行中的请求。
  const [status, setStatus] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  // 点开来源查看器的当前引用;null 时关闭。
  const [viewing, setViewing] = useState<Citation | null>(null);
  // 当前流的 abort 句柄(切换会话 / 停止 / 卸载时调用)。
  const abortRef = useRef<(() => void) | null>(null);

  // 切换会话:中断在途流、拉取历史消息。
  useEffect(() => {
    abortRef.current?.();
    abortRef.current = null;
    setStreaming(false);
    setStatus(null);
    setMessages([]);
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
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  // 组件卸载时中断在途流,避免回调写入已卸载组件。
  useEffect(() => () => abortRef.current?.(), []);

  const send = useCallback(
    (content: string) => {
      if (conversationId == null || streaming) return;
      const assistantId = `assistant-${Date.now()}`;
      // 乐观插入用户消息 + 一条空的流式助手消息。
      setMessages((prev) => [
        ...prev,
        { id: `user-${Date.now()}`, role: "user", content, citations: [] },
        { id: assistantId, role: "assistant", content: "", citations: [], streaming: true },
      ]);
      setStreaming(true);
      setStatus("检索中");

      const patch = (fn: (m: ViewMessage) => ViewMessage) =>
        setMessages((prev) => prev.map((m) => (m.id === assistantId ? fn(m) : m)));

      abortRef.current = api.sendMessage(conversationId, content, {
        onStatus: (s) => setStatus(s),
        onToken: (t) => patch((m) => ({ ...m, content: m.content + t })),
        onCitations: (c) => patch((m) => ({ ...m, citations: c })),
        onDone: () => {
          patch((m) => ({ ...m, streaming: false }));
          setStreaming(false);
          setStatus(null);
          abortRef.current = null;
          onConversationActivity();
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
    [conversationId, streaming, onConversationActivity],
  );

  const stop = useCallback(() => {
    abortRef.current?.();
    abortRef.current = null;
    setStreaming(false);
    setStatus(null);
    setMessages((prev) =>
      prev.map((m) => (m.streaming ? { ...m, streaming: false } : m)),
    );
  }, []);

  const hasMessages = messages.length > 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        {conversationId == null ? (
          <CenteredEmpty
            title={`与「${projectTitle}」对话`}
            body="为项目文件建立索引后,即可在此基于你的资料提问,并跳回答案引用的原始来源。"
            actionLabel="新建对话"
            onAction={onCreateConversation}
          />
        ) : loading ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" />
            正在加载对话…
          </div>
        ) : hasMessages ? (
          <MessageList messages={messages} status={status} onCitation={setViewing} />
        ) : (
          <CenteredEmpty
            title={`与「${projectTitle}」对话`}
            body="基于本项目已索引的资料提问。回答会带来源引用,点引用编号可跳回原始片段。"
          />
        )}
      </div>

      <Composer
        llmConfigured={llmConfigured}
        streaming={streaming}
        onSend={send}
        onStop={stop}
        onOpenSettings={onOpenSettings}
      />

      <SourceViewer citation={viewing} onClose={() => setViewing(null)} />
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
