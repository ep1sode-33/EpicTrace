import { useEffect, useState } from "react";
import { api, type IngestRecord, type Project } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export function IngestPanel({ project }: { project: Project }) {
  const [files, setFiles] = useState<IngestRecord[]>([]);
  const [path, setPath] = useState("");
  const [desc, setDesc] = useState("");

  const refresh = () => api.listFiles(project.id).then(setFiles).catch(console.error);
  useEffect(() => { refresh(); }, [project.id]);

  const ingest = async () => {
    if (!path) return;
    await api.ingestFile(project.id, path, desc);
    setPath(""); setDesc("");
    await refresh();
  };

  return (
    <div className="space-y-3">
      <h2 className="text-lg font-semibold">Ingest into "{project.title}"</h2>
      <Input placeholder="absolute file path" value={path} onChange={(e) => setPath(e.target.value)} />
      <Textarea placeholder="optional description (按 Enter 留空也行)" value={desc} onChange={(e) => setDesc(e.target.value)} />
      <Button onClick={ingest}>Ingest file</Button>
      <ul className="text-sm space-y-1">
        {files.map((f) => (
          <li key={f.id} className="border-b py-1">
            <b>{f.original_filename}</b> · {f.size_bytes}B · {f.description}
          </li>
        ))}
      </ul>
    </div>
  );
}
