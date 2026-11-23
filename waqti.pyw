# -*- coding: utf-8 -*-
"""
وقتي - برنامج تنظيم وقت اللعب على كمبيوتر البيت (ويندوز)

- كل واحد له وقت باليوم.
- إذا خلص الوقت: مهلة (افتراضياً 5 دقايق) وبعدها ينقفل الجهاز.
- وقت إضافي: إما بموافقة الكبير (رمز)، أو بخيار "ما أحد صاحي"
  وهذا ينكتب في السجل ويوصل رسالة للكل (تيليجرام).
- السجل ظاهر للكل على شاشة القفل.
"""
import copy
import contextlib
import datetime
import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.request

IS_WIN = sys.platform.startswith("win")

# ---- التحديث التلقائي: كل دقيقة يشيك على النسخة اللي في الرابط، وإذا أحدث يتحدث ويعيد تشغيل نفسه
VERSION = 53
CHANGES = ("لو أحد سكّر البرنامج بدون رمز الكبير (مثلاً من مدير المهام): يرجع يشتغل بروحه خلال دقيقة،\n"
           "ويرن جوال الكبير «⚠️ البرنامج انقفل».")
MSGS_URL = "https://nawafz2011-blip.github.io/waqti/msgs.html"
PAGES_URL = "https://nawafz2011-blip.github.io/waqti/"
UPDATE_URL = "https://raw.githubusercontent.com/nawafz2011-blip/waqti/ccr-8b9f1bcf-7vtkhv/waqti.pyw"
UPDATE_EVERY_SEC = 60

try:
    USER = getpass.getuser().strip().lower()
except Exception:
    USER = "user"

# مجلد مشترك بين كل حسابات ويندوز (C:\ProgramData\Waqti)
OLD_DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Waqti")
if os.environ.get("WAQTI_DATA"):
    DATA_DIR = os.environ["WAQTI_DATA"]
elif IS_WIN:
    DATA_DIR = os.path.join(os.environ.get("ProgramData") or r"C:\ProgramData", "Waqti")
else:
    DATA_DIR = OLD_DATA_DIR
_new_dir = not os.path.isdir(DATA_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
if IS_WIN and (_new_dir or not os.path.exists(os.path.join(DATA_DIR, ".shared"))):
    try:   # نخلي كل الحسابات تقدر تكتب في المجلد
        subprocess.run(["icacls", DATA_DIR, "/grant", "*S-1-5-32-545:(OI)(CI)M", "/T", "/Q"],
                       creationflags=0x08000000, timeout=30, capture_output=True)
        open(os.path.join(DATA_DIR, ".shared"), "w").close()
    except Exception:
        pass
if os.path.normcase(DATA_DIR) != os.path.normcase(OLD_DATA_DIR) and \
        not os.path.exists(os.path.join(DATA_DIR, "config.json")):
    for _f in ("config.json", "state.json", "log.jsonl"):   # ننقل البيانات القديمة
        try:
            shutil.copyfile(os.path.join(OLD_DATA_DIR, _f), os.path.join(DATA_DIR, _f))
        except Exception:
            pass
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
LOG_PATH = os.path.join(DATA_DIR, "log.jsonl")

FONT = "Tahoma" if IS_WIN else "DejaVu Sans"
BG = "#0f172a"
CARD = "#1e293b"
FG = "#f1f5f9"
MUTED = "#94a3b8"
ACCENT = "#2563eb"
GOOD = "#16a34a"
WARN = "#d97706"
BAD = "#dc2626"
DIM = "#475569"

KEEP = "(بدون تغيير)"
TEXT_KEYS = ("telegram_token", "telegram_chat_id", "free_accounts")


def hash_pin(pin):
    pin = pin.strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"))
    return hashlib.sha256(("waqti:" + pin).encode("utf-8")).hexdigest()


DEFAULT_CONFIG = {
    "admin_pin": hash_pin("1234"),
    "players": [
        {"name": "اللاعب 1", "daily_minutes": 120, "pin": ""},
        {"name": "اللاعب 2", "daily_minutes": 120, "pin": ""},
    ],
    "grace_minutes": 5,            # المهلة بعد ما يخلص الوقت
    "warn_minutes": 10,            # تنبيه قبل نهاية الوقت
    "nobody_awake_minutes": 30,    # كم دقيقة يعطي خيار "ما أحد صاحي"
    "nobody_awake_per_day": 1,     # كم مرة باليوم لكل لاعب
    "day_start_hour": 4,           # اليوم الجديد يبدأ الساعة 4 الفجر
    "last_wait_hours": 2,          # آخر واحد لعب ينتظر كم ساعة قبل ما يبدأ دور جديد
    "free_accounts": "",          # حسابات ويندوز بدون حد، مفصولة بفاصلة
    "ntfy_topic": "",             # اسم صفحة الإشعارات على ntfy.sh (ينصنع تلقائياً)
    "telegram_token": "",
    "telegram_chat_id": "",
    "autostart": False,
}


# ---------------------------------------------------------------- storage

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return copy.deepcopy(default)


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def new_topic():
    return "waqti-" + secrets.token_hex(5)


def ntfy_url(cfg):
    return "https://ntfy.sh/" + cfg.get("ntfy_topic", "")


# ---- ختم الرسائل (Ed25519): البرنامج يختم، وموقع الرسائل يعرض المختوم بس
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_BY = 4 * pow(5, _P - 2, _P) % _P


def _xrecover(y):
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * pow(2, (_P - 1) // 4, _P) % _P
    return _P - x if x % 2 else x


_BX = _xrecover(_BY)
_BASE = (_BX, _BY, 1, _BX * _BY % _P)


def _padd(a, b):
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    A = (y1 - x1) * (y2 - x2) % _P
    B = (y1 + x1) * (y2 + x2) % _P
    C = t1 * 2 * _D * t2 % _P
    Dd = z1 * 2 * z2 % _P
    E, F, G, H = B - A, Dd - C, Dd + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _pmul(k, pt):
    q = (0, 1, 1, 0)
    while k:
        if k & 1:
            q = _padd(q, pt)
        pt = _padd(pt, pt)
        k >>= 1
    return q


def _penc(pt):
    x, y, z, _ = pt
    zi = pow(z, _P - 2, _P)
    x, y = x * zi % _P, y * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def ed25519_keys(seed):
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a = (a & ((1 << 254) - 8)) | (1 << 254)
    return a, h[32:], _penc(_pmul(a, _BASE))


def ed25519_sign(seed, msg):
    a, prefix, pub = ed25519_keys(seed)
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), "little") % _L
    R = _penc(_pmul(r, _BASE))
    k = int.from_bytes(hashlib.sha512(R + pub + msg).digest(), "little") % _L
    return R + ((r + k * a) % _L).to_bytes(32, "little")


def msgs_link(cfg):
    pub = ed25519_keys(bytes.fromhex(cfg["sign_key"]))[2].hex()
    return f"{MSGS_URL}?t={cfg['ntfy_topic']}&k={pub}"


NET = {"ok_at": 0.0, "err_at": 0.0, "err": "", "pause_until": 0.0}


def net_ok():
    NET["ok_at"] = time.time()


def net_err(ex):
    NET["err_at"] = time.time()
    NET["err"] = f"{type(ex).__name__}: {ex}"[:90]
    if "429" in str(ex):                      # ntfy يقول «كثير»: نوقف الإضافي نص ساعة
        NET["pause_until"] = time.time() + 1800


FB_URL = "https://waqti-47a62-default-rtdb.europe-west1.firebasedatabase.app"


def fb_key(topic):
    """اسم المكان في قاعدة Firebase (القاعدة تقبل الأسماء الطويلة بس، عشان ما ينخمن)"""
    return topic if len(topic) >= 20 else topic + "-pub-feed"


def fb_path(topic):
    """القنوات القديمة لـ ntfy ← مكانها في Firebase. ردود الأوامر كلها تحت مكان واحد"""
    if "-res-" in topic:
        base, rid = topic.split("-res-", 1)
        return f"r/{fb_key(base + '-res')}/{rid}"
    return "r/" + fb_key(topic)


def fb_req(method, path, data=None, timeout=15):
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{FB_URL}/{path}.json", data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        txt = r.read().decode("utf-8", "ignore")
    return json.loads(txt) if txt else None


def fb_entry(text, title="", click="", prio=3):
    e = {"m": text, "t": {".sv": "timestamp"}}
    if title:
        e["ti"] = title
    if click:
        e["c"] = click
    if prio != 3:
        e["p"] = prio
    return e


def ntfy_send_sync(cfg, text):
    """الرسالة تروح لقاعدتنا (Firebase) مختومة، وموقعنا على GitHub يعرضها"""
    if not cfg.get("ntfy_topic"):
        return False, "ما فيه رابط"
    try:
        ts = int(time.time())
        sig = ed25519_sign(bytes.fromhex(cfg["sign_key"]), f"{ts}|{text}".encode("utf-8")).hex()
        fb_req("POST", fb_path(cfg["ntfy_topic"]), fb_entry(text, "وقتي", f"{msgs_link(cfg)}#s={sig}&ts={ts}"))
        net_ok()
        return True, ""
    except Exception as ex:
        net_err(ex)
        return False, str(ex)


@contextlib.contextmanager
def file_lock(name="state.lock"):
    path = os.path.join(DATA_DIR, name)
    fd = None
    for _ in range(100):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) > 10:
                    os.remove(path)
            except Exception:
                pass
            time.sleep(0.05)
        except Exception:
            break
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
            try:
                os.remove(path)
            except Exception:
                pass


def session_active():
    """هل هذا الحساب هو اللي قدام الشاشة الحين؟ (مو مقفل ومو حساب ثاني)"""
    if not IS_WIN:
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        sid = ctypes.c_ulong()
        k.ProcessIdToSessionId(k.GetCurrentProcessId(), ctypes.byref(sid))
        if k.WTSGetActiveConsoleSessionId() != sid.value:
            return False
        u = ctypes.windll.user32
        h = u.OpenInputDesktop(0, False, 0x0100)
        if not h:
            return False
        u.CloseDesktop(h)
        return True
    except Exception:
        return True


def ntfy_admin_send(cfg, title, text, click=None, topic=None):
    """رسالة للمنظم بس (مكان خاص فيه). ردود الأوامر تنكتب في مكانها (-res-<id>)"""
    def run():
        try:
            t = topic or cfg["admin_topic"]
            e = fb_entry(text, title, click or "", 4)
            fb_req("PUT" if "-res-" in t else "POST", fb_path(t), e)
            net_ok()
        except Exception as ex:
            net_err(ex)
    threading.Thread(target=run, daemon=True).start()


def ntfy_publish_raw(topic, body):
    """قيمة وحدة تنكتب فوق القديمة (حالة اللاعبين، الأسماء)"""
    def run():
        try:
            fb_req("PUT", fb_path(topic), fb_entry(body))
            net_ok()
        except Exception as ex:
            net_err(ex)
    threading.Thread(target=run, daemon=True).start()


def fb_items(data):
    """[(id, الرسالة, الوقت بالثواني)] من قائمة Firebase"""
    out = []
    for k, v in (data or {}).items() if isinstance(data, dict) else []:
        if isinstance(v, dict) and "m" in v:
            out.append((k, v.get("m", ""), float(v.get("t", 0)) / 1000))
    return sorted(out, key=lambda x: x[2])


def ntfy_poll(topic, since):
    """يرجع الرسائل اللي وصلت للقناة من وقت معين"""
    try:
        return [m for _, m, t in fb_items(fb_req("GET", fb_path(topic))) if t >= since - 5]
    except Exception:
        return []


def fb_cleanup(cfg):
    """نمسح القديم عشان القاعدة تبقى صغيرة: الردود بعد يوم، والأوامر والرسائل بعد 8 أيام"""
    now = time.time()
    for topic, keep in ((cfg["admin_topic"] + "-ok", 8 * 86400), (cfg["admin_topic"] + "-res-x", 86400),
                        (cfg["admin_topic"], 8 * 86400), (cfg.get("ntfy_topic", ""), 8 * 86400)):
        if not topic:
            continue
        path = fb_path(topic).rsplit("/", 1)[0] if "-res-" in topic else fb_path(topic)
        try:
            old = {k: None for k, _, t in fb_items(fb_req("GET", path)) if t < now - keep}
            if old:
                fb_req("PATCH", path, old)
        except Exception:
            pass


def _png(w, h, rgb):
    """صورة PNG بدون مكتبات إضافية"""
    import zlib
    import struct
    row = w * 3
    raw = b"".join(b"\x00" + bytes(rgb[y * row:(y + 1) * row]) for y in range(h))

    def chunk(kind, data):
        c = kind + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def screen_shot(width=720):
    """صورة صغيرة للشاشة (للبث للكبير). ترجع رابط data: أو None"""
    import base64
    import io
    try:
        if IS_WIN:
            import ctypes
            from ctypes import wintypes
            u, g = ctypes.windll.user32, ctypes.windll.gdi32
            try:
                u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))    # الشاشة كاملة بالحجم الحقيقي
            except Exception:
                pass
            sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
            w = min(width, sw)
            h = max(1, sh * w // sw)
            src = u.GetDC(0)
            dc = g.CreateCompatibleDC(src)
            bmp = g.CreateCompatibleBitmap(src, w, h)
            g.SelectObject(dc, bmp)
            g.SetStretchBltMode(dc, 4)                                  # HALFTONE: تصغير ناعم
            g.StretchBlt(dc, 0, 0, w, h, src, 0, 0, sw, sh, 0x00CC0020)

            class BIH(ctypes.Structure):
                _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                            ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                            ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
            bi = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            buf = ctypes.create_string_buffer(w * h * 4)
            g.GetDIBits(dc, bmp, 0, h, buf, ctypes.byref(bi), 0)
            g.DeleteObject(bmp)
            g.DeleteDC(dc)
            u.ReleaseDC(0, src)
            bgra = buf.raw
            try:
                from PIL import Image
                im = Image.frombuffer("RGB", (w, h), bgra, "raw", "BGRX", 0, 1)
                out = io.BytesIO()
                im.save(out, "JPEG", quality=55)
                return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()
            except Exception:
                rgb = bytearray(w * h * 3)
                rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
                return "data:image/png;base64," + base64.b64encode(_png(w, h, rgb)).decode()
        from PIL import ImageGrab                                        # غير ويندوز (للتجربة)
        im = ImageGrab.grab().convert("RGB")
        im = im.resize((width, max(1, im.height * width // im.width)))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=55)
        return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()
    except Exception:
        return None


def load_config():
    cfg = load_json(CONFIG_PATH, DEFAULT_CONFIG)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, copy.deepcopy(v))
    if not cfg.get("ntfy_topic"):
        cfg["ntfy_topic"] = new_topic()
    if not cfg.get("sign_key"):
        cfg["sign_key"] = secrets.token_hex(32)
    if not cfg.get("admin_topic"):
        cfg["admin_topic"] = "waqti-admin-" + secrets.token_hex(6)
    save_json(CONFIG_PATH, cfg)
    return cfg


def read_log(limit=200):
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
    except Exception:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return list(reversed(out))  # newest first


def fmt(sec):
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_hours(text):
    """يحول الساعات المكتوبة (2 أو 1.5 أو ١٫٥) إلى دقايق. يرجع None إذا غلط."""
    t = text.strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫،,", "0123456789...")).replace("ساعة", "").replace("ساعات", "").strip()
    try:
        return int(round(float(t) * 60))
    except ValueError:
        return None


def hm_minutes(h, m):
    """ساعات + دقايق من خانتين → دقايق. يرجع None إذا غلط"""
    tr = str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789.")
    h, m = (h or "0").strip().translate(tr) or "0", (m or "0").strip().translate(tr) or "0"
    try:
        return int(round(float(h) * 60 + float(m)))
    except ValueError:
        return None


def hours_num(minutes):
    h = minutes / 60
    return str(int(h)) if h == int(h) else f"{h:.2f}".rstrip("0")


def dur(minutes):
    """مدة بالكلام: ساعة، ساعتين، 3 ساعات، نص ساعة، ساعة و 15 دقيقة"""
    m = abs(int(minutes))
    if m == 0:
        return "أقل من دقيقة"
    h, r = divmod(m, 60)
    hs = {0: "", 1: "ساعة", 2: "ساعتين"}.get(h, f"{h} ساعات" if h <= 10 else f"{h} ساعة")
    if r == 0:
        return hs
    if not h:
        return {30: "نص ساعة", 15: "ربع ساعة"}.get(r, f"{r} دقيقة")
    return f"{hs} و{'نص' if r == 30 else f' {r} دقيقة'}"


def h12(dt):
    """6:32 م بدل 18:32"""
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'ص' if dt.hour < 12 else 'م'}"


def now_hm():
    return h12(datetime.datetime.now())


def entry_time(e):
    try:
        d = datetime.datetime.fromisoformat(e["t"])
        return d.strftime("%d/%m") + "  " + h12(d)
    except Exception:
        return ""


# ---------------------------------------------------------------- telegram

def telegram_send_sync(cfg, text):
    token = (cfg.get("telegram_token") or "").strip()
    chat = (cfg.get("telegram_chat_id") or "").strip()
    if not token or not chat:
        return False, "ما فيه إعدادات تيليجرام"
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        with urllib.request.urlopen(url, data=data, timeout=10) as r:
            ok = json.loads(r.read().decode()).get("ok", False)
        return ok, "" if ok else "تيليجرام رفض الرسالة"
    except Exception as ex:
        return False, str(ex)


def notify_all(cfg, text):
    cfg = copy.deepcopy(cfg)

    def run():
        ntfy_send_sync(cfg, text)
        telegram_send_sync(cfg, text)
    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------- windows helpers

def screen_geom(root):
    if IS_WIN:
        try:
            import ctypes
            u = ctypes.windll.user32
            x, y, w, h = (u.GetSystemMetrics(i) for i in (76, 77, 78, 79))
            if w > 0 and h > 0:
                return x, y, w, h
        except Exception:
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def set_autostart(on):
    if not IS_WIN:
        return
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                         r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
    try:
        if on:
            if getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}"'
            else:
                pyw = sys.executable.replace("python.exe", "pythonw.exe")
                cmd = f'"{pyw}" "{os.path.abspath(__file__)}"'
            winreg.SetValueEx(key, "Waqti", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, "Waqti")
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)


def p_accounts(p):
    return [a.strip().lower() for a in str(p.get("windows_user") or "").replace("،", ",").split(",") if a.strip()]


def windows_accounts():
    """أسماء حسابات ويندوز اللي في الجهاز"""
    names = []
    if IS_WIN:
        try:
            import winreg
            base = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
            i = 0
            while True:
                try:
                    sid = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                if not sid.startswith("S-1-5-21-"):
                    continue
                try:
                    sk = winreg.OpenKey(k, sid)
                    path = winreg.QueryValueEx(sk, "ProfileImagePath")[0]
                    n = os.path.basename(os.path.expandvars(path)).strip().lower()
                    if n and not n.startswith("defaultuser") and n not in (
                            "wsiaccount", "wdagutilityaccount", "defaultaccount", "guest"):
                        names.append(n)
                except OSError:
                    pass
        except Exception:
            pass
    if USER not in names:
        names.insert(0, USER)
    return sorted(set(names), key=lambda n: (n != USER, n))


def python_cmd():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pyw}" "{os.path.abspath(__file__)}"'


def set_autostart_all():
    """تشغيل تلقائي لكل الحسابات (يطلب موافقة المسؤول من ويندوز)"""
    import ctypes
    cmd = python_cmd().replace('"', '\\"')
    params = f'add "HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v Waqti /t REG_SZ /d "{cmd}" /f'
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", "reg.exe", params, None, 0)
    return r > 32


def set_watchdog():
    """مهمة ويندوز كل دقيقة: لو أحد سكّر البرنامج (مدير المهام)، يرجع يشتغل خلال دقيقة.
    ما يرجع إذا الكبير سكّره بالرمز. لو البرنامج شغال، النسخة الجديدة تطلع على طول"""
    if not IS_WIN or getattr(sys, "frozen", False):
        return False
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    tr = f'"{pyw}" "{INSTALL_PATH}" --watchdog'
    try:
        r = subprocess.run(["schtasks", "/create", "/f", "/sc", "minute", "/mo", "1",
                            "/tn", f"Waqti watchdog ({USER})", "/tr", tr],
                           capture_output=True, creationflags=0x08000000, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def start_key_hook(callback, vk=0x70):
    """نسمع زر F1 في ويندوز كله (حتى داخل اللعبة) بدون ما نمنعه عن اللعبة"""
    if not IS_WIN:
        return
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32

    class KBD(ctypes.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
    LRESULT = ctypes.c_ssize_t
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    def proc(code, wparam, lparam):
        try:
            if code == 0 and wparam in (0x0100, 0x0104):          # WM_KEYDOWN / WM_SYSKEYDOWN
                if ctypes.cast(lparam, ctypes.POINTER(KBD)).contents.vkCode == vk:
                    callback()
        except Exception:
            pass
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def run():
        try:
            run.proc = HOOKPROC(proc)                                # نخليه حي
            user32.SetWindowsHookExW(13, run.proc, kernel32.GetModuleHandleW(None), 0)   # WH_KEYBOARD_LL
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


def beep():
    if IS_WIN:
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass


def btn(parent, text, cmd, bg=ACCENT, size=12, width=None, fg=FG):
    b = tk.Button(parent, text=text, command=cmd, font=(FONT, size, "bold"), bg=bg, fg=fg,
                  activebackground=bg, activeforeground=fg, relief="flat", bd=0,
                  padx=14, pady=8, cursor="hand2")
    if width:
        b.configure(width=width)
    return b


# ---------------------------------------------------------------- app

class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("وقتي")
        self.cfg = load_config()
        self.state = {}
        self.base = {}
        self.free = False
        self.sync_state()
        self.dialogs = []
        self.active = None
        self.grace_left = None
        self.warned = False
        self.last = time.monotonic()
        self.save_tick = 0

        self.ensure_today()
        resume = self.state.pop("resume", None)
        if resume and self.state.get("active") == resume.get("name") and time.time() - resume.get("at", 0) < 120:
            self.log("updated", f"تحدّث البرنامج للنسخة {VERSION}")
        elif self.state.get("active"):
            name = self.state["active"]
            self.log("abnormal", f"⚠️ البرنامج أو الجهاز انطفى وجلسة {name} شغالة، بدون ما تنتهي بشكل طبيعي",
                     name, notify=True)
            self.state["active"] = None
            self.save_state()
        was_killed = False
        if (self.state.get("running_by") or {}).get(USER) and not resume:
            was_killed = True                   # كان شغال وما انقفل بالرمز ولا طلع من الحساب
        if was_killed:
            self.log("killed", f"⚠️ البرنامج انقفل في حساب {self.linked_player() or USER} بدون رمز الكبير (أو الجهاز انطفى فجأة). رجع اشتغل الحين",
                     self.linked_player(), admin=True)
        self.state_op(lambda st: (st.setdefault("running_by", {}).__setitem__(USER, True),
                                  st.setdefault("closed_by", {}).pop(USER, None)))
        self.log("app_start", "تشغّل البرنامج")
        if IS_WIN:
            threading.Thread(target=set_watchdog, daemon=True).start()
        if IS_WIN and self.cfg.get("autostart"):
            try:
                set_autostart(True)   # نتأكد إن التشغيل التلقائي يأشر على المكان الصحيح
            except Exception:
                pass

        self.save_state()
        self.setup_lock_window()
        self.setup_timer_window()
        me = self.linked_player()
        if self.is_free_account() and not self.linked_players():
            self.show_free()
        else:
            self.show_lock()
        if self.free:
            if resume:
                self.log("updated", f"تحدّث البرنامج للنسخة {VERSION}")
        elif resume and resume.get("name") and self.state.get("active") == resume.get("name"):
            self.continue_session(resume)
        else:
            if resume:
                self.log("updated", f"تحدّث البرنامج للنسخة {VERSION}")
            if me:
                self.maybe_open_round(me)
            if me and self.remaining(me) > 0 and self.can_play(me):
                self.start_session(me)          # دخل حسابه = يبدأ وقته على طول
        self.update_ready = None
        self.cmd_queue = []
        self.cmd_since = time.time() - 60
        self.root.after(1000, self.tick)
        self.root.after(500, self.keep_on_top)
        self.root.after(5000, self.check_update)
        self.start_stream()
        self.root.after(2000, self.publish_roster)    # الإعدادات لجوال الكبير من أول ما يشتغل
        self.root.after(200, self.fast_cmds)
        seen = self.cfg.setdefault("seen_versions", {})
        if seen.get(USER) != VERSION:
            first = USER not in seen
            seen[USER] = VERSION
            self.save_cfg()
            if not first or resume:     # أول تشغيل للبرنامج ما نحتاج رسالة تحديث
                self.root.after(1500, lambda: self.info(f"تحدّث البرنامج ✅  النسخة {VERSION}", CHANGES))

    # ---------- windows accounts
    def linked_players(self):
        return [p["name"] for p in self.cfg["players"]
                if USER in p_accounts(p)]

    def linked_player(self):
        """صاحب الحساب إذا كان شخص واحد بس (الحساب المشترك ما يبدأ تلقائياً)"""
        lp = self.linked_players()
        return lp[0] if len(lp) == 1 and not self.is_free_account() else None

    def is_free_account(self):
        free = [a.strip().lower() for a in str(self.cfg.get("free_accounts", "")).replace("،", ",").split(",")]
        return USER in [a for a in free if a]

    def show_free(self):
        """حساب بدون حد (حساب الكبير): نافذة صغيرة عادية فيها الإعدادات والسجل"""
        self.free = True
        r = self.root
        self.lock_frame.place_forget()
        r.overrideredirect(False)
        r.attributes("-topmost", False)
        r.title("وقتي")
        if not hasattr(self, "free_frame"):
            self.free_frame = tk.Frame(r, bg=BG, padx=20, pady=16)
            tk.Label(self.free_frame, text="⏱ وقتي", font=(FONT, 18, "bold"), bg=BG, fg=FG).pack()
            tk.Label(self.free_frame, text=f"حساب {USER}: بدون حد للوقت  ·  النسخة {VERSION}", font=(FONT, 11), bg=BG,
                     fg=MUTED).pack(pady=(2, 12))
            row = tk.Frame(self.free_frame, bg=BG)
            row.pack()
            btn(row, "السجل", self.open_log, bg=CARD, size=10).pack(side="right", padx=4)
            btn(row, "الإعدادات", self.open_settings, bg=CARD, size=10).pack(side="right", padx=4)
            btn(row, "إغلاق البرنامج", self.exit_app, bg=CARD, size=10).pack(side="right", padx=4)
            self.lock_back_btn = btn(row, "🔒 شاشة القفل", self.back_to_lock, bg=CARD, size=10)
        if self.linked_players():
            self.lock_back_btn.pack(side="right", padx=4)
        else:
            self.lock_back_btn.pack_forget()
        self.free_frame.pack(fill="both", expand=True)
        r.geometry("")
        r.deiconify()
        r.protocol("WM_DELETE_WINDOW", r.iconify)
        r.iconify()

    def elder_enter(self):
        if self.check_admin("حساب الكبير: اكتب رمزك وتستخدم الجهاز بدون حد"):
            self.log("elder", "👑 الكبير دخل حسابه (بدون حد)")
            self.show_free()

    def back_to_lock(self):
        """من وضع الكبير ترجع لشاشة القفل عشان لاعب يختار اسمه"""
        self.free = False
        r = self.root
        self.free_frame.pack_forget()
        r.deiconify()
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.protocol("WM_DELETE_WINDOW", lambda: None)
        self.lock_frame.place(relx=0.5, rely=0.45, anchor="center")
        self.show_lock()

    def logoff(self):
        v, _ = self.ask("تسجيل خروج", "تطلع من حساب ويندوز؟ (احفظ شغلك أول)", [("إيه، اطلع", "y"), ("لا", None)])
        if v and IS_WIN:
            subprocess.Popen(["shutdown", "/l"], creationflags=0x08000000)

    # ---------- auto update
    def check_update(self):
        if self.update_ready:
            self.apply_update()
        elif UPDATE_URL and not getattr(sys, "frozen", False):
            threading.Thread(target=self._fetch_update, daemon=True).start()
        self.root.after(UPDATE_EVERY_SEC * 1000, self.check_update)

    def _fetch_update(self):
        try:
            url = f"{UPDATE_URL}?t={int(time.time())}"
            req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=15) as r:
                code = r.read().decode("utf-8")
            m = re.search(r"^VERSION = (\d+)", code, re.M)
            if not m or int(m.group(1)) <= VERSION:
                return
            compile(code, "waqti", "exec")  # نتأكد إن الكود سليم قبل لا نبدل
            self.update_ready = (int(m.group(1)), code)
        except Exception:
            pass

    def apply_update(self):
        if self.dialogs:              # لا نعيد التشغيل والكبير فاتح الإعدادات
            return
        ver, code = self.update_ready
        path = os.path.abspath(__file__)
        try:
            tmp = path + ".new"
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(code)
            try:
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)  # لو الملف للقراءة بس
            except Exception:
                pass
            os.replace(tmp, path)
        except Exception as ex:
            self.update_ready = None
            if getattr(self, "update_failed_ver", None) != ver:   # نسجل الفشل مرة وحدة بس
                self.update_failed_ver = ver
                self.log("update_fail", f"فشل التحديث: {ex}")
            return
        self.state["resume"] = {"name": self.active, "grace_left": self.grace_left,
                                "warned": self.warned, "at": time.time()}
        self.save_state()
        global _instance_lock
        try:
            _instance_lock.close()
        except Exception:
            pass
        flags = 0x00000008 if IS_WIN else 0  # DETACHED_PROCESS
        subprocess.Popen([sys.executable, path], creationflags=flags, close_fds=True)
        self.root.destroy()

    def continue_session(self, resume):
        self.active = resume["name"]
        self.grace_left = resume.get("grace_left")
        self.warned = bool(resume.get("warned"))
        self.show_timer()

    # ---------- data
    def _disk_state(self):
        st = load_json(STATE_PATH, {})
        st.setdefault("active_by", {})
        st.setdefault("resume_by", {})
        for k in ("active", "resume"):          # من النسخ القديمة
            if k in st:
                if st[k]:
                    st[k + "_by"][USER] = st[k]
                del st[k]
        for k in ("used", "bonus", "nobody", "week"):
            st.setdefault(k, {})
        return st

    def state_op(self, fn):
        """تعديل مباشر على الملف المشترك (للدور والريست)"""
        self.sync_state(op=fn)

    def sync_state(self, op=None):
        """نحفظ تغييراتنا وناخذ تغييرات الحسابات الثانية (كل حساب له نسخة من البرنامج)"""
        with file_lock():
            disk = self._disk_state()
            mine = self.state
            if mine:
                if mine.get("date") and (disk.get("date") or "") < mine["date"]:
                    keep = {k: disk[k] for k in ("round", "reset_fails", "reset_locked", "open", "penalty", "skipped", "blocked", "cmds_done", "week", "week_id", "report_sent", "remote_fails", "remote_lock", "round_announced", "finished_at", "roster_fb", "say_shown", "running_by", "closed_by") if k in disk}
                    disk = {"date": mine["date"], "used": {}, "bonus": {}, "nobody": {},
                            "active_by": disk["active_by"], "resume_by": disk["resume_by"], **keep}
                if disk.get("date") == mine.get("date"):
                    base = self.base if self.base.get("date") == mine.get("date") else {}
                    for k in ("used", "bonus", "nobody", "week"):
                        cur, old = mine.get(k, {}), base.get(k, {})
                        for n in set(cur) | set(old):
                            d = cur.get(n, 0) - old.get(n, 0)
                            if d:
                                disk[k][n] = disk[k].get(n, 0) + d
                                if disk[k][n] == 0 or (k == "used" and n not in cur and abs(disk[k][n]) < 1):
                                    disk[k].pop(n, None)
                disk["active_by"][USER] = mine.get("active")
                if mine.get("resume"):
                    disk["resume_by"][USER] = mine["resume"]
                else:
                    disk["resume_by"].pop(USER, None)
            if op:
                op(disk)
            if mine or disk.get("date") or op:
                save_json(STATE_PATH, disk)
        self.base = copy.deepcopy(disk)
        st = copy.deepcopy(disk)
        st["active"] = st["active_by"].get(USER)
        st["resume"] = st["resume_by"].get(USER)
        self.state = st

    def save_state(self):
        self.sync_state()

    def save_cfg(self):
        save_json(CONFIG_PATH, self.cfg)

    def log(self, kind, text, player=None, notify=False, admin=False):
        e = {"t": datetime.datetime.now().isoformat(timespec="seconds"),
             "kind": kind, "player": player, "text": text}
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except Exception:
            pass
        if notify:
            notify_all(self.cfg, f"⏱ وقتي\n{text}\nالساعة {now_hm()}")
        if admin:
            ntfy_admin_send(self.cfg, "وقتي: تنبيه للمنظم", f"{text}\nالساعة {now_hm()}")

    def today(self):
        h = int(self.cfg.get("day_start_hour", 4))
        return (datetime.datetime.now() - datetime.timedelta(hours=h)).date().isoformat()

    def ensure_today(self):
        t = self.today()
        if self.state.get("date") != t:
            self.state = {"date": t, "used": {}, "bonus": {}, "nobody": {},
                          "week": self.state.get("week", {}), "week_id": self.state.get("week_id"),
                          "active": self.state.get("active"), "resume": self.state.get("resume")}
            self.save_state()
            return True
        for k in ("used", "bonus", "nobody", "week"):
            self.state.setdefault(k, {})
        return False

    def player(self, name):
        for p in self.cfg["players"]:
            if p["name"] == name:
                return p
        return None

    def remaining(self, name):
        p = self.player(name)
        base = int(p["daily_minutes"]) * 60 if p else 0
        return base + self.state["bonus"].get(name, 0) - self.state["used"].get(name, 0)

    # ---------- dialogs
    def ask(self, title, msg, buttons, fields=None):
        d = tk.Toplevel(self.root)
        d.title(title)
        d.configure(bg=CARD, padx=26, pady=22)
        d.attributes("-topmost", True)
        d.resizable(False, False)
        result = {"v": None, "vals": []}
        entries = []

        def close(v):
            result["v"] = v
            result["vals"] = [e.get() for e in entries]
            try:
                d.grab_release()
            except Exception:
                pass
            if d in self.dialogs:
                self.dialogs.remove(d)
            d.destroy()

        d.protocol("WM_DELETE_WINDOW", lambda: close(None))
        tk.Label(d, text=title, font=(FONT, 16, "bold"), bg=CARD, fg=FG).pack(anchor="e")
        if msg:
            tk.Label(d, text=msg, font=(FONT, 12), bg=CARD, fg=MUTED, justify="right",
                     wraplength=440).pack(anchor="e", pady=(6, 12))
        for label, default, secret in fields or []:
            row = tk.Frame(d, bg=CARD)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=label, font=(FONT, 12), bg=CARD, fg=FG).pack(side="right")
            e = tk.Entry(row, font=(FONT, 13), justify="right", width=22, show="•" if secret else "")
            e.insert(0, str(default))
            e.pack(side="left", padx=(0, 12))
            entries.append(e)
        bf = tk.Frame(d, bg=CARD)
        bf.pack(fill="x", pady=(16, 0))
        default_v = None
        for lbl, val in buttons:
            if val is not None and default_v is None:
                default_v = val
            btn(bf, lbl, lambda v=val: close(v), bg=DIM if val is None else ACCENT).pack(side="right", padx=4)
        d.bind("<Return>", lambda e: close(default_v))
        d.bind("<Escape>", lambda e: close(None))

        self.dialogs.append(d)
        d.update_idletasks()
        w, h = d.winfo_reqwidth(), d.winfo_reqheight()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        d.geometry(f"+{(sw - w) // 2}+{(sh - h) // 3}")
        d.lift()
        try:
            d.wait_visibility()
            d.grab_set()
        except Exception:
            pass
        d.focus_force()
        if entries:
            entries[0].focus_set()
            entries[0].select_range(0, "end")
        self.root.wait_window(d)
        return result["v"], result["vals"]

    def info(self, title, msg):
        self.ask(title, msg, [("حسناً", "ok")])

    def check_admin(self, reason="اكتب رمز الكبير"):
        v, vals = self.ask("رمز الكبير", reason, [("دخول", True), ("نسيت الرمز", "forgot"), ("إلغاء", None)],
                           fields=[("الرمز", "", True)])
        if not v:
            return False
        if v == "forgot":
            return self.forgot_pin()
        if hash_pin(vals[0]) == self.cfg["admin_pin"]:
            return True
        self.log("wrong_admin_pin", "⚠️ أحد حاول يدخل برمز الكبير وكان الرمز غلط", self.active, admin=True)
        self.info("خطأ", "الرمز غلط. وانكتبت المحاولة في السجل.")
        return False

    def forgot_pin(self):
        """رمز استرجاع يوصل للمنظم بس (قناة المنظم + رابط المنظم)"""
        code = "".join(secrets.choice("0123456789") for _ in range(6))
        expires = time.time() + 600
        ntfy_admin_send(self.cfg, "وقتي: رمز استرجاع",
                        f"🔑 رمز الاسترجاع: {code}\nصالح 10 دقايق. إذا ما طلبته أنت، أحد يحاول يغير رمزك. لا تعطيه أحد.")
        self.log("pin_recover", "🔑 أحد طلب رمز استرجاع لرمز الكبير", self.active)
        for tries in range(3):
            v, vals = self.ask("نسيت الرمز",
                               "انرسل رمز استرجاع (6 أرقام) لجوال المنظم: قناة المنظم في ntfy أو رابط المنظم.\n"
                               f"اكتبه هنا. باقي {3 - tries} محاولات.",
                               [("تأكيد", True), ("إلغاء", None)], fields=[("رمز الاسترجاع", "", False)])
            if not v:
                return False
            typed = vals[0].strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
            if time.time() > expires:
                self.info("انتهى", "رمز الاسترجاع انتهت صلاحيته. اطلب واحد جديد.")
                return False
            if secrets.compare_digest(typed, code):
                break
        else:
            self.log("pin_recover_fail", "⚠️ أحد كتب رمز استرجاع غلط 3 مرات", self.active, admin=True)
            self.info("غلط", "3 محاولات غلط. اطلب رمز استرجاع جديد.")
            return False
        while True:
            v, vals = self.ask("رمز الكبير الجديد", "اكتب رمز جديد ما أحد يعرفه.", [("حفظ", True), ("إلغاء", None)],
                               fields=[("الرمز الجديد", "", True), ("أعده مرة ثانية", "", True)])
            if not v:
                return False
            if vals[0].strip() and vals[0] == vals[1]:
                break
            self.info("خطأ", "الرمزين مو نفس بعض، أو فاضي.")
        self.cfg["admin_pin"] = hash_pin(vals[0])
        self.save_cfg()
        self.log("pin_changed", "🔑 الكبير غيّر رمزه عن طريق «نسيت الرمز»", admin=True)
        self.info("تم ✅", "تغيّر رمز الكبير.")
        return True

    # ---------- lock screen
    def setup_lock_window(self):
        r = self.root
        r.configure(bg=BG)
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.protocol("WM_DELETE_WINDOW", lambda: None)
        r.protocol("WM_SAVE_YOURSELF", self.on_logoff)   # ويندوز يرسلها لما يطلع من الحساب
        x, y, w, h = screen_geom(r)
        r.geometry(f"{w}x{h}+{x}+{y}")

        lf = self.lock_frame = tk.Frame(r, bg=BG)
        lf.place(relx=0.5, rely=0.45, anchor="center")
        tk.Label(lf, text="⏱  وقتي", font=(FONT, 42, "bold"), bg=BG, fg=FG).pack()
        self.clock_lbl = tk.Label(lf, text="", font=(FONT, 16), bg=BG, fg=MUTED)
        self.clock_lbl.pack(pady=(4, 0))
        tk.Label(lf, text="مين بيلعب الحين؟", font=(FONT, 22, "bold"), bg=BG, fg=FG).pack(pady=(34, 12))
        self.players_frame = tk.Frame(lf, bg=BG)
        self.players_frame.pack()
        self.round_lbl = tk.Label(lf, text="", font=(FONT, 13), bg=BG, fg=MUTED)
        self.round_lbl.pack(pady=(6, 0))

        bottom = tk.Frame(lf, bg=BG)
        bottom.pack(pady=(26, 0))
        btn(bottom, "السجل", self.open_log, bg=CARD).pack(side="right", padx=6)
        btn(bottom, "ريست الوقت", lambda: self.reset_flow(), bg=CARD).pack(side="right", padx=6)
        btn(bottom, "ريست الدور", lambda: self.reset_flow(kind="round"), bg=CARD).pack(side="right", padx=6)
        btn(bottom, "الإعدادات", self.open_settings, bg=CARD).pack(side="right", padx=6)
        btn(bottom, "إغلاق البرنامج", self.exit_app, bg=CARD).pack(side="right", padx=6)
        if IS_WIN:
            btn(bottom, "تسجيل خروج من ويندوز", self.logoff, bg=CARD).pack(side="right", padx=6)
        self.elder_btn = btn(bottom, "👑 أنا الكبير", self.elder_enter, bg=CARD)

        tk.Label(lf, text="آخر الأحداث (الكل يشوفها)", font=(FONT, 13, "bold"), bg=BG,
                 fg=MUTED).pack(pady=(30, 6))
        self.recent_lbl = tk.Label(lf, text="", font=(FONT, 12), bg=BG, fg=MUTED, justify="right")
        self.recent_lbl.pack()
        self.link_lbl = tk.Label(lf, text="", font=(FONT, 11), bg=BG, fg=MUTED)
        self.link_lbl.pack(pady=(18, 0))

    def refresh_lock(self):
        for w in self.players_frame.winfo_children():
            w.destroy()
        cols = 4
        mine = self.linked_players()
        if self.is_free_account():
            self.elder_btn.pack(side="right", padx=6)
        else:
            self.elder_btn.pack_forget()
        shown = [p for p in self.cfg["players"] if p["name"] in mine] \
            if (len(mine) > 1 or (mine and self.is_free_account())) else self.cfg["players"]
        for i, p in enumerate(shown):
            rem = self.remaining(p["name"])
            if p["name"] in (self.state.get("blocked") or []):
                txt, color = f'{p["name"]}\nممنوع (المنظم)', BAD
            elif self.is_open(p["name"]):
                txt, color = f'{p["name"]}\nوقت مفتوح', WARN
            elif p["name"] in self.state.get("skipped", []) and not self.can_play(p["name"]):
                txt, color = f'{p["name"]}\nخسر دوره (عقوبة)', DIM
            elif self.round_complete() and self.can_open_round(p["name"]):
                txt, color = f'{p["name"]}\nيبدأ الدور الجديد ▶', GOOD
            elif self.round_complete():
                at = h12(datetime.datetime.now() + datetime.timedelta(seconds=self.last_wait_left(p["name"])))
                txt, color = f'{p["name"]}\nتوك لعب · بعد {at}', DIM
            elif not self.can_play(p["name"]):
                txt, color = f'{p["name"]}\nخلص وقته ✓', DIM
            elif rem > 0:
                txt, color = f'{p["name"]}\nباقي {fmt(rem)}', ACCENT
            else:
                txt, color = f'{p["name"]}\nخلص وقته', DIM
            b = btn(self.players_frame, txt, lambda n=p["name"]: self.choose_player(n),
                    bg=color, size=16, width=12)
            b.grid(row=i // cols, column=cols - 1 - (i % cols), padx=10, pady=10)
        r = self.state.get("round", [])
        allp = self.turn_players()
        if r and not set(allp) <= set(r):
            left = [n for n in allp if n not in r]
            rt = "خلص وقتهم:  " + "، ".join(f"{n} ✓" for n in r) + "     ·     باقي:  " + "، ".join(left)
        elif self.round_complete():
            rt = f"🏁 الكل خلص وقته · أول واحد يدخل يرجّع الوقت للكل (إلا {self.last_finisher()[0]})"
        else:
            rt = "دور جديد: الكل عنده وقته كامل"
        if getattr(self, "pending_req", None):
            rt += "     ·     📨 بانتظار موافقة المنظم"
        self.round_lbl.configure(text=rt)
        hide = ("app_start", "update_fail", "updated")
        lines = []
        for e in read_log(30):
            if e.get("kind") in hide:
                continue
            txt = e["text"] if len(e["text"]) <= 80 else e["text"][:78] + "…"
            lines.append(f"{txt}   {entry_time(e)}")
            if len(lines) == 6:
                break
        self.recent_lbl.configure(text="\n".join(lines) or "ما فيه أحداث")
        if NET["err_at"] > NET["ok_at"]:
            net = f"❌ الإشعارات ما توصل: {NET['err']}"
        elif NET["ok_at"]:
            net = f"✅ الإشعارات: آخر إرسال {h12(datetime.datetime.fromtimestamp(NET['ok_at']))}"
        else:
            net = "الإشعارات: ما انرسل شي للحين"
        self.link_lbl.configure(text=f"النسخة {VERSION}     ·     {net}")

    def show_lock(self):
        if self.free:
            self.timer.withdraw()
            return
        self.timer.withdraw()
        self.root.deiconify()
        x, y, w, h = screen_geom(self.root)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.attributes("-topmost", True)
        self.refresh_lock()
        self.root.lift()
        self.root.focus_force()

    def choose_player(self, name):
        p = self.player(name)
        if not p:
            return
        self.save_state()                         # آخر الأوقات من الحسابات الثانية
        if p.get("pin"):
            v, vals = self.ask(f"رمز {name}", "اكتب رمزك", [("دخول", True), ("إلغاء", None)],
                               fields=[("الرمز", "", True)])
            if not v:
                return
            if hash_pin(vals[0]) != p["pin"]:
                self.log("wrong_pin", f"⚠️ محاولة دخول على حساب {name} برمز غلط", name, admin=True)
                self.info("خطأ", "الرمز غلط. وانكتبت المحاولة في السجل.")
                return
        if name in (self.state.get("blocked") or []):
            self.info("ممنوع", f"{name}، المنظم طلّعك ومنعك. ما تقدر تدخل لين يسمح لك.")
            return
        self.maybe_open_round(name)
        if self.round_complete() and not self.can_open_round(name) and \
                self.remaining(name) <= 0 and not self.is_open(name):
            left = self.last_wait_left(name)
            at = h12(datetime.datetime.now() + datetime.timedelta(seconds=left))
            v, _ = self.ask("توك لعبت", f"{name}، توك لعبت، ما تقدر تلعب ورا بعض.\n"
                                         f"تقدر تدخل الساعة {at} (بعد {dur(round(left / 60))})، أو لما يبدأ واحد غيرك.\n\n"
                                         "أو اطلب موافقة الكبير:",
                            [("موافقة الكبير", "admin"), ("إلغاء", None)])
            if v and self.check_admin("الكبير يكتب رمزه عشان يوافق على الوقت الإضافي"):
                m, _ = self.ask("كم تعطيه؟", f"كم وقت تعطي {name}؟",
                                [("ربع ساعة", "15"), ("نص ساعة", "30"), ("ساعة", "60"), ("ساعتين", "120"), ("إلغاء", None)])
                if m:
                    self.grant(name, int(m), "admin")
                    if self.remaining(name) > 0:
                        self.start_session(name)
                        return
            self.refresh_lock()
            return
        if not self.can_play(name):
            left = [n for n in self.turn_players() if n not in self.state.get("round", [])]
            if name in self.state.get("skipped", []):
                self.info("مو دورك", f"{name}، خسرت دورك هالمرة (عقوبة كذب).\nباقي: {'، '.join(left)}")
                return
            v, _ = self.ask("خلص وقتك", f"{name}، خلص وقتك هالدور.\nلما يخلص وقت {'، '.join(left)}، الكل يرجع له وقته.",
                            [("وقت إضافي", "extra"), ("إلغاء", None)])
            if v:
                self.extension_flow(name, from_lock=True)
                self.refresh_lock()
            return
        if self.remaining(name) <= 0 and not self.is_open(name):
            self.extension_flow(name, from_lock=True)
            self.refresh_lock()
            return
        self.start_session(name)

    # ---------- turns (الدور)
    def turn_players(self):
        return [p["name"] for p in self.cfg["players"] if int(p.get("daily_minutes", 0)) > 0]

    def round_complete(self):
        allp = set(self.turn_players())
        return bool(allp) and allp <= set(self.state.get("round", []))

    def last_finisher(self):
        """آخر واحد خلص وقته في الدور اللي راح، ومتى خلص"""
        fa = self.state.get("finished_at") or {}
        r = [n for n in self.state.get("round", []) if n not in self.state.get("skipped", [])]
        if not r:
            return None, 0
        n = max(r, key=lambda x: fa.get(x, 0)) if fa else r[-1]
        return n, float(fa.get(n, 0))

    def last_wait_left(self, name):
        """كم ثانية باقي على آخر لاعب عشان يقدر يبدأ دور جديد (0 = يقدر)"""
        last, at = self.last_finisher()
        if name != last or not at:
            return 0
        return max(0, at + float(self.cfg.get("last_wait_hours", 2)) * 3600 - time.time())

    def can_open_round(self, name):
        return self.round_complete() and name in self.turn_players() and \
            name not in (self.state.get("blocked") or []) and self.last_wait_left(name) <= 0

    def can_play(self, name):
        """الدور = الوقت: اللي خلص وقته ينتظر لين يخلص وقت الكل،
        وبعدها أي واحد يدخل يبدأ دور جديد، إلا آخر واحد لعب (لين تمر الساعات أو يبدأ غيره)"""
        if name in (self.state.get("blocked") or []):
            return False
        if self.round_complete():
            return self.can_open_round(name) or self.remaining(name) > 0 or self.is_open(name)
        return name not in self.state.get("round", [])

    def maybe_open_round(self, name):
        """الدور خلص: أي واحد (غير آخر واحد لعب) يبدأ دور جديد والكل يرجع له وقته"""
        if not self.can_open_round(name):
            return
        self.state_op(self._new_round)
        self.log("round", f"🔄 {name} بدأ الدور الجديد: الكل رجع له وقته كامل", notify=True)
        for n in self.state.get("skipped", []):
            self.log("penalty_skip", f"⚖️ {n} خسر دوره هالمرة (عقوبة كذب)", n, notify=True)

    def mark_finished(self, name):
        def op(st):
            r = st.setdefault("round", [])
            if name not in r:
                r.append(name)
            st.setdefault("finished_at", {})[name] = time.time()
        self.state_op(op)

    def unmark_finished(self, name):
        if name in self.state.get("round", []) and name not in self.state.get("skipped", []):
            self.state_op(lambda st: st.setdefault("round", []).remove(name) if name in st.get("round", []) else None)

    def is_open(self, name):
        return name in (self.state.get("open") or {})

    def end_open(self, name):
        """نهاية الوقت المفتوح: الوقت يصير صفر"""
        p = self.player(name)
        base = int(p["daily_minutes"]) * 60 if p else 0
        started = (self.state.get("open") or {}).get(name, time.time())

        def op(st):
            st.setdefault("open", {}).pop(name, None)
            st.setdefault("used", {})[name] = base + st.setdefault("bonus", {}).get(name, 0)
        self.state_op(op)
        self.log("open_end", f"{name} أنهى الوقت المفتوح («ما أحد صاحي») بعد {dur(int((time.time() - started) // 60))}، "
                             "ووقته صار صفر", name, notify=True)

    @staticmethod
    def _new_round(st):
        """دور جديد: الكل يرجع له وقته، واللي عليه عقوبة يخسر دوره"""
        st["round"] = []
        st["used"] = {}
        st["bonus"] = {}
        st["skipped"] = []
        st["finished_at"] = {}
        for n, pen in (st.get("penalty") or {}).items():
            if int(pen.get("skip", 0)) > 0:
                pen["skip"] = int(pen["skip"]) - 1
                st["round"].append(n)
                st["skipped"].append(n)

    def check_round_done(self):
        """الكل لعب دوره = دور جديد والكل يرجع له وقته كامل"""
        allp = set(self.turn_players())
        if not allp or not allp <= set(self.state.get("round", [])):
            return
        if any(self.state.get("active_by", {}).values()) or self.active:
            return   # أحد لسا يلعب في حساب ثاني
        last, _ = self.last_finisher()
        mark = "|".join(self.state.get("round", []))
        if self.state.get("round_announced") == mark:
            return
        self.state_op(lambda st: st.update({"round_announced": mark}))
        hrs = dur(int(float(self.cfg.get("last_wait_hours", 2)) * 60))
        self.log("round_done", f"🏁 الكل خلص وقته. أول واحد يدخل يرجّع الوقت للكل"
                               + (f" (إلا {last}: ينتظر {hrs} أو لين يبدأ غيره)" if last else ""), notify=True)

    def toggle_block(self, name):
        blocked = name in (self.state.get("blocked") or [])

        def op(st):
            b = st.setdefault("blocked", [])
            if blocked and name in b:
                b.remove(name)
            elif not blocked and name not in b:
                b.append(name)
        self.state_op(op)
        if blocked:
            self.log("allow", f"✅ المنظم سمح لـ {name} يدخل", name, notify=True)
            self.info("تم", f"{name} يقدر يدخل الحين.")
        else:
            self.log("kick", f"🚫 المنظم منع {name} يدخل", name, notify=True)
            self.info("تم", f"{name} ممنوع لين تسمح له.")
            if self.active == name:
                self.close_session()

    def apply_cfg(self, d, first):
        """أمر إعدادات من الجوال. يرجع نص للسجل، أو يبدأ بـ ❌ لو فيه غلط"""
        op = d.get("op")
        players = self.cfg["players"]
        find = lambda n: next((q for q in players if q["name"] == n), None)

        def mins_ok(m):
            m = int(m)
            if not 0 <= m <= 24 * 60:
                raise ValueError("الوقت لازم يكون بين 0 و 24 ساعة")
            return m

        def pin_val(v, old=""):
            if v == "keep":
                return old
            if v == "" or re.fullmatch(r"[0-9a-f]{64}", str(v)):
                return v
            raise ValueError("رمز اللاعب غلط")
        if op == "add":
            name = str(d.get("name", "")).strip()[:30]
            if not name:
                return "❌ اكتب اسم"
            if find(name):
                return "❌ فيه لاعب بنفس الاسم" if first else f"الكبير أضاف لاعب: {name}"
            m = mins_ok(d.get("mins", 120))
            players.append({"name": name, "daily_minutes": m, "pin": pin_val(d.get("pin", "")),
                            "windows_user": str(d.get("win", "")).strip().lower()})
            self.save_cfg()
            return f"الكبير أضاف لاعب: {name} ({dur(m)} باليوم)"
        if op == "edit":
            old, name = str(d.get("old", "")), str(d.get("name", "")).strip()[:30]
            p = find(old) or find(name)
            if not p or not name:
                return "❌ اللاعب مو موجود"
            if name != p["name"] and find(name):
                return "❌ فيه لاعب بنفس الاسم"
            m = mins_ok(d.get("mins", p["daily_minutes"]))
            if p["name"] != name:
                if first:
                    def op_r(st):
                        for k in ("used", "bonus", "nobody", "week"):
                            if old in st.get(k, {}):
                                st[k][name] = st[k].pop(old)
                        for k in ("round", "blocked", "skipped"):
                            st[k] = [name if x == old else x for x in st.get(k, [])]
                    self.state_op(op_r)
                if self.active == old:
                    self.active = name
                    self.state["active"] = name
            p["name"], p["daily_minutes"] = name, m
            p["pin"] = pin_val(d.get("pin", "keep"), p.get("pin", ""))
            if "win" in d:
                p["windows_user"] = str(d.get("win", "")).strip().lower()
            self.save_cfg()
            return f"الكبير عدّل {old}: صار {name}، {dur(m)} باليوم"
        if op == "del":
            p = find(str(d.get("name", "")))
            if not p:
                return "الكبير حذف لاعب" if not first else "❌ اللاعب مو موجود"
            players.remove(p)
            self.save_cfg()
            return f"الكبير حذف اللاعب {p['name']}"
        if op == "punish":
            name = str(d.get("name", ""))
            if not find(name):
                return "❌ اللاعب مو موجود"
            if first:
                self.do_punish(name)
            return f"⚖️ عقوبة كذب على {name}"
        if op == "settings":
            new = {}
            for k, v in (d.get("set") or {}).items():
                if k == "free_accounts":
                    new[k] = str(v).strip()[:200]
                elif k in ("grace_minutes", "warn_minutes", "nobody_awake_per_day", "day_start_hour", "last_wait_hours"):
                    v = int(v)
                    if v < 0 or v > (23 if k == "day_start_hour" else 1440):
                        return "❌ رقم غلط"
                    new[k] = v
            changed = {k: v for k, v in new.items() if self.cfg.get(k) != v}
            self.cfg.update(new)
            self.save_cfg()
            names = {"grace_minutes": "المهلة", "warn_minutes": "التنبيه", "nobody_awake_per_day": "«ما أحد صاحي» باليوم",
                     "day_start_hour": "بداية اليوم", "last_wait_hours": "انتظار آخر واحد", "free_accounts": "الحسابات بدون حد"}
            return "الكبير غيّر الإعدادات: " + ("، ".join(f"{names[k]} = {v}" for k, v in changed.items()) or "بدون تغيير")
        return "❌ أمر مو معروف"

    def punish(self, name):
        v, _ = self.ask("عقوبة كذب", f"{name} كذب في «ما أحد صاحي»؟\n\nالعقوبة:\n"
                                     "• يخسر دوره الجاي\n• ينحرم من «ما أحد صاحي» أسبوع\n• توصل رسالة للكل",
                        [("إيه، عاقبه", "y"), ("لا", None)])
        if v:
            self.do_punish(name)

    def do_punish(self, name):
        until = time.time() + 7 * 24 * 3600

        def op(st):
            pen = st.setdefault("penalty", {}).setdefault(name, {})
            pen["ban"] = until
            r = st.setdefault("round", [])
            if name not in r:              # ما لعب هالدور: يخسره الحين
                r.append(name)
                st.setdefault("skipped", []).append(name)
            else:                          # لعب: يخسر الدور الجاي
                pen["skip"] = int(pen.get("skip", 0)) + 1
        self.state_op(op)
        d = datetime.datetime.fromtimestamp(until).strftime("%d/%m")
        self.log("penalty", f"⚖️ عقوبة كذب على {name}: يخسر دوره الجاي، ومحروم من «ما أحد صاحي» لين {d}",
                 name, notify=True)
        if not self.active:
            self.check_round_done()
            self.refresh_lock()

    def reset_flow(self, who=None, kind="time"):
        self.save_state()                         # ناخذ آخر الأوقات من الحسابات الثانية
        if kind == "round":
            who = who or self.active or self.linked_player() or "أحد"
        elif not who:
            mine = self.linked_players()
            names = mine + [p["name"] for p in self.cfg["players"] if p["name"] not in mine]
            if self.active:
                names = [self.active] + [n for n in names if n != self.active]
            who, _ = self.ask("ريست الوقت", "لمين الريست؟", [(n, n) for n in names] + [("إلغاء", None)])
            if not who:
                return
        locked = self.state.get("reset_locked")
        fails = int(self.state.get("reset_fails", 0))
        opts = [] if locked else [("أكتب رمز الكبير", "pin")]
        opts += [("رسالة للمنظم", "msg"), ("إلغاء", None)]
        title = "ريست الدور" if kind == "round" else "ريست الوقت"
        msg = ("ريست الدور: دور جديد، والكل يرجع له وقته كامل." if kind == "round"
               else f"ريست الوقت: {who} يرجع له وقته كامل.")
        if locked:
            msg += "\n\n🔒 الرمز انقفل بعد 3 محاولات غلط. لازم موافقة المنظم."
        v, _ = self.ask(title, msg, opts)
        if v == "pin":
            ok, vals = self.ask("رمز الكبير", f"باقي لك {3 - fails} محاولات.", [("تم", True), ("إلغاء", None)],
                                fields=[("الرمز", "", True)])
            if not ok:
                return
            if hash_pin(vals[0]) == self.cfg["admin_pin"]:
                self.do_reset(kind, who, "بالرمز")
                return
            fails += 1

            def op(st):
                st["reset_fails"] = int(st.get("reset_fails", 0)) + 1
                if st["reset_fails"] >= 3:
                    st["reset_locked"] = True
            self.state_op(op)
            if self.state.get("reset_locked"):
                self.log("reset_locked", f"🔒 أحد كتب رمز غلط 3 مرات في «{title}». انقفل، ولازم موافقتك.",
                         who, admin=True)
                self.info("انقفل", "3 محاولات غلط. الرمز انقفل، والمنظم وصلته رسالة.")
            else:
                self.log("reset_wrong", f"⚠️ رمز غلط في «{title}» ({fails} من 3)", who, admin=True)
                self.info("غلط", f"الرمز غلط. باقي {3 - fails} محاولات.")
        elif v == "msg":
            self.send_reset_request(who, kind)

    def send_reset_request(self, who=None, kind="time"):
        req = {"r": secrets.token_hex(4), "n": secrets.token_hex(8), "at": time.time() - 5,
               "who": who or "أحد", "kind": kind}
        self.pending_req = req
        q = urllib.parse.urlencode({"t": self.cfg["admin_topic"] + "-ok", "r": req["r"], "n": req["n"],
                                    "w": req["who"], "k": kind})
        link = PAGES_URL + "?" + q
        what = "ريست للدور (الكل يرجع له وقته)" if kind == "round" else "ريست لوقته"
        ntfy_admin_send(self.cfg, "وقتي: طلب " + ("ريست الدور" if kind == "round" else "ريست الوقت"),
                        f"{req['who']} يبي {what}. اضغط هنا واكتب الرمز إذا توافق.", click=link)
        self.log("reset_request", f"📨 {req['who']} طلب {what} من المنظم", who)
        self.info("انرسلت ✅", "وصلت رسالة للمنظم. أول ما يوافق من جواله، يصير الريست بروحه.")
        self.refresh_lock()

    def start_stream(self):
        """اتصال واحد مفتوح مع قاعدتنا: أوامر الجوال وموافقات الريست توصل منه على طول"""
        seen = self._ok_seen = set()
        lock = threading.Lock()
        path = fb_path(self.cfg["admin_topic"] + "-ok")

        def take(items):
            for k, m, t in fb_items(items):
                with lock:
                    if k in seen or t < time.time() - 7 * 86400:
                        continue          # الأوامر الأقدم من أسبوع ما تتنفذ
                    seen.add(k)
                self.handle_ok_message(m)

        def run():
            wait = 3
            while True:
                try:
                    req = urllib.request.Request(f"{FB_URL}/{path}.json", headers={"Accept": "text/event-stream"})
                    with urllib.request.urlopen(req, timeout=70) as r:   # القاعدة ترسل «موجود» كل 30 ثانية: لو سكتت، انقطع
                        event = ""
                        for raw in r:
                            line = raw.decode("utf-8", "ignore").strip()
                            if line.startswith("event:"):
                                event = line[6:].strip()
                                continue
                            if not line.startswith("data:") or event not in ("put", "patch"):
                                continue
                            wait = 3                       # الاتصال شغال
                            try:
                                d = json.loads(line[5:])
                            except Exception:
                                continue
                            p, v = d.get("path", "/"), d.get("data")
                            items = (v if isinstance(v, dict) else {}) if p == "/" else {p.strip("/").split("/")[0]: v}
                            take(items)
                except Exception:
                    pass
                time.sleep(wait)                           # يرجع يتصل خلال ثواني، ويبطّئ لو تكرر الفشل
                wait = min(wait * 2, 60)
        threading.Thread(target=run, daemon=True).start()

        def backup():
            """احتياط: لو الاتصال المفتوح انقطع بدون ما نحس، نشيك على آخر الأوامر كل 10 ثواني"""
            q = urllib.parse.urlencode({"orderBy": '"$key"', "limitToLast": 10})
            while True:
                time.sleep(10)
                try:
                    with urllib.request.urlopen(f"{FB_URL}/{path}.json?{q}", timeout=15) as r:
                        take(json.loads(r.read().decode("utf-8", "ignore") or "null"))
                except Exception:
                    pass
        threading.Thread(target=backup, daemon=True).start()

        def clean():
            while True:
                fb_cleanup(self.cfg)
                time.sleep(3600)
        threading.Thread(target=clean, daemon=True).start()

    def fast_cmds(self):
        """أوامر الجوال تتنفذ أول ما توصل (كل خُمس ثانية نشيك)"""
        try:
            if getattr(self, "reset_approved", None):
                (kind, who), self.reset_approved = self.reset_approved, None
                self.do_reset(kind, who, "بموافقة المنظم من الجوال")
            for cmd in self.cmd_queue[:]:
                self.cmd_queue.remove(cmd)
                self.apply_cmd(cmd)
        except Exception:
            pass
        self.root.after(200, self.fast_cmds)

    def handle_ok_message(self, m):
        try:
            d = json.loads(m)
        except Exception:
            return
        if (d.get("cmd") in ("kick", "allow", "reset_time", "reset_round", "set_pin", "logout")
                or re.fullmatch(r"adjust:-?\d{1,4}", str(d.get("cmd"))) or d.get("cmd") in ("say", "watch", "cfg")) and d.get("id"):
            self.cmd_queue.append(d)
            return
        req = getattr(self, "pending_req", None)
        if not req or d.get("r") != req.get("r"):
            return
        good = hashlib.sha256((self.cfg["admin_pin"] + req["n"]).encode()).hexdigest()
        ok = d.get("p") == good
        ntfy_admin_send(self.cfg, "وقتي", "✅ تم الريست" if ok else "❌ الرمز غلط",
                        topic=self.cfg["admin_topic"] + "-res-" + req["r"])
        if ok:
            self.reset_approved = (req.get("kind", "time"), req["who"])

    def poll_reset(self):
        req = getattr(self, "pending_req", None)
        if not req or getattr(self, "_polling", False):
            return
        if time.time() - req["at"] > 3600:
            self.pending_req = None
            return
        self._polling = True

        def run():
            try:
                good = hashlib.sha256((self.cfg["admin_pin"] + req["n"]).encode()).hexdigest()
                for m in ntfy_poll(self.cfg["admin_topic"] + "-ok", req["at"]):
                    try:
                        d = json.loads(m)
                    except Exception:
                        continue
                    if d.get("r") != req["r"]:
                        continue
                    ok = d.get("p") == good
                    ntfy_admin_send(self.cfg, "وقتي", "✅ تم الريست" if ok else "❌ الرمز غلط",
                                    topic=self.cfg["admin_topic"] + "-res-" + req["r"])
                    if ok:
                        self.reset_approved = (req.get("kind", "time"), req["who"])
                        break
            finally:
                self._polling = False
        threading.Thread(target=run, daemon=True).start()

    def publish_status(self, force=False, sync=False):
        """مين يلعب الحين وكم باقي، لرابط المنظم (كل دقيقة لو أحد يلعب، وكل 10 دقايق لو لا)"""
        playing = {n for n in (self.state.get("active_by") or {}).values() if n}
        if self.active:
            playing.add(self.active)
        last = getattr(self, "_status_sent", 0)
        if time.time() < NET["pause_until"]:
            return                             # ntfy موقفنا: ما نرسل الإضافي
        if not force and (not self.active or time.time() - last < 60):
            return                             # التحديث الدوري: كل دقيقة ومن حساب اللي يلعب بس
        if force and not sync and time.time() - last < 2:      # أحداث ورا بعض: نرسل وحدة بعد 3 ثواني
            if not getattr(self, "_status_pending", False):
                self._status_pending = True

                def later():
                    self._status_pending = False
                    self.publish_status(force=True)
                self.root.after(3000, later)
            return
        self._status_sent = time.time()
        rows = []
        for p in self.cfg["players"]:
            n = p["name"]
            st = ("blocked" if n in (self.state.get("blocked") or []) else "open" if self.is_open(n)
                  else "playing" if n in playing else "done" if n in self.state.get("round", []) else "idle")
            rows.append({"name": n, "state": st, "rem": int(max(0, self.remaining(n)))})
        body = json.dumps({"ts": int(time.time()), "players": rows}, ensure_ascii=False)
        if sync:                               # الكمبيوتر يطفي: نرسل على طول قبل لا يقفل البرنامج
            try:
                fb_req("PUT", fb_path(self.cfg["admin_topic"] + "-status"), fb_entry(body), timeout=4)
            except Exception:
                pass
            return
        ntfy_publish_raw(self.cfg["admin_topic"] + "-status", body)

    def week_id(self):
        d = datetime.date.fromisoformat(self.today())
        return (d - datetime.timedelta(days=(d.weekday() - 4) % 7)).isoformat()   # الأسبوع يبدأ الجمعة

    def check_week(self):
        """تقرير الأسبوع: أول ما يبدأ أسبوع جديد (الجمعة) يرسل ملخص الأسبوع اللي راح"""
        wid = self.week_id()
        if self.state.get("week_id") == wid:
            return
        info = {}

        def op(st):
            if st.get("week_id") == wid:
                return
            info["old"], info["week"] = st.get("week_id"), dict(st.get("week") or {})
            st["week_id"], st["week"] = wid, {}
        self.state_op(op)
        if info.get("old") and info.get("week"):
            parts = sorted(info["week"].items(), key=lambda kv: -kv[1])
            lines = "، ".join(f"{n} {dur(round(sec / 60))}" for n, sec in parts if sec >= 60)
            self.log("report", f"📊 تقرير الأسبوع: {lines or 'ما أحد لعب'}", notify=True)

    def publish_roster(self, force=False):
        """أسماء اللاعبين وإعداداتهم لموقع المنظم (عشان رابط المنظم يبقى ثابت ويقدر يعدّل من الجوال)"""
        names = [p["name"] for p in self.cfg["players"]]
        if time.time() < NET["pause_until"]:
            return
        try:
            accts = windows_accounts() if IS_WIN else []
        except Exception:
            accts = []
        info = {"roster": names,
                "players": [{"name": p["name"], "mins": int(p["daily_minutes"]), "pin": bool(p.get("pin")),
                             "win": p.get("windows_user", "")} for p in self.cfg["players"]],
                "settings": {k: self.cfg.get(k) for k in ("grace_minutes", "warn_minutes", "nobody_awake_per_day",
                                                          "day_start_hour", "last_wait_hours", "free_accounts")},
                "accounts": accts}
        body = json.dumps(info, ensure_ascii=False, sort_keys=True)
        key = hashlib.sha256(body.encode()).hexdigest()
        sent = self.state.get("roster_fb") or {}
        if not force and sent.get("key") == key and time.time() - float(sent.get("at", 0)) < 10 * 3600:
            return                             # ما تغيّر شي: ما نرسله مرة ثانية
        self.state_op(lambda st: st.update({"roster_fb": {"key": key, "at": time.time()}}))
        ntfy_publish_raw(self.cfg["admin_topic"] + "-roster", body)

    # ---------- أوامر المنظم من الجوال (طلّعه / اسمح له)
    def poll_cmds(self):
        if getattr(self, "_cmd_polling", False):
            return
        self._cmd_polling = True

        def run():
            try:
                for m in ntfy_poll(self.cfg["admin_topic"] + "-ok", self.cmd_since):
                    try:
                        d = json.loads(m)
                    except Exception:
                        continue
                    if (d.get("cmd") in ("kick", "allow", "reset_time", "reset_round", "set_pin", "logout")
                or re.fullmatch(r"adjust:-?\d{1,4}", str(d.get("cmd"))) or d.get("cmd") in ("say", "watch", "cfg")) and d.get("id"):
                        self.cmd_queue.append(d)
            finally:
                self._cmd_polling = False
        threading.Thread(target=run, daemon=True).start()

    def apply_cmd(self, d):
        self.root.after(300, lambda: self.publish_status(force=True))
        cid, cmd, who, ts = str(d.get("id")), d.get("cmd"), str(d.get("who", "")), str(d.get("ts", ""))
        if cid in getattr(self, "_seen_cmds", set()):
            return
        self._seen_cmds = getattr(self, "_seen_cmds", set()) | {cid}
        try:
            age = time.time() - int(ts) / 1000
            fresh = -300 < age < 7 * 86400          # الكمبيوتر كان طافي: ينفذ أوامر آخر أسبوع لما يشتغل
        except ValueError:
            fresh = False
        good = hashlib.sha256((self.cfg["admin_pin"] + f"{cmd}|{who}|{ts}").encode()).hexdigest()
        res_topic = self.cfg["admin_topic"] + "-res-" + cid
        valid_target = (cmd == "reset_round" or (cmd == "set_pin" and re.fullmatch(r"[0-9a-f]{64}", who))
                        or (cmd == "say" and 0 < len(who.strip()) <= 300)
                        or cmd == "watch"
                        or (cmd == "cfg" and len(who) < 3000)
                        or self.player(who))
        if not fresh or d.get("p") != good or not valid_target:
            if cid not in (self.state.get("cmds_done") or []):
                def op_bad(st):
                    done = st.setdefault("cmds_done", [])
                    done.append(cid)
                    del done[:-500]
                self.state_op(op_bad)
                ntfy_admin_send(self.cfg, "وقتي", "❌ الرمز غلط أو الطلب قديم", topic=res_topic)
            return
        h = int(self.cfg.get("day_start_hour", 4))
        sent_day = (datetime.datetime.fromtimestamp(int(ts) / 1000) - datetime.timedelta(hours=h)).date().isoformat()
        stale = ((cmd.startswith("adjust:") or cmd in ("reset_time", "reset_round")) and sent_day != self.today()) \
            or (cmd in ("logout", "say") and age > 12 * 3600) \
            or (cmd == "watch" and age > 120)
        if stale:                          # تعديل/ريست من يوم راح (أو خروج قديم): ما يتنفذ على اليوم الجديد
            if cid not in (self.state.get("cmds_done") or []):
                def op_old(st):
                    done = st.setdefault("cmds_done", [])
                    done.append(cid)
                    del done[:-500]
                self.state_op(op_old)
                what = "تسجيل خروج" if cmd == "logout" else "رسالة للشاشة" if cmd == "say" else "شوف الشاشة" if cmd == "watch" else "ريست" if cmd.startswith("reset") else "تعديل وقت"
                self.log("cmd_expired", f"⌛ أمر {what} {'لـ ' + who if cmd not in ('reset_round', 'say', 'watch') else ''} من الجوال ما تنفذ: "
                                        + ("الكمبيوتر كان طافي أو ما أحد قدامه" if cmd == "watch" else "انرسل والكمبيوتر طافي، وصار له أكثر من 12 ساعة" if cmd in ("logout", "say") else "انرسل والكمبيوتر طافي، وراح يومه"), who if self.player(who) else None, admin=True)
                ntfy_admin_send(self.cfg, "وقتي", "⌛ الأمر قديم وما تنفذ" if cmd in ("logout", "say", "watch") else "⌛ الأمر قديم (راح يومه) وما تنفذ", topic=res_topic)
            return
        if cmd == "cfg":                   # الإعدادات من الجوال: كل نسخة شغالة تحدّث عندها (عشان ما ترجّع القديم)
            first = {}

            def op_c(st):
                done = st.setdefault("cmds_done", [])
                first["yes"] = cid not in done
                if first["yes"]:
                    done.append(cid)
                    del done[:-500]
            self.state_op(op_c)
            try:
                msg = self.apply_cfg(json.loads(who), first.get("yes"))
            except Exception as ex:
                msg = f"❌ ما تنفذ: {ex}"
            if first.get("yes"):
                if msg.startswith("❌"):
                    ntfy_admin_send(self.cfg, "وقتي", msg, topic=res_topic)
                else:
                    self.log("settings", msg + " (من الجوال)", notify=True)
                    ntfy_admin_send(self.cfg, "وقتي", "✅ تم", topic=res_topic)
                    self.publish_roster(force=True)
            if not self.active and not self.dialogs:
                self.refresh_lock()
            return
        if cmd == "watch":                 # الكبير يبي يشوف الشاشة: بث 30 ثانية من الحساب اللي قدام الشاشة
            if not session_active():
                return
            shown = {}

            def op_w(st):
                lst = st.setdefault("say_shown", [])
                if cid not in lst:
                    lst.append(cid)
                    del lst[:-200]
                    shown["yes"] = True
            self.state_op(op_w)
            if shown.get("yes"):
                ntfy_admin_send(self.cfg, "وقتي", "✅ البث بدأ", topic=res_topic)
                self.watch_screen(cid)
            return
        if cmd == "say":                   # رسالة من الكبير تطلع على شاشة اللي قدام الكمبيوتر
            if not session_active():
                return                     # الحساب اللي قدام الشاشة هو اللي يعرضها
            shown = {}

            def op_say(st):
                lst = st.setdefault("say_shown", [])
                if cid not in lst:
                    lst.append(cid)
                    del lst[:-200]
                    shown["yes"] = True
            self.state_op(op_say)
            if shown.get("yes"):
                self.show_say(who.strip(), cid)
                self.log("say_cmd", f"💬 رسالة الكبير طلعت على الشاشة: {who.strip()}", self.active, admin=True)
                ntfy_admin_send(self.cfg, "وقتي", "✅ طلعت على الشاشة", topic=res_topic)
            return
        if cmd == "set_pin":               # كل نسخة شغالة تحدّث الرمز عندها (عشان ما ترجعه القديم)
            self.cfg["admin_pin"] = who
            self.save_cfg()
        first = {}

        def op(st):
            done = st.setdefault("cmds_done", [])
            first["yes"] = cid not in done
            done.append(cid)
            del done[:-500]
            b = st.setdefault("blocked", [])
            if cmd.startswith("adjust:") and first["yes"]:     # زيادة/نقص وقت اليوم (ما ينقص تحت الصفر)
                p = self.player(who)
                base = int(p["daily_minutes"]) * 60 if p else 0
                bonus, used = st.setdefault("bonus", {}), st.setdefault("used", {})
                left = base + bonus.get(who, 0) - used.get(who, 0)
                delta = max(int(cmd[7:]) * 60, -max(0, left))
                first["delta"] = delta
                bonus[who] = bonus.get(who, 0) + delta
            if cmd in ("reset_time", "reset_round", "set_pin", "logout") or cmd.startswith("adjust:"):
                return
            if cmd == "kick" and who not in b:
                b.append(who)
            if cmd == "allow" and who in b:
                b.remove(who)
            if cmd == "allow":                 # «اسمح له» يسامحه بعد: يشيل عقوبة الكذب ويرجع له دوره
                pen = (st.get("penalty") or {}).pop(who, None)
                was = who in st.get("skipped", [])
                if was:
                    st["skipped"].remove(who)
                    if who in st.get("round", []):
                        st["round"].remove(who)
                first["forgave"] = bool(pen) or was
        self.state_op(op)
        if cmd == "set_pin":
            if first.get("yes"):
                self.log("pin_changed", "🔑 الكبير غيّر رمزه من الجوال", admin=True)
                ntfy_admin_send(self.cfg, "وقتي", "✅ تم", topic=res_topic)
            return
        if cmd == "logout":
            if first.get("yes"):
                self.log("logout_cmd", f"🚪 المنظم سجّل خروج {who} من الجهاز", who, notify=True)
                ntfy_admin_send(self.cfg, "وقتي", "✅ تم", topic=res_topic)
            if self.active == who:
                beep()
                self.close_session()
                if IS_WIN and self.linked_player() == who:
                    subprocess.Popen(["shutdown", "/l", "/f"], creationflags=0x08000000)
            elif not self.active:
                self.refresh_lock()
            return
        if cmd.startswith("adjust:"):
            if first.get("yes"):
                m = round(first.get("delta", 0) / 60)
                self.log("adjust_cmd", (f"✅ المنظم زاد وقت {who} {dur(m)}" if m >= 0 else
                                        f"المنظم نقّص من وقت {who} {dur(-m)}") + f"، باقي له {dur(max(0, round(self.remaining(who) / 60)))}",
                         who, notify=True)
                ntfy_admin_send(self.cfg, "وقتي", "✅ تم", topic=res_topic)
            self.root.after(1500, lambda: self.publish_status(force=True))
            if self.remaining(who) > 0:
                self.unmark_finished(who)
            if self.active == who and self.remaining(who) > 0:
                self.grace_left = None
                self.warned = self.remaining(who) <= int(self.cfg["warn_minutes"]) * 60
                self.update_timer_ui()
            elif not self.active:
                self.refresh_lock()
            return
        if cmd in ("reset_time", "reset_round"):
            if first.get("yes"):
                self.do_reset("round" if cmd == "reset_round" else "time", who, "المنظم من الجوال")
                ntfy_admin_send(self.cfg, "وقتي", "✅ تم", topic=res_topic)
            elif not self.active:
                self.refresh_lock()
            return
        if first.get("yes"):
            if cmd == "kick":
                self.log("kick", f"🚫 المنظم طلّع {who} من الجهاز ومنعه يدخل", who, notify=True)
            else:
                self.log("allow", f"✅ المنظم سمح لـ {who} يدخل" + (" وشال عقوبته" if first.get("forgave") else ""),
                         who, notify=True)
            ntfy_admin_send(self.cfg, "وقتي", "✅ تم", topic=res_topic)
        if cmd == "kick" and self.active == who:
            beep()
            self.close_session()
            if IS_WIN and self.linked_player() == who:
                subprocess.Popen(["shutdown", "/l", "/f"], creationflags=0x08000000)
        elif not self.active:
            self.refresh_lock()

    def do_reset(self, kind, who, how):
        self.root.after(1500, lambda: self.publish_status(force=True))
        if kind == "round":
            def op(st):
                self._new_round(st)
                st["reset_fails"] = 0
                st["reset_locked"] = False
            self.state_op(op)
            self.pending_req = None
            if self.active:
                self.grace_left = None
                self.warned = False
                self.update_timer_ui()
            self.log("reset", f"🔄 ريست للدور ({how}): دور جديد، والكل رجع له وقته كامل", who, notify=True)
            if not self.active:
                self.refresh_lock()
        else:
            self.do_reset_time(who, how)

    def do_reset_time(self, who, how):
        self.root.after(1500, lambda: self.publish_status(force=True))
        def op(st):
            for k in ("used", "bonus", "nobody"):
                st.setdefault(k, {}).pop(who, None)
            if who in st.get("round", []) and who not in st.get("skipped", []):
                st["round"].remove(who)          # رجع له وقت = ما خلص دوره
            st["reset_fails"] = 0
            st["reset_locked"] = False
        self.state_op(op)
        self.pending_req = None
        if self.active == who:
            self.grace_left = None
            self.warned = False
            self.update_timer_ui()
        self.log("reset", f"🔄 رجع وقت {who} كامل ({how})", who, notify=True)
        if not self.active:
            self.refresh_lock()

    # ---------- sessions
    def start_session(self, name):
        rem = self.remaining(name)
        self.active = name
        self.state["active"] = name
        self.grace_left = None
        self.warned = rem <= int(self.cfg["warn_minutes"]) * 60
        self.save_state()
        self.log("start", f"{name} بدأ يلعب (باقي له {fmt(rem)})", name)
        self._session_start = (name, time.time(), self.state["week"].get(name, 0))
        if self.is_open(name):
            ends = "وقته مفتوح («ما أحد صاحي»)"
        else:
            ends = f"ينتهي الساعة {h12(datetime.datetime.now() + datetime.timedelta(seconds=max(0, rem)))}"
        ntfy_admin_send(self.cfg, "وقتي", f"🎮 دخل {name}، باقي له {dur(max(0, rem) // 60)}، {ends}")
        self.publish_status(force=True)
        self.show_timer()

    def end_session_ask(self):
        if not self.active:
            return
        v, _ = self.ask("إنهاء الجلسة", f"{self.active}، متأكد تبي تنهي جلستك؟\nالوقت الباقي يتحفظ لك.",
                        [("إيه، أنهِ", "yes"), ("لا", None)])
        if v and self.active:
            name = self.active
            self.log("end", f"{name} أنهى جلسته (باقي له {fmt(self.remaining(name))})", name)
            self.close_session()

    def close_session(self):
        self.timer_hidden = False
        name = self.active
        if name:
            st = getattr(self, "_session_start", None)
            played = (self.state["week"].get(name, 0) - st[2]) if st and st[0] == name else 0
            rem = self.remaining(name)
            ntfy_admin_send(self.cfg, "وقتي", f"🚪 خرج {name}، لعب {dur(round(played / 60))}، "
                            + (f"باقي له {dur(round(rem / 60))}" if rem > 0 else "وخلص وقته"))
        self.active = None
        self.grace_left = None
        self.state["active"] = None
        self.save_state()
        if name and self.is_open(name):
            self.end_open(name)
        if name and self.remaining(name) <= 0 and not self.is_open(name):
            self.mark_finished(name)
        self.check_round_done()
        self.publish_status(force=True)
        self.show_lock()

    def on_logoff(self):
        """ويندوز يطلع من الحساب أو يطفي: ننهي الجلسة صح"""
        if self.active:
            self.log("logoff", f"{self.active} طلع من حساب ويندوز", self.active)
            self.close_session()
        else:
            self.save_state()
        self.publish_status(force=True, sync=True)   # جوال الكبير يعرف إنه وقف يلعب
        try:
            self.state_op(lambda st: st.setdefault("running_by", {}).__setitem__(USER, False))
        except Exception:
            pass

    def lock_now(self):
        name = self.active
        self.log("lock", f"⛔ انقفل الجهاز على {name}: خلص وقته وخلصت المهلة", name, notify=True)
        beep()
        self.close_session()

    def extension_flow(self, name, from_lock=False):
        nb_used = self.state["nobody"].get(name, 0)
        nb_max = int(self.cfg["nobody_awake_per_day"])
        left = nb_max - nb_used
        v, _ = self.ask("وقت إضافي", f"{name} يبي وقت إضافي. كيف؟",
                        [("موافقة الكبير (بالرمز)", "admin"),
                         ("ما أحد صاحي (وقت مفتوح)", "nobody"),
                         ("إلغاء", None)])
        if v == "admin":
            if not self.check_admin("الكبير يكتب رمزه عشان يوافق على الوقت الإضافي"):
                return
            m, _ = self.ask("كم تعطيه؟", f"كم وقت تعطي {name}؟",
                            [("ربع ساعة", "15"), ("نص ساعة", "30"), ("ساعة", "60"), ("ساعتين", "120"), ("إلغاء", None)])
            if not m:
                return
            self.grant(name, int(m), "admin")
        elif v == "nobody":
            ban = float((self.state.get("penalty") or {}).get(name, {}).get("ban", 0))
            if ban > time.time():
                until = datetime.datetime.fromtimestamp(ban).strftime("%d/%m")
                self.info("محروم", f"{name}، أنت محروم من «ما أحد صاحي» لين {until} (عقوبة كذب).")
                return
            if left <= 0:
                self.log("nobody_denied", f"⚠️ {name} حاول ياخذ وقت بخيار «ما أحد صاحي» بعد ما خلص الحد حقه اليوم",
                         name, notify=True)
                self.info("ما يصير", f"استخدمت خيار «ما أحد صاحي» {nb_used} مرة اليوم وهذا الحد.\n"
                                     "انتظر لين أحد يصحى. وانكتبت المحاولة في السجل.")
                return
            ok, _ = self.ask("متأكد ما أحد صاحي؟",
                             "الوقت يصير مفتوح لين تضغط «إنهاء» أو تطلع من الحساب، وبعدها وقتك يصير صفر.\n"
                             "بتوصل رسالة للكل، وتنكتب في السجل باسمك والساعة.\n\n"
                             "⚖️ إذا طلع أحد صاحي: تخسر دورك الجاي، وتنحرم من «ما أحد صاحي» أسبوع.",
                             [("إيه، متأكد", "yes"), ("لا", None)])
            if not ok:
                return
            self.open_time(name)
        else:
            return
        if from_lock and not self.active and (self.remaining(name) > 0 or self.is_open(name)):
            self.start_session(name)

    def open_time(self, name):
        self.root.after(1500, lambda: self.publish_status(force=True))
        def op(st):
            st.setdefault("open", {})[name] = time.time()
            st.setdefault("nobody", {})[name] = st["nobody"].get(name, 0) + 1
        self.state_op(op)
        self.unmark_finished(name)
        self.log("extra_nobody", f"⚠️ {name} فتح الوقت بخيار «ما أحد صاحي»", name, notify=True)
        if self.active == name:
            self.grace_left = None
            self.update_timer_ui()

    def grant(self, name, minutes, by):
        self.root.after(1500, lambda: self.publish_status(force=True))
        self.state["bonus"][name] = self.state["bonus"].get(name, 0) + minutes * 60
        if by == "nobody":
            self.state["nobody"][name] = self.state["nobody"].get(name, 0) + 1
            text = f"⚠️ {name} أخذ {dur(minutes)} إضافية بخيار «ما أحد صاحي»"
        elif by == "admin":
            text = f"✅ الكبير أعطى {name} {dur(minutes)} إضافية"
        else:
            text = f"الكبير نقّص من وقت {name} {dur(minutes)}"
        self.save_state()
        if self.remaining(name) > 0:
            self.unmark_finished(name)
        self.log("extra_" + by, text, name, notify=True)
        if self.active == name and self.remaining(name) > 0:
            self.grace_left = None
            self.warned = self.remaining(name) <= int(self.cfg["warn_minutes"]) * 60
            self.update_timer_ui()

    # ---------- timer window
    def setup_timer_window(self):
        t = self.timer = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.configure(bg=CARD, highlightthickness=2, highlightbackground=ACCENT)
        t.protocol("WM_DELETE_WINDOW", lambda: None)
        self.t_name = tk.Label(t, text="", font=(FONT, 12, "bold"), bg=CARD, fg=FG)
        self.t_name.pack(pady=(8, 0), padx=16)
        self.t_status = tk.Label(t, text="", font=(FONT, 10), bg=CARD, fg=MUTED)
        self.t_status.pack()
        self.t_time = tk.Label(t, text="", font=(FONT, 28, "bold"), bg=CARD, fg=GOOD)
        self.t_time.pack(padx=16)
        row = tk.Frame(t, bg=CARD)
        row.pack(pady=(4, 10), padx=10)
        btn(row, "إنهاء", self.end_session_ask, bg=DIM, size=10).pack(side="right", padx=3)
        btn(row, "إخفاء", self.hide_timer, bg=DIM, size=10).pack(side="left", padx=3)
        self.timer_hidden = False
        self.f1_pressed = False
        start_key_hook(lambda: setattr(self, "f1_pressed", True))
        btn(row, "وقت إضافي", lambda: self.active and self.extension_flow(self.active),
            bg=ACCENT, size=10).pack(side="right", padx=3)
        for w in (t, self.t_name, self.t_status, self.t_time):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
        t.withdraw()

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.timer.winfo_x(), e.y_root - self.timer.winfo_y()

    def _drag_move(self, e):
        self.timer.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def hide_timer(self):
        self.timer_hidden = True
        self.timer.withdraw()
        self.toast("انخفى الوقت", "اضغط F1 عشان يرجع يطلع.", ACCENT)

    def unhide_timer(self):
        if self.timer_hidden and self.active:
            self.timer_hidden = False
            self.timer.deiconify()
            self.timer.lift()

    def show_timer(self):
        self.f1_pressed = False
        self.root.withdraw()
        self.update_timer_ui()
        if self.timer_hidden:
            return
        self.timer.deiconify()
        self.timer.update_idletasks()
        sw = self.root.winfo_screenwidth()
        self.timer.geometry(f"+{sw - self.timer.winfo_reqwidth() - 24}+24")
        self.timer.lift()

    def update_timer_ui(self):
        if not self.active:
            return
        self.t_name.configure(text=self.active)
        if self.is_open(self.active):
            started = self.state["open"][self.active]
            self.t_status.configure(text="وقت مفتوح (ما أحد صاحي) · له")
            self.t_time.configure(text=fmt(time.time() - started), fg=WARN)
            self.timer.configure(highlightbackground=WARN)
            return
        if self.grace_left is None:
            rem = self.remaining(self.active)
            warn = rem <= int(self.cfg["warn_minutes"]) * 60
            color = WARN if warn else GOOD
            self.t_status.configure(text="باقي من وقتك اليوم")
            self.t_time.configure(text=fmt(rem), fg=color)
        else:
            color = BAD
            self.t_status.configure(text="خلص وقتك! الجهاز بيتقفل بعد")
            self.t_time.configure(text=fmt(self.grace_left), fg=BAD)
        self.timer.configure(highlightbackground=color)

    def toast(self, title, msg, color=WARN):
        beep()
        t = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.configure(bg=color, padx=22, pady=16)
        tk.Label(t, text=title, font=(FONT, 16, "bold"), bg=color, fg="white").pack(anchor="e")
        tk.Label(t, text=msg, font=(FONT, 12), bg=color, fg="white", justify="right",
                 wraplength=380).pack(anchor="e")
        t.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        t.geometry(f"+{(sw - t.winfo_reqwidth()) // 2}+{sh // 5}")
        t.bind("<Button-1>", lambda e: t.destroy())
        t.after(8000, lambda: t.winfo_exists() and t.destroy())

    def show_say(self, text, cid=""):
        """رسالة الكبير: كبيرة بوسط الشاشة مع «أوكي» و«لا» و20 ثانية للجواب.
        من أول ما تطلع لين يجاوب: الكبير يشوف الشاشة بث مباشر من جواله"""
        beep()
        who = self.active or self.linked_player() or USER
        live = {"ans": None, "left": 20, "done": False}
        bg = ACCENT
        t = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.configure(bg=bg, padx=30, pady=24)
        tk.Label(t, text="💬 رسالة من الكبير", font=(FONT, 18, "bold"), bg=bg, fg="white").pack(anchor="e")
        tk.Label(t, text=text, font=(FONT, 20), bg=bg, fg="white", justify="right", wraplength=560).pack(anchor="e", pady=(10, 12))
        cnt = tk.Label(t, text="", font=(FONT, 12), bg=bg, fg="white")
        cnt.pack(anchor="e")
        row = tk.Frame(t, bg=bg)
        row.pack(pady=(12, 0))

        def answer(a):
            if live["ans"]:
                return
            live["ans"] = a
            try:
                t.destroy()
            except Exception:
                pass
            txt = {"ok": f"👍 {who} جاوب: أوكي", "no": f"👎 {who} جاوب: لا",
                   "none": f"⌛ ما أحد جاوب على رسالتك خلال 20 ثانية ({who})"}[a]
            self.log("say_answer", txt + f" · الرسالة: {text}", self.active, admin=True)
        tk.Button(row, text="أوكي 👍", font=(FONT, 14, "bold"), command=lambda: answer("ok"), bg=GOOD, fg="white",
                  relief="flat", padx=22, pady=6).pack(side="right", padx=6)
        tk.Button(row, text="لا 👎", font=(FONT, 14, "bold"), command=lambda: answer("no"), bg=BAD, fg="white",
                  relief="flat", padx=22, pady=6).pack(side="right", padx=6)

        def count():
            if live["ans"]:
                return
            if live["left"] <= 0:
                answer("none")
                return
            cnt.configure(text=f"جاوب خلال {live['left']} ثانية")
            live["left"] -= 1
            t.after(1000, count)
        count()
        t.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        t.geometry(f"+{(sw - t.winfo_reqwidth()) // 2}+{(sh - t.winfo_reqheight()) // 3}")
        t.lift()

        self.stream_screen(cid, live, who, text, "say", 26)

    def watch_screen(self, cid, seconds=30):
        """بث الشاشة للكبير بدون رسالة. يطلع بس «📺» صغير جنب الوقت عشان يكون واضح"""
        who = self.active or self.linked_player() or USER
        live = {"ans": None, "left": seconds}
        mark = None
        try:
            mark = tk.Toplevel(self.root)
            mark.overrideredirect(True)
            mark.attributes("-topmost", True)
            tk.Label(mark, text="📺", font=(FONT, 11), bg="#111827", fg="white", padx=6, pady=2).pack()
            mark.geometry(f"+{self.root.winfo_screenwidth() - 40}+{self.root.winfo_screenheight() - 80}")
        except Exception:
            pass

        def count():
            live["left"] -= 1
            if live["left"] <= 0:
                live["ans"] = "end"
                try:
                    mark and mark.destroy()
                except Exception:
                    pass
                return
            self.root.after(1000, count)
        self.root.after(1000, count)
        self.stream_screen(cid, live, who, "", "watch", seconds + 4)

    def stream_screen(self, cid, live, who, text, kind, max_s):
        """صورة للشاشة كل ثانية تقريباً لين يخلص (وصورة أخيرة فيها النتيجة)"""
        def stream():
            path = fb_path(self.cfg["admin_topic"] + "-live")
            n = 0
            start = time.time()
            while True:
                t0 = time.time()
                final = bool(live["ans"]) or t0 - start > max_s
                img = screen_shot()
                try:
                    fb_req("PUT", path, fb_entry(json.dumps({"id": cid, "n": n, "img": img, "who": who, "text": text,
                                                             "kind": kind, "left": max(0, live["left"]),
                                                             "ans": live["ans"] or ("none" if final else None)},
                                                            ensure_ascii=False)), timeout=20)
                    net_ok()
                except Exception as ex:
                    net_err(ex)
                n += 1
                if final:
                    return
                time.sleep(max(0.2, 1.0 - (time.time() - t0)))
        threading.Thread(target=stream, daemon=True).start()

    # ---------- loops
    def tick(self):
        try:
            now = time.monotonic()
            dt = now - self.last
            self.last = now
            if dt < 0 or dt > 5:      # الجهاز كان نايم (Sleep) - ما نحسب
                dt = 0
            if not session_active():  # الحساب مقفل أو حساب ثاني قدام الشاشة - ما نحسب
                dt = 0
            if getattr(self, "reset_approved", None):
                (kind, who), self.reset_approved = self.reset_approved, None
                self.do_reset(kind, who, "بموافقة المنظم من الجوال")
            for cmd in self.cmd_queue[:]:
                self.cmd_queue.remove(cmd)
                self.apply_cmd(cmd)
            if getattr(self, "pending_req", None) and time.time() - self.pending_req["at"] > 3600:
                self.pending_req = None
            if self.free:
                self.save_tick += 1
                if self.save_tick >= 10:      # حساب الكبير: نتزامن ونرسل الحالة للمنظم بس
                    self.save_tick = 0
                    self.save_state()
                    self.cfg = load_config()
                    self.publish_roster()
                    self.publish_status()
                    self.check_week()
                self.root.after(1000, self.tick)
                return
            if self.ensure_today():
                self.warned = False
                self.grace_left = None
                if not self.active:
                    self.refresh_lock()
            name = self.active
            if name:
                if self.grace_left is None:
                    self.state["used"][name] = self.state["used"].get(name, 0) + dt
                    self.state["week"][name] = self.state["week"].get(name, 0) + dt
                    rem = self.remaining(name)
                    warn_s = int(self.cfg["warn_minutes"]) * 60
                    if self.is_open(name):
                        rem = 10 ** 9      # وقت مفتوح: ما ينتهي
                    if not self.warned and 0 < rem <= warn_s:
                        self.warned = True
                        self.toast("تنبيه", f"{name}، باقي لك {fmt(rem)} بس.")
                    if rem <= 0:
                        g = int(self.cfg["grace_minutes"])
                        self.grace_left = g * 60
                        self.unhide_timer()           # خلص الوقت: الوقت يرجع يطلع غصب
                        self.publish_status(force=True)
                        self.log("timeout", f"⏰ خلص وقت {name}. عنده {g} دقايق مهلة وبعدها ينقفل الجهاز",
                                 name, notify=True)
                        self.toast("خلص وقتك!", f"عندك {g} دقايق وبعدها ينقفل الجهاز.\n"
                                                "احفظ لعبتك، أو اطلب وقت إضافي.", BAD)
                elif self.remaining(name) > 0 or self.is_open(name):
                    self.grace_left = None    # رجع له وقت (ريست أو وقت إضافي من حساب ثاني)
                    self.warned = False
                else:
                    self.grace_left -= dt
                    self.state["week"][name] = self.state["week"].get(name, 0) + dt
                    if self.grace_left <= 0:
                        self.lock_now()
                self.update_timer_ui()
            else:
                self.clock_lbl.configure(text=now_hm())
            self.save_tick += 1
            if self.save_tick >= 10:          # كل 10 ثواني نتزامن مع الحسابات الثانية
                self.save_tick = 0
                self.save_state()
                if not self.active and not self.dialogs:
                    self.cfg = load_config()
                    self.refresh_lock()
                self.publish_roster()
                self.publish_status()
                self.check_week()
        except Exception as ex:  # ما نبي البرنامج يطيح
            print("tick error:", ex)
        self.root.after(1000, self.tick)

    def keep_on_top(self):
        try:
            if self.dialogs:
                for d in list(self.dialogs):
                    if d.winfo_exists():
                        d.lift()
                        d.attributes("-topmost", True)
            elif self.free:
                pass
            elif not self.active:
                self.root.lift()
                self.root.attributes("-topmost", True)
            else:
                if self.f1_pressed:
                    self.f1_pressed = False
                    if self.timer_hidden:
                        self.unhide_timer()
                    else:
                        self.hide_timer()
                if not self.timer_hidden:
                    self.timer.lift()
                    self.timer.attributes("-topmost", True)
        except Exception:
            pass
        self.root.after(500, self.keep_on_top)

    # ---------- log window
    def open_log(self):
        w = tk.Toplevel(self.root)
        w.title("السجل")
        w.configure(bg=CARD, padx=16, pady=16)
        w.attributes("-topmost", True)
        self.dialogs.append(w)

        def close():
            if w in self.dialogs:
                self.dialogs.remove(w)
            w.destroy()

        w.protocol("WM_DELETE_WINDOW", close)
        tk.Label(w, text="السجل (ما أحد يقدر يمسحه)", font=(FONT, 16, "bold"), bg=CARD, fg=FG).pack(anchor="e")
        frame = tk.Frame(w, bg=CARD)
        frame.pack(fill="both", expand=True, pady=10)
        sb = tk.Scrollbar(frame)
        sb.pack(side="left", fill="y")
        txt = tk.Text(frame, width=70, height=24, font=(FONT, 12), bg=BG, fg=FG, relief="flat",
                      yscrollcommand=sb.set, padx=10, pady=10)
        txt.pack(side="right", fill="both", expand=True)
        sb.configure(command=txt.yview)
        txt.tag_configure("r", justify="right")
        for e in read_log(500):
            txt.insert("end", f"{e['text']}    {entry_time(e)}\n", "r")
        txt.configure(state="disabled")
        btn(w, "إغلاق", close, bg=DIM).pack(anchor="e")
        w.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w.geometry(f"+{(sw - w.winfo_reqwidth()) // 2}+{max(0, (sh - w.winfo_reqheight()) // 3)}")
        w.lift()
        w.focus_force()

    # ---------- settings
    def open_settings(self):
        if not self.check_admin("الإعدادات للكبير بس"):
            return
        s = tk.Toplevel(self.root)
        s.title("الإعدادات")
        s.configure(bg=CARD, padx=20, pady=18)
        s.attributes("-topmost", True)
        self.dialogs.append(s)

        def close():
            if s in self.dialogs:
                self.dialogs.remove(s)
            s.destroy()
            if not self.active:
                self.refresh_lock()

        s.protocol("WM_DELETE_WINDOW", close)
        tk.Label(s, text="الإعدادات", font=(FONT, 18, "bold"), bg=CARD, fg=FG).pack(anchor="e")
        tk.Label(s, text=f"أنت الحين في حساب ويندوز: {USER}   ·   النسخة {VERSION}", font=(FONT, 11), bg=CARD, fg=MUTED).pack(anchor="e")

        # players
        tk.Label(s, text="اللاعبين", font=(FONT, 13, "bold"), bg=CARD, fg=MUTED).pack(anchor="e", pady=(12, 4))
        pf = tk.Frame(s, bg=CARD)
        pf.pack(fill="x")
        lb = tk.Listbox(pf, font=(FONT, 12), width=38, height=6, justify="right", bg=BG, fg=FG,
                        selectbackground=ACCENT, relief="flat", exportselection=False)
        lb.pack(side="right")
        pbtns = tk.Frame(pf, bg=CARD)
        pbtns.pack(side="left", padx=(0, 10))

        def refresh_lb():
            lb.delete(0, "end")
            for p in self.cfg["players"]:
                lock = "  🔒" if p.get("pin") else ""
                lb.insert("end", f'{p["name"]} — {dur(p["daily_minutes"])} باليوم — باقي {fmt(self.remaining(p["name"]))}{lock}' + (f'  [{p["windows_user"]}]' if p.get("windows_user") else ""))

        def selected():
            sel = lb.curselection()
            if not sel:
                self.info("اختر لاعب", "اختر لاعب من القائمة أول.")
                return None
            return self.cfg["players"][sel[0]]

        def edit_player(p=None):
            v, vals = self.ask("لاعب جديد" if p is None else "تعديل لاعب",
                               "الرمز اختياري: لو حطيت رمز، ما أحد يقدر يلعب باسمه غيره.\n"
                               "حساب ويندوز اختياري: إذا دخل حسابه، يبدأ وقته على طول.",
                               [("حفظ", True), ("إلغاء", None)],
                               fields=[("الاسم", p["name"] if p else "", False),
                                       ("ساعات باليوم", (int(p["daily_minutes"]) // 60) if p else 2, False),
                                       ("ودقايق", (int(p["daily_minutes"]) % 60) if p else 0, False),
                                       ("رمز اللاعب", KEEP if p and p.get("pin") else "", False),
                                       ("حساب ويندوز حقه", p.get("windows_user", "") if p else "", False)])
            if not v:
                return
            name, pin, winuser = vals[0].strip(), vals[3].strip(), vals[4].strip().lower()
            mins = hm_minutes(vals[1], vals[2])
            if not name or mins is None or mins < 0 or mins > 24 * 60:
                self.info("خطأ", "اكتب اسم، والساعات والدقايق لازم تكون أرقام.")
                return
            if any(q["name"] == name and q is not p for q in self.cfg["players"]):
                self.info("خطأ", "فيه لاعب بنفس الاسم.")
                return
            if p is None:
                p = {"name": name, "daily_minutes": mins, "pin": ""}
                self.cfg["players"].append(p)
                self.log("settings", f"الكبير أضاف لاعب: {name} ({dur(mins)} باليوم)", notify=True)
            else:
                old = p["name"]
                if old != name:
                    for k in ("used", "bonus", "nobody"):
                        if old in self.state[k]:
                            self.state[k][name] = self.state[k].pop(old)
                    if self.active == old:
                        self.active = name
                        self.state["active"] = name
                if int(mins) != int(p["daily_minutes"]) or old != name:
                    self.log("settings", f"الكبير عدّل {old}: صار {name}، {dur(mins)} باليوم", notify=True)
                p["name"], p["daily_minutes"] = name, mins
            if pin != KEEP:
                p["pin"] = hash_pin(pin) if pin else ""
            p["windows_user"] = winuser
            self.save_cfg()
            self.save_state()
            refresh_lb()

        def del_player():
            p = selected()
            if not p:
                return
            v, _ = self.ask("حذف", f"تحذف {p['name']}؟", [("احذف", "y"), ("لا", None)])
            if v:
                self.cfg["players"].remove(p)
                self.save_cfg()
                self.log("settings", f"الكبير حذف اللاعب {p['name']}", notify=True)
                refresh_lb()

        def add_time():
            p = selected()
            if not p:
                return
            v, vals = self.ask("زيادة / نقص وقت",
                               f"كم تضيف لـ {p['name']}؟\nاختر «زيادة» أو «نقص».",
                               [("زيادة", 1), ("نقص", -1), ("إلغاء", None)],
                               fields=[("ساعات", "0", False), ("ودقايق", "15", False)])
            if not v:
                return
            m = hm_minutes(vals[0].replace("-", ""), vals[1].replace("-", ""))
            if not m:
                self.info("خطأ", "اكتب أرقام، مثلاً 1 ساعة و 30 دقيقة.")
                return
            m = m * v
            self.grant(p["name"], m, "admin" if m > 0 else "adjust")
            refresh_lb()

        def reset_day():
            p = selected()
            if not p:
                return
            v, _ = self.ask("تصفير", f"ترجع وقت {p['name']} لليوم من جديد؟", [("إيه", "y"), ("لا", None)])
            if v:
                for k in ("used", "bonus", "nobody"):
                    self.state[k].pop(p["name"], None)
                self.save_state()
                self.log("settings", f"الكبير صفّر وقت {p['name']} لليوم", notify=True)
                refresh_lb()

        for label, cmd in (("إضافة", lambda: edit_player()),
                           ("تعديل", lambda: (lambda p: p and edit_player(p))(selected())),
                           ("حذف", del_player), ("زيادة/نقص وقت", add_time), ("تصفير اليوم", reset_day),
                           ("عقوبة كذب", lambda: (lambda p: p and self.punish(p["name"]))(selected())),
                           ("منع / سماح", lambda: (lambda p: p and self.toggle_block(p["name"]))(selected()))):
            btn(pbtns, label, cmd, bg=BG, size=10, width=12).pack(pady=2)
        btn(pbtns, "ربط الحسابات", lambda: link_accounts(), bg=GOOD, size=10, width=12).pack(pady=(8, 2))
        refresh_lb()

        def link_accounts():
            w = tk.Toplevel(self.root)
            w.title("ربط الحسابات")
            w.configure(bg=CARD, padx=22, pady=18)
            w.attributes("-topmost", True)
            self.dialogs.append(w)
            tk.Label(w, text="ربط الحسابات", font=(FONT, 16, "bold"), bg=CARD, fg=FG).pack(anchor="e")
            tk.Label(w, text="اضغط على الزر جنب كل حساب واختر صاحبه. إذا دخل حسابه، يبدأ وقته على طول.",
                     font=(FONT, 11), bg=CARD, fg=MUTED).pack(anchor="e", pady=(2, 12))
            NONE, FREE = "—  (يختار اسمه بنفسه)", "بدون حد للوقت"
            options = [NONE, FREE] + [p["name"] for p in self.cfg["players"]]
            free = [a.strip().lower() for a in str(self.cfg.get("free_accounts", "")).replace("،", ",").split(",") if a.strip()]
            grid = tk.Frame(w, bg=CARD)
            grid.pack(fill="x")
            vars_ = {}
            names = [p["name"] for p in self.cfg["players"]]
            for i, acc in enumerate(windows_accounts()):
                cur = [FREE] if acc in free else []
                cur += [p["name"] for p in self.cfg["players"] if acc in p_accounts(p)]
                label = acc + ("   (أنت)" if acc == USER else "")
                tk.Label(grid, text=label, font=(FONT, 12, "bold"), bg=CARD, fg=FG).grid(row=i, column=1, sticky="e", pady=4)
                b = btn(grid, "، ".join(cur) or NONE, None, bg=BG, size=11, width=26)

                def pick(acc=acc, b=b):
                    sel = self.pick_many(f"حساب {acc}", "مين يستخدم هالحساب؟ تقدر تختار أكثر من واحد.\n"
                                         "«بدون حد» = حسابك أنت، وتقدر تختار معه لاعبين يستخدمونه.",
                                         names, FREE, vars_[acc])
                    if sel is not None:
                        vars_[acc] = sel
                        b.configure(text="، ".join(sel) or NONE)
                b.configure(command=pick)
                b.grid(row=i, column=0, sticky="w", padx=(0, 14), pady=4)
                vars_[acc] = cur

            def close_w():
                if w in self.dialogs:
                    self.dialogs.remove(w)
                w.destroy()

            def save_w():
                chosen = {}
                for acc, sel in vars_.items():
                    for val in sel:
                        if val != FREE:
                            chosen.setdefault(val, []).append(acc)
                new_free = [acc for acc, sel in vars_.items() if FREE in sel]
                listed = set(vars_)
                for p in self.cfg["players"]:
                    keep = [a for a in p_accounts(p) if a not in listed]
                    p["windows_user"] = ",".join(keep + chosen.get(p["name"], []))
                keep_free = [a for a in free if a not in listed]
                self.cfg["free_accounts"] = ", ".join(keep_free + new_free)
                self.save_cfg()
                ents["free_accounts"].delete(0, "end")
                ents["free_accounts"].insert(0, self.cfg["free_accounts"])
                desc = "، ".join(f"{n} = {' + '.join(accs)}" for n, accs in chosen.items()) or "ما فيه"
                self.log("settings", f"الكبير ربط الحسابات: {desc}" + (f"، وبدون حد: {', '.join(new_free)}" if new_free else ""),
                         notify=True)
                refresh_lb()
                close_w()

            w.protocol("WM_DELETE_WINDOW", close_w)
            row = tk.Frame(w, bg=CARD)
            row.pack(fill="x", pady=(16, 0))
            btn(row, "حفظ", save_w, bg=GOOD).pack(side="right", padx=4)
            btn(row, "إلغاء", close_w, bg=DIM).pack(side="left", padx=4)
            w.update_idletasks()
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            w.geometry(f"+{(sw - w.winfo_reqwidth()) // 2}+{(sh - w.winfo_reqheight()) // 3}")
            w.lift()
            w.focus_force()

        # general
        tk.Label(s, text="عام", font=(FONT, 13, "bold"), bg=CARD, fg=MUTED).pack(anchor="e", pady=(16, 4))
        gf = tk.Frame(s, bg=CARD)
        gf.pack(fill="x")
        fields = [("grace_minutes", "المهلة بعد ما يخلص الوقت (دقايق)"),
                  ("warn_minutes", "تنبيه قبل نهاية الوقت (دقايق)"),
                  ("nobody_awake_per_day", "كم مرة «ما أحد صاحي» باليوم لكل واحد"),
                  ("day_start_hour", "اليوم الجديد يبدأ الساعة (0-23)"),
                  ("last_wait_hours", "آخر واحد لعب ينتظر كم ساعة قبل الدور الجديد"),
                  ("telegram_token", "تيليجرام: توكن البوت"),
                  ("telegram_chat_id", "تيليجرام: رقم القروب (chat id)"),
                  ("free_accounts", "حسابات ويندوز بدون حد (افصلها بفاصلة)")]
        ents = {}
        for i, (key, label) in enumerate(fields):
            tk.Label(gf, text=label, font=(FONT, 11), bg=CARD, fg=FG).grid(row=i, column=1, sticky="e", pady=3)
            e = tk.Entry(gf, font=(FONT, 11), width=30 if key in TEXT_KEYS else 8, justify="right")
            e.insert(0, str(self.cfg.get(key, "")))
            e.grid(row=i, column=0, sticky="w", padx=(0, 12), pady=3)
            ents[key] = e

        auto_var = tk.BooleanVar(value=bool(self.cfg.get("autostart")))
        if IS_WIN:
            tk.Checkbutton(s, text="يشتغل تلقائياً مع تشغيل ويندوز", variable=auto_var, font=(FONT, 11),
                           bg=CARD, fg=FG, selectcolor=BG, activebackground=CARD,
                           activeforeground=FG).pack(anchor="e", pady=(8, 0))

        def collect():
            new = {}
            for key, _ in fields:
                val = ents[key].get().strip()
                if key in TEXT_KEYS:
                    new[key] = val
                else:
                    if not val.isdigit():
                        self.info("خطأ", "كل الخانات اللي فوق التيليجرام لازم تكون أرقام.")
                        return None
                    new[key] = int(val)
            if new["day_start_hour"] > 23:
                self.info("خطأ", "ساعة بداية اليوم لازم تكون بين 0 و 23.")
                return None
            return new

        def test_tg():
            new = collect()
            if new is None:
                return
            ok, err = telegram_send_sync(new, "⏱ وقتي: رسالة تجربة. إذا وصلتكم، التيليجرام شغال ✅")
            self.info("تجربة تيليجرام", "وصلت الرسالة ✅" if ok else f"ما وصلت ❌\n{err}")

        def change_admin_pin():
            v, vals = self.ask("رمز الكبير الجديد", "", [("حفظ", True), ("إلغاء", None)],
                               fields=[("الرمز الجديد", "", True), ("أعده مرة ثانية", "", True)])
            if not v:
                return
            if not vals[0].strip() or vals[0] != vals[1]:
                self.info("خطأ", "الرمزين مو نفس بعض، أو فاضي.")
                return
            self.cfg["admin_pin"] = hash_pin(vals[0])
            self.save_cfg()
            self.log("settings", "الكبير غيّر رمز الإدارة", notify=True)
            self.info("تم", "تغيّر الرمز.")

        def save():
            new = collect()
            if new is None:
                return
            changed = {k: v for k, v in new.items() if self.cfg.get(k) != v}
            self.cfg.update(new)
            if IS_WIN and bool(auto_var.get()) != bool(self.cfg.get("autostart")):
                try:
                    set_autostart(bool(auto_var.get()))
                    self.cfg["autostart"] = bool(auto_var.get())
                except Exception as ex:
                    self.info("خطأ", f"ما قدرت أضبط التشغيل التلقائي:\n{ex}")
            self.save_cfg()
            visible = [k for k in changed if not k.startswith("telegram")]
            if self.is_free_account() and not self.free and not self.linked_players():
                self.info("حساب بدون حد", "هذا الحساب صار بدون حد. البرنامج بيصير نافذة صغيرة في شريط المهام.")
                self.root.after(300, self.show_free)
            if visible:
                self.log("settings", "الكبير غيّر الإعدادات: " + "، ".join(f"{k}={changed[k]}" for k in visible),
                         notify=True)
            close()

        bb = tk.Frame(s, bg=CARD)
        bb.pack(fill="x", pady=(16, 0))
        btn(bb, "حفظ وإغلاق", save, bg=GOOD).pack(side="right", padx=4)
        btn(bb, "تجربة تيليجرام", test_tg, bg=BG).pack(side="right", padx=4)
        btn(bb, "رابط الإشعارات", self.show_ntfy, bg=GOOD).pack(side="right", padx=4)
        btn(bb, "رابط المنظم", self.show_admin_channel, bg=GOOD).pack(side="right", padx=4)
        if IS_WIN:
            btn(bb, "تشغيل لكل الحسابات", self.autostart_all, bg=BG).pack(side="right", padx=4)
        btn(bb, "تغيير رمز الكبير", change_admin_pin, bg=BG).pack(side="right", padx=4)
        btn(bb, "إلغاء", close, bg=DIM).pack(side="left", padx=4)

        s.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        s.geometry(f"+{(sw - s.winfo_reqwidth()) // 2}+{max(0, (sh - s.winfo_reqheight()) // 4)}")
        s.lift()
        s.focus_force()

    def pick_many(self, title, msg, names, free_label, current):
        """نافذة اختيار أكثر من شخص (أو «بدون حد»)"""
        d = tk.Toplevel(self.root)
        d.title(title)
        d.configure(bg=CARD, padx=24, pady=20)
        d.attributes("-topmost", True)
        self.dialogs.append(d)
        tk.Label(d, text=title, font=(FONT, 15, "bold"), bg=CARD, fg=FG).pack(anchor="e")
        tk.Label(d, text=msg, font=(FONT, 11), bg=CARD, fg=MUTED).pack(anchor="e", pady=(2, 10))
        vs = {}
        for n in names + [free_label]:
            v = tk.BooleanVar(value=n in current)
            tk.Checkbutton(d, text=n, variable=v, font=(FONT, 13), bg=CARD, fg=FG, selectcolor=BG,
                           activebackground=CARD, activeforeground=FG, anchor="e").pack(anchor="e", pady=2)
            vs[n] = v
        res = {"v": None}

        def done(ok):
            if ok:
                sel = [n for n, v in vs.items() if v.get()]
                res["v"] = ([free_label] if free_label in sel else []) + [n for n in sel if n != free_label]
            if d in self.dialogs:
                self.dialogs.remove(d)
            d.destroy()
        row = tk.Frame(d, bg=CARD)
        row.pack(fill="x", pady=(14, 0))
        btn(row, "تم", lambda: done(True), bg=GOOD).pack(side="right", padx=4)
        btn(row, "إلغاء", lambda: done(False), bg=DIM).pack(side="left", padx=4)
        d.protocol("WM_DELETE_WINDOW", lambda: done(False))
        d.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        d.geometry(f"+{(sw - d.winfo_reqwidth()) // 2}+{(sh - d.winfo_reqheight()) // 3}")
        d.lift()
        try:
            d.wait_visibility()
            d.grab_set()
        except Exception:
            pass
        self.root.wait_window(d)
        return res["v"]

    def autostart_all(self):
        msg = "البرنامج بيشتغل تلقائياً في كل حسابات ويندوز. ويندوز بيطلب منك موافقة المسؤول."
        if "\\users\\" in sys.executable.lower():
            msg += ("\n\n⚠️ Python مثبت لحسابك بس، فحسابات إخوانك ما تقدر تشغله.\n"
                    "ثبّت Python مرة ثانية واختر Customize ← Install Python for all users.")
        v, _ = self.ask("تشغيل لكل الحسابات", msg, [("كمّل", "y"), ("إلغاء", None)])
        if v:
            ok = set_autostart_all()
            self.info("تشغيل لكل الحسابات", "تم ✅ (إذا وافقت على رسالة ويندوز)" if ok else "ما تم. لازم توافق على رسالة ويندوز.")

    def show_admin_channel(self):
        v, _ = self.ask("رابط المنظم (لك بس)",
                        "افتح هالرابط من جوالك: تشوف رسائل البيت + طلبات الريست، وتوافق عليها بزر «وافق».\n"
                        "لا تعطيه أحد. (ولو تبي إشعار يرن: ثبّت تطبيق «وقتي» للأندرويد وحط فيه هالرابط)",
                        [("تجربة", "test"), ("إغلاق", None)],
                        fields=[("الرابط", msgs_link(self.cfg) + "&a=" + self.cfg["admin_topic"], False)])
        if v == "test":
            ntfy_admin_send(self.cfg, "وقتي", "رسالة تجربة للمنظم ✅")
            self.info("انرسلت", "إذا مشترك في القناة، المفروض وصلتك.")

    def show_ntfy(self):
        while True:
            v, _ = self.ask("رابط الإشعارات",
                            "انسخ الرابط وأرسله لإخوانك. يفتحونه من الجوال ويشوفون الرسائل، بدون حساب، وللقراءة بس.\n"
                            "واللي يبي إشعار يرن: يثبت تطبيق «وقتي» للأندرويد ويحط فيه الرابط.",
                            [("تجربة", "test"), ("رابط جديد", "new"), ("إغلاق", None)],
                            fields=[("الرابط", msgs_link(self.cfg), False)])
            if v == "test":
                ok, err = ntfy_send_sync(self.cfg, "⏱ وقتي: رسالة تجربة. إذا شفتها، الإشعارات شغالة ✅")
                self.info("تجربة", "انرسلت ✅ افتح الرابط وشوفها." if ok else f"ما انرسلت ❌\n{err}")
            elif v == "new":
                ok, _ = self.ask("رابط جديد؟", "الرابط القديم يوقف، ولازم ترسل الجديد للكل.",
                                 [("إيه، غيّره", "y"), ("لا", None)])
                if ok:
                    self.cfg["ntfy_topic"] = new_topic()
                    self.save_cfg()
                    self.log("settings", "الكبير غيّر رابط الإشعارات")
            else:
                return

    # ---------- exit
    def exit_app(self):
        if not self.check_admin("إغلاق البرنامج للكبير بس"):
            return
        v, _ = self.ask("إغلاق", "إذا قفلت البرنامج الجهاز يصير مفتوح بدون حد للوقت. متأكد؟",
                        [("إيه، اقفله", "y"), ("لا", None)])
        if not v:
            return
        self.log("app_exit", "الكبير قفل البرنامج", notify=True)
        self.state["active"] = None
        self.save_state()
        self.state_op(lambda st: (st.setdefault("running_by", {}).__setitem__(USER, False),
                                  st.setdefault("closed_by", {}).__setitem__(USER, time.time())))
        self.root.after(800, self.root.destroy)  # نعطي التيليجرام فرصة

    def run(self):
        self.root.mainloop()


_instance_lock = None


INSTALL_PATH = os.path.join(DATA_DIR, "waqti.pyw")


def version_of(path):
    try:
        with open(path, encoding="utf-8") as f:
            m = re.search(r"^VERSION = (\d+)", f.read(), re.M)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


def install_self():
    """البرنامج ينسخ نفسه لمكان ثابت (AppData) ويشتغل من هناك،
    عشان يقدر يحدّث نفسه حتى لو شغلته من داخل ملف مضغوط أو من التنزيلات."""
    if getattr(sys, "frozen", False):
        return False
    here = os.path.abspath(__file__)
    if os.path.normcase(here) == os.path.normcase(INSTALL_PATH):
        return False
    try:
        if version_of(INSTALL_PATH) <= VERSION:
            if os.path.exists(INSTALL_PATH):
                os.chmod(INSTALL_PATH, stat.S_IWRITE | stat.S_IREAD)
            shutil.copyfile(here, INSTALL_PATH)
        exe = sys.executable
        if IS_WIN and exe.lower().endswith("python.exe"):
            exe = exe[:-len("python.exe")] + "pythonw.exe"
        subprocess.Popen([exe, INSTALL_PATH], creationflags=0x00000008 if IS_WIN else 0, close_fds=True)
        return True
    except Exception:
        return False   # لو ما قدر، يشتغل من مكانه عادي


def main():
    if "--watchdog" in sys.argv:
        st = load_json(STATE_PATH, {})
        if (st.get("closed_by") or {}).get(USER):
            return                         # الكبير سكّره بالرمز: ما نرجّعه لين يدخل الحساب من جديد
        if not session_active():
            return
    if install_self():
        return
    # نسخة وحدة بس من البرنامج
    global _instance_lock
    _instance_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    for _ in range(20):  # بعد التحديث ننتظر النسخة القديمة تطلع
        try:
            _instance_lock.bind(("127.0.0.1", 47613 + int(hashlib.md5(USER.encode()).hexdigest(), 16) % 2000))
            break
        except OSError:
            time.sleep(0.5)
    else:
        return
    App().run()


if __name__ == "__main__":
    main()
