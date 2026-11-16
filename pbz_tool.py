#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之刃零 (Phantom Blade Zero) 存档编辑器 —— PBZS v3 容器读写
支持：查看 / 修改 每档难度、Boss 身陨次数(挑战次数)、击杀用时，以及全局死亡总数。

用法：
  python pbz_tool.py info                                  # 查看存档概况 + Boss 战报
  python pbz_tool.py info --slot ManualSave17              # 只看某一档
  python pbz_tool.py set-difficulty 3                      # 改难度 (0残疾 1入阵者 2破局者 3逆天者 4六十六天)
  python pbz_tool.py set-boss GuiChai --diff 3 --deaths 0  # 把某 Boss 在该难度的身陨次数改成 0
  python pbz_tool.py set-boss GuiChai --diff 3 --time 1:35.200   # 改最佳击杀时间
  python pbz_tool.py set-boss all --diff 3 --deaths 0      # 批量：该难度下所有 Boss
  python pbz_tool.py set-total-deaths 0                    # 全局死亡总数(PBZDeathMeta)
  python pbz_tool.py move-records --from 1 --to 3          # 把某难度的战报改名到另一难度
  python pbz_tool.py export --out dump/                    # 导出所有槽位 JSON
  python pbz_tool.py apply --in dump/                      # 回写改过的 JSON

所有写操作都会：备份原文件 → 校验新文件 → 原子替换。务必先完全退出游戏和 Steam。
"""
import argparse
import ctypes
import datetime
import json
import os
import re
import shutil
import struct
import subprocess
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HERE = os.path.dirname(os.path.abspath(__file__))


def app_dir():
    """打包成 exe 后，配置/缓存写在 exe 旁边。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return HERE


APP_DIR = app_dir()
KEY_CACHE = os.path.join(APP_DIR, "pbz_aes_key.txt")
OODLE_CFG = os.path.join(APP_DIR, "oodle_path.txt")
DIFF_NAMES = {0: "残疾(Handicapped)", 1: "入阵者(Easy)", 2: "破局者(Normal)",
              3: "逆天者(Hard)", 4: "六十六天(NightMare)"}
OODLE_NAMES = ["oo2core_9_win64.dll", "oo2core_8_win64.dll", "oo2core_7_win64.dll",
               "oo2core_6_win64.dll", "oo2core_5_win64.dll"]


def steam_libraries():
    """找出本机所有 Steam 库根目录。"""
    cands = []
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    cands.append(winreg.QueryValueEx(k, "SteamPath")[0])
            except OSError:
                pass
    except ImportError:
        pass
    cands += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam",
              r"C:\Steam", r"D:\Steam", r"E:\Steam", r"G:\Steam", r"Z:\media\game\steam"]
    out, seen = [], set()
    for r in cands:
        if not r or not os.path.isdir(r):
            continue
        r = os.path.normpath(r)
        if r.lower() in seen:
            continue
        seen.add(r.lower())
        out.append(r)
        vdf = os.path.join(r, r"steamapps\libraryfolders.vdf")
        if os.path.exists(vdf):
            try:
                txt = open(vdf, encoding="utf-8", errors="ignore").read()
                for m in re.finditer(r'"path"\s*"([^"]+)"', txt):
                    p = os.path.normpath(m.group(1).replace("\\\\", "\\"))
                    if os.path.isdir(p) and p.lower() not in seen:
                        seen.add(p.lower())
                        out.append(p)
            except OSError:
                pass
    return out


def find_oodle(explicit=None):
    for p in (explicit, os.environ.get("OODLE_DLL")):
        if p and os.path.exists(p):
            return p
    if os.path.exists(OODLE_CFG):
        try:
            p = open(OODLE_CFG, encoding="utf-8").read().strip()
            if p and os.path.exists(p):
                return p
        except OSError:
            pass
    libs = steam_libraries()
    for lib in libs:                                    # 已知位置优先
        common = os.path.join(lib, "steamapps", "common")
        for game, subs in (("Death Stranding", ("",)),
                           ("PBZ", ("", os.path.join("ProjectPBZ", "Binaries", "Win64")))):
            for sub in subs:
                for n in OODLE_NAMES:
                    p = os.path.join(common, game, sub, n)
                    if os.path.exists(p):
                        _remember_oodle(p)
                        return p
    for lib in libs:                                    # 浅扫每个游戏目录
        common = os.path.join(lib, "steamapps", "common")
        try:
            dirs = [e.path for e in os.scandir(common) if e.is_dir()]
        except OSError:
            continue
        for d in dirs:
            for n in OODLE_NAMES:
                p = os.path.join(d, n)
                if os.path.exists(p):
                    _remember_oodle(p)
                    return p
    p = _ask_dll()
    if p and os.path.exists(p):
        _remember_oodle(p)
        return p
    raise RuntimeError("找不到 oo2core_*_win64.dll（Oodle 解压库）。\n"
                       "随便找一个装了 UE 游戏的 Steam 目录就能借到，例如\n"
                       "  ...\\steamapps\\common\\Death Stranding\\oo2core_7_win64.dll\n"
                       "把路径写进 oodle_path.txt，或用 --oodle 指定。")


def _remember_oodle(p):
    try:
        with open(OODLE_CFG, "w", encoding="utf-8") as f:
            f.write(p)
    except OSError:
        pass


def _ask_dll():
    try:
        import tkinter as tk
        from tkinter import filedialog
        r = tk.Tk()
        r.withdraw()
        r.attributes("-topmost", True)
        p = filedialog.askopenfilename(
            title="选择 oo2core_*_win64.dll（任意 UE 游戏目录里都有）",
            filetypes=[("Oodle 库", "oo2core*.dll"), ("DLL", "*.dll")])
        r.destroy()
        return p
    except Exception:
        return None


# ---------------------------------------------------------------- Oodle
class Oodle:
    def __init__(self, path=None):
        self.path = find_oodle(path)
        self.lib = ctypes.WinDLL(self.path)
        d = self.lib.OodleLZ_Decompress
        d.restype = ctypes.c_ssize_t
        d.argtypes = [ctypes.c_char_p, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_ssize_t,
                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t,
                      ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int]
        c = self.lib.OodleLZ_Compress
        c.restype = ctypes.c_ssize_t
        c.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_int,
                      ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t]
        self._d, self._c = d, c

    def decompress(self, buf, raw_size):
        out = ctypes.create_string_buffer(raw_size)
        n = self._d(buf, len(buf), ctypes.cast(out, ctypes.c_void_p), raw_size,
                    1, 0, 0, None, 0, None, None, None, 0, 3)
        if n <= 0:
            raise RuntimeError("Oodle 解压失败")
        return out.raw[:n]

    def compress(self, raw, level=4):
        out = ctypes.create_string_buffer(len(raw) + len(raw) // 2 + 4096)
        n = self._c(8, raw, len(raw), ctypes.cast(out, ctypes.c_void_p), level,
                    None, None, None, None, 0)  # compressor 8 = Kraken
        if n <= 0:
            raise RuntimeError("Oodle 压缩失败")
        return out.raw[:n]


# ---------------------------------------------------------------- key
def find_exe():
    for lib in steam_libraries():
        c = os.path.join(lib, r"steamapps\common\PBZ\platform\steam\PBZ.exe")
        if os.path.exists(c):
            return c
    raise RuntimeError("找不到 PBZ.exe（游戏主程序），请确认已安装《影之刃零》")


def derive_key(exe_path, save_buf):
    if os.path.exists(KEY_CACHE):
        k = bytes.fromhex(open(KEY_CACHE).read().strip())
        if _try_open_dir(save_buf, k):
            return k
    with open(exe_path, "rb") as f:
        exe = f.read()
    anchor = "sealed but block is PLAINTEXT".encode("utf-16-le")
    i = exe.find(anchor)
    if i < 0:
        raise RuntimeError("主程序里找不到密钥锚点，可能版本更新了")
    for delta in range(len(anchor), len(anchor) + 8192):
        base = i + delta
        if base + 64 > len(exe):
            break
        m = exe[base:base + 64]
        k = bytes(a ^ b for a, b in zip(m[:32], m[32:]))
        if _try_open_dir(save_buf, k):
            open(KEY_CACHE, "w").write(k.hex())
            return k
    raise RuntimeError("密钥推导失败，可能版本更新了")


def _try_open_dir(save_buf, key):
    if len(save_buf) < 12:
        return False
    size = struct.unpack_from("<I", save_buf, 8)[0]
    b = save_buf[12:12 + size]
    if len(b) < 36:
        return False
    try:
        AESGCM(key).decrypt(b[8:20], b[36:] + b[20:36], None)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- container
class Slot:
    def __init__(self, name, algo, flag, raw_size, comp, oodle=None):
        self.name, self.algo, self.flag, self.raw_size, self.comp = name, algo, flag, raw_size, comp
        self.oodle = oodle
        self.raw = None      # 解压后的 bytes
        self.data = None     # json dict
        self.dirty = False

    def bytes(self):
        if self.raw is None:
            self.raw = self.oodle.decompress(self.comp, self.raw_size)
        return self.raw

    @property
    def json(self):
        if self.data is None:
            self.data = json.loads(self.bytes().decode("utf-8"))
        return self.data

    def mark(self):
        self.dirty = True


class SaveFile:
    def __init__(self, path, oodle):
        self.path, self.oodle = path, oodle
        self.buf = open(path, "rb").read()
        if self.buf[:4] != b"PBZS":
            raise RuntimeError("不是 PBZS 存档")
        self.ver, self.flags = self.buf[4], self.buf[5]
        self.key = derive_key(find_exe(), self.buf)
        self.slots = []
        self._read()

    def _read(self):
        buf = self.buf
        count = struct.unpack_from("<H", buf, 6)[0]
        dsize = struct.unpack_from("<I", buf, 8)[0]
        self.header = buf[:12]
        db = buf[12:12 + dsize]
        dpt = AESGCM(self.key).decrypt(db[8:20], db[36:] + db[20:36], None)
        self.dir_prefix = dpt[:12]
        p = 12
        recs = []
        for _ in range(count):
            nlen = struct.unpack_from("<H", dpt, p)[0]; p += 2
            name = dpt[p:p + nlen].decode("utf-8"); p += nlen
            algo, flag = dpt[p], dpt[p + 1]; p += 2
            raw_size, off, bsize = struct.unpack_from("<III", dpt, p); p += 12
            recs.append((name, algo, flag, raw_size, off, bsize))
        self.tail = dpt[p:]
        base = 12 + dsize
        for name, algo, flag, raw_size, off, bsize in recs:
            blk = buf[base + off: base + off + bsize]
            pt = AESGCM(self.key).decrypt(blk[8:20], blk[36:] + blk[20:36], None)
            sl = Slot(name, algo, flag, raw_size, pt, self.oodle)
            sl.blk = blk          # 原封不动的整块密文，未修改时原样复用
            self.slots.append(sl)

    def get(self, name):
        for s in self.slots:
            if s.name == name:
                return s
        return None

    # ---- write
    def save(self, out_path):
        blocks, recs, off = [], [], 0
        for s in self.slots:
            if s.dirty:
                raw = json.dumps(s.json, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                s.raw = raw
            else:
                raw = s.bytes()
            if s.dirty or not getattr(s, "blk", None):
                comp = self.oodle.compress(raw) if s.dirty else s.comp
                nonce = os.urandom(12)
                ct = AESGCM(self.key).encrypt(nonce, comp, None)
                body, tag = ct[:-16], ct[-16:]
                blk = b"PBZS" + bytes([1, 1, 0, 0]) + nonce + tag + body
            else:
                blk = s.blk
            recs.append((s.name, s.algo, s.flag, len(raw), off, len(blk)))
            blocks.append(blk)
            off += len(blk)
        dpt = bytearray(self.dir_prefix)
        for name, algo, flag, raw_size, o, bsize in recs:
            nb = name.encode("utf-8")
            dpt += struct.pack("<H", len(nb)) + nb + bytes([algo, flag]) + struct.pack("<III", raw_size, o, bsize)
        dpt += self.tail
        nonce = os.urandom(12)
        ct = AESGCM(self.key).encrypt(nonce, bytes(dpt), None)
        dblk = b"PBZS" + bytes([1, 1, 0, 0]) + nonce + ct[-16:] + ct[:-16]
        header = self.header[:6] + struct.pack("<H", len(recs)) + struct.pack("<I", len(dblk))
        data = header + dblk + b"".join(blocks)
        tmp = out_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        SaveFile(tmp, self.oodle)  # 完整性校验
        os.replace(tmp, out_path)

    def backup(self):
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = f"{self.path}.bak-{ts}"
        shutil.copy2(self.path, dst)
        return dst


def find_sav():
    hits = []
    for r in steam_libraries():
        base = os.path.join(r, r"steamapps\common\PBZ\ProjectPBZ\Saved\SaveGames\Steam")
        if os.path.isdir(base):
            for d in os.listdir(base):
                p = os.path.join(base, d, "PBZSaveGame.sav")
                if os.path.exists(p):
                    hits.append(p)
    if hits:
        return max(hits, key=lambda x: os.path.getmtime(x))
    raise RuntimeError("没找到 PBZSaveGame.sav，请先运行一次游戏，或用 --sav 指定")


def ensure_game_closed():
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq PBZ.exe"], capture_output=True)
        out = (r.stdout or b"").decode("utf-8", "ignore") + (r.stdout or b"").decode("gbk", "ignore")
        if "PBZ.exe" in out or "pbz.exe" in out.lower():
            sys.exit("检测到游戏正在运行，请先完全退出游戏（以及 Steam）再操作。")
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------- helpers
def fmt_ms(ms):
    if not ms:
        return "-"
    ms = int(ms)
    return f"{ms // 60000}:{ms % 60000 / 1000:06.3f}"


def parse_ms(t):
    if t is None:
        return None
    t = str(t).strip()
    if ":" in t:
        m, s = t.split(":")
        return int((int(m) * 60 + float(s)) * 1000)
    v = float(t)
    return int(v * 1000) if v < 10000 else int(v)


def boss_name(key):
    """读 boss_names.json（与脚本/exe 同目录），没有就退回内部键。"""
    global _BN
    try:
        _BN
    except NameError:
        _BN = None
    if _BN is None:
        p = os.path.join(APP_DIR, "boss_names.json")
        try:
            _BN = json.load(open(p, encoding="utf-8"))
        except Exception:
            _BN = {}
    n = _BN.get(key) or {}
    return n.get("cn") or key


def boss_keys(slot):
    return (slot.json.get("BossRecord") or {})


def rec_key(cycle, diff):
    return f"{cycle}_{diff}"


def slot_diff(slot):
    return (slot.json.get("PBZGameSystem") or {}).get("DifficultModeByte")


def pick_slot(sf, name):
    if name:
        s = sf.get(name)
        if not s:
            sys.exit(f"没有槽位 {name}，可用：{', '.join(x.name for x in sf.slots)}")
        return s
    adv = sf.get("__Advanced__")
    last = None
    if adv:
        last = adv.json.get("LastContinueSlot")
    if last and sf.get(last):
        return sf.get(last)
    autos = [s for s in sf.slots if s.name.startswith(("AutoSave", "ManualSave"))]
    return autos[-1] if autos else sf.slots[0]


# ---------------------------------------------------------------- commands
def cmd_info(sf, args):
    print(f"存档: {sf.path}")
    print(f"容器: PBZS v{sf.ver}  槽位 {len(sf.slots)}  大小 {len(sf.buf):,} 字节\n")
    hdr = f"{'槽位':<14}{'难度':<20}{'周目':<5}{'游戏时长':<11}{'死亡':<6}{'余天':<5}{'存档时间':<10}"
    print(hdr); print("-" * len(hdr) * 1)
    for s in sf.slots:
        if s.name.startswith("__"):
            continue
        g = s.json.get("PBZGameSystem") or {}
        d = g.get("DifficultModeByte")
        gt = g.get("HasGameTime") or 0
        print(f"{s.name:<14}{DIFF_NAMES.get(d, d)!s:<20}{g.get('CycleCount', 0):<6}"
              f"{gt // 3600}h{gt % 3600 // 60:02d}m{'':<5}{(s.json.get('PBZDeathMeta') or {}).get('DiedTimes', '-'):<6}"
              f"{(s.json.get('Player') or {}).get('RemainingDays', '-'):<5}{g.get('DateTimeString', '')[-8:]:<10}")
    adv = sf.get("__Advanced__")
    if adv:
        a = adv.json
        print(f"\n全局(__Advanced__): 上次继续={a.get('LastContinueSlot')}  "
              f"已解锁成就={len((sf.get('__Achievements__').json.get('UnlockedIds') if sf.get('__Achievements__') else []) or [])}")

    slot = pick_slot(sf, args.slot)
    print(f"\n=== {slot.name} 的 Boss 战报（难度 {DIFF_NAMES.get(slot_diff(slot))}）===")
    print(f"{'Boss':<38}{'难度':<12}{'挑战':<6}{'击杀':<6}{'身陨':<6}{'最佳用时':<10}")
    allrecs = []
    for bname, v in boss_keys(slot).items():
        for k, r in (v.get("Records") or {}).items():
            try:
                cycle, diff = k.split("_")
            except ValueError:
                cycle, diff = "0", k
            att, kil = r.get("AttemptCount", 0), r.get("KillCount", 0)
            allrecs.append((bname, int(diff), att, kil, r.get("BestTimeMs")))
    for bname, diff, att, kil, bt in sorted(allrecs, key=lambda x: (-x[1], x[0])):
        label = boss_name(bname)
        print(f"{label:<38}{DIFF_NAMES.get(diff, diff)!s:<12}{att:<6}{kil:<6}{max(att - kil, 0):<6}{fmt_ms(bt):<10}")
    print(f"\n合计 {len(allrecs)} 条记录；当前档死亡总数 = {(slot.json.get('PBZDeathMeta') or {}).get('DiedTimes')}")

    if adv:
        ar = adv.json.get("BossRecord") or {}
        n = sum(len(v.get("Records") or {}) for v in ar.values())
        print(f"全局生涯战报(__Advanced__.BossRecord)：{len(ar)} 个 Boss / {n} 条记录")


def cmd_export(sf, args):
    os.makedirs(args.out, exist_ok=True)
    for s in sf.slots:
        p = os.path.join(args.out, s.name.replace("/", "_") + ".json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(s.json, f, ensure_ascii=False, indent=1)
    print(f"已导出 {len(sf.slots)} 个槽位到 {args.out}")


def cmd_apply(sf, args):
    n = 0
    for s in sf.slots:
        p = os.path.join(args.input, s.name.replace("/", "_") + ".json")
        if os.path.exists(p):
            s.data = json.load(open(p, encoding="utf-8"))
            s.dirty = True
            n += 1
    if not n:
        sys.exit("目录里没有可用的槽位 JSON")
    ensure_game_closed()
    bak = sf.backup()
    sf.save(sf.path)
    print(f"已回写 {n} 个槽位；原档备份：{bak}")


def _write(sf, changed, dry):
    if not changed:
        print("没有需要修改的内容。")
        return
    if dry:
        print("(dry-run，不写盘)")
        return
    ensure_game_closed()
    bak = sf.backup()
    sf.save(sf.path)
    print(f"完成。原档已备份：{bak}")


def cmd_set_difficulty(sf, args):
    target = args.slots
    n = 0
    for s in sf.slots:
        if s.name.startswith("__") or "PBZGameSystem" not in s.json:
            continue
        if target != "all" and s.name not in target:
            continue
        old = s.json["PBZGameSystem"].get("DifficultModeByte")
        s.json["PBZGameSystem"]["DifficultModeByte"] = args.value
        s.mark(); n += 1
        print(f"{s.name}: {DIFF_NAMES.get(old, old)} -> {DIFF_NAMES.get(args.value, args.value)}")
    _write(sf, n, args.dry_run)


def cmd_set_boss(sf, args):
    want = args.match.lower()
    touched = 0
    for s in sf.slots:
        if args.slots != "all" and s.name not in args.slots:
            continue
        recs = s.json.get("BossRecord") or {}
        for bname, v in recs.items():
            if want != "all" and want not in bname.lower():
                continue
            for k, r in list((v.get("Records") or {}).items()):
                diff = k.split("_")[-1] if "_" in k else k
                if args.diff is not None and diff != str(args.diff):
                    continue
                old = dict(r)
                if args.attempts is not None:
                    r["AttemptCount"] = args.attempts
                if args.kills is not None:
                    r["KillCount"] = args.kills
                if args.deaths is not None:
                    r["AttemptCount"] = r.get("KillCount", 0) + args.deaths
                if args.time is not None:
                    r["BestTimeMs"] = parse_ms(args.time)
                if r != old:
                    s.mark(); touched += 1
                    print(f"{s.name} / {bname} / {k}: {old} -> {r}")
    _write(sf, touched, args.dry_run)


def cmd_set_total_deaths(sf, args):
    n = 0
    for s in sf.slots:
        dm = s.json.get("PBZDeathMeta")
        if not dm:
            continue
        dm["DiedTimes"] = args.value
        g = s.json.get("PBZGameSystem") or {}
        if args.reset_cycle and "CycleStartDiedTimes" in g:
            g["CycleStartDiedTimes"] = args.value
        s.mark(); n += 1
    _write(sf, n, args.dry_run)


def cmd_move_records(sf, args):
    n = 0
    for s in sf.slots:
        recs = s.json.get("BossRecord") or {}
        for bname, v in recs.items():
            rs = v.get("Records") or {}
            for k in list(rs.keys()):
                if k.endswith(f"_{args.src}"):
                    nk = k[: -len(str(args.src))] + str(args.dst)
                    rs[nk] = rs.pop(k); n += 1
                    print(f"{s.name} / {bname}: {k} -> {nk}")
        if n:
            s.mark()
    _write(sf, n, args.dry_run)


def main():
    ap = argparse.ArgumentParser(description="影之刃零 存档编辑器 (PBZS v3)")
    ap.add_argument("--sav", default=None)
    ap.add_argument("--oodle", default=None)
    ap.add_argument("--dry-run", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info"); p.add_argument("--slot", default=None)
    p = sub.add_parser("export"); p.add_argument("--out", required=True)
    p = sub.add_parser("apply"); p.add_argument("--in", dest="input", required=True)

    p = sub.add_parser("set-difficulty")
    p.add_argument("value", type=int, choices=[0, 1, 2, 3, 4])
    p.add_argument("--slots", default="all", nargs="+")

    p = sub.add_parser("set-boss")
    p.add_argument("match", help="Boss 名包含的字符串，或 all")
    p.add_argument("--diff", type=int, default=None, help="只改该难度的记录 (1/2/3)")
    p.add_argument("--attempts", type=int)
    p.add_argument("--kills", type=int)
    p.add_argument("--deaths", type=int, help="身陨次数 = 挑战次数 - 击杀次数")
    p.add_argument("--time", help="最佳用时，如 1:35.200 或毫秒")
    p.add_argument("--slots", default="all", nargs="+")

    p = sub.add_parser("set-total-deaths")
    p.add_argument("value", type=int)
    p.add_argument("--reset-cycle", action="store_true")

    p = sub.add_parser("move-records")
    p.add_argument("--from", dest="src", type=int, required=True)
    p.add_argument("--to", dest="dst", type=int, required=True)

    args = ap.parse_args()
    oo = Oodle(args.oodle)
    sf = SaveFile(args.sav or find_sav(), oo)
    {"info": cmd_info, "export": cmd_export, "apply": cmd_apply,
     "set-difficulty": cmd_set_difficulty, "set-boss": cmd_set_boss,
     "set-total-deaths": cmd_set_total_deaths, "move-records": cmd_move_records}[args.cmd](sf, args)


if __name__ == "__main__":
    main()
