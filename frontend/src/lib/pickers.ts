// 原生选择器:打包态走 pywebview js_api;开发态(浏览器)回退 prompt 手填路径。
declare global {
  interface Window {
    pywebview?: {
      api: {
        pick_folder(): Promise<string | null>;
        pick_file(): Promise<string | null>;
        reveal_in_finder(path: string): Promise<void>;
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

export {};
