"""EpicTrace 桌面外壳:后台起 uvicorn,健康检查就绪后用 pywebview 开窗;暴露原生文件对话框给前端。"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn
import webview

from epictrace.api.app import create_app

HOST, PORT = "127.0.0.1", 8765


def _is_real_file(p: str) -> bool:
    """真实的绝对文件路径(非目录、非相对/脏路径)才放行。"""
    return bool(p) and os.path.isabs(p) and os.path.isfile(p)


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
            # 只放行真实的绝对文件路径:剪贴板可能带目录、相对/脏路径或非文件项,
            # 一律过滤,避免下游对不存在/非文件路径误触提取。
            names = pb.propertyListForType_("NSFilenamesPboardType")
            if names:
                return [str(p) for p in names if _is_real_file(str(p))]
            urls = pb.readObjectsForClasses_options_([NSURL], None) or []
            return [
                str(u.path())
                for u in urls
                if u.isFileURL() and _is_real_file(str(u.path()))
            ]
        except Exception as e:  # noqa: BLE001 — 读剪贴板任何异常都退化为空
            print(f"[EpicTrace] read_clipboard_files failed: {e}", flush=True)
            return []

    def capture_screenshot(self) -> str | None:
        """用系统 screencapture 抓全屏存 PNG 进当前 session 的 staging_dir,POST 截图事件,返回文件名;
        失败→None。需「屏幕录制」权限;无活动监听(_cap 未设)或未选 screenshot 源 → None。
        用 screencapture CLI 而非 Quartz CGWindowListCreateImage:后者在新版 macOS 已废/受限,
        常返回空图(=用户看到的「截图失效」),CLI 走系统标准路径、权限提示也正常。"""
        cap = getattr(self, "_cap", None)
        if not cap:
            return None
        # 仅当本次 session 选了 screenshot 源才允许抓屏(与 clipboard 同样按源 gate)。
        if "screenshot" not in (cap.get("sources") or []):
            return None
        try:
            import subprocess
            import time

            name = f"shot-{int(time.time() * 1000)}.png"
            out = Path(cap["dir"]) / name
            out.parent.mkdir(parents=True, exist_ok=True)
            # -x 静默(无快门声),-t png 指定格式;抓整屏。
            result = subprocess.run(
                ["/usr/sbin/screencapture", "-x", "-t", "png", str(out)],
                capture_output=True, timeout=10,
            )
            if result.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
                print(f"[EpicTrace] screencapture failed rc={result.returncode} "
                      f"(需「屏幕录制」权限?)", flush=True)
                return None
            self._post_event(cap["sid"], "screenshot", name)
            return name
        except Exception as e:  # noqa: BLE001 — 抓屏任何异常降级
            print(f"[EpicTrace] capture_screenshot failed: {e}", flush=True)
            return None

    def _post_event(self, session_id: int, kind: str, payload: str) -> None:
        """shell 把采到的事件直接 POST 给后端(截图/剪贴板共用);失败重试一次后记日志。"""
        import urllib.request

        body = json.dumps({"kind": kind, "payload": payload}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:8765/api/capture/sessions/{session_id}/events",
            data=body, headers={"Content-Type": "application/json"}, method="POST")
        for attempt in (1, 2):
            try:
                urllib.request.urlopen(req, timeout=3)
                return
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    print(f"[EpicTrace] post event failed ({kind}): {e}", flush=True)

    def start_capture_monitors(self, session_id: int, staging_dir: str, sources: list) -> dict:
        """按所选源起原生监听(剪贴板轮询 + 全局热键触发截图)。重复调用先停旧的(并 join 旧线程)。"""
        self.stop_capture_monitors()
        sources = list(sources or [])
        cap = {"sid": session_id, "dir": staging_dir, "sources": sources,
               "last_clip": None, "stop": False}
        self._cap = cap
        try:
            from AppKit import NSPasteboard
            import threading

            pb = NSPasteboard.generalPasteboard()
            cap["clip_count"] = pb.changeCount()

            # 线程内只读自己的局部快照 cap,绝不读 self._cap:pause→resume(stop 后再 start)
            # 会把 self._cap 换成新 dict;旧线程若读 self._cap 可能崩或往旧 session 投事件。
            def _poll(cap=cap):
                while not cap.get("stop"):
                    try:
                        cnt = pb.changeCount()
                        if "clipboard" in cap["sources"] and cnt != cap["clip_count"]:
                            cap["clip_count"] = cnt
                            txt = pb.stringForType_("public.utf8-plain-text")
                            if txt and txt != cap["last_clip"]:
                                cap["last_clip"] = txt
                                self._post_event(cap["sid"], "clipboard", str(txt))
                    except Exception as e:  # noqa: BLE001
                        print(f"[EpicTrace] clipboard poll: {e}", flush=True)
                    import time
                    time.sleep(1.0)

            t = threading.Thread(target=_poll, daemon=True)
            t.start()
            cap["thread"] = t
            # 全局热键:仅当本次选了 screenshot 源才注册(否则不应有截图热键)。
            # 若 PyObjC 全局监听在当前 pywebview 主循环不便挂载,降级为「仅 HUD/应用内按钮触发」。
            if "screenshot" in sources:
                try:
                    from AppKit import NSEvent, NSKeyDownMask
                    # ⌘⇧2 = keyCode 19,modifierFlags 含 NSCommandKeyMask|NSShiftKeyMask
                    NSCommandKeyMask = 1 << 20
                    NSShiftKeyMask = 1 << 17

                    def _hotkey_handler(event):
                        try:
                            flags = event.modifierFlags()
                            if (event.keyCode() == 19
                                    and (flags & NSCommandKeyMask)
                                    and (flags & NSShiftKeyMask)):
                                self.capture_screenshot()
                        except Exception as he:  # noqa: BLE001
                            print(f"[EpicTrace] hotkey handler: {he}", flush=True)

                    monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                        NSKeyDownMask, _hotkey_handler)
                    cap["hotkey_monitor"] = monitor
                except Exception as hke:  # noqa: BLE001 — 辅助功能权限不足或 API 不可用
                    print(
                        f"[EpicTrace] global hotkey unavailable, use HUD/in-app button for screenshot: {hke}",
                        flush=True,
                    )
            return {"ok": True}
        except Exception as e:  # noqa: BLE001 — 原生不可用降级
            print(f"[EpicTrace] start_capture_monitors failed: {e}", flush=True)
            return {"ok": False, "reason": str(e)}

    def stop_capture_monitors(self) -> None:
        cap = getattr(self, "_cap", None)
        if cap:
            cap["stop"] = True
            # 先 join 旧 poll 线程(置停止位后等它退出),再清理 —— 避免旧线程还在跑、
            # 与新一轮 start 竞态(往旧 session 投事件 / 读到被替换的状态)。
            thread = cap.get("thread")
            if thread is not None:
                try:
                    thread.join(timeout=2)
                except Exception as e:  # noqa: BLE001
                    print(f"[EpicTrace] join clipboard thread: {e}", flush=True)
            # 清理全局热键监听器(若已注册)
            monitor = cap.get("hotkey_monitor")
            if monitor is not None:
                try:
                    from AppKit import NSEvent
                    NSEvent.removeMonitor_(monitor)
                except Exception as e:  # noqa: BLE001
                    print(f"[EpicTrace] remove hotkey monitor: {e}", flush=True)
        self._cap = None

    def show_recording_hud(self, session_id: int) -> dict:
        """开第二个无边框、置顶、可拖动的**紧凑**小窗口渲染 HUD(指向前端 ?view=hud 路由)。
        **必须传 js_api=self**:否则 HUD 窗口里 window.pywebview.api 为 undefined,
        导致 HUD 的停止/截图/收起全是空操作(停止点了不停、窗口销毁不掉卡在「已停止」)。"""
        try:
            self._hud = webview.create_window(
                "EpicTrace 录制",
                f"http://127.0.0.1:8765/?view=hud&session={session_id}",
                js_api=self,
                frameless=True, on_top=True, easy_drag=True, resizable=False,
                width=280, height=40, x=60, y=60,
            )
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            print(f"[EpicTrace] show_recording_hud failed: {e}", flush=True)
            return {"ok": False, "reason": str(e)}

    def resize_recording_hud(self, width: int, height: int) -> None:
        """调整 HUD 窗口尺寸(箭头向下展开时间线预览 / 收起时用)。"""
        hud = getattr(self, "_hud", None)
        if hud is not None:
            try:
                hud.resize(int(width), int(height))
            except Exception as e:  # noqa: BLE001
                print(f"[EpicTrace] resize_recording_hud failed: {e}", flush=True)

    def hide_recording_hud(self) -> None:
        hud = getattr(self, "_hud", None)
        if hud is not None:
            try:
                hud.destroy()
            except Exception as e:  # noqa: BLE001
                print(f"[EpicTrace] hide_recording_hud failed: {e}", flush=True)
        self._hud = None


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
