"""pywebview 桌面壳：本地起 FastAPI，再用系统 WebView2 打开。"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser


def _find_free_port() -> int:
    """找一个本地空闲端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(('127.0.0.1', port)) == 0


class _ServerThread(threading.Thread):
    """在后台线程运行 uvicorn。"""

    def __init__(self, app, port: int) -> None:
        super().__init__(daemon=True)
        import uvicorn

        self.server = uvicorn.Server(
            uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning')
        )

    def run(self) -> None:
        self.server.run()

    def stop(self) -> None:
        self.server.should_exit = True


def run_webui(browser: bool = False, debug: bool = False) -> int:
    """启动 Web GUI。

    Args:
        browser: 不打开 pywebview 窗口，改用系统浏览器。
        debug: 打开 pywebview 调试。
    """
    from script_chainer.webui.server import create_app

    port = _find_free_port()
    server = _ServerThread(create_app(), port)
    server.start()

    for _ in range(200):
        if _port_open(port):
            break
        time.sleep(0.05)
    else:
        print(f'错误：本地服务未能启动（端口 {port}）', file=sys.stderr)
        server.stop()
        return 1

    url = f'http://127.0.0.1:{port}/'
    try:
        if not browser:
            import webview

            webview.create_window(
                '千机链',
                url,
                width=1500,
                height=940,
                min_size=(1180, 760),
            )
            webview.start(debug=debug)
        else:
            webbrowser.open(url)
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f'窗口启动失败，改用系统浏览器：{e}', file=sys.stderr)
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        server.stop()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description='千机链 Web GUI')
    parser.add_argument('--browser', action='store_true', help='用系统浏览器打开，不使用 pywebview')
    parser.add_argument('--debug', action='store_true', help='打开 webview 调试')
    args = parser.parse_args()
    sys.exit(run_webui(browser=args.browser, debug=args.debug))


if __name__ == '__main__':
    main()
