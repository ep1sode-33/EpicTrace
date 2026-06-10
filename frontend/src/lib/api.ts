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
};
