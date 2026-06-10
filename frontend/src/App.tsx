import { useState } from "react";
import { TopBar, type TabKey } from "@/components/TopBar";
import { CaptureView } from "@/views/CaptureView";
import { ProcessIngestView } from "@/views/ProcessIngestView";
import { ProjectsConversationView } from "@/views/ProjectsConversationView";

export default function App() {
  const [activeTab, setActiveTab] = useState<TabKey>("projects");

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <TopBar active={activeTab} onChange={setActiveTab} />
      <main className="flex-1">
        {activeTab === "capture" && <CaptureView />}
        {activeTab === "process" && <ProcessIngestView />}
        {activeTab === "projects" && <ProjectsConversationView />}
      </main>
    </div>
  );
}
