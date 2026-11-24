#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phantom Blade Zero Save Studio -- local web edition

    python pbz_web.py            # opens the browser at http://127.0.0.1:8765
    python pbz_web.py --port 9000 --no-open

All reads and writes happen locally; nothing is uploaded. A .bak backup is always
created before writing to disk.
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

# When packaged as an exe the front-end assets are unpacked to sys._MEIPASS
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
    """Switch the save/executable paths and persist them; an empty string clears
    the override and restores auto-detection."""
    if exe is not None:
        if exe == "":
            T.find_exe = _ORIG_FIND_EXE
            _write_cfg(EXE_CFG, None)
        else:
            if not os.path.isfile(exe):
                return False, f"Executable does not exist: {exe}"
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
        return False, f"File does not exist: {sav}"
    if not sav.lower().endswith(".sav"):
        return False, "This does not look like PBZSaveGame.sav"

    T.find_sav = lambda: sav
    _write_cfg(SAV_CFG, sav)
    _CACHE.clear()
    try:
        sf = load(force=True)
        if not sf.slots:
            raise RuntimeError("The file opens but contains no slots")
    except Exception as e:
        _write_cfg(SAV_CFG, None)
        T.find_sav = _ORIG_FIND_SAV
        _CACHE.clear()
        try: load(force=True)
        except Exception: pass
        return False, f"Cannot open this save: {e}"
    return True, None


# ---------------------------------------------------------------- names
_NAMES = None


def names_path():
    """Prefer the boss_names.json next to the exe/script (you can edit it); fall back
    to copying the bundled one when missing."""
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
    "Item_Traversing": "Traversal Talisman",
    "Item_Piece_002": "Fragment",
    "Return_Weapon_1": "Weapon Reforge Talisman",
    "Return_ShaqiWeapon_1": "Phantom Blade Reforge Talisman",
}


def item_hint(iid):
    if iid in ITEM_HINT:
        return ITEM_HINT[iid]
    if iid.startswith("Weapon_Material"):
        return "Weapon upgrade material"
    if iid.startswith("Page_"):
        return "Flower of Memory (torn page)"
    if iid.startswith("Book_"):
        return "Book"
    if iid.startswith("Medi_"):
        return "Medicine material"
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
    """Infer when each boss was first killed, using the snapshot times of the save
    slots. The game does not timestamp boss records, so the only signal is which
    snapshot first shows KillCount>0 for that boss."""
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
    """Return (changes, backup). With commit=False it only runs in memory as a preview."""
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
                note(f"{s.name}: difficulty {T.DIFF_NAMES.get(old, old)} -> {T.DIFF_NAMES.get(diff_want, diff_want)}")

    # Per-slot difficulty overrides: {"ManualSave13": 2, ...}
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
            note(f"{s.name}: difficulty {T.DIFF_NAMES.get(old, old)} -> {T.DIFF_NAMES.get(int(dv), dv)}")

    # Move a record to another difficulty: rename the key <cycle>_<difficulty>;
    # career records are keyed by <difficulty> alone.
    deaths_want = req.get("totalDeaths")
    if deaths_want is not None:
        for s in sf.slots:
            dm = s.json.get("PBZDeathMeta")
            if dm and dm.get("DiedTimes") != deaths_want:
                if commit:
                    dm["DiedTimes"] = deaths_want
                    s.mark()
                real[0] += 1
                note(f"{s.name}: total deaths {dm.get('DiedTimes')} -> {deaths_want}")

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
        # Keep the career records in sync
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
                    note(f"career records / {boss.split('_')[-1]} [{diff}]: {old} -> {new}")
                    hit = True
        if not found:
            note(f"✗ {boss.split('_')[-1]} [{key}] not found: no save in the selected scope has that record")
        elif not hit:
            note(f"= {boss.split('_')[-1]} [{key}]: the value matches the save, nothing changed")

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
            note(f"{s.name} / {boss.split('_')[-1]}: record {key} -> {newkey}")
        if adv:
            br = ((adv.json.get("BossRecord") or {}).get(boss) or {}).get("Records")
            if br and str(src_diff) in br:
                if commit:
                    br[str(to_diff)] = br.pop(str(src_diff))
                    adv.mark()
                real[0] += 1
                note(f"career records / {boss.split('_')[-1]}: record {src_diff} -> {to_diff}")

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
                note(f"{s.name} / {iid}: amount {old} -> {amt}")
        if not found:
            note(f"✗ item {iid} not found: no save in the selected scope has that item")
        elif not changed:
            note(f"= {iid}: amount is already {amt}, nothing changed")

    bak = None
    if commit and real[0] > 0:
        if game_running():
            raise RuntimeError("PBZ.exe is running. Fully quit the game and Steam first.")
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
                    return self._send(404, {"error": "Slot not found"})
                return self._send(200, {"slot": sl.name, "items": item_rows(sl)})
            if u.path == "/api/boss":
                q = parse_qs(u.query)
                name = (q.get("slot") or [None])[0]
                sf = load()
                s = sf.get(name) if name else None
                if not s:
                    return self._send(404, {"error": "Slot not found"})
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
                    # Important: a preview must mutate a throwaway copy, never the
                    # cached object, otherwise the preview itself changes memory and
                    # the write that follows is misjudged as "the value did not change".
                    sf = T.SaveFile(load().path, OODLE)
                    changes, _, n = apply_patch(sf, req, commit=False)
                    return self._send(200, {"changes": changes, "applied": n})
                if u.path == "/api/apply":
                    # Likewise, re-read from disk before writing so the comparison
                    # basis is the real on-disk value.
                    sf = load(force=True)
                    changes, bak, n = apply_patch(sf, req, commit=True)
                    if n == 0:
                        load(force=True)
                    return self._send(200, {"changes": changes, "backup": bak, "applied": n})
                if u.path == "/api/restore":
                    p = sav_path()
                    src = os.path.join(os.path.dirname(p), os.path.basename(req["file"]))
                    if not os.path.exists(src):
                        raise RuntimeError("Backup file does not exist")
                    if game_running():
                        raise RuntimeError("PBZ.exe is running. Quit the game first.")
                    shutil.copy2(src, src + ".restore-tmp")
                    shutil.copy2(p, p + ".bak-before-restore")
                    os.replace(src + ".restore-tmp", p)
                    sf = load(force=True)
                    return self._send(200, {"ok": True, "restored": req["file"]})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})


def _fatal(msg):
    """Double-clicking flashes the console past too fast, so show a message box."""
    try:
        ctypes.windll.user32.MessageBoxW(None, str(msg), "Phantom Blade Zero Save Studio - startup failed", 0x10)
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
        print("Startup failed:", e, flush=True)
        _fatal(e)
        input("Press Enter to exit...")
        return
    url = f"http://127.0.0.1:{args.port}/"
    print("=" * 62)
    print("  Phantom Blade Zero - Save Studio")
    print(f"  URL: {url}")
    print(f"  Save: {sav}")
    print(f"  Oodle：{OODLE.path}")
    print("=" * 62)
    print("Close this window (or press Ctrl+C) to quit.")
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    except OSError as e:
        _fatal(f"Port {args.port} is in use; another window may already be running.\n{e}")
    except KeyboardInterrupt:
        print("\nExited")


if __name__ == "__main__":
    main()
