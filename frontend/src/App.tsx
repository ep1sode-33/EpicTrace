import { useCallback, useEffect, useState } from "react";
import { TopBar, type TabKey } from "@/components/TopBar";
import { SettingsView } from "@/views/SettingsView";
import { CaptureView } from "@/views/CaptureView";
import { ProcessIngestView } from "@/views/ProcessIngestView";
import { ProjectsConversationView } from "@/views/ProjectsConversationView";
import { api } from "@/lib/api";

export default function App() {
  const [activeTab, setActiveTab] = useState<TabKey>("projects");
  // 设置作为整页视图替换主内容区(非弹窗);点任一顶部 Tab 即离开设置。
  const [inSettings, setInSettings] = useState(false);
  // 对话模型是否已配置(settings.configured,与是否有 key 解耦——无密钥的本地端点保存后也算已配置)。
  // 决定 Composer 是否解禁;启动时拉一次,保存后刷新。
  const [llmConfigured, setLlmConfigured] = useState(false);

  const refreshSettings = useCallback(() => {
    api
      .getSettings()
      .then((s) => setLlmConfigured(s.configured))
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
        onChange={(t) => {
          setInSettings(false);
          setActiveTab(t);
        }}
        onOpenSettings={() => setInSettings(true)}
        inSettings={inSettings}
      />
      <main className="flex-1">
        {inSettings ? (
          <SettingsView onSaved={(s) => setLlmConfigured(s.configured)} />
        ) : (
          <>
            {activeTab === "capture" && <CaptureView />}
            {activeTab === "process" && <ProcessIngestView />}
            {activeTab === "projects" && (
              <ProjectsConversationView
                llmConfigured={llmConfigured}
                onOpenSettings={() => setInSettings(true)}
              />
            )}
          </>
        )}
      </main>
    </div>
  );
}
