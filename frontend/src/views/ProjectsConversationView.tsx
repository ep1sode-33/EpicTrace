import { useEffect, useState } from "react";
import { FolderGit2, MessagesSquare, SendHorizontal, Sparkles } from "lucide-react";

import { api, type Project } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { CreateProjectModal } from "@/components/CreateProjectModal";
import { FileList } from "@/components/FileList";
import { ProjectSidebar } from "@/components/ProjectSidebar";

export function ProjectsConversationView() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<Project | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

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

  return (
    <div className="flex h-[calc(100vh-3.5rem)]">
      <ProjectSidebar
        projects={projects}
        selectedId={selected?.id ?? null}
        onSelect={setSelected}
        onCreate={() => setCreateOpen(true)}
      />

      <section className="flex min-w-0 flex-1 flex-col">
        {selected ? (
          <Workspace key={selected.id} project={selected} />
        ) : (
          <EmptyState onCreate={() => setCreateOpen(true)} />
        )}
      </section>

      <CreateProjectModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={handleCreated}
      />
    </div>
  );
}

function Workspace({ project }: { project: Project }) {
  return (
    <div className="flex h-full flex-col">
      {/* Workspace header */}
      <header className="shrink-0 border-b border-border/70 px-8 py-5">
        <h1 className="text-xl font-semibold tracking-tight text-foreground">
          {project.title}
        </h1>
        <div className="mt-1.5 flex items-center gap-1.5 text-xs text-muted-foreground">
          <FolderGit2 className="size-3.5 shrink-0" strokeWidth={1.75} />
          <span className="truncate font-mono" title={project.folder_path}>
            {project.folder_path}
          </span>
        </div>
      </header>

      {/* Scrollable body: files + conversation entry */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-8 py-7">
          <section className="flex flex-col gap-3">
            <h2 className="text-sm font-medium text-foreground">项目文件</h2>
            <FileList projectId={project.id} />
          </section>

          <ChatPlaceholder />
        </div>
      </div>
    </div>
  );
}

function ChatPlaceholder() {
  return (
    <section className="flex flex-col items-center gap-4 rounded-2xl border border-dashed border-border/70 bg-muted/15 px-6 py-10">
      <span
        aria-hidden
        className="flex size-11 items-center justify-center rounded-2xl bg-background text-muted-foreground ring-1 ring-border/70"
      >
        <MessagesSquare className="size-5" strokeWidth={1.75} />
      </span>
      <div className="text-center">
        <p className="text-sm font-medium text-foreground">
          对话功能开发中(需先建立索引)
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          为项目文件建立索引后,即可在此基于资料对话。
        </p>
      </div>

      <div className="mt-1 flex w-full max-w-xl items-center gap-2 rounded-xl border border-border/70 bg-background/70 px-3 py-2 opacity-70">
        <input
          type="text"
          disabled
          placeholder="向项目提问…"
          aria-label="对话输入(开发中)"
          className="h-7 min-w-0 flex-1 cursor-not-allowed bg-transparent text-sm text-muted-foreground outline-none placeholder:text-muted-foreground/60"
        />
        <Button type="button" size="icon-sm" disabled aria-label="发送(开发中)">
          <SendHorizontal className="size-4" />
        </Button>
      </div>
    </section>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-8 text-center">
      <span
        aria-hidden
        className="flex size-14 items-center justify-center rounded-2xl bg-muted text-foreground ring-1 ring-border/70"
      >
        <Sparkles className="size-6" strokeWidth={1.75} />
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
