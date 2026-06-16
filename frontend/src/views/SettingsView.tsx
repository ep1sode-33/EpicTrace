import { useEffect, useRef, useState } from "react";
import {
  Check,
  CheckCircle2,
  Loader2,
  Pencil,
  Plug,
  Plus,
  Settings2,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";

import {
  api,
  type AsrModel,
  type AsrSettings,
  type AsrStatus,
  type ExtractionSettings,
  type ExtractionStatus,
  type LLMProfile,
  type Settings,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type FormState = { name: string; base_url: string; api_key: string; model: string; context_window: string };
const BLANK: FormState = { name: "", base_url: "", api_key: "", model: "", context_window: "32768" };

/**
 * 「模型配置」整页设置视图(替换主内容区,非弹窗)。
 * 现仅一节(对话模型 Profile 管理);后续设置项以新的 <section> 追加即可。
 */
export function SettingsView({
  onSaved,
}: {
  /** 任一变更成功后回调,携带最新公开设置(configured 已更新),供父级解禁 Composer。 */
  onSaved?: (settings: Settings) => void;
}) {
  const [profiles, setProfiles] = useState<LLMProfile[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 表单态:editingId === null 表示「新建」;非空表示编辑该 Profile。undefined === 表单关闭。
  const [editingId, setEditingId] = useState<string | null | undefined>(undefined);
  const [form, setForm] = useState<FormState>(BLANK);
  const [busy, setBusy] = useState<string | null>(null); // "save" | "active:<id>" | "delete:<id>"
  const nameRef = useRef<HTMLInputElement>(null);

  const apply = (s: Settings) => {
    setProfiles(s.profiles);
    setActiveId(s.active_profile_id);
    onSaved?.(s);
  };

  // 进入页面时拉取当前设置(api_key 永不回传)。
  useEffect(() => {
    let cancelled = false;
    api
      .getSettings()
      .then((s) => {
        if (cancelled) return;
        setProfiles(s.profiles);
        setActiveId(s.active_profile_id);
      })
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  // 表单打开时聚焦名称输入。
  useEffect(() => {
    if (editingId !== undefined) nameRef.current?.focus();
  }, [editingId]);

  const formOpen = editingId !== undefined;
  const anyBusy = busy !== null;

  const openCreate = () => {
    setError(null);
    setForm(BLANK);
    setEditingId(null);
  };

  const openEdit = (p: LLMProfile) => {
    setError(null);
    // 回填全部字段,含真 key(本地单机,可见可编辑可复制)。
    setForm({
      name: p.name,
      base_url: p.base_url,
      api_key: p.api_key,
      model: p.model,
      context_window: String(p.context_window ?? 32768),
    });
    setEditingId(p.id);
  };

  const closeForm = () => {
    setEditingId(undefined);
    setForm(BLANK);
    setError(null);
  };

  const canSubmit =
    Boolean(form.name.trim() && form.base_url.trim() && form.model.trim()) && !anyBusy;

  const save = async () => {
    if (!canSubmit) return;
    setBusy("save");
    setError(null);
    try {
      const key = form.api_key.trim();
      let s: Settings;
      if (typeof editingId === "string") {
        // 编辑:总是回传 key(可见可编辑;清空即清空)。
        s = await api.updateProfile(editingId, {
          name: form.name.trim(),
          base_url: form.base_url.trim(),
          model: form.model.trim(),
          api_key: key,
          context_window: Number(form.context_window) || 32768,
        });
      } else {
        // 新建(editingId === null):api_key 可空(无 key 的本地端点)。
        s = await api.createProfile({
          name: form.name.trim(),
          base_url: form.base_url.trim(),
          api_key: key,
          model: form.model.trim(),
          context_window: Number(form.context_window) || 32768,
        });
      }
      apply(s);
      closeForm();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const setActive = async (id: string) => {
    if (id === activeId || anyBusy) return;
    setBusy(`active:${id}`);
    setError(null);
    try {
      apply(await api.setActiveProfile(id));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const remove = async (id: string) => {
    if (anyBusy) return;
    setBusy(`delete:${id}`);
    setError(null);
    try {
      const s = await api.deleteProfile(id);
      apply(s);
      // 若正在编辑被删的 Profile,关掉表单。
      if (editingId === id) closeForm();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-2xl px-6 py-8">
        {/* 页头 */}
        <header className="flex flex-col gap-2">
          <span
            aria-hidden
            className="flex size-9 items-center justify-center rounded-xl bg-muted text-foreground ring-1 ring-border/70"
          >
            <Settings2 className="size-[18px]" strokeWidth={2} />
          </span>
          <h1 className="text-xl font-semibold tracking-tight text-foreground">模型配置</h1>
          <p className="text-sm leading-relaxed text-muted-foreground">
            管理多个 OpenAI-Compatible 端点,选一个作为当前对话使用的 Profile。密钥仅保存在本机,不会上传。
          </p>
        </header>

        {/* 对话模型 Profile 管理。后续设置项以新的 <section> 追加于此下方。 */}
        <section className="mt-8 flex flex-col gap-4">
          {loading ? (
            <ProfileSkeleton />
          ) : (
            <>
              {profiles.length > 0 && (
                <ul className="flex flex-col gap-2">
                  {profiles.map((p) => (
                    <ProfileRow
                      key={p.id}
                      profile={p}
                      active={p.id === activeId}
                      busy={busy}
                      onActivate={() => setActive(p.id)}
                      onEdit={() => openEdit(p)}
                      onDelete={() => remove(p.id)}
                    />
                  ))}
                </ul>
              )}

              {profiles.length === 0 && !formOpen && (
                <div className="flex flex-col items-center gap-1 rounded-xl border border-dashed border-border/80 bg-muted/30 px-6 py-8 text-center">
                  <p className="text-sm font-medium text-foreground">还没有 Profile</p>
                  <p className="max-w-xs text-xs leading-relaxed text-muted-foreground">
                    新建一个 OpenAI-Compatible 端点(名称、Base URL、密钥、模型)即可开始对话。
                  </p>
                </div>
              )}

              {formOpen ? (
                <ProfileForm
                  editing={editingId !== null}
                  form={form}
                  setForm={setForm}
                  saving={busy === "save"}
                  canSubmit={canSubmit}
                  nameRef={nameRef}
                  onSave={save}
                  onCancel={closeForm}
                />
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  size="lg"
                  disabled={anyBusy}
                  className="justify-center border-dashed"
                  onClick={openCreate}
                >
                  <Plus className="size-4" strokeWidth={2} />
                  新建 Profile
                </Button>
              )}

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
            </>
          )}
        </section>

        <ExtractionSection />
        <AsrSection />
      </div>
    </div>
  );
}

const ASR_STATE_LABEL: Record<AsrStatus["state"], string> = {
  not_downloaded: "未下载",
  downloading: "下载中",
  ready: "就绪",
  failed: "失败",
};

const ASR_MODEL_LABEL: Record<AsrModel, string> = {
  "large-v3": "large-v3 —— 质量最高(默认)",
  "distil-large-v3": "distil-large-v3 —— 更快,质量接近",
  medium: "medium —— 速度/质量折中",
  small: "small —— 最快,质量较低",
};

/**
 * ASR(语音转写)设置区:模型大小下拉 + 下载/进度/状态。改动即 PUT 持久化
 * (乐观更新 + 失败回滚),与 Extraction 同款。面向普通用户,不暴露 VAD/阈值旋钮
 * (这些走后端默认 + 离线评测脚本调参,见 scripts/asr_eval.py)。
 */
function AsrSection() {
  const [settings, setSettings] = useState<AsrSettings | null>(null);
  const [status, setStatus] = useState<AsrStatus | null>(null);
  const [busy, setBusy] = useState(false); // 下载进行中
  const [saving, setSaving] = useState(false); // 任一字段持久化中
  const [err, setErr] = useState<string | null>(null);

  // 进入页面:并行拉设置 + 状态。
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getAsrSettings(), api.getAsrStatus()])
      .then(([cfg, s]) => {
        if (cancelled) return;
        setSettings(cfg);
        setStatus(s);
      })
      .catch((e) => !cancelled && setErr(String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  // 下载中:轮询 status 直到 ready/failed 静止态。
  const transient = busy || status?.state === "downloading";
  useEffect(() => {
    if (!transient) return;
    const t = setInterval(() => {
      api
        .getAsrStatus()
        .then((s) => {
          setStatus(s);
          if (s.state === "ready" || s.state === "failed") {
            setBusy(false);
            clearInterval(t);
          }
        })
        .catch(() => {});
    }, 2000);
    return () => clearInterval(t);
  }, [transient]);

  const download = async () => {
    setBusy(true);
    setErr(null);
    try {
      setStatus(await api.downloadAsrModel());
    } catch (e) {
      setErr(String(e));
      setBusy(false);
    }
  };

  // 任一字段改动即持久化(乐观更新 + 失败回滚)。改模型后顺带刷新 status(就绪针对的是选中模型)。
  const update = async (patch: Partial<AsrSettings>) => {
    if (!settings) return;
    const prev = settings;
    setSettings({ ...settings, ...patch });
    setSaving(true);
    setErr(null);
    try {
      const next = await api.putAsrSettings(patch);
      setSettings(next);
      if ("model" in patch) {
        api.getAsrStatus().then(setStatus).catch(() => {});
      }
    } catch (e) {
      setSettings(prev); // 回滚
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  };

  const ready = status?.ready === true;
  const downloading = busy || status?.state === "downloading";
  const failed = status?.state === "failed";

  return (
    <section className="mt-10 flex flex-col gap-3 border-t border-border/60 pt-8">
      <div className="flex flex-col gap-1">
        <h2 className="text-sm font-semibold text-foreground">语音转写(ASR)</h2>
        <p className="text-xs leading-relaxed text-muted-foreground">
          采集 session 里 🎤 外录 / 🔊 内录的实时转写引擎(faster-whisper,本地运行)。
        </p>
      </div>

      {/* 模型大小:改动即 PUT 持久化(乐观更新 + 失败回滚)。 */}
      {settings && (
        <Field id="asr-model" label="模型大小">
          <select
            id="asr-model"
            value={settings.model}
            disabled={saving || downloading}
            onChange={(e) => update({ model: e.target.value as AsrModel })}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
          >
            {(["large-v3", "distil-large-v3", "medium", "small"] as const).map((v) => (
              <option key={v} value={v}>{ASR_MODEL_LABEL[v]}</option>
            ))}
          </select>
        </Field>
      )}

      {/* 状态徽标 + 下载/重下按钮(模型首次用到需下载,约数百 MB~GB)。 */}
      <div className="flex items-center gap-3 rounded-xl border border-border/70 bg-muted/30 px-3 py-2.5">
        <span className="flex items-center gap-2 text-sm text-foreground">
          {downloading && <Loader2 className="size-3.5 animate-spin" />}
          {ready && !downloading && <CheckCircle2 className="size-3.5 text-primary" strokeWidth={2.25} />}
          {failed && <TriangleAlert className="size-3.5 text-destructive" />}
          状态:{status ? ASR_STATE_LABEL[status.state] : "…"}
        </span>
        <div className="ml-auto flex gap-2">
          {ready ? (
            <Button type="button" variant="outline" size="sm" disabled={downloading}
                    onClick={download} title="按当前模型大小重新下载权重">
              重新下载
            </Button>
          ) : (
            <Button type="button" size="sm" disabled={downloading} onClick={download}
                    title="下载所选模型权重(首次较久)">
              {downloading ? (<><Loader2 className="size-3.5 animate-spin" />下载中…</>)
                : failed ? "重试下载" : "下载模型"}
            </Button>
          )}
        </div>
      </div>

      {(err || status?.error) && (
        <p className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive">
          {err || status?.error}
        </p>
      )}
    </section>
  );
}

const STATE_LABEL: Record<ExtractionStatus["state"], string> = {
  not_installed: "未安装",
  installing: "安装中",
  installed_no_models: "已安装·未下模型",
  downloading_models: "下载模型中",
  ready: "就绪",
  failed: "失败",
};

const ENGINE_LABEL: Record<ExtractionSettings["engine"], string> = {
  pypdf: "pypdf —— 简单文字处理",
  mineru: "MinerU —— OCR/VLM 多重处理,质量高",
};
const EFFORT_LABEL: Record<ExtractionSettings["effort"], string> = {
  high: "高",
  medium: "中",
};
const SOURCE_LABEL: Record<ExtractionSettings["model_source"], string> = {
  modelscope: "ModelScope",
  huggingface: "HuggingFace",
  local: "本地",
};

function ExtractionSection() {
  const [status, setStatus] = useState<ExtractionStatus | null>(null);
  const [settings, setSettings] = useState<ExtractionSettings | null>(null);
  const [busy, setBusy] = useState(false); // 安装/下载进行中
  const [savingField, setSavingField] = useState<null | "engine" | "effort" | "model_source">(null);
  const [err, setErr] = useState<string | null>(null);

  // 进入页面:并行拉状态 + 设置。
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getExtractionStatus(), api.getExtractionSettings()])
      .then(([s, cfg]) => {
        if (cancelled) return;
        setStatus(s);
        setSettings(cfg);
      })
      .catch((e) => !cancelled && setErr(String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  // 安装中 / 下载中:轮询 status 直到 ready/failed/installed_no_models 静止态。
  const transient =
    busy ||
    status?.state === "installing" ||
    status?.state === "downloading_models";
  useEffect(() => {
    if (!transient) return;
    const t = setInterval(() => {
      api
        .getExtractionStatus()
        .then((s) => {
          setStatus(s);
          if (
            s.state === "ready" ||
            s.state === "failed" ||
            s.state === "installed_no_models"
          ) {
            setBusy(false);
            clearInterval(t);
          }
        })
        .catch(() => {});
    }, 2000);
    return () => clearInterval(t);
  }, [transient]);

  const install = async () => {
    setBusy(true);
    setErr(null);
    try {
      setStatus(await api.provisionExtraction());
    } catch (e) {
      setErr(String(e));
      setBusy(false);
    }
  };

  const download = async () => {
    setBusy(true);
    setErr(null);
    try {
      setStatus(await api.downloadModels());
    } catch (e) {
      setErr(String(e));
      setBusy(false);
    }
  };

  // engine / effort / model_source 改动即持久化(乐观更新 + 失败回滚)。
  const update = async (patch: Partial<ExtractionSettings>) => {
    if (!settings) return;
    const prev = settings;
    const next: ExtractionSettings = { ...settings, ...patch };
    setSettings(next);
    const field = "engine" in patch ? "engine" : "effort" in patch ? "effort" : "model_source";
    setSavingField(field as "engine" | "effort" | "model_source");
    setErr(null);
    try {
      setSettings(await api.putExtractionSettings(next));
    } catch (e) {
      setSettings(prev); // 回滚
      setErr(String(e));
    } finally {
      setSavingField(null);
    }
  };

  const state = status?.state;
  const failedStage = status?.failed_stage ?? null;
  // 「装了包」的判定:不能把所有 failed 都当装好——装包失败(failed_stage==="install")
  // 其实没装好,应回到「安装」。只有非 install 失败才算包就绪(failed_stage==="download"
  // 意味着包已装、是下模型那步失败)。
  const installFailed = state === "failed" && failedStage === "install";
  const installed =
    state === "installed_no_models" ||
    state === "downloading_models" ||
    state === "ready" ||
    (state === "failed" && !installFailed);
  const ready = status?.ready === true;
  const installing = busy && !installed ? true : state === "installing";
  const downloading = state === "downloading_models" || (busy && installed && !ready);
  // cached 模型仍可用(ready)但上次重下失败 → 仍要把失败暴露给用户。
  const downloadFailed = failedStage === "download" || (state === "failed" && installed);

  const isMineru = settings?.engine === "mineru";

  return (
    <section className="mt-10 flex flex-col gap-3 border-t border-border/60 pt-8">
      <div className="flex flex-col gap-1">
        <h2 className="text-sm font-semibold text-foreground">文档处理引擎</h2>
        <p className="text-xs leading-relaxed text-muted-foreground">
          选择 PDF/DOCX/PPTX 的文本抽取引擎。
        </p>
      </div>

      {/* 引擎选择器:改动即 PUT 持久化(乐观更新 + 失败回滚)。 */}
      {settings && (
        <Field id="ext-engine" label="引擎">
          <select
            id="ext-engine"
            value={settings.engine}
            disabled={savingField !== null}
            onChange={(e) => update({ engine: e.target.value as ExtractionSettings["engine"] })}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
          >
            {(["pypdf", "mineru"] as const).map((v) => (
              <option key={v} value={v}>{ENGINE_LABEL[v]}</option>
            ))}
          </select>
        </Field>
      )}

      {/* pypdf:无旋钮,只给一行极简说明。 */}
      {settings && !isMineru && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          内置,免安装,纯文本抽取。
        </p>
      )}

      {/* 选中 MinerU 才显示:状态徽标 / 安装 / 下载 / 重新下载 / 解析力度 / 模型源。 */}
      {isMineru && (
        <>
          <div className="flex items-center gap-3 rounded-xl border border-border/70 bg-muted/30 px-3 py-2.5">
            <span className="flex items-center gap-2 text-sm text-foreground">
              {(installing || downloading) && <Loader2 className="size-3.5 animate-spin" />}
              {/* 就绪但上次重下失败:仍标就绪(cached 可用),但用警告色提示一次失败的重下。 */}
              {ready && !downloadFailed && <CheckCircle2 className="size-3.5 text-primary" strokeWidth={2.25} />}
              {(state === "failed" || (ready && downloadFailed)) && (
                <TriangleAlert className="size-3.5 text-destructive" />
              )}
              状态:{status ? STATE_LABEL[status.state] : "…"}
              {ready && downloadFailed && <span className="text-destructive">(上次重新下载失败)</span>}
            </span>
            <div className="ml-auto flex gap-2">
              {!installed && state !== "installing" && (
                <Button type="button" size="sm" disabled={installing} onClick={install}
                        title="安装 MinerU(装包)">
                  {installing ? (<><Loader2 className="size-3.5 animate-spin" />安装中…</>)
                    : installFailed ? "重试安装" : "安装"}
                </Button>
              )}
              {installed && !ready && (
                <Button type="button" size="sm" disabled={downloading} onClick={download}
                        title="下载模型(约数 GB)">
                  {downloading ? (<><Loader2 className="size-3.5 animate-spin" />下载中…</>)
                    : downloadFailed ? "重试下载" : "下载模型"}
                </Button>
              )}
              {ready && (
                <Button type="button" variant="outline" size="sm" disabled={downloading}
                        onClick={download}
                        title={downloadFailed ? "上次重新下载失败,可重试" : "按当前模型源重新下载模型"}>
                  {downloadFailed ? "重试下载模型" : "重新下载模型"}
                </Button>
              )}
            </div>
          </div>

          {settings && (
            <div className="flex flex-col gap-3 rounded-xl border border-border/70 bg-muted/20 px-3 py-3">
              <Field id="ext-effort" label="解析力度">
                <select
                  id="ext-effort"
                  value={settings.effort}
                  disabled={savingField !== null}
                  onChange={(e) => update({ effort: e.target.value as ExtractionSettings["effort"] })}
                  className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                >
                  {(["medium", "high"] as const).map((v) => (
                    <option key={v} value={v}>{EFFORT_LABEL[v]}</option>
                  ))}
                </select>
              </Field>
              <Field id="ext-source" label="模型源">
                <select
                  id="ext-source"
                  value={settings.model_source}
                  disabled={savingField !== null}
                  onChange={(e) => update({ model_source: e.target.value as ExtractionSettings["model_source"] })}
                  className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                >
                  {(["modelscope", "huggingface", "local"] as const).map((v) => (
                    <option key={v} value={v}>{SOURCE_LABEL[v]}</option>
                  ))}
                </select>
              </Field>
              <p className="-mt-1 text-[0.7rem] leading-relaxed text-muted-foreground">
                换模型源后需手动「重新下载模型」才生效。
              </p>
            </div>
          )}
        </>
      )}

      {(err || (isMineru && status?.error)) && (
        <p className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive">
          {err || status?.error}
        </p>
      )}
    </section>
  );
}

function ProfileRow({
  profile,
  active,
  busy,
  onActivate,
  onEdit,
  onDelete,
}: {
  profile: LLMProfile;
  active: boolean;
  busy: string | null;
  onActivate: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const activating = busy === `active:${profile.id}`;
  const deleting = busy === `delete:${profile.id}`;
  const anyBusy = busy !== null;

  return (
    <li
      className={cn(
        "group/row flex items-center gap-3 rounded-xl border px-3 py-2.5 transition-colors",
        active
          ? "border-foreground/15 bg-muted/50 ring-1 ring-foreground/[0.04]"
          : "border-border/70 bg-background hover:bg-muted/40",
      )}
    >
      {/* 活动选择器:整块可点的单选。活动态为实心标记,非活动为空心环。 */}
      <button
        type="button"
        role="radio"
        aria-checked={active}
        disabled={anyBusy}
        onClick={onActivate}
        title={active ? "当前使用中" : "设为当前使用"}
        className={cn(
          "flex size-5 shrink-0 items-center justify-center rounded-full border outline-none transition-all",
          "focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50",
          active
            ? "border-transparent bg-primary text-primary-foreground"
            : "border-border bg-background hover:border-foreground/40",
        )}
      >
        {activating ? (
          <Loader2 className="size-3 animate-spin" />
        ) : active ? (
          <Check className="size-3" strokeWidth={3} />
        ) : null}
      </button>

      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-foreground">{profile.name}</span>
          <span className="truncate font-mono text-xs text-muted-foreground">{profile.model}</span>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="truncate font-mono">{profile.base_url}</span>
          <span aria-hidden className="text-border">·</span>
          <span className={cn("shrink-0", profile.api_key_set ? "" : "text-muted-foreground/80")}>
            {profile.api_key_set ? "已配置 key" : "无 key"}
          </span>
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-0.5 opacity-60 transition-opacity group-hover/row:opacity-100 focus-within:opacity-100">
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          disabled={anyBusy}
          title="编辑"
          onClick={onEdit}
        >
          <Pencil className="size-3.5" />
          <span className="sr-only">编辑 {profile.name}</span>
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          disabled={anyBusy}
          title="删除"
          className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
          onClick={onDelete}
        >
          {deleting ? <Loader2 className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}
          <span className="sr-only">删除 {profile.name}</span>
        </Button>
      </div>
    </li>
  );
}

/** 测试连接结果:成功(可带样例文本)/ 失败(原始错误)。null === 尚未测试。 */
type TestResult = { ok: true; sample?: string } | { ok: false; error: string } | null;

function ProfileForm({
  editing,
  form,
  setForm,
  saving,
  canSubmit,
  nameRef,
  onSave,
  onCancel,
}: {
  editing: boolean;
  form: FormState;
  setForm: React.Dispatch<React.SetStateAction<FormState>>;
  saving: boolean;
  canSubmit: boolean;
  nameRef: React.RefObject<HTMLInputElement | null>;
  onSave: () => void;
  onCancel: () => void;
}) {
  // 测试连接是表单内的本地态:不阻塞保存,字段一变就清掉旧结果。
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<TestResult>(null);

  const set = (k: keyof FormState) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setResult(null); // 字段变更 → 旧测试结果作废
    setForm((f) => ({ ...f, [k]: e.target.value }));
  };
  const onEnter = (e: React.KeyboardEvent) => {
    // 输入法(IME)合成期间按 Enter 是确认候选词,不应触发保存。
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === "Enter" && canSubmit) onSave();
  };

  // 测试当前正在编辑的值(保存前即可验证)。base_url / model 任一为空则禁用。
  const canTest = Boolean(form.base_url.trim() && form.model.trim()) && !testing && !saving;
  const runTest = async () => {
    if (!canTest) return;
    setTesting(true);
    setResult(null);
    try {
      const r = await api.testProfile({
        base_url: form.base_url.trim(),
        api_key: form.api_key.trim(),
        model: form.model.trim(),
      });
      setResult(r.ok ? { ok: true, sample: r.sample } : { ok: false, error: r.error ?? "未知错误" });
    } catch (e) {
      // 网络/HTTP 层失败(非 provider 错误)也照常呈现。
      setResult({ ok: false, error: String(e) });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-border/70 bg-muted/30 p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-foreground">
          {editing ? "编辑 Profile" : "新建 Profile"}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          disabled={saving}
          onClick={onCancel}
          title="取消"
        >
          <X className="size-4" />
          <span className="sr-only">取消</span>
        </Button>
      </div>

      <Field id="pf-name" label="名称">
        <Input
          id="pf-name"
          ref={nameRef}
          value={form.name}
          disabled={saving}
          placeholder="给这个端点起个名字"
          onChange={set("name")}
          onKeyDown={onEnter}
        />
      </Field>

      <Field id="pf-base-url" label="Base URL">
        <Input
          id="pf-base-url"
          value={form.base_url}
          disabled={saving}
          placeholder="https://…/v1"
          className="font-mono text-xs"
          onChange={set("base_url")}
          onKeyDown={onEnter}
        />
      </Field>

      <Field id="pf-api-key" label="API Key">
        <Input
          id="pf-api-key"
          type="text"
          value={form.api_key}
          disabled={saving}
          placeholder="sk-…(本地端点可留空)"
          className="font-mono text-xs"
          autoComplete="off"
          spellCheck={false}
          onChange={set("api_key")}
          onKeyDown={onEnter}
        />
      </Field>

      <Field id="pf-model" label="模型">
        <Input
          id="pf-model"
          value={form.model}
          disabled={saving}
          placeholder="如 deepseek-chat"
          className="font-mono text-xs"
          onChange={set("model")}
          onKeyDown={onEnter}
        />
      </Field>

      <Field id="pf-ctx" label="上下文窗口(token)">
        <Input id="pf-ctx" type="number" inputMode="numeric" value={form.context_window}
               disabled={saving} placeholder="如 32768 / 128000"
               className="font-mono text-xs" onChange={set("context_window")} />
      </Field>
      <p className="-mt-2 text-[0.7rem] leading-relaxed text-muted-foreground">
        决定多大的附件能整篇进上下文(超出则留给后续大文件处理)。
      </p>

      {result && <TestNotice result={result} />}

      <div className="flex items-center gap-2 pt-0.5">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!canTest}
          onClick={runTest}
          title={canTest ? "向该端点发一次最小请求以验证连通" : "先填好 Base URL 和模型"}
        >
          {testing ? (
            <>
              <Loader2 className="size-3.5 animate-spin" />
              正在测试…
            </>
          ) : (
            <>
              <Plug className="size-3.5" />
              测试连接
            </>
          )}
        </Button>
        <div className="ml-auto flex gap-2">
          <Button type="button" variant="ghost" size="sm" disabled={saving} onClick={onCancel}>
            取消
          </Button>
          <Button type="button" size="sm" disabled={!canSubmit} onClick={onSave}>
            {saving ? (
              <>
                <Loader2 className="size-3.5 animate-spin" />
                正在保存…
              </>
            ) : editing ? (
              "保存修改"
            ) : (
              "保存"
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** 测试连接的内联结果:成功是平静的中性提示 + 正向图标;失败用 destructive 显示原始报错。 */
function TestNotice({ result }: { result: NonNullable<TestResult> }) {
  if (result.ok) {
    return (
      <div
        role="status"
        className={cn(
          "flex items-start gap-2 rounded-lg border border-border/70 bg-background px-3 py-2",
          "text-xs leading-relaxed text-foreground",
        )}
      >
        <CheckCircle2 className="mt-px size-3.5 shrink-0 text-primary" strokeWidth={2.25} />
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="font-medium">连接正常</span>
          {result.sample && (
            <span className="truncate font-mono text-[0.7rem] text-muted-foreground">
              {result.sample}
            </span>
          )}
        </div>
      </div>
    );
  }
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-2 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2",
        "text-xs leading-relaxed text-destructive",
      )}
    >
      <TriangleAlert className="mt-px size-3.5 shrink-0" strokeWidth={2.25} />
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="font-medium">连接失败</span>
        <span className="break-words font-mono text-[0.7rem] opacity-90">{result.error}</span>
      </div>
    </div>
  );
}

function Field({
  id,
  label,
  children,
}: {
  id: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-xs font-medium text-muted-foreground">
        {label}
      </label>
      {children}
    </div>
  );
}

function ProfileSkeleton() {
  return (
    <div className="flex flex-col gap-2" aria-hidden>
      {[0, 1].map((i) => (
        <div
          key={i}
          className="flex items-center gap-3 rounded-xl border border-border/70 px-3 py-2.5"
        >
          <span className="size-5 shrink-0 animate-pulse rounded-full bg-muted" />
          <div className="flex flex-1 flex-col gap-1.5">
            <span className="h-3.5 w-1/3 animate-pulse rounded bg-muted" />
            <span className="h-3 w-2/3 animate-pulse rounded bg-muted" />
          </div>
        </div>
      ))}
    </div>
  );
}
