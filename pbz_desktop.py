#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phantom Blade Zero Save Studio - desktop edition

A native window (WebView2): no console window, no browser.
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
        ctypes.windll.user32.MessageBoxW(None, str(msg), "Phantom Blade Zero Save Studio - startup failed", 0x10)
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
    ap.add_argument("--sav", default=None, help="Manually specify the save file (for non-Steam builds)")
    ap.add_argument("--exe", default=None, help="Manually specify the game executable PBZ.exe (used to recover the key)")
    ap.add_argument("--oodle", default=None)
    ap.add_argument("--view", default=None, help="Default view on startup: table / card")
    ap.add_argument("--tab", default=None, help="Tab to open on startup: boss / items / global")
    args = ap.parse_args()

    if args.sav:
        T.find_sav = lambda: args.sav
    if args.exe:
        T.find_exe = lambda: args.exe

    try:
        W.OODLE = T.Oodle(args.oodle)
        sav = W.sav_path()
        log(f"exe={T.find_exe() if not args.exe else args.exe}")
        W.load()          # read once up-front so key/decode problems surface here
    except Exception as e:
        fatal(e)
        return 1

    # At 125%/150% display scaling WebView2 enlarges the content, so render 1:1 to
    # avoid clipping the right-hand side
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--force-device-scale-factor=1")

    port = free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), W.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    qs = []
    if args.view:
        qs.append(f"view={args.view}")
    if args.tab:
        qs.append(f"tab={args.tab}")
    if qs:
        url += "?" + "&".join(qs)
    log(f"start {url}  sav={sav}")

    wa = work_area()
    win_w = min(1380, max(1024, wa[0] - 80))
    win_h = min(920, max(660, wa[1] - 80))
    log(f"workarea={wa} window={win_w}x{win_h}")

    import webview

    class Bridge:
        """Native capability exposed to the front-end: open a file picker."""
        def pick_save(self):
            try:
                w = webview.windows[0]
                r = w.create_file_dialog(
                    webview.OPEN_DIALOG, allow_multiple=False,
                    file_types=("Save files (*.sav;*.sav.bak-*)", "All files (*.*)"))
                return r[0] if r else ""
            except Exception:
                return ""

        def pick_exe(self):
            try:
                w = webview.windows[0]
                r = w.create_file_dialog(
                    webview.OPEN_DIALOG, allow_multiple=False,
                    file_types=("Programs (*.exe)", "All files (*.*)"))
                return r[0] if r else ""
            except Exception:
                return ""

    webview.create_window(
        "Phantom Blade Zero - Save Studio",
        url,
        width=win_w,
        height=win_h,
        min_size=(1000, 620),
        background_color="#f6f3ec",
        text_select=False,
        js_api=Bridge(),
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
