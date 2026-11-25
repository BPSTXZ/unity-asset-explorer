"""Asset Explorer 桌面启动入口（PyInstaller 打包用）。"""
import threading
import webbrowser

import uvicorn

from backend.main import app

HOST = "127.0.0.1"
PORT = 8765


def _open_browser():
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    threading.Timer(1.5, _open_browser).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")