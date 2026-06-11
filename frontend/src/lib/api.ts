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
}
export interface ChatMessage {
  id: number; role: "user" | "assistant"; content: string; citations_json: string | null; created_at: string;
}
export interface SourceText { filename: string; path: string; text: string; }
export interface ChatLLMSettings { base_url: string; model: string; api_key_set: boolean; }
export interface Settings { configured: boolean; chat_llm: ChatLLMSettings; }

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
  listMessages: (cid: number) =>
    fetch(`${BASE}/api/conversations/${cid}/messages`).then(j<ChatMessage[]>),
  getSource: (recordId: number) =>
    fetch(`${BASE}/api/source/${recordId}`).then(j<SourceText>),
  getSettings: () => fetch(`${BASE}/api/settings`).then(j<Settings>),
  // api_key 可选:留空(undefined)时不放进请求体,后端据此保留既有 key,避免「只改模型」误清密钥。
  putSettings: (payload: { chat_llm: { base_url: string; api_key?: string; model: string } }) =>
    fetch(`${BASE}/api/settings`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(j<Settings>),

  /**
   * 发消息并流式接收回答。后端是 SSE(events: status/token/citations/done);
   * 因为要 POST,不能用 EventSource——改用 fetch + ReadableStream 手解析 `event:`/`data:` 行。
   * 返回一个 abort 函数:调用即取消本次流(切换会话/卸载时用)。
   */
  sendMessage(cid: number, content: string, h: StreamHandlers): () => void {
    const ctrl = new AbortController();
    (async () => {
      let res: Response;
      try {
        res = await fetch(`${BASE}/api/conversations/${cid}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
          body: JSON.stringify({ content }),
          signal: ctrl.signal,
        });
      } catch (e) {
        if (!ctrl.signal.aborted) h.onError?.(e instanceof Error ? e : new Error(String(e)));
        return;
      }
      if (!res.ok || !res.body) {
        const detail = await res.text().catch(() => "");
        h.onError?.(new Error(`${res.status}: ${detail}`));
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      // 一个 SSE 事件以空行分隔;每行可能是 `event:` 或 `data:`(同事件可有多行 data)。
      // 注:服务端(sse-starlette)用 CRLF,故先把 \r\n 归一为 \n,再按 \n\n 切事件块。
      const dispatch = (event: string, data: string) => {
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
      };
      const flush = (block: string) => {
        let event = "message";
        const dataLines: string[] = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
          // 忽略注释行(`:`)、id:、retry: 等
        }
        if (dataLines.length || event !== "message") dispatch(event, dataLines.join("\n"));
      };

      try {
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
      } catch (e) {
        if (!ctrl.signal.aborted) h.onError?.(e instanceof Error ? e : new Error(String(e)));
      }
    })();
    return () => ctrl.abort();
  },
};
