// frontend/src/lib/native.ts —— 录制相关原生能力封装;无 pywebview(开发态)时静默降级。
type CaptureApi = {
  capture_screenshot?: () => Promise<string | null>;
  start_capture_monitors?: (sid: number, dir: string, sources: string[]) => Promise<unknown>;
  stop_capture_monitors?: () => Promise<void>;
  show_recording_hud?: (sid: number) => Promise<unknown>;
  resize_recording_hud?: (width: number, height: number) => Promise<void>;
  hide_recording_hud?: () => Promise<void>;
};

function api(): CaptureApi | null {
  return (window as unknown as { pywebview?: { api: CaptureApi } }).pywebview?.api ?? null;
}

export const native = {
  available: () => api() !== null,
  // 截图无参:shell 用 start_capture_monitors 存下的 staging_dir 存图并自行 POST 截图事件。
  screenshot: () => api()?.capture_screenshot?.() ?? Promise.resolve(null),
  startMonitors: (sid: number, dir: string, sources: string[]) => api()?.start_capture_monitors?.(sid, dir, sources) ?? Promise.resolve(null),
  stopMonitors: () => api()?.stop_capture_monitors?.() ?? Promise.resolve(),
  showHud: (sid: number) => api()?.show_recording_hud?.(sid) ?? Promise.resolve(null),
  resizeHud: (width: number, height: number) => api()?.resize_recording_hud?.(width, height) ?? Promise.resolve(),
  hideHud: () => api()?.hide_recording_hud?.() ?? Promise.resolve(),
};
