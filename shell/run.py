"""EpicTrace 桌面外壳:后台起 uvicorn,健康检查就绪后用 pywebview 开窗;暴露原生文件对话框给前端。"""
from __future__ import annotations

import json
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
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return result[0] if result else None

    def pick_file(self) -> str | None:
        if self._window is None:
            return None
        result = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False)
        return result[0] if result else None

    def pick_files(self) -> list[str]:
        """多选文件(对话附件用)。返回绝对路径列表;取消则空列表。"""
        if self._window is None:
            return []
        result = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True)
        return list(result) if result else []

    def reveal_in_finder(self, path: str) -> dict:
        """在 Finder 中显示并选中给定文件(来源查看器「在 Finder 中显示」)。
        路径不存在则不动作(避免对脏路径 / 已移动文件误触 open),返回状态供前端提示。"""
        import subprocess

        if not path or not os.path.exists(path):
            return {"ok": False, "reason": "not_found"}
        subprocess.run(["open", "-R", path])  # argv 列表形式,不经 shell,杜绝注入
        return {"ok": True}

    def read_clipboard_files(self) -> list[str]:
        """读 macOS 系统剪贴板里的文件绝对路径(Finder 复制文件 → file URL)。
        浏览器 paste 事件的 File 没有真实路径,故粘贴文件时由前端调本方法,从原生剪贴板取路径
        (与拖拽走 cocoa 拖拽板同理)。剪贴板非文件 / 出错 → 空列表(不影响主流程)。"""
        try:
            from AppKit import NSURL, NSPasteboard  # type: ignore

            pb = NSPasteboard.generalPasteboard()
            names = pb.propertyListForType_("NSFilenamesPboardType")
            if names:
                return [str(p) for p in names]
            urls = pb.readObjectsForClasses_options_([NSURL], None) or []
            return [str(u.path()) for u in urls if u.isFileURL()]
        except Exception as e:  # noqa: BLE001 — 读剪贴板任何异常都退化为空
            print(f"[EpicTrace] read_clipboard_files failed: {e}", flush=True)
            return []


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


def _register_native_drop(window: "webview.Window") -> None:
    """在 document.body 上挂原生 drop 处理,把拖入文件的**真实绝对路径**转发给前端。

    背景:浏览器侧的 File.path 在 pywebview 里始终为空;真实路径只在 pywebview 的
    Python 侧 DOM drop 事件里拿得到——cocoa.py 的 performDragOperation_ 会把拖拽板的
    file URL 读进 _dnd_state['paths'](仅当注册了 drop 监听,num_listeners>0 才读),
    随后 util.py 在派发前给每个文件补上 pywebviewFullPath。
    注意:在 body 上调 .on('drop', ...) 必须等 DOM 就绪(它内部要 evaluate_js),
    故挂在 events.loaded 回调里;且注册 drop 监听这一步本身让 cocoa 开始采集路径。
    """

    def _on_drop(event: dict) -> None:
        # 处理器收到的是原始事件 dict(util.py 以 args=(event,) 起线程回调),
        # 拖入文件在 event['dataTransfer']['files'],每个文件被补了 pywebviewFullPath。
        try:
            files = (event.get("dataTransfer") or {}).get("files") or []
            paths = [f["pywebviewFullPath"] for f in files if f.get("pywebviewFullPath")]
        except Exception as e:  # 事件结构异常不应拖垮回调
            print(f"[EpicTrace] native drop: bad event shape: {e}", flush=True)
            return
        if not paths:
            return
        # 转发给前端约定的全局钩子(React 侧定义 window.__onNativeFilesDropped)。
        window.evaluate_js(
            f"window.__onNativeFilesDropped && window.__onNativeFilesDropped({json.dumps(paths)})"
        )

    def _bind() -> None:
        # 此刻 DOM 已就绪;在 body 上注册 drop(会令 _dnd_state['num_listeners'] 自增,
        # 从而触发 cocoa 侧采集真实文件路径)。
        try:
            window.dom.body.on("drop", _on_drop)
        except Exception as e:
            # DOM API 形态不符也不让外壳崩溃,只告警一次。
            print(f"[EpicTrace] native drag-drop unavailable: {e}", flush=True)

    # 等窗口 DOM 加载完再绑定;events.loaded 是 pywebview 的 Event,支持 += 注册回调。
    window.events.loaded += _bind


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
    _register_native_drop(window)  # 原生拖拽转发,纯附加,不改动既有逻辑
    webview.start()


if __name__ == "__main__":
    main()
