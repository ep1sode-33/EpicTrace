"""EpicTrace 桌面外壳:后台起 uvicorn,健康检查就绪后用 pywebview 开窗。"""
from __future__ import annotations

import threading
import time
import urllib.request

import uvicorn
import webview

from epictrace.api.app import create_app

HOST, PORT = "127.0.0.1", 8765


def _serve() -> None:
    try:
        uvicorn.run(create_app(), host=HOST, port=PORT, log_level="warning")
    except Exception as e:  # surface bind/startup failures
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
    webview.create_window("EpicTrace", f"http://{HOST}:{PORT}", width=1100, height=750)
    webview.start()


if __name__ == "__main__":
    main()
