#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之刃零 存档工坊 —— 本地 Web 版

    python pbz_web.py            # 自动开浏览器 http://127.0.0.1:8765
    python pbz_web.py --port 9000 --no-open

所有读写都在本机完成，不上传任何数据；写盘前必定生成 .bak 备份。
"""
import argparse
import ctypes
import datetime
import glob
import json
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pbz_tool as T  # noqa: E402

# 打包成 exe 时前端资源被解到 sys._MEIPASS
_RES = getattr(sys, "_MEIPASS", HERE)
WEB = os.path.join(_RES, "web", "index.html")
if not os.path.exists(WEB):
    WEB = os.path.join(HERE, "web", "index.html")
LOCK = threading.Lock()
_CACHE = {}


# ---------------------------------------------------------------- data
SAV_CFG = os.path.join(T.APP_DIR, "sav_path.txt")
EXE_CFG = os.path.join(T.APP_DIR, "exe_path.txt")


def _read_cfg(p):
    try:
        v = open(p, encoding="utf-8").read().strip()
        return v or None
    except OSError:
        return None


def _write_cfg(p, v):
    try:
        if v:
            open(p, "w", encoding="utf-8").write(v + "\n")
        elif os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


def sav_source():
    return "manual" if _read_cfg(SAV_CFG) else "auto"


def sav_path():
    manual = _read_cfg(SAV_CFG)
    if manual and os.path.exists(manual):
        return manual
    return T.find_sav()


_ORIG_FIND_SAV = T.find_sav
_ORIG_FIND_EXE = T.find_exe


def apply_paths(sav=None, exe=None):
    """切换存档/主程序路径并落盘；传空串表示清空（恢复自动探测）"""
    if exe is not None:
        if exe == "":
            T.find_exe = _ORIG_FIND_EXE
            _write_cfg(EXE_CFG, None)
        else:
            if not os.path.isfile(exe):
                return False, f"主程序不存在：{exe}"
            T.find_exe = lambda: exe
            _write_cfg(EXE_CFG, exe)

    if sav is None or sav == "":
        if sav == "":
            T.find_sav = _ORIG_FIND_SAV
            _write_cfg(SAV_CFG, None)
            _CACHE.clear()
            load(force=True)
        return True, None

    if not os.path.isfile(sav):
        return False, f"文件不存在：{sav}"
    if not sav.lower().endswith(".sav"):
        return False, "这看起来不是 PBZSaveGame.sav"

    T.find_sav = lambda: sav
    _write_cfg(SAV_CFG, sav)
    _CACHE.clear()
    try:
        sf = load(force=True)
        if not sf.slots:
            raise RuntimeError("文件能打开但没有槽位")
    except Exception as e:
        _write_cfg(SAV_CFG, None)
        T.find_sav = _ORIG_FIND_SAV
        _CACHE.clear()
        try: load(force=True)
        except Exception: pass
        return False, f"打不开这个存档：{e}"
    return True, None


# ---------------------------------------------------------------- 名字
_NAMES = None


def names_path():
    """优先用 exe/脚本旁边的 boss_names.json（可自己改），没有再从内嵌资源拷一份。"""
    p = os.path.join(T.APP_DIR, "boss_names.json")
    src = os.path.join(_RES, "boss_names.json")
    if os.path.abspath(src) == os.path.abspath(p):
        return p
    if not os.path.exists(p) or _json_ver(src) > _json_ver(p):
        try:
            shutil.copy2(src, p)
        except OSError:
            return src if os.path.exists(src) else p
    return p


def _json_ver(path):
    try:
        return int(json.load(open(path, encoding="utf-8")).get("_version", 0))
    except Exception:
        return -1


def names():
    global _NAMES
    if _NAMES is None:
        try:
            _NAMES = json.load(open(names_path(), encoding="utf-8"))
        except Exception:
            _NAMES = {}
    return _NAMES


def with_name(row):
    n = names().get(row["boss"]) or {}
    row["cn"] = n.get("cn") or row["boss"].replace("BP_Monster_", "").replace("_", " ")
    row["en"] = n.get("en") or ""
    row["verified"] = bool(n.get("verified", False))
    return row


def load(force=False):
    p = sav_path()
    st = os.stat(p)
    key = (p, st.st_mtime_ns, st.st_size)
    if force or key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = T.SaveFile(p, OODLE)
    return _CACHE[key]


def playable(sf):
    return [s for s in sf.slots if not s.name.startswith("__")]


def slot_brief(s, active):
    g = s.json.get("PBZGameSystem") or {}
    d = g.get("DifficultModeByte")
    gt = g.get("HasGameTime") or 0
    return {
        "name": s.name,
        "diff": d,
        "diffName": T.DIFF_NAMES.get(d, str(d)),
        "cycle": g.get("CycleCount", 0),
        "playtime": f"{gt // 3600}h{gt % 3600 // 60:02d}m",
        "deaths": (s.json.get("PBZDeathMeta") or {}).get("DiedTimes"),
        "days": (s.json.get("Player") or {}).get("RemainingDays"),
        "savedAt": g.get("DateTimeString", ""),
        "active": s.name == active,
        "level": g.get("CurrentLevelName", ""),
    }


ITEM_HINT = {
    "Item_Traversing": "穿行符",
    "Item_Piece_002": "碎片",
    "Return_Weapon_1": "武器重铸符",
    "Return_ShaqiWeapon_1": "幻刃重铸符",
}


def item_hint(iid):
    if iid in ITEM_HINT:
        return ITEM_HINT[iid]
    if iid.startswith("Weapon_Material"):
        return "武器升级材料"
    if iid.startswith("Page_"):
        return "记忆之花·残页"
    if iid.startswith("Book_"):
        return "书籍"
    if iid.startswith("Medi_"):
        return "药品材料"
    return ""


def item_rows(slot_obj):
    inv = (slot_obj.json.get("Inventory") or {}).get("InventoryInfos") or {}
    out = []
    for iid, v in inv.items():
        out.append({
            "id": iid,
            "hint": item_hint(iid),
            "amount": v.get("CurrentAmount", 0),
            "max": v.get("MaxAmount"),
            "first": v.get("FirstAddTime"),
        })
    out.sort(key=lambda x: (-(x["amount"] or 0), x["id"]))
    return out


def snapshot_first_kills(sf):
    """用各存档槽的快照时间，推断每个 Boss 首次被击杀的时间。
    游戏没给 Boss 记录时间戳，只能看它在哪个快照里第一次出现 KillCount>0。"""
    snaps = []
    for s in sf.slots:
        if s.name.startswith("__"):
            continue
        g = s.json.get("PBZGameSystem") or {}
        ts = g.get("DateTimeString")
        if ts:
            snaps.append((ts, s.name, s.json.get("BossRecord") or {}))
    snaps.sort(key=lambda x: x[0])
    first = {}
    for i, (ts, name, br) in enumerate(snaps):
        for b, v in br.items():
            for k, r in (v.get("Records") or {}).items():
                if r.get("KillCount", 0) > 0 and b not in first:
                    first[b] = {"ts": ts, "slot": name, "known": i > 0}
    return first, (snaps[0][0] if snaps else None)


def rows_of(slot_obj):
    out = []
    for bname, v in (slot_obj.json.get("BossRecord") or {}).items():
        for k, r in (v.get("Records") or {}).items():
            if "_" in k:
                cycle, diff = k.split("_", 1)
            else:
                cycle, diff = "0", k
            att, kil = r.get("AttemptCount", 0), r.get("KillCount", 0)
            out.append(with_name({
                "boss": bname, "key": k, "cycle": cycle, "diff": int(diff),
                "diffName": T.DIFF_NAMES.get(int(diff), diff),
                "attempts": att, "kills": kil, "deaths": max(att - kil, 0),
                "bestMs": r.get("BestTimeMs"),
                "classPath": v.get("ClassPath", ""),
            }))
    out.sort(key=lambda x: (-x["diff"], -x["deaths"], x["boss"]))
    return out


def global_rows(sf):
    adv = sf.get("__Advanced__")
    out = []
    if not adv:
        return out
    for bname, v in (adv.json.get("BossRecord") or {}).items():
        for k, r in (v.get("Records") or {}).items():
            att, kil = r.get("AttemptCount", 0), r.get("KillCount", 0)
            out.append(with_name({"boss": bname, "key": k, "diff": int(k),
                                  "attempts": att, "kills": kil, "deaths": max(att - kil, 0),
                                  "bestMs": r.get("BestTimeMs"),
                                  "cycle": r.get("Cycle", 0)}))
    out.sort(key=lambda x: (-x["diff"], -x["deaths"], x["boss"]))
    return out


def backups(p):
    items = []
    for f in sorted(glob.glob(p + ".bak-*"), reverse=True):
        st = os.stat(f)
        items.append({"file": os.path.basename(f),
                      "time": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                      "size": st.st_size})
    return items


def game_running():
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq PBZ.exe"], capture_output=True)
        out = (r.stdout or b"").decode("utf-8", "ignore") + (r.stdout or b"").decode("gbk", "ignore")
        return "PBZ.exe" in out
    except Exception:
        return False


# ---------------------------------------------------------------- patch
def apply_patch(sf, req, commit):
    """返回 (changes, backup)。commit=False 时只在内存里跑一遍做预览。"""
    changes = []
    real = [0]
    scope_slot = req.get("slot")
    scope = req.get("scope", "all")
    targets = playable(sf) if scope == "all" else [sf.get(scope_slot)]
    targets = [t for t in targets if t]
    adv = sf.get("__Advanced__")

    def note(msg):
        changes.append(msg)

    diff_want = req.get("difficulty")
    if diff_want is not None:
        for s in targets:
            g = s.json.get("PBZGameSystem")
            if not g:
                continue
            old = g.get("DifficultModeByte")
            if old != diff_want:
                if commit:
                    g["DifficultModeByte"] = diff_want
                    s.mark()
                real[0] += 1
                note(f"{s.name}: 难度 {T.DIFF_NAMES.get(old, old)} → {T.DIFF_NAMES.get(diff_want, diff_want)}")

    # 按槽位单独改难度：{"ManualSave13": 2, ...}
    for sname, dv in (req.get("slotDiffs") or {}).items():
        s = sf.get(sname)
        if not s:
            continue
        g = s.json.get("PBZGameSystem")
        if not g:
            continue
        old = g.get("DifficultModeByte")
        if old != int(dv):
            if commit:
                g["DifficultModeByte"] = int(dv)
                s.mark()
            real[0] += 1
            note(f"{s.name}: 难度 {T.DIFF_NAMES.get(old, old)} → {T.DIFF_NAMES.get(int(dv), dv)}")

    # 把某条战报挪到别的难度下：键名 <周目>_<难度> 重命名，生涯战报键名 <难度>
    deaths_want = req.get("totalDeaths")
    if deaths_want is not None:
        for s in sf.slots:
            dm = s.json.get("PBZDeathMeta")
            if dm and dm.get("DiedTimes") != deaths_want:
                if commit:
                    dm["DiedTimes"] = deaths_want
                    s.mark()
                real[0] += 1
                note(f"{s.name}: 总死亡数 {dm.get('DiedTimes')} → {deaths_want}")

    for e in req.get("edits") or []:
        boss, key = e["boss"], e["key"]
        diff = int(key.split("_")[-1]) if "_" in key else int(key)
        hit = False
        found = False
        for s in targets:
            rec = ((s.json.get("BossRecord") or {}).get(boss) or {}).get("Records", {}).get(key)
            if not rec:
                continue
            found = True
            old = (rec.get("AttemptCount"), rec.get("KillCount"), rec.get("BestTimeMs"))
            if e.get("attempts") is not None:
                rec["AttemptCount"] = int(e["attempts"])
            if e.get("kills") is not None:
                rec["KillCount"] = int(e["kills"])
            if e.get("bestMs") is not None:
                rec["BestTimeMs"] = int(e["bestMs"])
            new = (rec.get("AttemptCount"), rec.get("KillCount"), rec.get("BestTimeMs"))
            if new != old:
                if commit:
                    s.mark()
                real[0] += 1
                note(f"{s.name} / {boss.split('_')[-1]} [{key}]: {old} → {new}")
                hit = True
        # 生涯战报同步
        if adv:
            arec = ((adv.json.get("BossRecord") or {}).get(boss) or {}).get("Records", {}).get(str(diff))
            if arec:
                found = True
                old = (arec.get("AttemptCount"), arec.get("KillCount"), arec.get("BestTimeMs"))
                if e.get("attempts") is not None:
                    arec["AttemptCount"] = int(e["attempts"])
                if e.get("kills") is not None:
                    arec["KillCount"] = int(e["kills"])
                if e.get("bestMs") is not None:
                    arec["BestTimeMs"] = int(e["bestMs"])
                new = (arec.get("AttemptCount"), arec.get("KillCount"), arec.get("BestTimeMs"))
                if new != old:
                    if commit:
                        adv.mark()
                    real[0] += 1
                    note(f"生涯战报 / {boss.split('_')[-1]} [{diff}]: {old} → {new}")
                    hit = True
        if not found:
            note(f"✗ 找不到 {boss.split('_')[-1]} [{key}]：所选范围里的存档没有这条记录")
        elif not hit:
            note(f"= {boss.split('_')[-1]} [{key}]：填的数值和存档里一样，没有改动")

    for mv in req.get("moves") or []:
        boss, key, to_diff = mv["boss"], mv["key"], int(mv["toDiff"])
        parts = key.split("_")
        src_diff = int(parts[-1])
        cycle = parts[0] if len(parts) > 1 else "0"
        if src_diff == to_diff:
            continue
        newkey = f"{cycle}_{to_diff}"
        for s in targets:
            rs = ((s.json.get("BossRecord") or {}).get(boss) or {}).get("Records")
            if not rs or key not in rs:
                continue
            if commit:
                rs[newkey] = rs.pop(key)
                s.mark()
            real[0] += 1
            note(f"{s.name} / {boss.split('_')[-1]}: 记录 {key} → {newkey}")
        if adv:
            br = ((adv.json.get("BossRecord") or {}).get(boss) or {}).get("Records")
            if br and str(src_diff) in br:
                if commit:
                    br[str(to_diff)] = br.pop(str(src_diff))
                    adv.mark()
                real[0] += 1
                note(f"生涯战报 / {boss.split('_')[-1]}: 记录 {src_diff} → {to_diff}")

    for e in req.get("items") or []:
        iid = e["id"]
        amt = int(e["amount"])
        found = changed = False
        for s in targets:
            inv = (s.json.get("Inventory") or {}).get("InventoryInfos") or {}
            rec = inv.get(iid)
            if not rec:
                continue
            found = True
            old = rec.get("CurrentAmount")
            if old != amt:
                if commit:
                    rec["CurrentAmount"] = amt
                    s.mark()
                real[0] += 1
                changed = True
                note(f"{s.name} / {iid}: 数量 {old} → {amt}")
        if not found:
            note(f"✗ 找不到物品 {iid}：所选范围里的存档没有这一项")
        elif not changed:
            note(f"= {iid}：数量已经是 {amt}，没有改动")

    bak = None
    if commit and real[0] > 0:
        if game_running():
            raise RuntimeError("检测到 PBZ.exe 正在运行，请先完全退出游戏和 Steam")
        bak = sf.backup()
        sf.save(sf.path)
        load(force=True)
    return changes, bak, real[0]


# ---------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    server_version = "PBZSaveStudio/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _open_folder(self):
        try:
            p = sav_path()
            target = os.path.normpath(p)
            if os.path.exists(target):
                subprocess.Popen(["explorer", "/select,", target])
            else:
                subprocess.Popen(["explorer", os.path.dirname(target)])
            return self._send(200, {"ok": True, "path": p})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                with open(WEB, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if u.path == "/api/state":
                sf = load()
                adv = sf.get("__Advanced__")
                active = adv.json.get("LastContinueSlot") if adv else None
                p = sf.path
                return self._send(200, {
                    "sav": p, "savSource": sav_source(), "size": len(sf.buf), "slotCount": len(sf.slots),
                    "gameRunning": game_running(),
                    "active": active,
                    "slots": [slot_brief(s, active) for s in playable(sf)],
                    "backups": backups(p),
                    "difficulties": T.DIFF_NAMES,
                })
            if u.path == "/api/debug":
                q = parse_qs(u.query)
                msg = (q.get("m") or [""])[0]
                try:
                    with open(os.path.join(T.APP_DIR, "app.log"), "a", encoding="utf-8") as f:
                        f.write(f"[debug] {msg}\n")
                except OSError:
                    pass
                return self._send(200, {"ok": True})
            if u.path == "/api/open-folder":
                return self._open_folder()
            if u.path == "/api/items":
                q = parse_qs(u.query)
                name = (q.get("slot") or [None])[0]
                sf = load()
                sl = sf.get(name) if name else None
                if not sl:
                    return self._send(404, {"error": "槽位不存在"})
                return self._send(200, {"slot": sl.name, "items": item_rows(sl)})
            if u.path == "/api/boss":
                q = parse_qs(u.query)
                name = (q.get("slot") or [None])[0]
                sf = load()
                s = sf.get(name) if name else None
                if not s:
                    return self._send(404, {"error": "槽位不存在"})
                rows = rows_of(s)
                first, earliest = snapshot_first_kills(sf)
                for r in rows:
                    r["firstKill"] = first.get(r["boss"])
                return self._send(200, {"slot": s.name, "rows": rows,
                                        "global": global_rows(sf), "earliestSnap": earliest})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
            with LOCK:
                if u.path == "/api/open-folder":
                    return self._open_folder()
                if u.path == "/api/set-sav":
                    ok, err = apply_paths(req.get("sav"), req.get("exe"))
                    if not ok:
                        return self._send(400, {"error": err})
                    return self._send(200, {"ok": True, "sav": sav_path()})
                if u.path == "/api/preview":
                    # 关键：预览必须改在“一次性副本”上，绝不能碰缓存里的对象，
                    # 否则预览就把内存改掉了，紧接着的写入会误判为“数值没变”。
                    sf = T.SaveFile(load().path, OODLE)
                    changes, _, n = apply_patch(sf, req, commit=False)
                    return self._send(200, {"changes": changes, "applied": n})
                if u.path == "/api/apply":
                    # 同理，写入前也从磁盘重新读一份，保证判断基准是磁盘上的真实值
                    sf = load(force=True)
                    changes, bak, n = apply_patch(sf, req, commit=True)
                    if n == 0:
                        load(force=True)
                    return self._send(200, {"changes": changes, "backup": bak, "applied": n})
                if u.path == "/api/restore":
                    p = sav_path()
                    src = os.path.join(os.path.dirname(p), os.path.basename(req["file"]))
                    if not os.path.exists(src):
                        raise RuntimeError("备份文件不存在")
                    if game_running():
                        raise RuntimeError("检测到 PBZ.exe 正在运行，请先退出游戏")
                    shutil.copy2(src, src + ".restore-tmp")
                    shutil.copy2(p, p + ".bak-before-restore")
                    os.replace(src + ".restore-tmp", p)
                    sf = load(force=True)
                    return self._send(200, {"ok": True, "restored": req["file"]})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})


def _fatal(msg):
    """双击运行时控制台一闪而过，弹个框让人看得见。"""
    try:
        ctypes.windll.user32.MessageBoxW(None, str(msg), "影之刃零 存档工坊 · 启动失败", 0x10)
    except Exception:
        pass


def main():
    global OODLE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--sav", default=None)
    ap.add_argument("--oodle", default=None)
    args = ap.parse_args()
    ce = _read_cfg(EXE_CFG)
    if ce and os.path.isfile(ce):
        T.find_exe = lambda: ce
    if args.sav:
        T.find_sav = lambda: args.sav
    try:
        OODLE = T.Oodle(args.oodle)
        sav = sav_path()
    except Exception as e:
        print("启动失败：", e, flush=True)
        _fatal(e)
        input("按回车退出…")
        return
    url = f"http://127.0.0.1:{args.port}/"
    print("=" * 62)
    print("  影之刃零 · 存档工坊")
    print(f"  地址：{url}")
    print(f"  存档：{sav}")
    print(f"  Oodle：{OODLE.path}")
    print("=" * 62)
    print("关闭这个窗口（或按 Ctrl+C）即退出工具。")
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    except OSError as e:
        _fatal(f"端口 {args.port} 被占用了，可能已经开着一个窗口。\n{e}")
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
