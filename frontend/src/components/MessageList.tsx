import { Fragment, useEffect, useRef, useState } from "react";
import { AlertCircle, Check, Copy, Loader2, RotateCcw } from "lucide-react";

import { type Citation } from "@/lib/api";
import { Button } from "@/components/ui/button";
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
  onRegenerate,
}: {
  messages: ViewMessage[];
  /** 流式状态文案(如「检索中」「生成中」);为空表示无进行中的检索/生成。 */
  status: string | null;
  /** 点击某条引用 chip 时回调,打开来源查看器。 */
  onCitation: (citation: Citation) => void;
  /** 重新生成最后一轮回答;未提供时不显示重试入口。 */
  onRegenerate?: () => void;
}) {
  const endRef = useRef<HTMLDivElement | null>(null);
  // 最后一条助手消息的下标——只有它(以及出错轮次)可重试,旧轮次不显示重试。
  const lastAssistantIdx = messages.reduce(
    (acc, m, i) => (m.role === "assistant" ? i : acc),
    -1,
  );

  // 新消息 / 流式 token / 状态变化时贴底滚动,保持最新内容可见。
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, status]);

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-6 py-8">
      {messages.map((m, i) => (
        <MessageRow
          key={m.id}
          message={m}
          status={status}
          onCitation={onCitation}
          // 重试只挂在最后一条助手消息上;且需有回调、当前不在流式中。
          onRegenerate={
            onRegenerate && i === lastAssistantIdx && !m.streaming
              ? onRegenerate
              : undefined
          }
        />
      ))}
      <div ref={endRef} />
    </div>
  );
}

function MessageRow({
  message,
  status,
  onCitation,
  onRegenerate,
}: {
  message: ViewMessage;
  status: string | null;
  onCitation: (citation: Citation) => void;
  /** 若可重试(最后一条助手消息),传入重新生成回调。 */
  onRegenerate?: () => void;
}) {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      // group/msg + focus-within:悬停或聚焦时显露消息工具条(复制)。
      <div className="group/msg flex flex-col items-end gap-1">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-3.5 py-2.5 text-sm leading-relaxed whitespace-pre-wrap break-words text-primary-foreground">
          {message.content}
        </div>
        <MessageToolbar content={message.content} align="end" />
      </div>
    );
  }

  // 助手消息:左对齐纯文本流,无气泡(Codex/ChatGPT 式),内联引用 chip。
  // 出错时不再显示「检索中/生成中」状态(否则与错误并存读作仍在进行)。
  const showStatus = message.streaming && status && !message.error;
  const showCursor = message.streaming && message.content.length > 0;
  // 工具条仅在不再流式后出现(避免与「生成中」状态/光标并存读作仍在进行)。
  const hasContent = message.content.length > 0;
  const showToolbar = !message.streaming && (hasContent || Boolean(message.error));
  return (
    <div className="group/msg flex flex-col gap-2">
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
      {hasContent && (
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
      {message.error && <ErrorNotice message={message.error} onRetry={onRegenerate} />}
      {showToolbar && (
        <MessageToolbar
          // 出错且无正文时复制按钮无意义,只留重试;有正文则两者都给。
          content={hasContent ? message.content : undefined}
          onRegenerate={onRegenerate}
          align="start"
        />
      )}
    </div>
  );
}

/**
 * 消息级悬停工具条(Codex/ChatGPT 式):默认隐藏,悬停或键盘聚焦该消息时安静淡入。
 * 复制(所有消息)+ 可选重新生成(仅最后一条助手消息)。沿用全项目 ghost icon 按钮语汇,
 * 克制、不与对话争注意力。reduce-motion 下退化为即时显隐(仅过渡 opacity)。
 */
function MessageToolbar({
  content,
  onRegenerate,
  align,
}: {
  /** 可复制的文本;为空(出错无正文)时不渲染复制按钮。 */
  content?: string;
  /** 可重试时的重新生成回调。 */
  onRegenerate?: () => void;
  /** 工具条对齐:user 一侧靠右,assistant 一侧靠左。 */
  align: "start" | "end";
}) {
  if (!content && !onRegenerate) return null;
  return (
    <div
      className={cn(
        "flex items-center gap-0.5 text-muted-foreground",
        // 默认透明、不可交互;悬停消息或工具条内聚焦时淡入并恢复交互。
        "opacity-0 transition-opacity duration-150 [pointer-events:none]",
        "group-hover/msg:opacity-100 group-hover/msg:[pointer-events:auto]",
        "group-focus-within/msg:opacity-100 group-focus-within/msg:[pointer-events:auto]",
        "focus-within:opacity-100 focus-within:[pointer-events:auto]",
        align === "end" && "justify-end",
      )}
    >
      {content !== undefined && <CopyButton content={content} />}
      {onRegenerate && (
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          onClick={onRegenerate}
          aria-label="重新生成"
          title="重新生成"
        >
          <RotateCcw aria-hidden />
        </Button>
      )}
    </div>
  );
}

/** 复制按钮:写入剪贴板后短暂以对勾 + 「已复制」回执确认,随后复位。 */
function CopyButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current); }, []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(content);
    } catch {
      return; // 剪贴板不可用(无权限等):静默,不打断对话。
    }
    setCopied(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setCopied(false), 1500);
  };

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-xs"
      onClick={copy}
      aria-label={copied ? "已复制" : "复制"}
      title={copied ? "已复制" : "复制"}
    >
      {copied ? (
        <Check className="text-foreground" aria-hidden />
      ) : (
        <Copy aria-hidden />
      )}
    </Button>
  );
}

/**
 * 流式出错时的安静内联通知(左对齐,贴合 assistant 一侧)。
 * 复用全项目一致的 destructive 轻量样式(border-destructive/20 + bg-destructive/5 + text-destructive),
 * 不喧哗、不阻断会话——告知出错并引导去检查设置;附一个就地「重试」入口。
 */
function ErrorNotice({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex w-fit max-w-full flex-col gap-2 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive"
    >
      <div className="flex items-start gap-2">
        <AlertCircle className="mt-px size-3.5 shrink-0" strokeWidth={2} aria-hidden />
        <span className="break-words">对话出错:{message},请检查设置。</span>
      </div>
      {onRetry && (
        <Button
          type="button"
          variant="ghost"
          size="xs"
          onClick={onRetry}
          className="-ml-1 self-start text-destructive hover:bg-destructive/10 hover:text-destructive"
        >
          <RotateCcw aria-hidden />
          重试
        </Button>
      )}
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
