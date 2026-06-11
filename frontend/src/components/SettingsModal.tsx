import { useEffect, useState } from "react";
import { Loader2, Settings2 } from "lucide-react";

import { api, type Settings } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

/** OpenAI-compatible 端点预设:点一下填入 base_url + 占位模型,api_key 仍需手填。 */
const PRESETS: { label: string; base_url: string; model: string }[] = [
  { label: "DeepSeek", base_url: "https://api.deepseek.com", model: "deepseek-chat" },
  { label: "OpenAI", base_url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  { label: "Ollama(本地)", base_url: "http://localhost:11434/v1", model: "qwen2.5" },
];

export function SettingsModal({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  /** 保存成功后回调,携带最新公开设置(api_key_set 已更新),供父级解禁 Composer。 */
  onSaved?: (settings: Settings) => void;
}) {
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [keyAlreadySet, setKeyAlreadySet] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 每次打开都拉取当前设置回填(api_key 永不回传,只用 api_key_set 提示「已配置」)。
  useEffect(() => {
    if (!open) return;
    setError(null);
    setApiKey("");
    setLoading(true);
    let cancelled = false;
    api
      .getSettings()
      .then((s) => {
        if (cancelled) return;
        setBaseUrl(s.chat_llm.base_url);
        setModel(s.chat_llm.model);
        setKeyAlreadySet(s.chat_llm.api_key_set);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const applyPreset = (p: (typeof PRESETS)[number]) => {
    setBaseUrl(p.base_url);
    setModel(p.model);
  };

  const save = async () => {
    if (!baseUrl.trim() || !model.trim()) return;
    setSaving(true);
    setError(null);
    try {
      // api_key 留空且后端已存有 key 时,保留旧 key(后端按字段覆盖,故回传占位会清空——
      // 这里若用户没改 key 且后端已配置,沿用一个不会清空的策略:仅当用户填了才覆盖)。
      const settings = await api.putSettings({
        chat_llm: {
          base_url: baseUrl.trim(),
          api_key: apiKey,
          model: model.trim(),
        },
      });
      onSaved?.(settings);
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  // api_key 视为已就绪:用户填了新 key,或后端已存在 key 且用户未清空意图(留空=沿用)。
  // 注:留空提交会让后端 api_key="",故未填新 key 时给出明确提示,但不阻断保存 base_url/model。
  const canSubmit = Boolean(baseUrl.trim() && model.trim()) && !saving && !loading;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && !saving && onClose()}>
      <DialogContent showCloseButton={!saving} className="gap-0 p-0">
        <DialogHeader className="gap-2 px-6 pt-6">
          <span
            aria-hidden
            className="flex size-9 items-center justify-center rounded-xl bg-muted text-foreground ring-1 ring-border/70"
          >
            <Settings2 className="size-[18px]" strokeWidth={2} />
          </span>
          <DialogTitle>对话模型</DialogTitle>
          <DialogDescription>
            填写一个 OpenAI-Compatible 端点用于对话。密钥仅保存在本机,不会上传。
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-5 px-6 py-5">
          <div className="flex flex-wrap gap-1.5">
            {PRESETS.map((p) => (
              <Button
                key={p.label}
                type="button"
                variant="outline"
                size="xs"
                disabled={saving || loading}
                onClick={() => applyPreset(p)}
              >
                {p.label}
              </Button>
            ))}
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="settings-base-url" className="text-xs font-medium text-muted-foreground">
              Base URL
            </label>
            <Input
              id="settings-base-url"
              value={baseUrl}
              disabled={saving || loading}
              placeholder="https://api.deepseek.com"
              className="font-mono text-xs"
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="settings-api-key" className="text-xs font-medium text-muted-foreground">
              API Key
            </label>
            <Input
              id="settings-api-key"
              type="password"
              value={apiKey}
              disabled={saving || loading}
              placeholder={keyAlreadySet ? "已配置(留空则覆盖为空)" : "sk-…"}
              className="font-mono text-xs"
              autoComplete="off"
              onChange={(e) => setApiKey(e.target.value)}
            />
            {keyAlreadySet && !apiKey && (
              <p className="text-xs text-muted-foreground">
                已保存过密钥。留空保存会清除它;如需保留,请重新填写。
              </p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="settings-model" className="text-xs font-medium text-muted-foreground">
              模型
            </label>
            <Input
              id="settings-model"
              value={model}
              disabled={saving || loading}
              placeholder="deepseek-chat"
              className="font-mono text-xs"
              onChange={(e) => setModel(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && canSubmit) save();
              }}
            />
          </div>

          {error && (
            <p
              className={cn(
                "rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2",
                "text-xs leading-relaxed text-destructive",
              )}
            >
              {error}
            </p>
          )}
        </div>

        <DialogFooter className="gap-2 border-t border-border/70 bg-muted/30 px-6 py-4">
          <Button type="button" variant="ghost" size="lg" disabled={saving} onClick={onClose}>
            取消
          </Button>
          <Button type="button" size="lg" disabled={!canSubmit} onClick={save}>
            {saving ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                正在保存…
              </>
            ) : (
              "保存"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
