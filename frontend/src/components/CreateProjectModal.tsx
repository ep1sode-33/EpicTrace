import { useEffect, useState } from "react";
import { FolderOpen, FolderPlus, Loader2 } from "lucide-react";

import { api, type Project } from "@/lib/api";
import { pickFolder } from "@/lib/pickers";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

export function CreateProjectModal({
  open,
  onClose,
  onCreated,
  onScanComplete,
  onScanError,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (project: Project) => void;
  /** 创建后的自动扫描成功完成时调用,用于触发列表刷新(扫描是异步的,完成晚于 onCreated)。 */
  onScanComplete?: (project: Project) => void;
  /** 创建成功后的自动扫描失败时调用(非阻塞告警);创建本身不受影响。 */
  onScanError?: (project: Project, error: unknown) => void;
}) {
  const [title, setTitle] = useState("");
  const [folderPath, setFolderPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset transient state whenever the dialog is (re)opened.
  useEffect(() => {
    if (open) {
      setTitle("");
      setFolderPath("");
      setBusy(false);
      setError(null);
    }
  }, [open]);

  const choose = async () => {
    setError(null);
    const picked = await pickFolder();
    if (picked) {
      setFolderPath(picked);
      // 若标题为空,用文件夹名做一个友好的默认标题。
      if (!title.trim()) {
        const leaf = picked.replace(/[/\\]+$/, "").split(/[/\\]/).pop();
        if (leaf) setTitle(leaf);
      }
    }
  };

  const create = async () => {
    if (!title || !folderPath) return;
    setBusy(true);
    setError(null);
    try {
      // 仅创建步骤会阻塞并影响 UI:失败则报错并保持弹窗打开。
      const p = await api.createProject(title, folderPath); // 接受非空文件夹
      onCreated(p); // 项目立即进入 UI,避免扫描失败时孤立/重复创建
      onClose();
      // 扫描独立于创建、非阻塞:失败不应阻塞或回滚创建。
      api
        .scanProject(p.id)
        .then(() => {
          // 扫描异步完成(晚于 onCreated),通知页面刷新文件/待索引列表。
          onScanComplete?.(p);
        })
        .catch((e) => {
          // 创建已成功且弹窗已关闭,这里只做非阻塞告警。
          onScanError?.(p, e);
          console.warn(`项目「${p.title}」创建后自动扫描失败:`, e);
        });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const canSubmit = Boolean(title.trim() && folderPath) && !busy;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && !busy && onClose()}>
      <DialogContent showCloseButton={!busy} className="gap-0 p-0">
        <DialogHeader className="gap-2 px-6 pt-6">
          <span
            aria-hidden
            className="flex size-9 items-center justify-center rounded-xl bg-muted text-foreground ring-1 ring-border/70"
          >
            <FolderPlus className="size-[18px]" strokeWidth={2} />
          </span>
          <DialogTitle>新建项目</DialogTitle>
          <DialogDescription>
            选择一个本地文件夹作为项目根目录。其中已存在的内容会被就地登记为「待索引」。
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-5 px-6 py-5">
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="project-title"
              className="text-xs font-medium text-muted-foreground"
            >
              项目标题
            </label>
            <Input
              id="project-title"
              autoFocus
              value={title}
              disabled={busy}
              placeholder="例如:虚拟内存研究"
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && canSubmit) create();
              }}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              项目文件夹
            </label>
            <div className="flex items-stretch gap-2">
              <div
                className={cn(
                  "flex h-9 min-w-0 flex-1 items-center rounded-lg border border-input bg-muted/40 px-3",
                  "font-mono text-xs",
                  folderPath ? "text-foreground" : "text-muted-foreground",
                )}
                title={folderPath || undefined}
              >
                <span className="truncate">
                  {folderPath || "尚未选择文件夹"}
                </span>
              </div>
              <Button
                type="button"
                variant="outline"
                size="lg"
                disabled={busy}
                onClick={choose}
              >
                <FolderOpen className="size-4" />
                选择文件夹
              </Button>
            </div>
          </div>

          {error && (
            <p className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive">
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
            size="lg"
            disabled={!canSubmit}
            onClick={create}
          >
            {busy ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                正在创建…
              </>
            ) : (
              "创建项目"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
