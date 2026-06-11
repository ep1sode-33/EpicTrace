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

import { api, type LLMProfile, type Settings } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

type FormState = { name: string; base_url: string; api_key: string; model: string };
const BLANK: FormState = { name: "", base_url: "", api_key: "", model: "" };

export function SettingsModal({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  /** 任一变更成功后回调,携带最新公开设置(configured 已更新),供父级解禁 Composer。 */
  onSaved?: (settings: Settings) => void;
}) {
  const [profiles, setProfiles] = useState<LLMProfile[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 表单态:editingId === null 表示「新建」;非空表示编辑该 Profile。null === 表单关闭。
  const [editingId, setEditingId] = useState<string | null | undefined>(undefined);
  const [form, setForm] = useState<FormState>(BLANK);
  const [busy, setBusy] = useState<string | null>(null); // "save" | "active:<id>" | "delete:<id>"
  const nameRef = useRef<HTMLInputElement>(null);

  const apply = (s: Settings) => {
    setProfiles(s.profiles);
    setActiveId(s.active_profile_id);
    onSaved?.(s);
  };

  // 每次打开都拉取当前设置(api_key 永不回传)。重置表单态。
  useEffect(() => {
    if (!open) return;
    setError(null);
    setEditingId(undefined);
    setForm(BLANK);
    setLoading(true);
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
  }, [open]);

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
    setForm({ name: p.name, base_url: p.base_url, api_key: p.api_key, model: p.model });
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
        });
      } else {
        // 新建(editingId === null):api_key 可空(无 key 的本地端点)。
        s = await api.createProfile({
          name: form.name.trim(),
          base_url: form.base_url.trim(),
          api_key: key,
          model: form.model.trim(),
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
    <Dialog open={open} onOpenChange={(o) => !o && !anyBusy && onClose()}>
      <DialogContent showCloseButton={!anyBusy} className="gap-0 p-0">
        <DialogHeader className="gap-2 px-6 pt-6">
          <span
            aria-hidden
            className="flex size-9 items-center justify-center rounded-xl bg-muted text-foreground ring-1 ring-border/70"
          >
            <Settings2 className="size-[18px]" strokeWidth={2} />
          </span>
          <DialogTitle>对话模型</DialogTitle>
          <DialogDescription>
            管理多个 OpenAI-Compatible 端点,选一个作为当前对话使用的 Profile。密钥仅保存在本机,不会上传。
          </DialogDescription>
        </DialogHeader>

        <div className="flex max-h-[min(70vh,40rem)] flex-col gap-4 overflow-y-auto px-6 py-5">
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
        </div>
      </DialogContent>
    </Dialog>
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
