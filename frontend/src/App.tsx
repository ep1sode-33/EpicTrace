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
  // 「重建索引」跨页签跳转用:由项目页触发后,记下要在「信息处理和入库」里聚焦的项目 id;
  // 自增 focusKey 让 ProcessIngestView 即便已挂载也能重新响应同一项目的再次重建。
  const [processFocus, setProcessFocus] = useState<{ projectId: number; key: number } | null>(
    null,
  );

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
            {activeTab === "process" && (
              <ProcessIngestView
                focusProjectId={processFocus?.projectId ?? null}
                focusKey={processFocus?.key ?? 0}
              />
            )}
            {activeTab === "projects" && (
              <ProjectsConversationView
                llmConfigured={llmConfigured}
                onOpenSettings={() => setInSettings(true)}
                onReindexStarted={(projectId) => {
                  // 触发重建后切到「信息处理和入库」并聚焦该项目,在那儿看完整索引进度。
                  setProcessFocus((prev) => ({
                    projectId,
                    key: (prev?.key ?? 0) + 1,
                  }));
                  setInSettings(false);
                  setActiveTab("process");
                }}
              />
            )}
          </>
        )}
      </main>
    </div>
  );
}
