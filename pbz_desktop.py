#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之刃零 存档工坊 · 桌面版

原生窗口（WebView2），不弹命令窗口、不进浏览器。
    python pbz_desktop.py
"""
import argparse
import ctypes
import datetime
import os
import socket
import sys
import threading
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pbz_tool as T  # noqa: E402
import pbz_web as W  # noqa: E402


def log(msg):
    try:
        p = os.path.join(T.APP_DIR, "app.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except OSError:
        pass


def fatal(msg):
    log("FATAL: " + str(msg))
    try:
        ctypes.windll.user32.MessageBoxW(None, str(msg), "影之刃零 存档工坊 · 启动失败", 0x10)
    except Exception:
        pass


def work_area():
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
    r = RECT()
    try:
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
            return (r.right - r.left, r.bottom - r.top)
    except Exception:
        pass
    u = ctypes.windll.user32
    return (u.GetSystemMetrics(0), u.GetSystemMetrics(1))


def free_port():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sav", default=None)
    ap.add_argument("--oodle", default=None)
    args = ap.parse_args()

    if args.sav:
        T.find_sav = lambda: args.sav

    try:
        W.OODLE = T.Oodle(args.oodle)
        sav = W.sav_path()
        W.load()          # 先读一次，密钥/解码有问题在这里就报出来
    except Exception as e:
        fatal(e)
        return 1

    # 屏幕缩放 125%/150% 时 WebView2 会把内容放大，用 1:1 渲染避免右侧被裁
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--force-device-scale-factor=1")

    port = free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), W.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    log(f"start {url}  sav={sav}")

    wa = work_area()
    win_w = min(1380, max(1024, wa[0] - 80))
    win_h = min(920, max(660, wa[1] - 80))
    log(f"workarea={wa} window={win_w}x{win_h}")

    import webview
    webview.create_window(
        "影之刃零 · 存档工坊",
        url,
        width=win_w,
        height=win_h,
        min_size=(1000, 620),
        background_color="#f6f3ec",
        text_select=False,
    )
    try:
        webview.start()
    except Exception as e:
        fatal(e)
        return 1
    finally:
        srv.shutdown()
        log("exit")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
