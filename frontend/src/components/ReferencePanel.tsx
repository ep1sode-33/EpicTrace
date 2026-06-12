import { FileText, Paperclip, X } from "lucide-react";

import { type ConversationReference } from "@/lib/api";
import { cn } from "@/lib/utils";

const MODE_LABEL: Record<ConversationReference["mode"], string> = {
  fulltext: "全文已载入",
  focus: "已索引聚焦",
  indexed: "已索引检索",
  deferred: "未能索引",
};

/**
 * 「本对话引用」侧栏内容:两栏(外部已引用/库内已引用),每条带模式标签 + 解挂。
 * 作为右侧引用侧栏的纵向内容渲染(无折叠外壳/无 max-w 居中)。空态给出拖放/添加提示。
 * 「从项目添加」入口已移至下方独立的「库内文件」区(ProjectFilesZone)。
 */
export function ReferencePanel({
  references,
  onDetach,
}: {
  references: ConversationReference[];
  onDetach: (rid: number) => void;
}) {
  const external = references.filter((r) => r.kind === "external");
  const internal = references.filter((r) => r.kind === "internal");

  return (
    <div className="flex flex-col gap-3">
      {references.length === 0 && (
        <p className="px-1 text-xs leading-relaxed text-muted-foreground/70">
          拖文件进来,或从下方「库内文件」添加引用。
        </p>
      )}
      <Zone title="外部已引用" icon={<Paperclip className="size-3" />}
            rows={external} onDetach={onDetach} />
      <Zone title="库内已引用" icon={<FileText className="size-3" />}
            rows={internal} onDetach={onDetach} />
    </div>
  );
}

function Zone({
  title, icon, rows, onDetach,
}: {
  title: string;
  icon: React.ReactNode;
  rows: ConversationReference[];
  onDetach: (rid: number) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1 text-[0.7rem] font-medium uppercase tracking-wide text-muted-foreground/80">
          {icon} {title}
        </span>
      </div>
      {rows.length === 0 ? (
        <p className="px-1 text-xs text-muted-foreground/60">无</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {rows.map((r) => (
            <li key={r.id}
                className="flex items-center gap-2 rounded-lg border border-border/60 bg-background px-2.5 py-1.5">
              <FileText className="size-3.5 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate text-xs text-foreground" title={r.display_name}>
                {r.display_name}
              </span>
              <span className={cn(
                "shrink-0 rounded px-1.5 py-0.5 text-[0.65rem] font-medium",
                r.mode === "deferred" ? "bg-amber-500/15 text-amber-700" : "bg-muted text-muted-foreground",
              )}>
                {MODE_LABEL[r.mode]}
              </span>
              <button type="button" onClick={() => onDetach(r.id)} aria-label="解挂"
                      className="shrink-0 rounded p-0.5 text-muted-foreground outline-none hover:bg-muted hover:text-foreground">
                <X className="size-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
