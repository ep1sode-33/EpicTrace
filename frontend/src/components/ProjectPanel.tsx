import { useEffect, useState } from "react";
import { api, type Project } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function ProjectPanel({
  selected, onSelect,
}: { selected: Project | null; onSelect: (p: Project) => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [title, setTitle] = useState("");
  const [folder, setFolder] = useState("");

  const refresh = () => api.listProjects().then(setProjects).catch(console.error);
  useEffect(() => { refresh(); }, []);

  const create = async () => {
    if (!title || !folder) return;
    const p = await api.createProject(title, folder);
    setTitle(""); setFolder("");
    await refresh();
    onSelect(p);
  };

  return (
    <div className="space-y-3">
      <h2 className="text-lg font-semibold">Projects</h2>
      <div className="flex gap-2">
        <Input placeholder="title (e.g. CS 2506)" value={title} onChange={(e) => setTitle(e.target.value)} />
        <Input placeholder="folder path (/Users/.../CS 2506)" value={folder} onChange={(e) => setFolder(e.target.value)} />
        <Button onClick={create}>Create</Button>
      </div>
      <ul className="space-y-1">
        {projects.map((p) => (
          <li key={p.id}>
            <button
              className={`w-full text-left px-2 py-1 rounded ${selected?.id === p.id ? "bg-accent" : "hover:bg-muted"}`}
              onClick={() => onSelect(p)}
            >
              {p.title} <span className="text-xs text-muted-foreground">{p.folder_path}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
