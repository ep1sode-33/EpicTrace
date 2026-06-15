// 原生选择器:打包态走 pywebview js_api;开发态(浏览器)回退 prompt 手填路径。
declare global {
  interface Window {
    pywebview?: {
      api: {
        pick_folder(): Promise<string | null>;
        pick_file(): Promise<string | null>;
        pick_files(): Promise<string[]>;
        reveal_in_finder(path: string): Promise<void>;
        read_clipboard_files(): Promise<string[]>;
      };
    };
  }
}

/** 在系统文件管理器中定位文件;仅打包态(pywebview)可用,开发态静默退化。 */
export async function revealInFinder(path: string): Promise<void> {
  if (window.pywebview?.api) {
    await window.pywebview.api.reveal_in_finder(path);
  }
}

export async function pickFolder(): Promise<string | null> {
  if (window.pywebview?.api) return window.pywebview.api.pick_folder();
  return window.prompt("(开发态)输入文件夹绝对路径:")?.trim() || null;
}

export async function pickFile(): Promise<string | null> {
  if (window.pywebview?.api) return window.pywebview.api.pick_file();
  return window.prompt("(开发态)输入文件绝对路径:")?.trim() || null;
}

/** 多选文件(对话附件)。打包态走 pywebview;开发态回退 prompt 单条路径。 */
export async function pickFiles(): Promise<string[]> {
  if (window.pywebview?.api) return window.pywebview.api.pick_files();
  const one = window.prompt("(开发态)输入文件绝对路径:")?.trim();
  return one ? [one] : [];
}

/** 读系统剪贴板里的文件路径(粘贴文件用)。仅打包态(pywebview)有原生剪贴板;开发态/无文件 → 空。 */
export async function readClipboardFiles(): Promise<string[]> {
  if (window.pywebview?.api?.read_clipboard_files) {
    try {
      return await window.pywebview.api.read_clipboard_files();
    } catch {
      return [];
    }
  }
  return [];
}

export {};
