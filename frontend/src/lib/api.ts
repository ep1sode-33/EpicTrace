const BASE = "";

export interface Project { id: number; title: string; folder_path: string; created_at: string; }
export interface IngestRecord {
  id: number; project_id: number; original_filename: string; stored_path: string;
  content_hash: string; size_bytes: number; ingest_method: string; description: string; indexed: boolean; created_at: string;
}
export interface ScanResult { added: number; missing: number; }
export interface IndexStatus {
  project_id: number; total: number; done: number; status: string; errors: string[];
}
export interface Conversation { id: number; project_id: number; title: string; created_at: string; }
export interface Citation {
  n: number; ingest_record_id: number; char_start: number; char_end: number; snippet: string; source_type: string;
  source_kind?: "project" | "attachment";
  reference_id?: number | null;
}
export interface ChatMessage {
  id: number; role: "user" | "assistant"; content: string; citations_json: string | null; created_at: string;
}
export interface SourceText { filename: string; path: string; text: string; }
/** 一个命名的 OpenAI-Compatible 端点。本地单机:api_key 明文回传,允许查看/编辑/复制。 */
export interface LLMProfile {
  id: string; name: string; base_url: string; model: string; api_key: string; api_key_set: boolean;
  context_window: number;
}
export interface ConversationReference {
  id: number; conversation_id: number; kind: "external" | "internal";
  display_name: string; source_path: string | null; ingest_record_id: number | null;
  mode: "fulltext" | "focus" | "indexed" | "deferred"; text_chars: number; detached: boolean; created_at: string;
}
export interface Settings {
  configured: boolean;
  active_profile_id: string | null;
  profiles: LLMProfile[];
}
export interface ExtractionStatus {
  state:
    | "not_installed"
    | "installing"
    | "installed_no_models"
    | "downloading_models"
    | "ready"
    | "failed";
  ready: boolean;
  error?: string | null;
  /** install | download | null —— 区分装包失败与下模型失败,前端据此把「重试」指向正确动作。 */
  failed_stage?: "install" | "download" | null;
}
export interface ExtractionSettings {
  engine: "pypdf" | "mineru";
  effort: "high" | "medium";
  model_source: "modelscope" | "huggingface" | "local";
}

/** ASR 模型大小:large-v3 / distil-large-v3 / medium / small。 */
export type AsrModel = "large-v3" | "distil-large-v3" | "medium" | "small";
/** ASR 可调配置(后端 AsrConfig 的形状)。model + 高级旋钮(VAD/阈值/确认纪律)。 */
export interface AsrSettings {
  model: AsrModel;
  language: string;
  vad: boolean;
  vad_threshold: number;
  no_speech: number;
  log_prob: number;
  compression_ratio: number;
  repetition_penalty: number;
  no_repeat_ngram: number;
  condition_prev: boolean;
  halluc_silence: number | null;
  force_confirm_after: number;
  stall_seek_seconds: number;
  rms_normalize: boolean;
  halluc_filter_enabled: boolean;
}
export interface AsrStatus {
  /** not_downloaded | downloading | ready | failed */
  state: "not_downloaded" | "downloading" | "ready" | "failed";
  ready: boolean;
  /** 状态针对的模型(== 当前配置选中的 model)。 */
  model: string;
  error?: string | null;
}

export type CaptureEvent = { id: number; kind: string; ts: string; payload: string; meta: Record<string, unknown> };
export type CaptureSession = { id: number; title: string; status: string; started_at: string; ended_at: string | null; sources: string[]; staging_dir: string };
export type CaptureSessionDetail = CaptureSession & { events: CaptureEvent[]; elapsed_seconds: number };

/** sendMessage 的流式回调。每个回调都是可选的;onError 兜底网络/解析/HTTP 错误。 */
export interface StreamHandlers {
  onStatus?: (status: string) => void;
  onToken?: (token: string) => void;
  onCitations?: (citations: Citation[]) => void;
  onDone?: () => void;
  onError?: (error: Error) => void;
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const api = {
  listProjects: () => fetch(`${BASE}/api/projects`).then(j<Project[]>),
  createProject: (title: string, folder_path: string) =>
    fetch(`${BASE}/api/projects`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, folder_path }),
    }).then(j<Project>),
  listFiles: (projectId: number) =>
    fetch(`${BASE}/api/files?project_id=${projectId}`).then(j<IngestRecord[]>),
  ingestFile: (project_id: number, source_path: string, description: string) =>
    fetch(`${BASE}/api/files/ingest`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id, source_path, ingest_method: "file_direct", description }),
    }).then(j<IngestRecord>),
  scanProject: (projectId: number) =>
    fetch(`${BASE}/api/projects/${projectId}/scan`, { method: "POST" }).then(j<ScanResult>),
  indexProject: (projectId: number) =>
    fetch(`${BASE}/api/projects/${projectId}/index`, { method: "POST" }).then(j<IndexStatus>),
  indexStatus: (projectId: number) =>
    fetch(`${BASE}/api/projects/${projectId}/index/status`).then(j<IndexStatus>),
  reindexProject: (projectId: number) =>
    fetch(`${BASE}/api/projects/${projectId}/reindex`, { method: "POST" }).then(j<IndexStatus>),
  deleteProject: (projectId: number, deleteFolder: boolean) =>
    fetch(
      `${BASE}/api/projects/${projectId}?delete_folder=${deleteFolder}`,
      { method: "DELETE" },
    ).then(j<{ deleted: boolean; project_id: number; folder_path: string | null }>),

  listConversations: (projectId: number) =>
    fetch(`${BASE}/api/projects/${projectId}/conversations`).then(j<Conversation[]>),
  createConversation: (projectId: number, title?: string) =>
    fetch(`${BASE}/api/projects/${projectId}/conversations`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title ?? null }),
    }).then(j<Conversation>),
  deleteConversation: (cid: number) =>
    fetch(`${BASE}/api/conversations/${cid}`, { method: "DELETE" }).then((r) => {
      // 后端在缺失时返回 404;视为「已不在」,与删除成功同样处理。
      if (!r.ok && r.status !== 404) throw new Error(`${r.status}: ${r.statusText}`);
    }),
  renameConversation: (cid: number, title: string) =>
    fetch(`${BASE}/api/conversations/${cid}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }).then(j<Conversation>),
  renameProject: (id: number, title: string) =>
    fetch(`${BASE}/api/projects/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }).then(j<Project>),
  listMessages: (cid: number) =>
    fetch(`${BASE}/api/conversations/${cid}/messages`).then(j<ChatMessage[]>),
  getSource: (recordId: number) =>
    fetch(`${BASE}/api/source/${recordId}`).then(j<SourceText>),
  getAttachmentSource: (referenceId: number) =>
    fetch(`${BASE}/api/attachment-source/${referenceId}`).then(j<SourceText>),
  listReferences: (cid: number) =>
    fetch(`${BASE}/api/conversations/${cid}/references`).then(j<ConversationReference[]>),
  addExternalReference: (cid: number, source_path: string) =>
    fetch(`${BASE}/api/conversations/${cid}/references`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "external", source_path }),
    }).then(j<ConversationReference>),
  addInternalReference: (cid: number, ingest_record_id: number) =>
    fetch(`${BASE}/api/conversations/${cid}/references`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "internal", ingest_record_id }),
    }).then(j<ConversationReference>),
  detachReference: (cid: number, rid: number) =>
    fetch(`${BASE}/api/conversations/${cid}/references/${rid}`, { method: "DELETE" }).then((r) => {
      if (!r.ok && r.status !== 404) throw new Error(`${r.status}: ${r.statusText}`);
    }),
  getSettings: () => fetch(`${BASE}/api/settings`).then(j<Settings>),
  createProfile: (payload: { name: string; base_url: string; api_key: string; model: string; context_window?: number }) =>
    fetch(`${BASE}/api/settings/profiles`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(j<Settings>),
  // api_key 留空(undefined/空串)时后端保留既有 key,避免「只改模型」误清密钥。
  updateProfile: (
    id: string,
    payload: { name?: string; base_url?: string; api_key?: string; model?: string; context_window?: number },
  ) =>
    fetch(`${BASE}/api/settings/profiles/${id}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(j<Settings>),
  deleteProfile: (id: string) =>
    fetch(`${BASE}/api/settings/profiles/${id}`, { method: "DELETE" }).then(j<Settings>),
  setActiveProfile: (id: string) =>
    fetch(`${BASE}/api/settings/active`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile_id: id }),
    }).then(j<Settings>),
  // 测试连接:对正在编辑的值发一次真实最小补全。失败也是 200(ok:false + 原始错误)。
  testProfile: (payload: { base_url: string; api_key: string; model: string }) =>
    fetch(`${BASE}/api/settings/test`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(j<{ ok: boolean; sample?: string; error?: string }>),

  getExtractionStatus: () =>
    fetch(`${BASE}/api/extraction/status`).then(j<ExtractionStatus>),
  provisionExtraction: () =>
    fetch(`${BASE}/api/extraction/provision`, { method: "POST" }).then(j<ExtractionStatus>),
  getExtractionSettings: () =>
    fetch(`${BASE}/api/extraction/settings`).then(j<ExtractionSettings>),
  putExtractionSettings: (payload: ExtractionSettings) =>
    fetch(`${BASE}/api/extraction/settings`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(j<ExtractionSettings>),
  downloadModels: () =>
    fetch(`${BASE}/api/extraction/download-models`, { method: "POST" }).then(j<ExtractionStatus>),

  getAsrSettings: () =>
    fetch(`${BASE}/api/asr/settings`).then(j<AsrSettings>),
  // 部分更新:只传改动的键(如 {model}),后端合并进现有设置(其余旋钮保留)。
  putAsrSettings: (patch: Partial<AsrSettings>) =>
    fetch(`${BASE}/api/asr/settings`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).then(j<AsrSettings>),
  getAsrStatus: () =>
    fetch(`${BASE}/api/asr/status`).then(j<AsrStatus>),
  downloadAsrModel: () =>
    fetch(`${BASE}/api/asr/download-model`, { method: "POST" }).then(j<AsrStatus>),

  startSession: (sources: string[]) =>
    fetch(`${BASE}/api/capture/sessions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sources }) }).then(j<CaptureSession>),
  listSessions: () => fetch(`${BASE}/api/capture/sessions`).then(j<CaptureSession[]>),
  activeSession: () => fetch(`${BASE}/api/capture/sessions/active`).then(j<CaptureSession | null>),
  getSession: (sid: number) => fetch(`${BASE}/api/capture/sessions/${sid}`).then(j<CaptureSessionDetail>),
  appendEvent: (sid: number, kind: string, payload = "") =>
    fetch(`${BASE}/api/capture/sessions/${sid}/events`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind, payload }) }).then(j<CaptureEvent>),
  pauseSession: (sid: number) => fetch(`${BASE}/api/capture/sessions/${sid}/pause`, { method: "POST" }).then(() => {}),
  resumeSession: (sid: number) => fetch(`${BASE}/api/capture/sessions/${sid}/resume`, { method: "POST" }).then(() => {}),
  stopSession: (sid: number) => fetch(`${BASE}/api/capture/sessions/${sid}/stop`, { method: "POST" }).then(j<CaptureSession>),
  renameSession: (sid: number, title: string) =>
    fetch(`${BASE}/api/capture/sessions/${sid}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) }).then(j<CaptureSession>),
  deleteSession: (sid: number) => fetch(`${BASE}/api/capture/sessions/${sid}`, { method: "DELETE" }).then(() => {}),
  organizeSession: (sid: number, projectId: number) =>
    fetch(`${BASE}/api/capture/sessions/${sid}/organize`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project_id: projectId }) }).then(j<IndexStatus>),

  /**
   * 发消息并流式接收回答。后端是 SSE(events: status/token/citations/done);
   * 因为要 POST,不能用 EventSource——改用 fetch + ReadableStream 手解析 `event:`/`data:` 行。
   * 返回一个 abort 函数:调用即取消本次流(切换会话/卸载时用)。
   */
  sendMessage(cid: number, content: string, h: StreamHandlers): () => void {
    return streamSSE(`${BASE}/api/conversations/${cid}/messages`, h, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ content }),
    });
  },

  /**
   * 重新生成最后一轮回答(用户无需重输)。后端删掉最后一条 user 消息之后的消息、
   * 对同一提问重跑同一流水线;事件流与 sendMessage 一致(status/token/citations/done,失败发 error)。
   * 返回一个 abort 函数。
   */
  regenerate(cid: number, h: StreamHandlers): () => void {
    return streamSSE(`${BASE}/api/conversations/${cid}/regenerate`, h, {
      method: "POST",
      headers: { Accept: "text/event-stream" },
    });
  },

  /**
   * 编辑某条 user 消息并就地重生成。后端把该消息内容改为 content、删它之后的全部消息、
   * 以它之前的消息作历史对新内容重跑流水线;事件流与 sendMessage 一致
   * (status/token/citations/done,失败发 error)。返回一个 abort 函数。
   */
  editMessage(cid: number, mid: number, content: string, h: StreamHandlers): () => void {
    return streamSSE(`${BASE}/api/conversations/${cid}/messages/${mid}/edit`, h, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ content }),
    });
  },

  /**
   * 附加外部文件(MinerU 解析),流式回报进度。后端 SSE 事件:
   * status(多次,实时进度文案)/ done(成功,data 是 ReferenceOut JSON)/ error(失败,nothing persisted)。
   * 阻塞直到 done/error:附件在 done 之前不可用——这里只是把仍在「等」的过程可视化。
   * 复用 sendMessage 同款 SSE 解析(fetch + ReadableStream 手解析 event:/data: 行)。
   * 返回的 Promise 在流结束(done/error/网络错误处理完)后 resolve。
   */
  attachExternalStream(
    cid: number,
    sourcePath: string,
    cb: {
      onStatus?: (text: string) => void;
      onDone?: (ref: ConversationReference) => void;
      onError?: (message: string) => void;
    },
  ): Promise<void> {
    return consumeSSE(
      `${BASE}/api/conversations/${cid}/references/stream`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ kind: "external", source_path: sourcePath }),
      },
      (event, data) => {
        switch (event) {
          case "status":
            cb.onStatus?.(data);
            break;
          case "done":
            try {
              cb.onDone?.(JSON.parse(data) as ConversationReference);
            } catch (e) {
              cb.onError?.(`解析结果失败:${e instanceof Error ? e.message : String(e)}`);
            }
            break;
          case "error":
            cb.onError?.(data || "服务端错误");
            break;
        }
      },
    );
  },
};

/**
 * POST 一个 SSE 端点并流式分发其事件(status/token/citations/done/error)。
 * sendMessage / regenerate 共用;底层 fetch + ReadableStream 解析复用 consumeSSE。
 * 返回一个 abort 函数:调用即取消本次流(切换会话/卸载时用)。
 */
function streamSSE(url: string, h: StreamHandlers, init: RequestInit): () => void {
  const ctrl = new AbortController();
  // 解析失败 / HTTP / 网络错误统一经 onError 兜底(consumeSSE 抛出);abort 时静默。
  void consumeSSE(url, { ...init, signal: ctrl.signal }, (event, data) => {
    switch (event) {
      case "status": h.onStatus?.(data); break;
      case "token": h.onToken?.(data); break;
      case "citations":
        try { h.onCitations?.(JSON.parse(data) as Citation[]); }
        catch { /* 引用解析失败不致命:答案正文已经流式呈现 */ }
        break;
      case "error": h.onError?.(new Error(data || "服务端错误")); break;
      case "done": h.onDone?.(); break;
    }
  }).catch((e) => {
    if (!ctrl.signal.aborted) h.onError?.(e instanceof Error ? e : new Error(String(e)));
  });
  return () => ctrl.abort();
}

/**
 * fetch 一个 SSE 端点,手解析 `event:`/`data:` 行,对每个事件调用 onEvent(event, data)。
 * sendMessage(经 streamSSE)与 attachExternalStream 共用同一套解析逻辑——不另起一套机制。
 * 流正常结束时 resolve;HTTP/网络/解析层错误 reject(由调用方决定如何上报)。
 */
async function consumeSSE(
  url: string,
  init: RequestInit,
  onEvent: (event: string, data: string) => void,
): Promise<void> {
  const res = await fetch(url, init);
  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${res.status}: ${detail}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  // 一个 SSE 事件以空行分隔;每行可能是 `event:` 或 `data:`(同事件可有多行 data)。
  // 注:服务端(sse-starlette)用 CRLF,故先把 \r\n 归一为 \n,再按 \n\n 切事件块。
  const flush = (block: string) => {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
      // 忽略注释行(`:`)、id:、retry: 等
    }
    if (dataLines.length || event !== "message") onEvent(event, dataLines.join("\n"));
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // CRLF → LF 归一在「累积缓冲」上做,才能吃掉跨 chunk 边界的 \r\n(sse-starlette 用 \r\n\r\n);
    // 已消费部分已 slice 掉,对整段重复归一是幂等且安全的。
    buf = buf.replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buf.indexOf("\n\n")) !== -1) {
      const block = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      if (block.trim()) flush(block);
    }
  }
  if (buf.trim()) flush(buf.replace(/\r\n/g, "\n")); // 收尾:无尾随空行时残留的最后一个事件
}
