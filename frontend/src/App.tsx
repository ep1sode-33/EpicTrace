import { useCallback, useEffect, useState } from "react";
import { TopBar, type TabKey } from "@/components/TopBar";
import { SettingsView } from "@/views/SettingsView";
import { CaptureView } from "@/views/CaptureView";
import { CaptureStagingView } from "@/views/CaptureStagingView";
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
  // 采集 tab 内部子视图切换:「采集」或「暂存区」
  const [captureSubTab, setCaptureSubTab] = useState<"capture" | "staging">("capture");
  // 「跳回会话时刻」跨页签跳转用(镜像 processFocus):点引用里的跳回按钮后,记下要在暂存区
  // 展开/定位的 session 与墙钟时刻;自增 key 让 CaptureStagingView 即便已挂载也能重复响应
  //(含对同一 citation 反复跳转)。
  const [sessionFocus, setSessionFocus] = useState<
    { sessionId: number; ts: string; key: number } | null
  >(null);

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
            {activeTab === "capture" && (
              <div className="flex flex-col">
                {/* 采集 / 暂存 段控 */}
                <div className="flex border-b border-border/60 px-6 pt-2">
                  <button
                    type="button"
                    onClick={() => setCaptureSubTab("capture")}
                    className={`border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                      captureSubTab === "capture"
                        ? "border-primary text-foreground"
                        : "border-transparent text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    采集
                  </button>
                  <button
                    type="button"
                    onClick={() => setCaptureSubTab("staging")}
                    className={`border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                      captureSubTab === "staging"
                        ? "border-primary text-foreground"
                        : "border-transparent text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    暂存区
                  </button>
                </div>
                {captureSubTab === "capture" && (
                  <CaptureView
                    onSessionStopped={() => {
                      // 停止后自动切换到暂存区
                      setCaptureSubTab("staging");
                    }}
                  />
                )}
                {captureSubTab === "staging" && (
                  <CaptureStagingView
                    focus={sessionFocus}
                    onOrganized={(pid) => {
                      // 归类后跳到「信息处理和入库」并聚焦该项目
                      setProcessFocus((prev) => ({
                        projectId: pid,
                        key: (prev?.key ?? 0) + 1,
                      }));
                      setInSettings(false);
                      setActiveTab("process");
                    }}
                  />
                )}
              </div>
            )}
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
                onJumpToSession={(sessionId, ts) => {
                  // 引用「跳回会话时刻」:切到采集/暂存区并聚焦目标 session 时刻。
                  // 自增 key 让同一 citation 反复点也能重新触发定位。
                  setSessionFocus((prev) => ({
                    sessionId,
                    ts,
                    key: (prev?.key ?? 0) + 1,
                  }));
                  setInSettings(false);
                  setActiveTab("capture");
                  setCaptureSubTab("staging");
                }}
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
