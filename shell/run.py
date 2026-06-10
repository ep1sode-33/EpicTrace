"""EpicTrace 桌面外壳:后台起 uvicorn,再用 pywebview 开窗。"""
from __future__ import annotations

import threading
import time

import uvicorn
import webview

from epictrace.api.app import create_app

HOST, PORT = "127.0.0.1", 8765


def _serve() -> None:
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="warning")


def main() -> None:
    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    time.sleep(1.0)  # 等服务起来
    webview.create_window("EpicTrace", f"http://{HOST}:{PORT}", width=1100, height=750)
    webview.start()


if __name__ == "__main__":
    main()
