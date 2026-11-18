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
VERSION = 18
CHANGES = ("جديد في رابط المنظم: زر «طلّعه» يطلّع اللاعب من الجهاز ويمنعه يدخل لين تضغط «اسمح له».\n"
           "خذ رابط المنظم من جديد من الإعدادات (تغيّر).")
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


def ntfy_send_sync(cfg, text):
    """الرسالة تروح لـ ntfy مختومة، وموقعنا على GitHub يعرضها"""
    if not cfg.get("ntfy_topic"):
        return False, "ما فيه رابط"
    try:
        ts = int(time.time())
        sig = ed25519_sign(bytes.fromhex(cfg["sign_key"]), f"{ts}|{text}".encode("utf-8")).hex()
        body = {"topic": cfg["ntfy_topic"], "message": text, "title": "وقتي", "tags": ["stopwatch"],
                "click": f"{msgs_link(cfg)}#s={sig}&ts={ts}"}
        req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200, ""
    except Exception as ex:
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
    """رسالة للمنظم بس (قناة خاصة فيه)"""
    def run():
        try:
            body = {"topic": topic or cfg["admin_topic"], "title": title, "message": text,
                    "priority": 4, "tags": ["stopwatch"]}
            if click:
                body["click"] = click
                body["actions"] = [{"action": "view", "label": "افتح ووافق", "url": click}]
            req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(body).encode("utf-8"),
                                         method="POST", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15).close()
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


def ntfy_poll(topic, since):
    """يرجع الرسائل اللي وصلت للقناة من وقت معين"""
    try:
        url = f"https://ntfy.sh/{topic}/json?poll=1&since={int(since)}"
        with urllib.request.urlopen(url, timeout=15) as r:
            out = []
            for line in r.read().decode("utf-8", "ignore").splitlines():
                try:
                    m = json.loads(line)
                    if m.get("event") == "message":
                        out.append(m.get("message", ""))
                except Exception:
                    pass
            return out
    except Exception:
        return []


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


def now_hm():
    return datetime.datetime.now().strftime("%H:%M")


def entry_time(e):
    try:
        return datetime.datetime.fromisoformat(e["t"]).strftime("%d/%m  %H:%M")
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
        self.log("app_start", "تشغّل البرنامج")
        if IS_WIN and self.cfg.get("autostart"):
            try:
                set_autostart(True)   # نتأكد إن التشغيل التلقائي يأشر على المكان الصحيح
            except Exception:
                pass

        self.save_state()
        self.setup_lock_window()
        self.setup_timer_window()
        me = self.linked_player()
        if self.is_free_account():
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
            if me and self.remaining(me) > 0 and self.can_play(me):
                self.start_session(me)          # دخل حسابه = يبدأ وقته على طول
        self.update_ready = None
        self.cmd_queue = []
        self.cmd_since = time.time() - 60
        self.root.after(1000, self.tick)
        self.root.after(500, self.keep_on_top)
        self.root.after(5000, self.check_update)
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
                if (p.get("windows_user") or "").strip().lower() == USER]

    def linked_player(self):
        """صاحب الحساب إذا كان شخص واحد بس (الحساب المشترك ما يبدأ تلقائياً)"""
        lp = self.linked_players()
        return lp[0] if len(lp) == 1 else None

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
        self.free_frame.pack(fill="both", expand=True)
        r.geometry("")
        r.deiconify()
        r.protocol("WM_DELETE_WINDOW", r.iconify)
        r.iconify()

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
        for k in ("used", "bonus", "nobody"):
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
                    keep = {k: disk[k] for k in ("round", "reset_fails", "reset_locked", "open", "penalty", "skipped", "blocked", "cmds_done") if k in disk}
                    disk = {"date": mine["date"], "used": {}, "bonus": {}, "nobody": {},
                            "active_by": disk["active_by"], "resume_by": disk["resume_by"], **keep}
                if disk.get("date") == mine.get("date"):
                    base = self.base if self.base.get("date") == mine.get("date") else {}
                    for k in ("used", "bonus", "nobody"):
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
                          "active": self.state.get("active"), "resume": self.state.get("resume")}
            self.save_state()
            return True
        for k in ("used", "bonus", "nobody"):
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
        v, vals = self.ask("رمز الكبير", reason, [("دخول", True), ("إلغاء", None)],
                           fields=[("الرمز", "", True)])
        if not v:
            return False
        if hash_pin(vals[0]) == self.cfg["admin_pin"]:
            return True
        self.log("wrong_admin_pin", "⚠️ أحد حاول يدخل برمز الكبير وكان الرمز غلط", self.active, admin=True)
        self.info("خطأ", "الرمز غلط. وانكتبت المحاولة في السجل.")
        return False

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
        shown = [p for p in self.cfg["players"] if p["name"] in mine] if len(mine) > 1 else self.cfg["players"]
        for i, p in enumerate(shown):
            rem = self.remaining(p["name"])
            if p["name"] in (self.state.get("blocked") or []):
                txt, color = f'{p["name"]}\nممنوع (المنظم)', BAD
            elif self.is_open(p["name"]):
                txt, color = f'{p["name"]}\nوقت مفتوح', WARN
            elif p["name"] in self.state.get("skipped", []) and not self.can_play(p["name"]):
                txt, color = f'{p["name"]}\nخسر دوره (عقوبة)', DIM
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
        self.link_lbl.configure(text=f"النسخة {VERSION}     ·     رابط الرسائل: عند الكبير (الإعدادات ← رابط الإشعارات)")

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

    def can_play(self, name):
        """الدور = الوقت: اللي خلص وقته (أو عليه عقوبة) ينتظر لين يخلص وقت الكل"""
        if name in (self.state.get("blocked") or []):
            return False
        return name not in self.state.get("round", [])

    def mark_finished(self, name):
        def op(st):
            r = st.setdefault("round", [])
            if name not in r:
                r.append(name)
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

        self.state_op(self._new_round)
        self.log("round", "🔄 الكل لعب دوره: دور جديد، والكل رجع له وقته كامل", notify=True)
        for n in self.state.get("skipped", []):
            self.log("penalty_skip", f"⚖️ {n} خسر دوره هالمرة (عقوبة كذب)", n, notify=True)

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

    def punish(self, name):
        v, _ = self.ask("عقوبة كذب", f"{name} كذب في «ما أحد صاحي»؟\n\nالعقوبة:\n"
                                     "• يخسر دوره الجاي\n• ينحرم من «ما أحد صاحي» أسبوع\n• توصل رسالة للكل",
                        [("إيه، عاقبه", "y"), ("لا", None)])
        if not v:
            return
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
        who = who or self.active or self.linked_player()
        if kind == "round":
            who = who or "أحد"
        if not who:
            names = [p["name"] for p in self.cfg["players"]]
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
                    if d.get("cmd") in ("kick", "allow") and d.get("id"):
                        self.cmd_queue.append(d)
            finally:
                self._cmd_polling = False
        threading.Thread(target=run, daemon=True).start()

    def apply_cmd(self, d):
        cid, cmd, who, ts = str(d.get("id")), d.get("cmd"), str(d.get("who", "")), str(d.get("ts", ""))
        if cid in getattr(self, "_seen_cmds", set()):
            return
        self._seen_cmds = getattr(self, "_seen_cmds", set()) | {cid}
        try:
            fresh = abs(time.time() - int(ts) / 1000) < 300
        except ValueError:
            fresh = False
        good = hashlib.sha256((self.cfg["admin_pin"] + f"{cmd}|{who}|{ts}").encode()).hexdigest()
        res_topic = self.cfg["admin_topic"] + "-res-" + cid
        if not fresh or d.get("p") != good or not self.player(who):
            if cid not in (self.state.get("cmds_done") or []):
                self.state_op(lambda st: st.setdefault("cmds_done", []).append(cid))
                ntfy_admin_send(self.cfg, "وقتي", "❌ الرمز غلط أو الطلب قديم", topic=res_topic)
            return
        first = {}

        def op(st):
            done = st.setdefault("cmds_done", [])
            first["yes"] = cid not in done
            done.append(cid)
            del done[:-50]
            b = st.setdefault("blocked", [])
            if cmd == "kick" and who not in b:
                b.append(who)
            if cmd == "allow" and who in b:
                b.remove(who)
        self.state_op(op)
        if first.get("yes"):
            if cmd == "kick":
                self.log("kick", f"🚫 المنظم طلّع {who} من الجهاز ومنعه يدخل", who, notify=True)
            else:
                self.log("allow", f"✅ المنظم سمح لـ {who} يدخل", who, notify=True)
            ntfy_admin_send(self.cfg, "وقتي", "✅ تم", topic=res_topic)
        if cmd == "kick" and self.active == who:
            beep()
            self.close_session()
            if IS_WIN and self.linked_player() == who:
                subprocess.Popen(["shutdown", "/l", "/f"], creationflags=0x08000000)
        elif not self.active:
            self.refresh_lock()

    def do_reset(self, kind, who, how):
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
        name = self.active
        self.active = None
        self.grace_left = None
        self.state["active"] = None
        self.save_state()
        if name and self.is_open(name):
            self.end_open(name)
        if name and self.remaining(name) <= 0 and not self.is_open(name):
            self.mark_finished(name)
        self.check_round_done()
        self.show_lock()

    def on_logoff(self):
        """ويندوز يطلع من الحساب أو يطفي: ننهي الجلسة صح"""
        if self.active:
            self.log("logoff", f"{self.active} طلع من حساب ويندوز", self.active)
            self.close_session()
        else:
            self.save_state()

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

    def show_timer(self):
        self.root.withdraw()
        self.update_timer_ui()
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
            if int(time.time()) % 5 == 0:
                self.poll_cmds()
            if getattr(self, "pending_req", None) and int(time.time()) % 3 == 0:
                self.poll_reset()
            if self.free:
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
                        self.log("timeout", f"⏰ خلص وقت {name}. عنده {g} دقايق مهلة وبعدها ينقفل الجهاز",
                                 name, notify=True)
                        self.toast("خلص وقتك!", f"عندك {g} دقايق وبعدها ينقفل الجهاز.\n"
                                                "احفظ لعبتك، أو اطلب وقت إضافي.", BAD)
                else:
                    self.grace_left -= dt
                    if self.grace_left <= 0:
                        self.lock_now()
                self.update_timer_ui()
            else:
                self.clock_lbl.configure(text=datetime.datetime.now().strftime("%H:%M"))
            self.save_tick += 1
            if self.save_tick >= 10:          # كل 10 ثواني نتزامن مع الحسابات الثانية
                self.save_tick = 0
                self.save_state()
                if not self.active and not self.dialogs:
                    self.cfg = load_config()
                    self.refresh_lock()
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
                                       ("كم ساعة باليوم", hours_num(p["daily_minutes"]) if p else "2", False),
                                       ("رمز اللاعب", KEEP if p and p.get("pin") else "", False),
                                       ("حساب ويندوز حقه", p.get("windows_user", "") if p else "", False)])
            if not v:
                return
            name, mins, pin, winuser = vals[0].strip(), vals[1].strip(), vals[2].strip(), vals[3].strip().lower()
            mins = parse_hours(mins)
            if not name or mins is None or mins < 0 or mins > 24 * 60:
                self.info("خطأ", "اكتب اسم، والساعات لازم تكون رقم (مثلاً 2 أو 1.5).")
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
            v, vals = self.ask("تعديل وقت اليوم",
                               f"كم ساعة تضيف لـ {p['name']}؟ (مثلاً 1 أو 0.5. وحط سالب عشان تنقص، مثلاً -0.5)",
                               [("تم", True), ("إلغاء", None)], fields=[("الساعات", "1", False)])
            if not v:
                return
            m = parse_hours(vals[0].replace("-", "")) if vals[0].strip() else None
            if not m:
                self.info("خطأ", "اكتب رقم، مثلاً 1 أو 0.5.")
                return
            if vals[0].strip().startswith("-"):
                m = -m
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
                cur += [p["name"] for p in self.cfg["players"] if (p.get("windows_user") or "").lower() == acc]
                label = acc + ("   (أنت)" if acc == USER else "")
                tk.Label(grid, text=label, font=(FONT, 12, "bold"), bg=CARD, fg=FG).grid(row=i, column=1, sticky="e", pady=4)
                b = btn(grid, "، ".join(cur) or NONE, None, bg=BG, size=11, width=26)

                def pick(acc=acc, b=b):
                    sel = self.pick_many(f"حساب {acc}", "مين يستخدم هالحساب؟ تقدر تختار أكثر من واحد.",
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
                        if val == FREE:
                            continue
                        if val in chosen:
                            self.info("خطأ", f"{val} مربوط بحسابين. كل لاعب له حساب واحد بس.")
                            return
                        chosen[val] = acc
                new_free = [acc for acc, sel in vars_.items() if FREE in sel]
                listed = set(vars_)
                for p in self.cfg["players"]:
                    old = (p.get("windows_user") or "").lower()
                    if p["name"] in chosen:
                        p["windows_user"] = chosen[p["name"]]
                    elif old in listed:
                        p["windows_user"] = ""
                keep_free = [a for a in free if a not in listed]
                self.cfg["free_accounts"] = ", ".join(keep_free + new_free)
                self.save_cfg()
                ents["free_accounts"].delete(0, "end")
                ents["free_accounts"].insert(0, self.cfg["free_accounts"])
                desc = "، ".join(f"{acc} = {n}" for n, acc in chosen.items()) or "ما فيه"
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
            if self.is_free_account() and not self.free:
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
                res["v"] = [free_label] if free_label in sel else sel
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
                        "لا تعطيه أحد. (ولو تبي إشعار يرن: تطبيق ntfy ← + ← اكتب: "
                        f"{self.cfg['admin_topic']})",
                        [("تجربة", "test"), ("إغلاق", None)],
                        fields=[("الرابط", msgs_link(self.cfg) + "&a=" + self.cfg["admin_topic"] + "&p="
                                 + urllib.parse.quote(",".join(p["name"] for p in self.cfg["players"])), False)])
        if v == "test":
            ntfy_admin_send(self.cfg, "وقتي", "رسالة تجربة للمنظم ✅")
            self.info("انرسلت", "إذا مشترك في القناة، المفروض وصلتك.")

    def show_ntfy(self):
        while True:
            v, _ = self.ask("رابط الإشعارات",
                            "انسخ الرابط وأرسله لإخوانك. يفتحونه من الجوال ويشوفون الرسائل، بدون حساب، وللقراءة بس.\n"
                            "واللي يبي إشعار يرن: يثبت تطبيق ntfy، ويضغط +، ويكتب:\n"
                            f"{self.cfg['ntfy_topic']}",
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
