import { Fragment, useEffect, useRef } from "react";
import { AlertCircle, Loader2 } from "lucide-react";

import { type Citation } from "@/lib/api";
import { cn } from "@/lib/utils";

/** 视图层的消息模型:已落库的消息 citations 已解析为数组;流式中的助手消息 streaming=true。 */
export interface ViewMessage {
  id: number | string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  /** 该助手消息正在流式生成(用于呈现光标/状态而非引用)。 */
  streaming?: boolean;
  /** 流式出错时的提示文案;有值则该条助手消息以安静的错误通知呈现(替代/附在正文之后)。 */
  error?: string;
}

export function MessageList({
  messages,
  status,
  onCitation,
}: {
  messages: ViewMessage[];
  /** 流式状态文案(如「检索中」「生成中」);为空表示无进行中的检索/生成。 */
  status: string | null;
  /** 点击某条引用 chip 时回调,打开来源查看器。 */
  onCitation: (citation: Citation) => void;
}) {
  const endRef = useRef<HTMLDivElement | null>(null);

  // 新消息 / 流式 token / 状态变化时贴底滚动,保持最新内容可见。
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, status]);

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-6 py-8">
      {messages.map((m) => (
        <MessageRow key={m.id} message={m} status={status} onCitation={onCitation} />
      ))}
      <div ref={endRef} />
    </div>
  );
}

function MessageRow({
  message,
  status,
  onCitation,
}: {
  message: ViewMessage;
  status: string | null;
  onCitation: (citation: Citation) => void;
}) {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-3.5 py-2.5 text-sm leading-relaxed whitespace-pre-wrap break-words text-primary-foreground">
          {message.content}
        </div>
      </div>
    );
  }

  // 助手消息:左对齐纯文本流,无气泡(Codex/ChatGPT 式),内联引用 chip。
  // 出错时不再显示「检索中/生成中」状态(否则与错误并存读作仍在进行)。
  const showStatus = message.streaming && status && !message.error;
  const showCursor = message.streaming && message.content.length > 0;
  return (
    <div className="flex flex-col gap-2">
      {showStatus && (
        <div
          role="status"
          aria-live="polite"
          className="inline-flex w-fit items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground"
        >
          <Loader2 className="size-3 animate-spin" aria-hidden />
          {status}…
        </div>
      )}
      {message.content.length > 0 && (
        <div className="text-sm leading-relaxed whitespace-pre-wrap break-words text-foreground">
          <AssistantContent
            content={message.content}
            citations={message.citations}
            onCitation={onCitation}
          />
          {showCursor && (
            <span
              aria-hidden
              className="ml-0.5 inline-block h-4 w-px translate-y-0.5 animate-pulse bg-foreground/60 align-middle"
            />
          )}
        </div>
      )}
      {message.error && <ErrorNotice message={message.error} />}
    </div>
  );
}

/**
 * 流式出错时的安静内联通知(左对齐,贴合 assistant 一侧)。
 * 复用全项目一致的 destructive 轻量样式(border-destructive/20 + bg-destructive/5 + text-destructive),
 * 不喧哗、不阻断会话——告知出错并引导去检查设置。
 */
function ErrorNotice({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="flex w-fit max-w-full items-start gap-2 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive"
    >
      <AlertCircle className="mt-px size-3.5 shrink-0" strokeWidth={2} aria-hidden />
      <span className="break-words">对话出错:{message},请检查设置。</span>
    </div>
  );
}

// 匹配答案中的引用标记 [n](1+ 位数字)。
const CITE_RE = /\[(\d+)\]/g;

/**
 * 把助手文本里的 `[n]` 替换为可点的引用 chip;其余按纯文本渲染。
 * citations 为按 n 索引的查找表;流式途中可能还没有 citations,此时 `[n]` 退化为普通文本 chip(禁用)。
 */
function AssistantContent({
  content,
  citations,
  onCitation,
}: {
  content: string;
  citations: Citation[];
  onCitation: (citation: Citation) => void;
}) {
  const byN = new Map(citations.map((c) => [c.n, c]));
  const parts: React.ReactNode[] = [];
  let last = 0;
  let key = 0;

  for (const match of content.matchAll(CITE_RE)) {
    const idx = match.index ?? 0;
    if (idx > last) parts.push(<Fragment key={key++}>{content.slice(last, idx)}</Fragment>);
    const n = Number(match[1]);
    const cite = byN.get(n);
    parts.push(
      <CitationChip key={key++} n={n} citation={cite} onCitation={onCitation} />,
    );
    last = idx + match[0].length;
  }
  if (last < content.length) parts.push(<Fragment key={key++}>{content.slice(last)}</Fragment>);

  return <>{parts}</>;
}

function CitationChip({
  n,
  citation,
  onCitation,
}: {
  n: number;
  citation: Citation | undefined;
  onCitation: (citation: Citation) => void;
}) {
  // 未拿到对应引用元数据(尚在流式)时,渲染为不可点的占位标记。
  if (!citation) {
    return (
      <sup className="mx-0.5 rounded bg-muted px-1 text-[0.7em] font-medium text-muted-foreground tabular-nums">
        {n}
      </sup>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onCitation(citation)}
      title={citation.snippet}
      className={cn(
        "mx-0.5 inline-flex translate-y-[-1px] items-center rounded bg-primary/10 px-1 align-baseline",
        "text-[0.7em] font-semibold text-primary tabular-nums leading-none",
        "outline-none transition-colors hover:bg-primary/20 focus-visible:ring-2 focus-visible:ring-ring/50",
      )}
      aria-label={`查看来源 ${n}`}
    >
      {n}
    </button>
  );
}
