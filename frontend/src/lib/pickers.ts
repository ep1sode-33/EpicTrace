// 原生选择器:打包态走 pywebview js_api;开发态(浏览器)回退 prompt 手填路径。
declare global {
  interface Window {
    pywebview?: { api: { pick_folder(): Promise<string | null>; pick_file(): Promise<string | null> } };
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
