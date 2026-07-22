import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bot,
  ChevronDown,
  ChevronRight,
  Loader2,
  Paperclip,
  Pencil,
  RefreshCw,
  SendHorizontal,
  Sparkles,
  Square,
  TriangleAlert,
  Wrench,
} from "lucide-react";

import {
  api,
  type Citation,
  type ConversationReference,
  type CoworkApproval,
  type CoworkApprovalDecision,
  type CoworkSession,
  type CoworkToolStep,
} from "@/lib/api";
import { STATUS_LABELS, sessionTitle, statusBadgeProps } from "@/lib/coworkMeta";
import { cn } from "@/lib/utils";
import { pickFiles } from "@/lib/pickers";
import { AssistantMarkdown } from "@/components/AssistantMarkdown";
import { ProjectFilesZone } from "@/components/ProjectFilesZone";
import { ReferencePanel } from "@/components/ReferencePanel";
import { SourceViewer } from "@/components/SourceViewer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/** 视图层的消息模型:已落库消息用数字 id;流式中的乐观消息用字符串 id。 */
type ViewMsg = {
  id: number | string;
  role: "user" | "assistant" | "tool";
  content: string;
  /** tool 消息的工具名(历史消息渲染折叠工具记录用)。 */
  name?: string | null;
  /** assistant 消息的引用链([n] → chunk);无引用为 undefined。 */
  citations?: Citation[];
  /** 该助手消息正在流式生成。 */
  streaming?: boolean;
  /** 流式出错时的提示文案;挂到该条助手消息上以内联错误条呈现。 */
  error?: string;
  /** 模型推理过程(累积;折叠展示);不入库,仅当轮可见。 */
  thinking?: string;
  /** 本轮工具调用步骤(活动时间线);不入库,仅当轮可见。 */
  toolSteps?: CoworkToolStep[];
};

/** 落库的 citations_json 解析为 Citation[](坏数据回空数组)。 */
function parseCitations(json: string | null | undefined): Citation[] {
  if (!json) return [];
  try {
    const arr = JSON.parse(json);
    return Array.isArray(arr) ? (arr as Citation[]) : [];
  } catch {
    return [];
  }
}

/**
 * 单个 Cowork 会话的对话区:历史消息 + SSE 流式发送 + 审批弹窗 + 引用侧栏。
 * Cowork tab 与「项目与对话」共用;随 key=session.id 重挂载,卸载清理中止进行中的流。
 *
 * 整个对话区是原生拖放靶区(pywebview 外壳经 window.__onNativeFilesDropped 交付绝对路径);
 * 会话绑定项目(session.project_id 非空)时,引用侧栏附带「库内文件」区,可 pin 内部引用。
 */
export function CoworkConversation({
  session,
  onSessionState,
  onActivity,
  onSessionRenamed,
  onJumpToSession,
}: {
  session: CoworkSession;
  /** 流式 session_state 事件上报,父级就地更新列表徽标。 */
  onSessionState: (sid: number, status: string) => void;
  /** 一轮回答结束后回调,父级立即刷新会话列表(状态/updated_at 已变)。 */
  onActivity: () => void;
  /** 首轮自动标题(session_renamed 事件)后回调,父级刷新会话列表拿新标题。 */
  onSessionRenamed?: (name: string) => void;
  /** 引用「跳回会话时刻」:透传给 SourceViewer 的 header 按钮(Cowork tab 不传)。 */
  onJumpToSession?: (sessionId: number, ts: string) => void;
}) {
  const [messages, setMessages] = useState<ViewMsg[]>([]);
  const [loading, setLoading] = useState(true);
  // 流式状态文案(思考中/执行中);null 表示无进行中的请求。
  const [status, setStatus] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [input, setInput] = useState("");
  // 引用查看器:点击答案里的 [n] chip 打开。
  const [viewing, setViewing] = useState<Citation | null>(null);
  // 会话引用(附件)侧栏:列表 + 挂载状态/进度。
  const [references, setReferences] = useState<ConversationReference[]>([]);
  const [refOpen, setRefOpen] = useState(false);
  const [attachStatus, setAttachStatus] = useState<string | null>(null);
  // 会话级权限模式(需求 7):ask=每次确认 / skip_all=自动执行(仅本地,有警示)。
  // follow_a_plan 当前行为等同 ask(后端注释),不暴露给用户以免误导。
  const [permMode, setPermMode] = useState(session.permission_mode);
  const [permBusy, setPermBusy] = useState(false);
  // skip_all 切换前的警示确认(Cowork「Bypass Permissions」警示的对应物)。
  const [bypassConfirmOpen, setBypassConfirmOpen] = useState(false);

  const applyPermMode = useCallback(
    async (mode: string) => {
      setPermBusy(true);
      try {
        await api.updateCoworkSession(session.id, { permission_mode: mode });
        setPermMode(mode);
      } catch {
        /* 切换失败:保持原模式,下次操作重试。 */
      } finally {
        setPermBusy(false);
        setBypassConfirmOpen(false);
      }
    },
    [session.id],
  );

  const onPermModeChange = useCallback(
    (mode: string) => {
      if (mode === permMode) return;
      // 切到自动执行前必须明示风险(对齐 Cowork 的 bypass 警示)
      if (mode === "skip_all") {
        setBypassConfirmOpen(true);
        return;
      }
      void applyPermMode(mode);
    },
    [permMode, applyPermMode],
  );
  const [attachError, setAttachError] = useState<string | null>(null);
  // 拖放覆盖层:在整个对话区拖动文件时显示半透明提示。
  const [dropOverlay, setDropOverlay] = useState(false);

  const refreshReferences = useCallback(() => {
    api
      .listReferences(session.id)
      .then(setReferences)
      .catch(() => {
        /* 引用列表失败:保持现状,不影响对话主流程。 */
      });
  }, [session.id]);

  // 挂载时与消息并行拉一次引用列表。
  useEffect(() => {
    refreshReferences();
  }, [refreshReferences]);

  // 逐个流式挂载一批路径(提取进度在侧栏头部展示);选文件与原生拖放共用。
  const attachPaths = useCallback(
    async (paths: string[]) => {
      if (!paths.length) return;
      setRefOpen(true);
      setAttachError(null);
      for (const p of paths) {
        setAttachStatus(`解析 ${p.split("/").pop()}…`);
        try {
          await api.attachExternalStream(session.id, p, {
            onStatus: (t) => setAttachStatus(t),
            // SSE error 事件(MinerU 失败/不支持类型等)经 onError 上来(codex review P2:
            // 此前没传,失败静默——附件凭空消失)。
            onError: (m) => setAttachError(`${p.split("/").pop()}:${m}`),
          });
        } catch (e) {
          setAttachError(`挂载失败:${e instanceof Error ? e.message : String(e)}`);
        }
      }
      setAttachStatus(null);
      refreshReferences();
    },
    [session.id, refreshReferences],
  );

  // 挂附件:原生选文件 → 逐个流式挂载。
  const attach = useCallback(async () => {
    const paths = await pickFiles();
    if (!paths.length) return;
    await attachPaths(paths);
  }, [attachPaths]);

  // 桌面外壳(pywebview)原生拖放:外壳能拿到真实绝对路径,通过该全局回调把路径交回前端。
  // 浏览器 drop 事件读不到路径,故路径走这条原生通道。组件按会话 key 重挂载;
  // 卸载时清掉全局回调,避免拖放落到已卸载的会话上。
  useEffect(() => {
    (window as unknown as { __onNativeFilesDropped?: (paths: string[]) => void }).__onNativeFilesDropped =
      (paths: string[]) => {
        if (paths?.length) void attachPaths(paths);
      };
    return () => {
      delete (window as unknown as { __onNativeFilesDropped?: unknown }).__onNativeFilesDropped;
    };
  }, [attachPaths]);

  const detach = useCallback(
    async (rid: number) => {
      try {
        await api.detachReference(session.id, rid);
      } catch {
        /* 解挂失败:刷新以权威状态为准。 */
      }
      refreshReferences();
    },
    [session.id, refreshReferences],
  );

  // 「库内文件」pin 为内部引用(仅项目绑定会话可用)。
  const addInternal = useCallback(
    async (ingestRecordId: number) => {
      try {
        await api.addInternalReference(session.id, ingestRecordId);
      } catch {
        /* pin 失败:刷新以权威状态为准。 */
      }
      refreshReferences();
    },
    [session.id, refreshReferences],
  );

  // 已 pin 为内部引用的项目文件 id 集合:供「库内文件」区标记「已引用」并禁用点选。
  const pinnedRecordIds = new Set(
    references
      .filter((r) => r.kind === "internal" && r.ingest_record_id != null)
      .map((r) => r.ingest_record_id as number),
  );

  // —— 对话区拖放(整个对话区都是拖放靶区)——
  // dragover 时显示半透明覆盖层;真正读路径走原生通道(__onNativeFilesDropped)。
  // 浏览器 drop 拿不到绝对路径,故 onDrop 仅做视觉收尾;开发态浏览器额外给出提示。
  const dragHasFiles = (e: React.DragEvent) =>
    Array.from(e.dataTransfer.types).includes("Files");

  const onAreaDragOver = (e: React.DragEvent) => {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    setDropOverlay(true);
  };
  const onAreaDragLeave = (e: React.DragEvent) => {
    // 仅当指针真正离开对话区(而非进入子元素)时才隐藏,避免覆盖层闪烁。
    if (e.relatedTarget && e.currentTarget.contains(e.relatedTarget as Node)) return;
    setDropOverlay(false);
  };
  const onAreaDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDropOverlay(false);
    // 路径由原生外壳经 __onNativeFilesDropped 交付;此处不读 File.path。
    // 开发态浏览器(无 pywebview)确实拿不到路径,给出与「附件」按钮一致的提示。
    const hasPywebview = "pywebview" in window;
    if (!hasPywebview && e.dataTransfer.files.length) {
      setRefOpen(true);
      setAttachError("当前为开发态浏览器,无法读取拖放文件路径;请点「附件」选择文件。");
    }
  };
  // 待审批队列:单循环串行,同一时刻实际只有一条,弹窗始终处理队首。
  const [approvals, setApprovals] = useState<CoworkApproval[]>([]);
  // 当前流的 abort 句柄(停止 / 卸载时调用)。
  const abortRef = useRef<(() => void) | null>(null);
  // 当轮的思考过程 / 工具步骤(不入库,仅当轮可见)。
  const thinkingRef = useRef("");
  const stepsRef = useRef<CoworkToolStep[]>([]);
  const endRef = useRef<HTMLDivElement | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  // 拉取历史消息(挂载初始化 + 断线恢复补拉共用)。
  const reloadMessages = useCallback(() => {
    return api
      .listCoworkMessages(session.id)
      .then((rows) => {
        setMessages(
          rows.map((m) => ({
            id: m.id,
            role: m.role,
            content: m.content,
            name: m.name,
            citations: parseCitations(m.citations_json),
          })),
        );
      })
      .catch(() => {
        /* 历史拉取失败:保持现状,下轮再试。 */
      });
  }, [session.id]);

  // 挂载时拉取历史消息(组件按会话 key 重挂载,loading 初值即为 true,无需处理会话切换的重置)。
  useEffect(() => {
    let cancelled = false;
    reloadMessages().finally(() => {
      if (cancelled) return;
      setLoading(false);
      // 重挂载时会话仍在跑(切走再回来):恢复一个「思考中」占位,
      // 否则界面看起来像停了(实际后端在跑,恢复轮询稍后会补拉结果)。
      if (["thinking", "executing", "waiting_approval"].includes(session.status)) {
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (!last || last.role !== "user") return prev; // 末尾不是未答的用户消息 → 不补
          if (prev.some((m) => m.id === "recovery-pending")) return prev;
          return [
            ...prev,
            { id: "recovery-pending", role: "assistant", content: "", streaming: true },
          ];
        });
        setStatus(
          session.status === "executing"
            ? "执行中"
            : session.status === "waiting_approval"
              ? "等待确认"
              : "思考中",
        );
      }
    });
    return () => {
      cancelled = true;
    };
    // session.status 只取挂载时快照,不随轮询变化重跑(占位由恢复补拉统一替换)。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadMessages]);

  // 断线恢复(切后台 WKWebView 挂起/切会话中断 SSE 后):后端循环仍在跑,
  // 会话处于运行态时轮询状态,跑完补拉消息(答复+引用)。有活 SSE(streaming)时不轮。
  useEffect(() => {
    const active = ["thinking", "executing", "waiting_approval"].includes(session.status);
    if (streaming || !active) return;
    const t = setInterval(() => {
      api
        .getCoworkSession(session.id)
        .then((s) => {
          onSessionState(session.id, s.status);
          if (!["thinking", "executing", "waiting_approval"].includes(s.status)) {
            setStatus(null); // 清掉「思考中」占位状态(恢复补拉统一替换消息)
            void reloadMessages();
          }
        })
        .catch(() => {
          /* 单轮失败:下轮重试。 */
        });
    }, 2000);
    return () => clearInterval(t);
  }, [streaming, session.id, session.status, onSessionState, reloadMessages]);

  // 卸载时中断在途流,避免回调写入已卸载组件(切换会话即卸载 → 流随之中止)。
  useEffect(
    () => () => {
      abortRef.current?.();
      abortRef.current = null;
    },
    [],
  );

  // 挂载(含切换会话重挂载)时拉一次待审批:有挂起项直接恢复确认弹窗
  //(例如上一轮审批未决策就切走/刷新,agent 仍挂起等待)。
  useEffect(() => {
    let cancelled = false;
    api
      .listCoworkApprovals()
      .then((rows) => {
        if (cancelled) return;
        setApprovals(rows.filter((r) => r.session_id === session.id));
      })
      .catch(() => {
        /* 恢复失败:静默,弹窗不出现,下次挂载再试。 */
      });
    return () => {
      cancelled = true;
    };
  }, [session.id]);

  // 流式期间轮询待审批(codex review R2:ask_user 的 SSE 事件可能漏接,
  // 子 agent 的审批挂起根本没有 SSE——轮询是唯一可靠通道;空闲时不轮)。
  useEffect(() => {
    if (!streaming) return;
    const t = setInterval(() => {
      api
        .listCoworkApprovals()
        .then((rows) => {
          setApprovals((prev) => {
            // 本地单用户:挂起的审批必与在跑的工作相关(含子 agent),不按 session 过滤
            const known = new Set(prev.map((x) => x.approval_id));
            const added = rows.filter((x) => !known.has(x.approval_id));
            return added.length ? [...prev, ...added] : prev;
          });
        })
        .catch(() => {
          /* 单轮失败:下轮重试。 */
        });
    }, 2000);
    return () => clearInterval(t);
  }, [streaming, session.id]);

  // 新消息 / 流式进展 / 状态变化时贴底滚动,保持最新内容可见。
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, status]);

  // 行内编辑态:正在编辑的 user 消息 id(null=无)。
  const [editingId, setEditingId] = useState<number | null>(null);

  // 三个入口(发送/重生成/编辑)共享的流式回调工厂:回调都 patch 同一条流式助手消息。
  const makeStreamHandlers = useCallback(
    (assistantId: string) => {
      const patch = (fn: (m: ViewMsg) => ViewMsg) =>
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? fn(m) : m)),
        );
      return {
        onStatus: (s: string) => setStatus(s),
        onSessionState: (st: string) => onSessionState(session.id, st),
        onThinking: (t: string) => {
          thinkingRef.current += t;
          patch((m) => ({ ...m, thinking: thinkingRef.current }));
        },
        onToolStep: (s: CoworkToolStep) => {
          if (s.status === "done") {
            // done 是同工具最近一次 started 的完成回执:就地合并,而非新增一行。
            const idx = stepsRef.current.findLastIndex(
              (x) => x.tool === s.tool && x.status === "started",
            );
            if (idx >= 0) {
              const next = [...stepsRef.current];
              next[idx] = { ...next[idx], status: "done", preview: s.preview };
              stepsRef.current = next;
            } else {
              stepsRef.current = [...stepsRef.current, s];
            }
          } else {
            stepsRef.current = [...stepsRef.current, s];
          }
          patch((m) => ({ ...m, toolSteps: stepsRef.current }));
        },
        // 工具调用待人工确认:入队(去重),弹窗处理队首;agent 循环挂起直到决策。
        onApprovalRequest: (a: CoworkApproval) => {
          setApprovals((prev) =>
            prev.some((x) => x.approval_id === a.approval_id) ? prev : [...prev, a],
          );
        },
        // 审批已决策(本端提交成功或其他端处理):出队,弹窗兜底关闭。
        onApprovalResolved: (approvalId: string) => {
          setApprovals((prev) => prev.filter((x) => x.approval_id !== approvalId));
        },
        // Phase 1:token 是最终答复全文一次性到达,直接替换而非追加。
        onToken: (full: string) => patch((m) => ({ ...m, content: full })),
        // 引用链:[n] 映射结果,token 之后到达,挂到同一条助手消息。
        onCitations: (c: Citation[]) => patch((m) => ({ ...m, citations: c })),
        // 首轮自动标题:服务端已改名,通知父级刷新会话列表(标题在侧栏/树里展示)。
        onSessionRenamed: (name: string) => onSessionRenamed?.(name),
        onDone: () => {
          patch((m) => ({ ...m, streaming: false }));
          setStreaming(false);
          setStatus(null);
          abortRef.current = null;
          onActivity();
        },
        onError: (e: Error) => {
          // SSE error 事件 / HTTP / 网络错误:挂到当前助手消息上,以错误条呈现。
          patch((m) => ({ ...m, streaming: false, error: e.message }));
          setStreaming(false);
          setStatus(null);
          abortRef.current = null;
        },
      };
    },
    [session.id, onSessionState, onActivity, onSessionRenamed],
  );

  // 开启一轮流式:重置当轮思考/步骤,插入流式助手消息,挂回调。
  const startTurn = useCallback(
    (optimistic: ViewMsg[], startStream: (assistantId: string) => () => void) => {
      const assistantId = `assistant-${Date.now()}`;
      setMessages([
        ...optimistic,
        { id: assistantId, role: "assistant", content: "", streaming: true },
      ]);
      setStreaming(true);
      setStatus("思考中");
      thinkingRef.current = "";
      stepsRef.current = [];
      abortRef.current = startStream(assistantId);
    },
    [],
  );

  const send = useCallback(
    (content: string) => {
      const text = content.trim();
      if (!text || streaming) return;
      setInput("");
      if (taRef.current) taRef.current.style.height = "auto";
      startTurn(
        [...messages, { id: `user-${Date.now()}`, role: "user", content: text }],
        (aid) => api.sendCoworkMessage(session.id, text, makeStreamHandlers(aid)),
      );
    },
    [streaming, messages, session.id, startTurn, makeStreamHandlers],
  );

  // 重生成最后一轮:截到最后一条 user 消息,其后的消息由后端删,前端同步丢弃。
  const regenerate = useCallback(() => {
    if (streaming) return;
    const idx = messages.findLastIndex((m) => m.role === "user");
    if (idx < 0) return;
    startTurn(messages.slice(0, idx + 1), (aid) =>
      api.regenerateCowork(session.id, makeStreamHandlers(aid)),
    );
  }, [streaming, messages, session.id, startTurn, makeStreamHandlers]);

  // 编辑某条 user 消息并重生成:前端就地改写 + 丢弃其后消息(后端同样处理)。
  const editAndRerun = useCallback(
    (mid: number, content: string) => {
      const text = content.trim();
      if (!text || streaming) return;
      const idx = messages.findIndex((m) => m.id === mid && m.role === "user");
      if (idx < 0) return;
      const next = messages.slice(0, idx + 1);
      next[idx] = { ...next[idx], content: text };
      setEditingId(null);
      startTurn(next, (aid) =>
        api.editCoworkMessage(session.id, mid, text, makeStreamHandlers(aid)),
      );
    },
    [streaming, messages, session.id, startTurn, makeStreamHandlers],
  );

  const stop = useCallback(() => {
    // 先通知后端取消(agent 循环/审批挂起随之中止),再断本地流(codex review P1:
    // 只 abort fetch 的话后端仍在跑,副作用与答复照样落库)。
    void api.stopCoworkSession(session.id).catch(() => {});
    abortRef.current?.();
    abortRef.current = null;
    setStreaming(false);
    setStatus(null);
    setMessages((prev) =>
      prev.map((m) => (m.streaming ? { ...m, streaming: false } : m)),
    );
  }, [session.id]);

  // 提交审批决策:POST 成功后出队(队首消失 → 弹窗关闭,队列下一条若有则顶上来)。
  // 404(已决策/不存在)在 api 层按成功处理,这里同样出队。失败抛给弹窗组件内联提示。
  const handleDecide = useCallback(
    async (approval: CoworkApproval, decision: CoworkApprovalDecision) => {
      await api.decideCoworkApproval(approval.approval_id, decision);
      setApprovals((prev) => prev.filter((x) => x.approval_id !== approval.approval_id));
    },
    [],
  );

  const grow = (el: HTMLTextAreaElement) => {
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  };

  const hasMessages = messages.length > 0;

  return (
    <div
      className="relative flex min-h-0 flex-1 flex-col"
      onDragOver={onAreaDragOver}
      onDragLeave={onAreaDragLeave}
      onDrop={onAreaDrop}
    >
      {/* 会话头:名称 + 状态徽标 + 引用侧栏开关 */}
      <header className="flex shrink-0 items-center gap-3 border-b border-border/70 px-8 py-4">
        <h1 className="min-w-0 truncate text-base font-semibold tracking-tight text-foreground">
          {sessionTitle(session)}
        </h1>
        {(() => {
          const badge = statusBadgeProps(session.status);
          return (
            <Badge variant={badge.variant} className={cn("shrink-0", badge.className)}>
              {STATUS_LABELS[session.status] ?? session.status}
            </Badge>
          );
        })()}
        <div className="ml-auto flex items-center gap-1">
          {/* 会话级权限模式切换(需求 7,会话头即时生效;ghost 化原生 select) */}
          <span className="relative inline-flex items-center">
            <select
              aria-label="权限模式"
              value={permMode}
              disabled={permBusy}
              onChange={(e) => onPermModeChange(e.target.value)}
              className={cn(
                "h-8 cursor-pointer appearance-none rounded-full border border-border/70 bg-transparent",
                "pl-3 pr-7 text-xs font-medium text-muted-foreground transition-colors",
                "hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
                permMode === "skip_all" && "border-amber-600/30 bg-amber-600/10 text-amber-700 hover:bg-amber-600/15 dark:text-amber-300",
              )}
              title="权限模式:手动确认=每次工具调用都需确认;自动执行=跳过所有确认(高风险)"
            >
              <option value="ask">手动确认</option>
              <option value="skip_all">自动执行</option>
            </select>
            <ChevronDown
              className={cn(
                "pointer-events-none absolute right-2.5 size-3 text-muted-foreground/70",
                permMode === "skip_all" && "text-amber-700/70 dark:text-amber-300/70",
              )}
              aria-hidden
            />
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => void attach()}
            disabled={attachStatus !== null}
            title="附加文件到本会话"
          >
            {attachStatus ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Paperclip className="size-3.5" />
            )}
            {attachStatus ?? "附件"}
          </Button>
          <Button
            type="button"
            variant={refOpen ? "secondary" : "ghost"}
            size="sm"
            onClick={() => setRefOpen((o) => !o)}
            title="会话引用面板"
          >
            引用{references.length > 0 ? ` ${references.length}` : ""}
          </Button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
      {/* 消息区 */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" />
            正在加载消息…
          </div>
        ) : hasMessages ? (
          <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-6 py-8">
            {messages.map((m, i) => (
              <MessageRow
                key={m.id}
                message={m}
                status={status}
                onCitation={setViewing}
                busy={streaming}
                isLastAssistant={
                  m.role === "assistant" &&
                  i === messages.length - 1 &&
                  !m.streaming
                }
                editing={editingId === m.id}
                onEditStart={(id) => setEditingId(id)}
                onEditCancel={() => setEditingId(null)}
                onEditSubmit={editAndRerun}
                onRegenerate={regenerate}
              />
            ))}
            <div ref={endRef} />
          </div>
        ) : (
          <EmptyConversation
            onPick={(text) => {
              setInput(text);
              taRef.current?.focus();
              grow(taRef.current!);
            }}
          />
        )}
      </div>

      {/* 输入区:浮动卡片;生成中发送键变停止键 */}
      <div className="shrink-0 px-6 pb-7">
        <div className="mx-auto w-full max-w-2xl">
          <div className={cn(
            "flex items-end gap-2 rounded-3xl border border-border/80 bg-background p-2.5",
            "shadow-[0_4px_24px_-6px_rgba(0,0,0,0.10),0_1px_4px_-1px_rgba(0,0,0,0.06)]",
            "transition-[border-color,box-shadow] duration-200",
            "focus-within:border-ring/70 focus-within:ring-4 focus-within:ring-ring/15",
          )}>
            <textarea
              ref={taRef}
              rows={1}
              value={input}
              placeholder="向 agent 下达任务…"
              aria-label="对话输入"
              className="max-h-40 min-h-9 w-full flex-1 resize-none bg-transparent px-3 py-2 text-sm leading-relaxed text-foreground outline-none placeholder:text-muted-foreground/80"
              onChange={(e) => {
                setInput(e.target.value);
                grow(e.target);
              }}
              onKeyDown={(e) => {
                // 输入法(IME)合成期间按 Enter 是确认候选词,不应触发发送。
                if (e.nativeEvent.isComposing || e.keyCode === 229) return;
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(input);
                }
              }}
            />
            {streaming ? (
              <button
                type="button"
                onClick={stop}
                aria-label="停止生成"
                title="停止生成"
                className={cn(
                  "mb-0.5 flex size-9 shrink-0 items-center justify-center rounded-full",
                  "bg-muted text-foreground transition-colors hover:bg-muted/70",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
                )}
              >
                <Square className="size-3.5" fill="currentColor" />
              </button>
            ) : (
              <button
                type="button"
                disabled={!input.trim()}
                onClick={() => send(input)}
                aria-label="发送"
                title="发送(Enter)"
                className={cn(
                  "mb-0.5 flex size-9 shrink-0 items-center justify-center rounded-full transition-all",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
                  input.trim()
                    ? "bg-primary text-primary-foreground shadow-sm hover:opacity-90 active:scale-95"
                    : "bg-muted/60 text-muted-foreground/50",
                )}
              >
                <SendHorizontal className="size-4" />
              </button>
            )}
          </div>
          <p className="mt-2 text-center text-[11px] text-muted-foreground/60">
            Enter 发送 · Shift+Enter 换行
          </p>
        </div>
      </div>
      </div>

      {/* 引用侧栏:本会话附件清单(挂载/解挂);项目会话附「库内文件」pin 区 */}
      {refOpen && (
        <aside className="flex w-[300px] shrink-0 flex-col gap-3 overflow-y-auto border-l border-border/70 px-4 py-4">
          <h2 className="text-xs font-semibold text-muted-foreground">本会话引用</h2>
          {attachError && (
            <p className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive">
              {attachError}
            </p>
          )}
          <ReferencePanel references={references} onDetach={(rid) => void detach(rid)} />
          {session.project_id != null && (
            <ProjectFilesZone
              projectId={session.project_id}
              pinnedRecordIds={pinnedRecordIds}
              onPin={(rid) => void addInternal(rid)}
              refreshSignal={references.length}
            />
          )}
        </aside>
      )}
      </div>

      {/* 拖放覆盖层:覆盖整个对话区,仅视觉提示;路径由原生通道交付。 */}
      {dropOverlay && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center rounded-lg border-2 border-dashed border-ring/60 bg-background/80 backdrop-blur-[1px]"
        >
          <span className="rounded-full bg-foreground/90 px-4 py-2 text-sm font-medium text-background shadow-sm">
            拖放文件到此处添加引用
          </span>
        </div>
      )}

      {/* 工具调用确认弹窗:处理审批队列队首;决策/resolved 事件后出队,自动顶上下一条。 */}
      <CoworkApprovalDialog approval={approvals[0] ?? null} onDecide={handleDecide} />

      {/* skip_all 切换警示(对齐 Cowork「Bypass Permissions」的风险明示) */}
      <Dialog open={bypassConfirmOpen} onOpenChange={setBypassConfirmOpen}>
        <DialogContent className="gap-0 p-0">
          <DialogHeader className="gap-2 px-6 pt-6">
            <span
              aria-hidden
              className="flex size-9 items-center justify-center rounded-xl bg-amber-600/10 text-amber-700 ring-1 ring-amber-600/15 dark:text-amber-300"
            >
              <TriangleAlert className="size-[18px]" strokeWidth={2} />
            </span>
            <DialogTitle>切换到自动执行模式?</DialogTitle>
            <DialogDescription>
              自动执行模式下,agent 跳过所有权限确认,会直接执行删除文件、运行命令等
              高风险操作,也可能受到注入内容的影响而做出意料之外的动作。
              仅在你信任任务内容、可随时中止的情况下使用。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 px-6 py-5">
            <Button
              type="button"
              variant="outline"
              disabled={permBusy}
              onClick={() => setBypassConfirmOpen(false)}
            >
              取消
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={permBusy}
              onClick={() => void applyPermMode("skip_all")}
            >
              {permBusy && <Loader2 className="size-4 animate-spin" />}
              切换到自动执行
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 引用来源查看器([n] chip 点击打开) */}
      <SourceViewer
        citation={viewing}
        onClose={() => setViewing(null)}
        onJumpToSession={onJumpToSession}
      />
    </div>
  );
}

/** 审批参数展示:JSON 美化(缩进 2 格),非 JSON 原样;过长截断,等宽块展示。 */
function formatApprovalArgs(args: string): string {
  const MAX = 1200;
  let pretty = args;
  try {
    pretty = JSON.stringify(JSON.parse(args), null, 2);
  } catch {
    /* 非 JSON 参数:原样展示。 */
  }
  return pretty.length > MAX ? `${pretty.slice(0, MAX)}\n…(已截断)` : pretty;
}

/**
 * 工具调用确认弹窗(agent 循环挂起等待决策)。受控:有队首审批即开,无即关。
 * 必须做出决策才能关闭(忽略 ESC/遮罩关闭请求,无关闭按钮)——拒绝即是退出路径;
 * 决策提交成功或 approval_resolved 到达后,父组件把该条出队,弹窗随之关闭。
 */
function CoworkApprovalDialog({
  approval,
  onDecide,
}: {
  /** 队首待审批;为 null 时弹窗关闭。 */
  approval: CoworkApproval | null;
  /** 提交决策;resolve 表示已被接受(出队),reject 由弹窗内联提示。 */
  onDecide: (approval: CoworkApproval, decision: CoworkApprovalDecision) => Promise<void>;
}) {
  return (
    // 忽略关闭请求:agent 正挂起等一个明确答复,关闭弹窗不等于决策。
    <Dialog open={approval !== null} onOpenChange={() => {}}>
      {approval && (
        // key=approval_id:每条审批重挂载,busy/错误等瞬态自然归零,无需重置 effect。
        <ApprovalDialogBody key={approval.approval_id} approval={approval} onDecide={onDecide} />
      )}
    </Dialog>
  );
}

function ApprovalDialogBody({
  approval,
  onDecide,
}: {
  approval: CoworkApproval;
  onDecide: (approval: CoworkApproval, decision: CoworkApprovalDecision) => Promise<void>;
}) {
  // 正在提交的决策(防重复点击);null 表示空闲。
  const [busy, setBusy] = useState<CoworkApprovalDecision | null>(null);
  const [error, setError] = useState<string | null>(null);
  // kind=question(ask_user)的回答文本。
  const [answer, setAnswer] = useState("");

  const decide = async (decision: CoworkApprovalDecision) => {
    if (busy) return;
    setBusy(decision);
    setError(null);
    try {
      await onDecide(approval, decision);
      // 成功后父组件出队 → approval 变 null → 弹窗关闭,无需本地收尾。
    } catch (e) {
      setError(`提交决策失败:${e instanceof Error ? e.message : String(e)}`);
      setBusy(null);
    }
  };

  // ask_user 提问:文本输入形态,回答作为工具结果回给 agent。
  if (approval.kind === "question") {
    return (
      <DialogContent showCloseButton={false} className="gap-0 p-0">
        <DialogHeader className="gap-2 px-6 pt-6">
          <span
            aria-hidden
            className="flex size-9 items-center justify-center rounded-xl bg-amber-600/10 text-amber-700 ring-1 ring-amber-600/15 dark:text-amber-300"
          >
            <Bot className="size-[18px]" strokeWidth={2} />
          </span>
          <DialogTitle>agent 需要你的回答</DialogTitle>
          <DialogDescription>agent 暂停等待输入,回答后它将继续执行。</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3 px-6 py-5">
          <div className="rounded-xl border border-border/70 bg-muted/30 px-3.5 py-3 text-sm leading-relaxed whitespace-pre-wrap text-foreground">
            {approval.prompt}
          </div>
          <Textarea
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder="输入你的回答…"
            rows={3}
            autoFocus
          />
          {error && (
            <p
              role="alert"
              className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive"
            >
              {error}
            </p>
          )}
        </div>

        <DialogFooter className="gap-2 border-t border-border/70 bg-muted/30 px-6 py-4">
          <Button
            type="button"
            variant="outline"
            size="lg"
            disabled={busy !== null}
            onClick={() => void decide("(用户选择不回答)")}
          >
            不回答
          </Button>
          <Button
            type="button"
            size="lg"
            disabled={busy !== null || !answer.trim()}
            onClick={() => void decide(answer.trim())}
          >
            {busy !== null && <Loader2 className="size-4 animate-spin" />}
            提交回答
          </Button>
        </DialogFooter>
      </DialogContent>
    );
  }

  return (
    <DialogContent showCloseButton={false} className="gap-0 p-0">
      <DialogHeader className="gap-2 px-6 pt-6">
        <span
          aria-hidden
          className="flex size-9 items-center justify-center rounded-xl bg-amber-600/10 text-amber-700 ring-1 ring-amber-600/15 dark:text-amber-300"
        >
          <TriangleAlert className="size-[18px]" strokeWidth={2} />
        </span>
        <DialogTitle>允许 agent 使用 {approval.tool}?</DialogTitle>
        <DialogDescription>
          agent 请求调用以下工具,确认前执行将保持暂停。
        </DialogDescription>
      </DialogHeader>

      <div className="px-6 py-5">
        <div className="rounded-xl border border-border/70 bg-muted/30 px-3.5 py-3">
          <div className="flex items-center gap-2 text-sm font-medium text-foreground">
            <Wrench className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
            <span className="font-mono">{approval.tool}</span>
          </div>
          <pre className="mt-2 max-h-48 overflow-y-auto font-mono text-xs leading-relaxed break-words whitespace-pre-wrap text-muted-foreground">
            {formatApprovalArgs(approval.args)}
          </pre>
        </div>

        {error && (
          <p
            role="alert"
            className="mt-4 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive"
          >
            {error}
          </p>
        )}
      </div>

      <DialogFooter className="gap-2 border-t border-border/70 bg-muted/30 px-6 py-4">
        <Button
          type="button"
          variant="destructive"
          size="lg"
          disabled={busy !== null}
          onClick={() => void decide("deny")}
        >
          {busy === "deny" && <Loader2 className="size-4 animate-spin" />}
          拒绝
        </Button>
        {/* 删除/派发类工具(allow_session_option=false)禁止「总是允许」,不显示该按钮。 */}
        {approval.allow_session_option && (
          <Button
            type="button"
            variant="outline"
            size="lg"
            disabled={busy !== null}
            onClick={() => void decide("session")}
          >
            {busy === "session" && <Loader2 className="size-4 animate-spin" />}
            本次会话都允许
          </Button>
        )}
        <Button
          type="button"
          size="lg"
          disabled={busy !== null}
          onClick={() => void decide("once")}
        >
          {busy === "once" && <Loader2 className="size-4 animate-spin" />}
          仅此一次
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

/** 会话内空态:hero + 建议指令 chips(点击填入输入框,降低首次使用门槛)。 */
const SUGGESTIONS: { icon: typeof Sparkles; label: string; prompt: string }[] = [
  { icon: Bot, label: "总结文档", prompt: "总结这个项目里的 PDF,给我一份中文要点摘要" },
  { icon: Sparkles, label: "检索资料", prompt: "在我的项目里检索一下:什么是……" },
  { icon: Wrench, label: "批量处理", prompt: "批量处理项目里的文档,每个文件总结一段,最后汇总" },
];

function EmptyConversation({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col items-center justify-center gap-8 px-6 py-10 text-center">
      <div className="flex flex-col items-center gap-4">
        <span
          aria-hidden
          className="flex size-14 items-center justify-center rounded-2xl bg-muted text-foreground shadow-sm ring-1 ring-border/70"
        >
          <Sparkles className="size-6" strokeWidth={1.5} />
        </span>
        <div className="flex flex-col gap-1.5">
          <p className="text-lg font-semibold tracking-tight text-foreground">开始对话</p>
          <p className="max-w-sm text-sm leading-relaxed text-muted-foreground">
            向 agent 下达任务;它的思考过程、工具调用与最终结果会逐步呈现在这里。
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2">
        {SUGGESTIONS.map((s) => (
          <button
            key={s.label}
            type="button"
            onClick={() => onPick(s.prompt)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border border-border/70 bg-background",
              "px-4 py-2 text-xs font-medium text-muted-foreground shadow-sm transition-all",
              "hover:-translate-y-px hover:border-border hover:bg-muted hover:text-foreground hover:shadow",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
            )}
          >
            <s.icon className="size-3.5" strokeWidth={1.75} aria-hidden />
            {s.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function MessageRow({
  message,
  status,
  onCitation,
  busy,
  isLastAssistant,
  editing,
  onEditStart,
  onEditCancel,
  onEditSubmit,
  onRegenerate,
}: {
  message: ViewMsg;
  status: string | null;
  onCitation: (citation: Citation) => void;
  busy: boolean;
  isLastAssistant: boolean;
  editing: boolean;
  onEditStart: (id: number) => void;
  onEditCancel: () => void;
  onEditSubmit: (id: number, content: string) => void;
  onRegenerate: () => void;
}) {
  // 用户消息:右对齐气泡;hover 出「编辑」(仅已落库的数字 id 消息可编辑)。
  if (message.role === "user") {
    return (
      <div className="group flex flex-col items-end gap-1">
        {editing ? (
          <UserEditForm
            initial={message.content}
            onCancel={onEditCancel}
            onSubmit={(text) => onEditSubmit(message.id as number, text)}
          />
        ) : (
          <>
            <div className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-3.5 py-2.5 text-sm leading-relaxed break-words whitespace-pre-wrap text-primary-foreground">
              {message.content}
            </div>
            {typeof message.id === "number" && !busy && (
              <button
                type="button"
                onClick={() => onEditStart(message.id as number)}
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 hover:text-foreground focus-visible:opacity-100"
              >
                <Pencil className="size-3" aria-hidden />
                编辑
              </button>
            )}
          </>
        )}
      </div>
    );
  }

  // 历史 tool 消息:折叠的工具记录(默认收起)。
  if (message.role === "tool") {
    return <ToolMessageRow message={message} />;
  }

  // 助手消息:左对齐纯文本流;流式中依次呈现 状态 → 思考 → 工具时间线 → 正文。
  const hasContent = message.content.length > 0;
  const hasThinking = Boolean(message.thinking);
  const hasSteps = Boolean(message.toolSteps?.length);
  // 状态药丸只在「还没出现思考块/步骤/正文」时显示;任一出现即由它们指示进度。
  const showStatus =
    message.streaming && status && !message.error && !hasContent && !hasThinking && !hasSteps;
  const showCursor = message.streaming && hasContent;
  return (
    <div className="flex flex-col gap-2">
      {showStatus && (
        <div
          role="status"
          aria-live="polite"
          className="inline-flex w-fit items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground"
        >
          <Loader2 className="size-3 animate-spin" aria-hidden />
          {status}
        </div>
      )}
      {hasThinking && (
        <ThinkingBlock
          thinking={message.thinking ?? ""}
          active={Boolean(message.streaming) && !hasContent}
        />
      )}
      {hasSteps && (
        <ToolTimeline steps={message.toolSteps ?? []} hasContent={hasContent} />
      )}
      {hasContent && (
        <div className="text-sm leading-relaxed break-words text-foreground">
          <AssistantMarkdown
            content={message.content}
            citations={message.citations ?? []}
            onCitation={onCitation}
          />
          {showCursor && (
            <span aria-hidden className="ml-0.5 inline-block size-2 animate-pulse rounded-full bg-foreground/70 align-baseline" />
          )}
        </div>
      )}
      {message.error && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive"
        >
          <span className="flex-1 break-words">{message.error}</span>
        </div>
      )}
      {isLastAssistant && !busy && (
        <button
          type="button"
          onClick={onRegenerate}
          className="inline-flex w-fit items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          <RefreshCw className="size-3" aria-hidden />
          重生成
        </button>
      )}
    </div>
  );
}

/** 行内编辑表单(textarea + 保存/取消);Enter 保存、Shift+Enter 换行、Esc 取消。 */
function UserEditForm({
  initial,
  onCancel,
  onSubmit,
}: {
  initial: string;
  onCancel: () => void;
  onSubmit: (text: string) => void;
}) {
  const [text, setText] = useState(initial);
  return (
    <div className="flex w-full max-w-[85%] flex-col gap-2">
      <Textarea
        value={text}
        rows={3}
        autoFocus
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.nativeEvent.isComposing || e.keyCode === 229) return;
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (text.trim()) onSubmit(text);
          }
          if (e.key === "Escape") onCancel();
        }}
        className="resize-none text-sm"
      />
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          取消
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={!text.trim() || text.trim() === initial.trim()}
          onClick={() => onSubmit(text)}
        >
          保存并重生成
        </Button>
      </div>
    </div>
  );
}

/** 历史 tool 消息:一行可展开的「工具记录 · 工具名」,内容默认收起。 */
function ToolMessageRow({ message }: { message: ViewMsg }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col gap-1.5">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="inline-flex w-fit items-center gap-1.5 rounded-md py-0.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronRight
          className={cn("size-3 transition-transform", open && "rotate-90")}
          strokeWidth={2.5}
          aria-hidden
        />
        <Wrench className="size-3" aria-hidden />
        工具记录{message.name ? ` · ${message.name}` : ""}
      </button>
      {open && (
        <div className="max-h-64 overflow-y-auto rounded-md bg-muted/50 px-3 py-2 text-xs leading-relaxed break-words whitespace-pre-wrap text-muted-foreground">
          {message.content || "(无输出)"}
        </div>
      )}
    </div>
  );
}

/**
 * 思考过程折叠块:思考进行中自动展开看推理流,
 * 答复一开始自动收起为「已思考」;用户手动展开/收起后尊重用户选择。
 */
function ThinkingBlock({ thinking, active }: { thinking: string; active: boolean }) {
  const [open, setOpen] = useState(false);
  const userToggled = useRef(false);

  useEffect(() => {
    if (!userToggled.current) setOpen(active && thinking.length > 0);
  }, [active, thinking]);

  if (!thinking) return null;
  return (
    <div className="flex flex-col gap-1.5">
      <button
        type="button"
        onClick={() => {
          userToggled.current = true;
          setOpen((o) => !o);
        }}
        aria-expanded={open}
        className="inline-flex w-fit items-center gap-1.5 rounded-md py-0.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronRight
          className={cn("size-3 transition-transform", open && "rotate-90")}
          strokeWidth={2.5}
          aria-hidden
        />
        <Sparkles
          className={cn("size-3", active && "animate-pulse text-primary")}
          aria-hidden
        />
        {active ? "思考中…" : "已思考"}
      </button>
      {open && (
        <div className="max-h-64 overflow-y-auto rounded-md bg-muted/50 px-3 py-2 text-xs leading-relaxed break-words whitespace-pre-wrap text-muted-foreground">
          {thinking}
        </div>
      )}
    </div>
  );
}

/**
 * 工具调用时间线:执行中展开看每一步(工具名 + 参数 + 结果预览),
 * 答复一出现自动收起为「工具调用 N 步」摘要;用户手动展开/收起后尊重用户选择。
 */
function ToolTimeline({
  steps,
  hasContent,
}: {
  steps: CoworkToolStep[];
  hasContent: boolean;
}) {
  const [open, setOpen] = useState(true);
  const userToggled = useRef(false);

  useEffect(() => {
    if (!userToggled.current) setOpen(!hasContent); // 答复出现前展开、出现后自动收起
  }, [hasContent]);

  if (!steps.length) return null;
  const running = steps[steps.length - 1]?.status === "started";
  return (
    <div className="flex flex-col gap-1.5">
      <button
        type="button"
        onClick={() => {
          userToggled.current = true;
          setOpen((o) => !o);
        }}
        aria-expanded={open}
        className="inline-flex w-fit items-center gap-1.5 rounded-md py-0.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronRight
          className={cn("size-3 transition-transform", open && "rotate-90")}
          strokeWidth={2.5}
          aria-hidden
        />
        <Wrench className="size-3" aria-hidden />
        工具调用 {steps.length} 步{running ? " · 执行中" : ""}
      </button>
      {open && (
        <ul className="ml-[7px] flex flex-col gap-2 border-l border-border/70 pl-3.5">
          {steps.map((s, i) => (
            <li key={i} className="relative flex flex-col gap-1 text-xs text-muted-foreground">
              {/* 节点标记:挂在连线上,运行中是转圈,完成是实心点 */}
              <span className="absolute -left-[19px] top-1 flex size-2.5 items-center justify-center">
                {s.status === "started" ? (
                  <Loader2 className="size-2.5 animate-spin text-amber-600" aria-hidden />
                ) : (
                  <span className="size-1.5 rounded-full bg-muted-foreground/40" aria-hidden />
                )}
              </span>
              <div className="flex min-w-0 items-baseline gap-2">
                <span className="shrink-0 font-mono text-[11px] font-medium text-foreground/80">
                  {s.tool}
                </span>
                {s.args ? (
                  <span className="min-w-0 truncate text-muted-foreground/80" title={s.args}>
                    {s.args}
                  </span>
                ) : null}
              </div>
              {s.status === "done" && s.preview ? (
                <div className="max-h-32 overflow-y-auto rounded-lg bg-muted/50 px-2.5 py-1.5 leading-relaxed break-words whitespace-pre-wrap text-muted-foreground/90">
                  {s.preview}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
