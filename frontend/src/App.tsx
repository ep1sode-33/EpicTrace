import { useCallback, useEffect, useState } from "react";
import { TopBar, type TabKey } from "@/components/TopBar";
import { SettingsModal } from "@/components/SettingsModal";
import { CaptureView } from "@/views/CaptureView";
import { ProcessIngestView } from "@/views/ProcessIngestView";
import { ProjectsConversationView } from "@/views/ProjectsConversationView";
import { api } from "@/lib/api";

export default function App() {
  const [activeTab, setActiveTab] = useState<TabKey>("projects");
  const [settingsOpen, setSettingsOpen] = useState(false);
  // 对话模型是否已配置(api_key_set)。决定 Composer 是否解禁;启动时拉一次,保存后刷新。
  const [llmConfigured, setLlmConfigured] = useState(false);

  const refreshSettings = useCallback(() => {
    api
      .getSettings()
      .then((s) => setLlmConfigured(s.chat_llm.api_key_set))
      .catch(() => {
        /* 设置拉取失败时保持未配置态:Composer 禁用并提示去设置,仍可重试。 */
      });
  }, []);

  useEffect(() => {
    refreshSettings();
  }, [refreshSettings]);

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <TopBar
        active={activeTab}
        onChange={setActiveTab}
        onOpenSettings={() => setSettingsOpen(true)}
      />
      <main className="flex-1">
        {activeTab === "capture" && <CaptureView />}
        {activeTab === "process" && <ProcessIngestView />}
        {activeTab === "projects" && (
          <ProjectsConversationView
            llmConfigured={llmConfigured}
            onOpenSettings={() => setSettingsOpen(true)}
          />
        )}
      </main>

      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onSaved={(s) => setLlmConfigured(s.chat_llm.api_key_set)}
      />
    </div>
  );
}
