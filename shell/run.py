"""EpicTrace 桌面外壳:后台起 uvicorn,健康检查就绪后用 pywebview 开窗;暴露原生文件对话框给前端。"""
from __future__ import annotations

import os
import threading
import time
import urllib.request

import uvicorn
import webview

from epictrace.api.app import create_app

HOST, PORT = "127.0.0.1", 8765


class Api:
    """暴露给前端 JS 的原生能力(window.pywebview.api.*)。"""

    def __init__(self) -> None:
        self._window: webview.Window | None = None

    def set_window(self, window: "webview.Window") -> None:
        self._window = window

    def pick_folder(self) -> str | None:
        if self._window is None:
            return None
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        return result[0] if result else None

    def pick_file(self) -> str | None:
        if self._window is None:
            return None
        result = self._window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False)
        return result[0] if result else None

    def reveal_in_finder(self, path: str) -> dict:
        """在 Finder 中显示并选中给定文件(来源查看器「在 Finder 中显示」)。
        路径不存在则不动作(避免对脏路径 / 已移动文件误触 open),返回状态供前端提示。"""
        import subprocess

        if not path or not os.path.exists(path):
            return {"ok": False, "reason": "not_found"}
        subprocess.run(["open", "-R", path])  # argv 列表形式,不经 shell,杜绝注入
        return {"ok": True}


def _serve() -> None:
    try:
        uvicorn.run(create_app(), host=HOST, port=PORT, log_level="warning")
    except Exception as e:
        print(f"[EpicTrace] backend failed to start: {e}", flush=True)


def _wait_until_ready(timeout: float = 15.0) -> bool:
    url = f"http://{HOST}:{PORT}/api/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def main() -> None:
    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    if not _wait_until_ready():
        print("[EpicTrace] backend not ready in time; opening window anyway.", flush=True)
    api = Api()
    window = webview.create_window(
        "EpicTrace", f"http://{HOST}:{PORT}", js_api=api, width=1100, height=750
    )
    api.set_window(window)
    webview.start()


if __name__ == "__main__":
    main()
