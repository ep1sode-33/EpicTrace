import { useState } from "react";
import { ProjectPanel } from "@/components/ProjectPanel";
import { IngestPanel } from "@/components/IngestPanel";
import { type Project } from "@/lib/api";

export default function App() {
  const [selected, setSelected] = useState<Project | null>(null);
  return (
    <div className="grid grid-cols-2 gap-8 p-8 max-w-5xl mx-auto">
      <ProjectPanel selected={selected} onSelect={setSelected} />
      {selected ? <IngestPanel project={selected} /> : <p className="text-muted-foreground">← 选或建一个 Project</p>}
    </div>
  );
}
