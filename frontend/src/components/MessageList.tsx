import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { AlertCircle, Check, Copy, Loader2, Pencil, RotateCcw, X } from "lucide-react";

import { type Citation, type ToolStep } from "@/lib/api";
import { ActivityTimeline } from "@/components/ActivityTimeline";
import { AssistantMarkdown } from "@/components/AssistantMarkdown";
import { ThinkingBlock } from "@/components/ThinkingBlock";
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
  /** 推理模型的思考过程(累积;透明对话折叠展示);不入库,仅当轮可见。 */
  thinking?: string;
  /** 本轮知识库检索步骤(透明对话活动时间线);不入库,仅当轮可见。 */
  toolSteps?: ToolStep[];
}

export function MessageList({
  messages,
  status,
  busy,
  onCitation,
  onRegenerate,
  onEdit,
}: {
  messages: ViewMessage[];
  /** 流式状态文案(如「检索中」「生成中」);为空表示无进行中的检索/生成。 */
  status: string | null;
  /** 当前是否有进行中的流(发送/重生成/编辑);为 true 时隐藏编辑等会打断流的入口。 */
  busy?: boolean;
  /** 点击某条引用 chip 时回调,打开来源查看器。 */
  onCitation: (citation: Citation) => void;
  /** 重新生成最后一轮回答;未提供时不显示重试入口。 */
  onRegenerate?: () => void;
  /** 编辑某条 user 消息并就地重生成(传入消息 id + 新内容);未提供时不显示编辑入口。 */
  onEdit?: (messageId: number | string, content: string) => void;
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
          // 编辑只挂在已落库(数字 id)的 user 消息上;且需有回调、当前无进行中的流
          // (否则打断在途生成)。乐观插入但尚未落库(字符串 id)的 user 消息暂不可编辑——
          // 后端 edit 需要真实消息 id。
          onEdit={
            onEdit && m.role === "user" && typeof m.id === "number" && !busy
              ? onEdit
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
  onEdit,
}: {
  message: ViewMessage;
  status: string | null;
  onCitation: (citation: Citation) => void;
  /** 若可重试(最后一条助手消息),传入重新生成回调。 */
  onRegenerate?: () => void;
  /** 若可编辑(user 消息且无在途流),传入编辑回调(消息 id + 新内容)。 */
  onEdit?: (messageId: number | string, content: string) => void;
}) {
  const isUser = message.role === "user";
  const [editing, setEditing] = useState(false);

  if (isUser) {
    if (editing && onEdit) {
      return (
        <UserMessageEditor
          initial={message.content}
          onCancel={() => setEditing(false)}
          onSave={(next) => {
            setEditing(false);
            onEdit(message.id, next);
          }}
        />
      );
    }
    return (
      // group/msg + focus-within:悬停或聚焦时显露消息工具条(复制 / 编辑)。
      <div className="group/msg flex flex-col items-end gap-1">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-3.5 py-2.5 text-sm leading-relaxed whitespace-pre-wrap break-words text-primary-foreground">
          {message.content}
        </div>
        <MessageToolbar
          content={message.content}
          onEdit={onEdit ? () => setEditing(true) : undefined}
          align="end"
        />
      </div>
    );
  }

  // 助手消息:左对齐纯文本流,无气泡(Codex/ChatGPT 式),内联引用 chip。
  // 出错时不再显示「检索中/生成中」状态(否则与错误并存读作仍在进行)。
  const hasContent = message.content.length > 0;
  const hasThinking = Boolean(message.thinking);
  const hasSteps = Boolean(message.toolSteps?.length);
  // 状态药丸只在「还没出现思考块/正文」时显示(检索中/思考前的最初阶段);
  // 思考块 / 活动时间线 / 正文一出现就由它们指示进度,药丸退场。
  const showStatus = message.streaming && status && !message.error && !hasContent && !hasThinking;
  const showCursor = message.streaming && hasContent;
  // 工具条仅在不再流式后出现(避免与「生成中」状态/光标并存读作仍在进行)。
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
      {hasThinking && (
        <ThinkingBlock
          thinking={message.thinking ?? ""}
          streaming={Boolean(message.streaming)}
          hasContent={hasContent}
        />
      )}
      {hasSteps && <ActivityTimeline steps={message.toolSteps ?? []} hasContent={hasContent} />}
      {hasContent && (
        <div className="text-sm leading-relaxed break-words text-foreground">
          <AssistantMarkdown
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
  onEdit,
  align,
}: {
  /** 可复制的文本;为空(出错无正文)时不渲染复制按钮。 */
  content?: string;
  /** 可重试时的重新生成回调。 */
  onRegenerate?: () => void;
  /** 可编辑时的进入编辑回调(仅 user 消息)。 */
  onEdit?: () => void;
  /** 工具条对齐:user 一侧靠右,assistant 一侧靠左。 */
  align: "start" | "end";
}) {
  if (!content && !onRegenerate && !onEdit) return null;
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
      {onEdit && (
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          onClick={onEdit}
          aria-label="编辑"
          title="编辑"
        >
          <Pencil aria-hidden />
        </Button>
      )}
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

/**
 * user 消息的就地编辑器:预填原内容的多行输入,保存/取消两个动作。
 * Enter 保存(空内容禁止)、Shift+Enter 换行、Esc 取消;沿用 Composer 同款的输入框语汇,
 * 靠右对齐贴合 user 一侧。挂载即自动聚焦并把光标移到末尾、按内容撑高。
 */
function UserMessageEditor({
  initial,
  onSave,
  onCancel,
}: {
  initial: string;
  onSave: (content: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  const grow = (el: HTMLTextAreaElement) => {
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  };

  // 挂载即聚焦、光标置末尾、按内容撑高。
  useLayoutEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
    grow(el);
  }, []);

  const save = () => {
    const text = value.trim();
    if (!text) return; // 空内容无意义:不保存(也不退出,留用户继续编辑或取消)。
    onSave(text);
  };

  return (
    <div className="flex flex-col items-end gap-2">
      <div className="w-[85%] rounded-2xl border border-ring bg-background p-2 shadow-sm ring-3 ring-ring/40">
        <textarea
          ref={taRef}
          rows={1}
          value={value}
          aria-label="编辑消息"
          className="max-h-60 min-h-9 w-full resize-none bg-transparent px-1.5 py-1 text-sm leading-relaxed text-foreground outline-none"
          onChange={(e) => {
            setValue(e.target.value);
            grow(e.target);
          }}
          onKeyDown={(e) => {
            // 输入法(IME)合成期间按 Enter 是确认候选词,不应触发保存。
            if (e.nativeEvent.isComposing || e.keyCode === 229) return;
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              save();
            } else if (e.key === "Escape") {
              e.preventDefault();
              onCancel();
            }
          }}
        />
      </div>
      <div className="flex items-center gap-1.5">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          <X aria-hidden />
          取消
        </Button>
        <Button type="button" size="sm" disabled={!value.trim()} onClick={save}>
          <Check aria-hidden />
          保存
        </Button>
      </div>
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

