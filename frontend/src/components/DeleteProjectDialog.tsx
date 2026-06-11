import { useEffect, useState } from "react";
import { Loader2, Trash2, TriangleAlert } from "lucide-react";

import { api, type Project } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function DeleteProjectDialog({
  project,
  onClose,
  onDeleted,
}: {
  /** 待删除的项目;为 null 时对话框关闭。 */
  project: Project | null;
  onClose: () => void;
  /** 删除成功后回调,供父级刷新列表 / 清理选中态。 */
  onDeleted: (project: Project) => void;
}) {
  // 重要:默认不勾选——除非用户主动选择,否则绝不删用户磁盘上的真实文件夹。
  const [deleteFolder, setDeleteFolder] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = project !== null;

  // 每次打开都把瞬时状态归零(尤其是 deleteFolder 必须回到未勾选)。
  useEffect(() => {
    if (open) {
      setDeleteFolder(false);
      setBusy(false);
      setError(null);
    }
  }, [open]);

  const confirm = async () => {
    if (!project) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteProject(project.id, deleteFolder);
      onDeleted(project);
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
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
          <DialogTitle>删除项目「{project?.title}」?</DialogTitle>
          <DialogDescription>
            将把该项目从 EpicTrace 移除——其文件登记与索引向量都会被清除。
            此操作不可撤销。
          </DialogDescription>
        </DialogHeader>

        <div className="px-6 py-5">
          {/* 默认不勾:不删盘。勾选后才会一并删除下方磁盘文件夹。 */}
          <label
            className="flex cursor-pointer items-start gap-3 rounded-xl border border-border/70 bg-muted/30 px-3.5 py-3 transition-colors hover:bg-muted/50 has-data-[state=checked]:border-destructive/30 has-data-[state=checked]:bg-destructive/[0.04]"
          >
            <Checkbox
              checked={deleteFolder}
              disabled={busy}
              onCheckedChange={(v) => setDeleteFolder(v === true)}
              className="mt-0.5 data-[state=checked]:border-destructive data-[state=checked]:bg-destructive"
              aria-describedby="delete-folder-path"
            />
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-medium text-foreground">
                同时删除磁盘文件夹
              </span>
              <span
                id="delete-folder-path"
                className="mt-0.5 block truncate font-mono text-xs text-muted-foreground"
                title={project?.folder_path}
              >
                {project?.folder_path}
              </span>
            </span>
          </label>

          {error && (
            <p className="mt-4 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive">
              {error}
            </p>
          )}
        </div>

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
