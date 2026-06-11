import { MessageSquarePlus, MessagesSquare } from "lucide-react";

import { type Conversation } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 侧栏的会话历史:列出当前项目的会话,支持新建 / 选择。
 * 受控组件——数据与「新建」动作由父级(view)持有,便于与发送流程联动。
 */
export function ConversationList({
  conversations,
  selectedId,
  loading,
  onSelect,
  onCreate,
}: {
  conversations: Conversation[];
  selectedId: number | null;
  loading: boolean;
  onSelect: (conversation: Conversation) => void;
  onCreate: () => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col border-t border-border/70">
      <div className="flex items-center justify-between px-4 pt-3 pb-1.5">
        <h3 className="text-xs font-semibold tracking-wide text-muted-foreground">对话</h3>
        <button
          type="button"
          onClick={onCreate}
          aria-label="新建对话"
          title="新建对话"
          className="flex size-6 items-center justify-center rounded-md text-muted-foreground outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <MessageSquarePlus className="size-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {loading ? (
          <ul className="flex flex-col gap-0.5" aria-hidden>
            {[0, 1, 2].map((i) => (
              <li key={i} className="px-2.5 py-2">
                <span className="block h-3.5 w-2/3 animate-pulse rounded bg-muted" />
              </li>
            ))}
          </ul>
        ) : conversations.length === 0 ? (
          <button
            type="button"
            onClick={onCreate}
            className="flex w-full flex-col items-start gap-1 rounded-lg px-2.5 py-3 text-left outline-none transition-colors hover:bg-background/60 focus-visible:ring-2 focus-visible:ring-ring/50"
          >
            <span className="inline-flex items-center gap-1.5 text-sm font-medium text-foreground">
              <MessageSquarePlus className="size-4" />
              开始第一个对话
            </span>
            <span className="text-xs leading-relaxed text-muted-foreground">
              基于本项目已索引的资料提问。
            </span>
          </button>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {conversations.map((c) => {
              const active = c.id === selectedId;
              return (
                <li key={c.id}>
                  <button
                    type="button"
                    aria-current={active ? "true" : undefined}
                    onClick={() => onSelect(c)}
                    className={cn(
                      "group flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm outline-none transition-colors",
                      "focus-visible:ring-2 focus-visible:ring-ring/50",
                      active
                        ? "bg-background text-foreground shadow-sm ring-1 ring-border/70"
                        : "text-muted-foreground hover:bg-background/60 hover:text-foreground",
                    )}
                  >
                    <MessagesSquare
                      className={cn(
                        "size-3.5 shrink-0 transition-colors",
                        active
                          ? "text-foreground"
                          : "text-muted-foreground group-hover:text-foreground",
                      )}
                      strokeWidth={active ? 2.25 : 2}
                    />
                    <span className="truncate">{c.title}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
