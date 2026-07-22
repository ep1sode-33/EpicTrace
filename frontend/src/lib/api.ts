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
export interface Citation {
  n: number; ingest_record_id: number; char_start: number; char_end: number; snippet: string; source_type: string;
  source_kind?: "project" | "attachment";
  reference_id?: number | null;
  /** 该引用来自某次采集 session 的 transcript(可跳回时间线时刻);null/缺省=非会话来源。旧消息无此键。 */
  capture_session_id?: number | null;
  /** 引用对应的墙钟时刻(naive-UTC ISO 秒级,无时区后缀);与 capture_session_id 配对用于跳回。 */
  ts?: string | null;
}
export interface SourceText { filename: string; path: string; text: string; }
/** 一个命名的 OpenAI-Compatible 端点。本地单机:api_key 明文回传,允许查看/编辑/复制。 */
export interface LLMProfile {
  id: string; name: string; base_url: string; model: string; api_key: string; api_key_set: boolean;
  context_window: number;
}
export interface ConversationReference {
  id: number; session_id: number; kind: "external" | "internal";
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

/** Embedding provider 设置(需求 8):local = 本地 BGE-M3;remote = OpenAI 兼容 /v1/embeddings。 */
export interface EmbeddingSettings {
  provider: "local" | "remote";
  base_url: string;
  api_key: string;
  model: string;
  dimensions: number;
}

/** Agent 循环设置:最大轮数 / 单轮超时 / 注入 system prompt 的用户自定义指令。 */
export interface AgentSettings {
  max_turns: number;
  turn_timeout_sec: number;
  user_instructions: string;
}

/** 沙箱设置(需求 5):内存/CPU 上限与网络档位(none=完全断网)。 */
export interface SandboxSettings {
  memory_mb: number;
  cpu_sec: number;
  network: "none" | "unrestricted";
}

/** 权限默认模式与工具级覆盖(需求 7)。 */
export interface PermissionSettings {
  mode: string;
  tool_overrides: Record<string, string>;
}

/** ASR 模型大小:large-v3 / medium / small(distil-large-v3 是英语专用,不入中文管线)。 */
export type AsrModel = "large-v3" | "medium" | "small";
/** ASR 可调配置(后端 AsrConfig 的形状)。model + 高级旋钮(VAD/阈值/确认纪律)。 */
export interface AsrSettings {
  model: AsrModel;
  language: string;
  vad: boolean;
  vad_threshold: number;
  /** VAD 最短语音块时长(ms);减少近静音幻觉。 */
  vad_min_speech_ms: number;
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
  /** sounddevice 输入设备索引;null = 系统默认输入。 */
  input_device: number | null;
  /** 每轮喂引擎的有界滑窗回看秒数(STEP 1)。 */
  window_seconds: number;
  /** CTranslate2 计算精度:int8_float32 / int8 / float32(STEP 3)。 */
  compute_type: string;
}
/** 一个可选输入设备(麦克风)。index = sounddevice 设备索引。 */
export interface AsrDevice {
  index: number;
  name: string;
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
/** 实时暂定段(ASR partial)快照:source("mic"|"device") -> 暂定文本。不落库。 */
export type CapturePartial = Record<string, string>;
export type CaptureSession = { id: number; title: string; status: string; started_at: string; ended_at: string | null; sources: string[]; staging_dir: string };
export type CaptureSessionDetail = CaptureSession & {
  events: CaptureEvent[];
  elapsed_seconds: number;
  /** 会话停止后正在整文件重转(权威转录尚未到达);暂存区据此显示「重新转写中…」并轮询。 */
  retranscribing?: boolean;
};

/** 一个 Cowork 会话(agent/chat)。status: idle|thinking|executing|waiting_approval|done|error。 */
export interface CoworkSession {
  id: number;
  type: string;
  parent_id: number | null;
  /** 绑定的项目 id(出现在「项目与对话」对应项目下);null = Cowork 自由会话。 */
  project_id: number | null;
  name: string | null;
  status: string;
  permission_mode: string;
  created_at: string;
  updated_at: string;
}
/** Cowork 会话内的一条消息。role: user|assistant|tool;tool 消息的 name 是工具名。 */
export interface CoworkMessage {
  id: number;
  session_id: number;
  role: "user" | "assistant" | "tool";
  content: string;
  name: string | null;
  tool_call_id: string | null;
  /** assistant 消息的引用链 JSON(与旧对话 citations_json 同契约);无引用为 null。 */
  citations_json: string | null;
  created_at: string;
}
/** Cowork 的一个工具步骤(活动时间线用):started(带参数)→ done(带结果预览)。 */
export interface CoworkToolStep {
  tool: string;
  args?: string;
  status: "started" | "done";
  preview?: string;
}
/** 一条待审批的工具调用(approval_request 事件 / GET /api/cowork/approvals 的元素同形)。 */
export interface CoworkApproval {
  approval_id: string;
  session_id: number;
  tool: string;
  /** JSON 字符串形式的工具参数(如 "{\"project_id\":1,\"path\":\"a.txt\"}")。 */
  args: string;
  /** false 时禁止「本次会话都允许」(删除/派发类工具),前端据此隐藏 session 选项。 */
  allow_session_option: boolean;
  /** permission=工具权限确认;question=ask_user 的自由文本提问(弹窗给文本框)。 */
  kind: "permission" | "question";
  /** kind=question 时 agent 的提问文本。 */
  prompt: string;
}
/** 审批决策:仅此一次 | 本次会话都允许 | 拒绝;kind=question 时是用户的回答文本。 */
export type CoworkApprovalDecision = "once" | "session" | "deny" | (string & {});

/** sendCoworkMessage 的流式回调。每个回调都是可选的;onError 兜底网络/解析/HTTP 错误。 */
export interface CoworkStreamHandlers {
  /** 中文字符串状态文案(如「思考中」)。 */
  onStatus?: (status: string) => void;
  /** 会话状态机变化:thinking|executing|idle|error。 */
  onSessionState?: (status: string) => void;
  /** 模型推理文本(可能没有;逐块累积,前端折叠展示)。 */
  onThinking?: (token: string) => void;
  /** 工具调用步骤(started/done 各一条,同一工具的 done 是 started 的完成回执)。 */
  onToolStep?: (step: CoworkToolStep) => void;
  /** 工具调用待人工确认:agent 循环挂起(session 转 waiting_approval),直到 decideCoworkApproval。 */
  onApprovalRequest?: (approval: CoworkApproval) => void;
  /** 审批已决策(本端或其他端):循环恢复;前端据此兜底关闭确认弹窗。 */
  onApprovalResolved?: (approvalId: string, decision: CoworkApprovalDecision) => void;
  /** 引用链:答案中的 [n] 映射回检索 chunk(最终答复后、done 前发出一次)。 */
  onCitations?: (citations: Citation[]) => void;
  /** 首轮自动标题完成:服务端已改名,data 是新标题;父级据此刷新会话列表。 */
  onSessionRenamed?: (name: string) => void;
  /** assistant 最终答复——Phase 1 为全文一次性(非增量)。 */
  onToken?: (fullText: string) => void;
  onDone?: () => void;
  onError?: (error: Error) => void;
}

/** Cowork SSE 流公共实现:send / regenerate / edit 三个入口同一事件协议。 */
function _coworkStream(url: string, h: CoworkStreamHandlers, init: RequestInit): () => void {
  const ctrl = new AbortController();
  // 解析失败 / HTTP / 网络错误统一经 onError 兜底(consumeSSE 抛出);abort 时静默。
  void consumeSSE(
    url,
    { ...init, signal: ctrl.signal },
    (event, data) => {
      switch (event) {
        case "status": h.onStatus?.(data); break;
        case "session_state":
          try { h.onSessionState?.((JSON.parse(data) as { status: string }).status); }
          catch { /* 状态解析失败不致命:列表轮询会校正徽标 */ }
          break;
        case "thinking": h.onThinking?.(data); break;
        case "tool_step":
          try { h.onToolStep?.(JSON.parse(data) as CoworkToolStep); }
          catch { /* 步骤解析失败不致命:不影响最终答复 */ }
          break;
        case "approval_request":
          try { h.onApprovalRequest?.(JSON.parse(data) as CoworkApproval); }
          catch { /* 审批解析失败不致命:挂起的审批仍可由 listCoworkApprovals 恢复 */ }
          break;
        case "approval_resolved":
          try {
            const r = JSON.parse(data) as { approval_id: string; decision: CoworkApprovalDecision };
            h.onApprovalResolved?.(r.approval_id, r.decision);
          }
          catch { /* 解析失败不致命:弹窗会在用户操作或下次恢复时关闭 */ }
          break;
        case "token": h.onToken?.(data); break;
        case "citations":
          try { h.onCitations?.(JSON.parse(data) as Citation[]); }
          catch { /* 引用解析失败不致命:正文已到,只是没有可点引用 */ }
          break;
        case "session_renamed":
          try { h.onSessionRenamed?.((JSON.parse(data) as { name: string }).name); }
          catch { /* 标题解析失败不致命:列表轮询/刷新会校正 */ }
          break;
        case "error": h.onError?.(new Error(data || "服务端错误")); break;
        case "done": h.onDone?.(); break;
      }
    },
  ).catch((e) => {
    if (!ctrl.signal.aborted) h.onError?.(e instanceof Error ? e : new Error(String(e)));
  });
  return () => ctrl.abort();
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

  renameProject: (id: number, title: string) =>
    fetch(`${BASE}/api/projects/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }).then(j<Project>),
  getSource: (recordId: number) =>
    fetch(`${BASE}/api/source/${recordId}`).then(j<SourceText>),
  getAttachmentSource: (referenceId: number) =>
    fetch(`${BASE}/api/attachment-source/${referenceId}`).then(j<SourceText>),
  listReferences: (sid: number) =>
    fetch(`${BASE}/api/cowork/sessions/${sid}/references`).then(j<ConversationReference[]>),
  addExternalReference: (sid: number, source_path: string) =>
    fetch(`${BASE}/api/cowork/sessions/${sid}/references`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "external", source_path }),
    }).then(j<ConversationReference>),
  addInternalReference: (sid: number, ingest_record_id: number) =>
    fetch(`${BASE}/api/cowork/sessions/${sid}/references`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "internal", ingest_record_id }),
    }).then(j<ConversationReference>),
  detachReference: (sid: number, rid: number) =>
    fetch(`${BASE}/api/cowork/sessions/${sid}/references/${rid}`, { method: "DELETE" }).then((r) => {
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

  getEmbeddingSettings: () =>
    fetch(`${BASE}/api/settings/embedding`).then(j<EmbeddingSettings>),
  putEmbeddingSettings: (payload: Partial<EmbeddingSettings>) =>
    fetch(`${BASE}/api/settings/embedding`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(j<EmbeddingSettings>),

  getAgentSettings: () =>
    fetch(`${BASE}/api/settings/agent`).then(j<AgentSettings>),
  putAgentSettings: (payload: Partial<AgentSettings>) =>
    fetch(`${BASE}/api/settings/agent`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(j<AgentSettings>),
  getSandboxSettings: () =>
    fetch(`${BASE}/api/settings/sandbox`).then(j<SandboxSettings>),
  putSandboxSettings: (payload: Partial<SandboxSettings>) =>
    fetch(`${BASE}/api/settings/sandbox`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(j<SandboxSettings>),
  getPermissionSettings: () =>
    fetch(`${BASE}/api/settings/permissions`).then(j<PermissionSettings>),
  putPermissionSettings: (payload: Partial<PermissionSettings>) =>
    fetch(`${BASE}/api/settings/permissions`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(j<PermissionSettings>),
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
  // 可选输入设备(麦克风)列表。无 sounddevice/PortAudio 时后端回 []。
  getAsrDevices: () =>
    fetch(`${BASE}/api/asr/devices`).then(j<AsrDevice[]>),
  getAsrStatus: () =>
    fetch(`${BASE}/api/asr/status`).then(j<AsrStatus>),
  downloadAsrModel: () =>
    fetch(`${BASE}/api/asr/download-model`, { method: "POST" }).then(j<AsrStatus>),

  startSession: (sources: string[]) =>
    fetch(`${BASE}/api/capture/sessions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sources }) }).then(j<CaptureSession>),
  listSessions: () => fetch(`${BASE}/api/capture/sessions`).then(j<CaptureSession[]>),
  activeSession: () => fetch(`${BASE}/api/capture/sessions/active`).then(j<CaptureSession | null>),
  getSession: (sid: number) => fetch(`${BASE}/api/capture/sessions/${sid}`).then(j<CaptureSessionDetail>),
  // 实时暂定段快照(内存态,不落库)。HUD 在现有轮询里与 getSession 一起拉,渲染为「暂定」行。
  getSessionPartial: (sid: number) => fetch(`${BASE}/api/capture/sessions/${sid}/partial`).then(j<CapturePartial>),
  // 期望开启音源集(内存态):当前开启的音频源 id 列表("mic"/"system_audio")。worker 周期性轮询同一接口。
  getAsrSources: (sid: number) =>
    fetch(`${BASE}/api/capture/sessions/${sid}/asr-source`).then(j<{ enabled: string[] }>),
  // 启停某路音频源(真启停采集,中途也能开开始没勾的源):204,无 body。模型未就绪时启用会 409。
  setAsrSource: (sid: number, source: string, enabled: boolean) =>
    fetch(`${BASE}/api/capture/sessions/${sid}/asr-source`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source, enabled }),
    }).then((r) => {
      if (!r.ok) throw new Error(`${r.status}`);
    }),
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

  // 会话列表过滤:project_id → 该项目绑定会话(「项目与对话」);free_only → 仅自由会话(Cowork tab);皆空 → 全部。
  listCoworkSessions: (filter?: { project_id?: number; free_only?: boolean }) => {
    const qs = new URLSearchParams();
    if (filter?.project_id != null) qs.set("project_id", String(filter.project_id));
    if (filter?.free_only) qs.set("free_only", "true");
    const suffix = qs.size ? `?${qs.toString()}` : "";
    return fetch(`${BASE}/api/cowork/sessions${suffix}`).then(j<CoworkSession[]>);
  },
  createCoworkSession: (payload?: { type?: string; name?: string; permission_mode?: string; project_id?: number }) =>
    fetch(`${BASE}/api/cowork/sessions`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload ?? {}),
    }).then(j<CoworkSession>),
  updateCoworkSession: (sid: number, patch: { name?: string; permission_mode?: string }) =>
    fetch(`${BASE}/api/cowork/sessions/${sid}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).then(j<CoworkSession>),
  getCoworkSession: (sid: number) =>
    fetch(`${BASE}/api/cowork/sessions/${sid}`).then(j<CoworkSession>),
  deleteCoworkSession: (sid: number) =>
    fetch(`${BASE}/api/cowork/sessions/${sid}`, { method: "DELETE" }).then((r) => {
      // 后端在缺失时返回 404;视为「已不在」,与删除成功同样处理。
      if (!r.ok && r.status !== 404) throw new Error(`${r.status}: ${r.statusText}`);
    }),
  /** 停止当前运行的 agent 循环(后端取消;幂等)。 */
  stopCoworkSession: (sid: number) =>
    fetch(`${BASE}/api/cowork/sessions/${sid}/stop`, { method: "POST" }).then((r) => {
      if (!r.ok && r.status !== 404) throw new Error(`${r.status}: ${r.statusText}`);
    }),
  listCoworkMessages: (sid: number) =>
    fetch(`${BASE}/api/cowork/sessions/${sid}/messages`).then(j<CoworkMessage[]>),
  // 当前待审批的工具调用列表:视图挂载/切换会话后拉一次,用于恢复确认弹窗。
  listCoworkApprovals: () =>
    fetch(`${BASE}/api/cowork/approvals`).then(j<CoworkApproval[]>),
  // 提交审批决策(once/session/deny);404 表示已决策/不存在——视为已处理,与成功同样静默。
  decideCoworkApproval: (approvalId: string, decision: CoworkApprovalDecision) =>
    fetch(`${BASE}/api/cowork/approvals/${encodeURIComponent(approvalId)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    }).then((r) => {
      if (!r.ok && r.status !== 404) throw new Error(`${r.status}: ${r.statusText}`);
    }),

  /**
   * 向 Cowork 会话发消息并流式接收执行过程。后端是 SSE
   * (events: status/session_state/thinking/tool_step/approval_request/approval_resolved/token/citations/session_renamed/error/done);
   * 因为要 POST,不能用 EventSource——复用 consumeSSE 手解析 `event:`/`data:` 行。
   * token 是最终答复全文一次性到达(Phase 1 不做增量流式)。
   * 返回一个 abort 函数:调用即取消本次流(切换会话/停止/卸载时用)。
   */
  sendCoworkMessage(sid: number, content: string, h: CoworkStreamHandlers): () => void {
    return _coworkStream(`${BASE}/api/cowork/sessions/${sid}/messages`, h, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ content }),
    });
  },

  /** 重生成最后一轮(后端删最后 user 消息之后的消息并重跑);事件协议同 sendCoworkMessage。 */
  regenerateCowork(sid: number, h: CoworkStreamHandlers): () => void {
    return _coworkStream(`${BASE}/api/cowork/sessions/${sid}/regenerate`, h, {
      method: "POST",
      headers: { Accept: "text/event-stream" },
    });
  },

  /** 编辑某条 user 消息并就地重生成;事件协议同 sendCoworkMessage。 */
  editCoworkMessage(sid: number, mid: number, content: string, h: CoworkStreamHandlers): () => void {
    return _coworkStream(`${BASE}/api/cowork/sessions/${sid}/messages/${mid}/edit`, h, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ content }),
    });
  },

  /**
   * 附加外部文件(MinerU 解析),流式回报进度。后端 SSE 事件:
   * status(多次,实时进度文案)/ done(成功,data 是 ReferenceOut JSON)/ error(失败,nothing persisted)。
   * 阻塞直到 done/error:附件在 done 之前不可用——这里只是把仍在「等」的过程可视化。
   * 复用 consumeSSE 同一套 SSE 解析(fetch + ReadableStream 手解析 event:/data: 行)。
   * 返回的 Promise 在流结束(done/error/网络错误处理完)后 resolve。
   */
  attachExternalStream(
    sid: number,
    sourcePath: string,
    cb: {
      onStatus?: (text: string) => void;
      onDone?: (ref: ConversationReference) => void;
      onError?: (message: string) => void;
    },
  ): Promise<void> {
    return consumeSSE(
      `${BASE}/api/cowork/sessions/${sid}/references/stream`,
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
 * fetch 一个 SSE 端点,手解析 `event:`/`data:` 行,对每个事件调用 onEvent(event, data)。
 * _coworkStream 与 attachExternalStream 共用同一套解析逻辑——不另起一套机制。
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
