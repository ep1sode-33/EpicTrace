import { useEffect, useState } from "react";
import { FolderInput, Loader2, Plus, RefreshCw } from "lucide-react";

import { api, type Project } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { CreateProjectModal } from "@/components/CreateProjectModal";
import { PendingList } from "@/components/PendingList";

export function ProcessIngestView() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [rescanning, setRescanning] = useState(false);
  // 创建后的自动扫描在途时为 true(扫描异步,完成晚于 onCreated);与 rescanning 共用「扫描中」反馈。
  const [scanning, setScanning] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [rescanFailures, setRescanFailures] = useState(0);

  // 创建扫描或重新扫描任一在途,即视为「正在扫描」。
  const isScanning = scanning || rescanning;

  useEffect(() => {
    let cancelled = false;
    api
      .listProjects()
      .then((rows) => {
        if (!cancelled) setProjects(rows);
      })
      .catch(() => {
        /* 保持空态;创建流程仍可用。 */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleCreated = async (project: Project) => {
    // 重新拉取权威列表,避免较慢的初始 listProjects 响应覆盖乐观插入的新项目。
    try {
      const rows = await api.listProjects();
      setProjects(rows);
    } catch {
      // 列表刷新失败时退回乐观插入,至少保证新项目可见。
      setProjects((prev) => [project, ...prev]);
    }
    setRefreshKey((k) => k + 1);
  };

  const rescanAll = async () => {
    if (isScanning || projects.length === 0) return;
    setRescanning(true);
    setRescanFailures(0);
    try {
      const results = await Promise.allSettled(
        projects.map((p) => api.scanProject(p.id)),
      );
      // 无论成败,都刷新待索引列表;成功的项目结果应当立即可见。
      setRefreshKey((k) => k + 1);
      const failed = results.filter((r) => r.status === "rejected").length;
      setRescanFailures(failed);
    } finally {
      setRescanning(false);
    }
  };

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-8 py-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold tracking-tight text-foreground">
          信息处理和入库
        </h1>
        <p className="text-sm text-muted-foreground">
          创建项目、扫描文件夹,把新发现的资料整理进「待索引」队列。
        </p>
      </header>

      {/* Action bar */}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="lg" onClick={() => setCreateOpen(true)}>
          <Plus className="size-4" />
          创建项目
        </Button>

        <Button
          type="button"
          variant="outline"
          size="lg"
          disabled={isScanning || projects.length === 0}
          onClick={rescanAll}
        >
          {isScanning ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <RefreshCw className="size-4" />
          )}
          {isScanning ? "正在扫描…" : "重新扫描"}
        </Button>

        <div className="ml-auto">
          <Tooltip>
            <TooltipTrigger asChild>
              {/* 包一层 span:disabled 按钮本身不触发 hover 事件。 */}
              <span tabIndex={0} className="inline-flex">
                <Button type="button" variant="outline" size="lg" disabled>
                  <FolderInput className="size-4" />
                  外部文件入库
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>整理归类 Agent 开发中(Plan 8)</TooltipContent>
          </Tooltip>
        </div>
      </div>

      {rescanFailures > 0 && (
        <p className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive">
          {rescanFailures} 个项目扫描失败,可稍后重试。
        </p>
      )}

      <section className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-medium text-foreground">待索引</h2>
          {isScanning && (
            <span
              className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"
              role="status"
              aria-live="polite"
            >
              <Loader2 className="size-3.5 animate-spin" />
              正在扫描文件夹…
            </span>
          )}
        </div>
        <PendingList
          projects={projects}
          refreshKey={refreshKey}
          onIndexed={() => setRefreshKey((k) => k + 1)}
        />
      </section>

      <CreateProjectModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={handleCreated}
        onScanStart={() => setScanning(true)}
        onScanComplete={() => {
          setScanning(false);
          setRefreshKey((k) => k + 1);
        }}
        onScanError={() => setScanning(false)}
      />
    </div>
  );
}
