import { useEffect, useState } from "react";
import {
  FolderGit2,
  FolderPlus,
  Loader2,
  PanelRight,
  Sparkles,
  SendHorizontal,
  X,
} from "lucide-react";

import { api, type IngestRecord, type Project } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { CreateProjectModal } from "@/components/CreateProjectModal";
import { DeleteProjectDialog } from "@/components/DeleteProjectDialog";
import { FileList } from "@/components/FileList";
import { ProjectSidebar } from "@/components/ProjectSidebar";

export function ProjectsConversationView() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<Project | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  // 待删除确认的项目;为 null 时确认对话框关闭。
  const [pendingDelete, setPendingDelete] = useState<Project | null>(null);
  // 创建后的自动扫描完成时自增,触发当前项目文件列表重新拉取(扫描晚于 onCreated)。
  const [scanTick, setScanTick] = useState(0);
  // 刚创建项目的自动扫描在途时为 true(扫描异步,完成晚于 onCreated)。
  const [scanning, setScanning] = useState(false);

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

  const handleCreated = async (project: Project) => {
    // 重新拉取权威列表,避免较慢的初始 listProjects 响应覆盖乐观插入的新项目;
    // 随后按 id 选中新项目。
    try {
      const rows = await api.listProjects();
      setProjects(rows);
      setSelected(rows.find((p) => p.id === project.id) ?? project);
    } catch {
      // 列表刷新失败时退回乐观插入,至少保证新项目可见且被选中。
      setProjects((prev) => [project, ...prev]);
      setSelected(project);
    }
  };

  const handleDeleted = (deleted: Project) => {
    // 删除成功:从列表移除;若删的是当前选中项,退回首个剩余项目(没有则清空)。
    const next = projects.filter((p) => p.id !== deleted.id);
    setProjects(next);
    setSelected((cur) =>
      cur && cur.id === deleted.id ? (next[0] ?? null) : cur,
    );
  };

  return (
    <div className="flex h-[calc(100vh-3.5rem)]">
      <ProjectSidebar
        projects={projects}
        selectedId={selected?.id ?? null}
        onSelect={setSelected}
        onCreate={() => setCreateOpen(true)}
        onDelete={setPendingDelete}
      />

      <section className="flex min-w-0 flex-1 flex-col">
        {selected ? (
          <Workspace
            key={selected.id}
            project={selected}
            refreshKey={scanTick}
            scanning={scanning}
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
}: {
  project: Project;
  refreshKey: number;
  scanning: boolean;
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

        {/* 对话主体:占据主纵向空间,居中空态 + 底部醒目输入 */}
        <Conversation projectTitle={project.title} />
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

/**
 * 对话主体——本工作区的中心元素(Codex/ChatGPT 式)。
 * 当前为占位:空态居中,底部为醒目但禁用的输入。真正的对话见后续计划。
 */
function Conversation({ projectTitle }: { projectTitle: string }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 居中空态:占据主纵向空间 */}
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-6 text-center">
        <span
          aria-hidden
          className="flex size-12 items-center justify-center rounded-2xl bg-muted text-foreground ring-1 ring-border/70"
        >
          <Sparkles className="size-5" strokeWidth={1.75} />
        </span>
        <h2 className="mt-5 text-lg font-semibold tracking-tight text-balance text-foreground">
          与「{projectTitle}」对话
        </h2>
        <p className="mt-2 max-w-md text-sm leading-relaxed text-pretty text-muted-foreground">
          为项目文件建立索引后,即可在此基于你的资料提问,
          并跳回答案引用的原始来源。
        </p>
      </div>

      {/* 底部输入:页面的主操作位,醒目但当前禁用(开发中) */}
      <div className="shrink-0 px-6 pb-7">
        <div className="mx-auto w-full max-w-2xl">
          <div className="flex items-end gap-2 rounded-2xl border border-border bg-background p-2 shadow-sm">
            <textarea
              rows={1}
              disabled
              placeholder="对话功能开发中(需先建立索引)"
              aria-label="对话输入(开发中)"
              className="max-h-40 min-h-9 w-full flex-1 resize-none cursor-not-allowed bg-transparent px-2.5 py-2 text-sm text-foreground outline-none placeholder:text-muted-foreground"
            />
            <Button
              type="button"
              size="icon"
              disabled
              aria-label="发送(开发中)"
              className="mb-px"
            >
              <SendHorizontal className="size-4" />
            </Button>
          </div>
          <p className="mt-2 text-center text-xs text-muted-foreground">
            对话功能开发中——需先在「信息处理和入库」为项目建立索引。
          </p>
        </div>
      </div>
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
