"""Claude Usage — widget de escritorio con el % de uso de tu plan de Claude.

Fuentes de datos:
  * Límites del plan: el endpoint que usa `/usage` en Claude Code, autenticado con el
    token OAuth que Claude Code guarda en ~/.claude/.credentials.json.
  * Tokens: los registros locales de Claude Code (~/.claude/projects/**/*.jsonl).

Solo usa la librería estándar de Python.
"""

import base64
import csv
import json
import os
import queue
import socket
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import webbrowser
import winreg
import zlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path

APP_NAME = "Claude Usage"
APP_ID = "ClaudeUsage.Tracker"
FROZEN = getattr(sys, "frozen", False)  # empaquetado como .exe con PyInstaller
APP_FILE = Path(sys.executable if FROZEN else __file__).resolve()
APP_DIR = APP_FILE.parent
DATA_DIR = APP_DIR / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
HISTORY_FILE = DATA_DIR / "history.jsonl"
ICON_PNG = DATA_DIR / "icon.png"
ICON_ICO = DATA_DIR / "icon.ico"
CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
USAGE_PAGE = "https://claude.ai/settings/usage"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ClaudeUsage"
AUMID_KEY = rf"Software\Classes\AppUserModelId\{APP_ID}"
INSTANCE_PORT = 47823
HISTORY_DAYS = 8
TOKEN_DAYS = 7
TOKEN_SCAN_MS = 5 * 60_000
THRESHOLDS = (75, 90, 100)
RATE_LIMIT_WAIT = 300  # s de espera tras un 429
DEFAULTS = {"on_top": True, "notify": True, "interval": 60, "details": False, "chart_hours": 24,
            "mode": "widget", "alpha": 1.0, "widget_style": "large"}

LABELS = {
    "session": "Sesión (5 h)",
    "weekly_all": "Semanal · todos los modelos",
    "weekly_opus": "Semanal · Opus",
    "weekly_sonnet": "Semanal · Sonnet",
}
LEGACY_KEYS = {
    "five_hour": "session",
    "seven_day": "weekly_all",
    "seven_day_opus": "weekly_opus",
    "seven_day_sonnet": "weekly_sonnet",
}
DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]

# alto contraste: fondo casi negro, texto blanco, colores de estado saturados
BG, CARD, FG, MUTED, TRACK, BORDER = "#111114", "#202026", "#ffffff", "#c8c8d2", "#34343e", "#4a4a55"
GREEN, AMBER, RED, ACCENT = "#34d17a", "#ffb224", "#ff5a52", "#e8825f"
SERIES = {"session": ACCENT, "weekly_all": "#6ea8fe", "weekly_opus": "#b48ead", "weekly_sonnet": "#88c0d0"}
FONT, FONT_B = "Segoe UI", "Segoe UI Semibold"


# ───────────────────────── datos ─────────────────────────

class AuthError(Exception):
    pass


class RateLimited(AuthError):
    pass


def fetch_usage():
    try:
        oauth = json.loads(CREDENTIALS.read_text(encoding="utf-8"))["claudeAiOauth"]
    except (OSError, KeyError, ValueError):
        raise AuthError("No se encontró la sesión de Claude Code. Inicia sesión con `claude`.")
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {oauth['accessToken']}",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-usage-tracker/2.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthError("Token caducado: abre Claude Code un momento para renovarlo.")
        if e.code == 429:
            raise RateLimited(f"Anthropic limita las consultas por ahora; reintento en {RATE_LIMIT_WAIT // 60} min.")
        raise


def to_epoch(iso):
    try:
        return datetime.fromisoformat(iso).timestamp() if iso else None
    except ValueError:
        return None


def parse_limits(data):
    """[{kind, pct, reset}] a partir de `limits` o, si no existe, de los campos clásicos."""
    out = []
    for item in data.get("limits") or []:
        if item.get("percent") is not None:
            out.append({"kind": item["kind"], "pct": float(item["percent"]), "reset": to_epoch(item.get("resets_at"))})
    if not out:
        for key, kind in LEGACY_KEYS.items():
            block = data.get(key)
            if block and block.get("utilization") is not None:
                out.append({"kind": kind, "pct": float(block["utilization"]), "reset": to_epoch(block.get("resets_at"))})
    return out


def window_seconds(kind):
    if kind == "session":
        return 5 * 3600
    if kind.startswith("weekly"):
        return 7 * 86400
    return None


def same_window(a, b):
    return a is not None and b is not None and abs(a - b) < 1800


class History:
    """Puntos {t, l: {kind: [pct, reset]}} guardados en data/history.jsonl."""

    def __init__(self):
        cutoff = time.time() - HISTORY_DAYS * 86400
        self.points = []
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                for line in f:
                    try:
                        p = json.loads(line)
                    except ValueError:
                        continue
                    if p.get("t", 0) >= cutoff:
                        self.points.append(p)
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                f.writelines(json.dumps(p) + "\n" for p in self.points)
        except OSError:
            pass

    def add(self, limits):
        p = {"t": time.time(), "l": {lim["kind"]: [lim["pct"], lim["reset"]] for lim in limits}}
        self.points.append(p)
        try:
            with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(p) + "\n")
        except OSError:
            pass

    def last(self, kind):
        for p in reversed(self.points):
            if kind in p["l"]:
                return p["l"][kind]
        return None

    def series(self, kind, since):
        return [(p["t"], p["l"][kind][0]) for p in self.points if p["t"] >= since and kind in p["l"]]

    def recent_rate(self, kind, reset, span=3600):
        """% por segundo en la última hora dentro de la misma ventana, o None si hay pocos datos."""
        since = time.time() - span
        pts = [(p["t"], p["l"][kind][0]) for p in self.points
               if p["t"] >= since and kind in p["l"] and same_window(p["l"][kind][1], reset)]
        if len(pts) < 2 or pts[-1][0] - pts[0][0] < 900:
            return None
        return (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0])


def scan_tokens():
    """Suma los tokens de los registros locales de Claude Code de los últimos TOKEN_DAYS días."""
    since = time.time() - TOKEN_DAYS * 86400
    seen = {}
    for f in PROJECTS_DIR.rglob("*.jsonl"):
        try:
            if f.stat().st_mtime < since:
                continue
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"usage"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    m = d.get("message")
                    if isinstance(m, dict) and isinstance(m.get("usage"), dict):
                        # cada respuesta se escribe varias veces mientras se genera: deduplicar
                        key = (m.get("id") or d.get("uuid"), d.get("requestId"))
                        seen[key] = (d.get("timestamp"), m.get("model") or "?", d.get("cwd") or "", m["usage"])
        except OSError:
            continue

    today = datetime.now().date()
    stats = {"today": defaultdict(int), "week": defaultdict(int),
             "models": defaultdict(int), "projects": defaultdict(int)}
    names = {}
    for ts, model, cwd, u in seen.values():
        try:
            when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            continue
        if when.timestamp() < since:
            continue
        parts = {
            "input": u.get("input_tokens") or 0,
            "output": u.get("output_tokens") or 0,
            "cache_write": u.get("cache_creation_input_tokens") or 0,
            "cache_read": u.get("cache_read_input_tokens") or 0,
        }
        total = sum(parts.values())
        if not total:
            continue
        for k, v in parts.items():
            stats["week"][k] += v
            if when.astimezone().date() == today:
                stats["today"][k] += v
        stats["models"][model] += total
        name = Path(cwd).name or "?"
        stats["projects"][names.setdefault(name.lower(), name)] += total  # rutas de Windows: sin mayúsculas
    return stats


# ───────────────────────── formato ─────────────────────────

def color_for(pct):
    return RED if pct >= 90 else AMBER if pct >= 70 else GREEN


def fmt_num(n):
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= div:
            return f"{n / div:.1f}{suf}".replace(".0", "")
    return str(int(n))


def fmt_when(epoch):
    local = datetime.fromtimestamp(epoch)
    if local.date() == datetime.now().date():
        return f"a las {local:%H:%M}"
    return f"el {DAYS[local.weekday()]} a las {local:%H:%M}"


def fmt_reset(epoch):
    if not epoch:
        return ""
    secs = int(epoch - time.time())
    if secs <= 0:
        return "reiniciado"
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    left = f"{d} d {h} h" if d else f"{h} h {m:02d} min" if h else f"{m} min"
    local = datetime.fromtimestamp(epoch)
    stamp = f"{DAYS[local.weekday()]} {local:%H:%M}" if d else f"{local:%H:%M}"
    return f"reinicia en {left} ({stamp})"


def pretty_model(model):
    """claude-opus-5-5-20260101 → Opus 5.5"""
    parts = [p for p in model.removeprefix("claude-").split("-") if not (p.isdigit() and len(p) == 8)]
    names = [p.capitalize() for p in parts if not p.isdigit()]
    nums = [p for p in parts if p.isdigit()]
    return " ".join(names + ([".".join(nums)] if nums else [])) or model


def pace_info(lim, history):
    """(texto, color, marcador del tiempo transcurrido 0-100 o None)."""
    total, reset, pct = window_seconds(lim["kind"]), lim["reset"], lim["pct"]
    if not total or not reset:
        return "", MUTED, None
    now = time.time()
    elapsed = min(max(now - (reset - total), 0), total)
    marker = 100 * elapsed / total
    if pct >= 100:
        return "Límite alcanzado: espera al reinicio", RED, marker
    # sesión: ritmo de la última hora (reacciona rápido); semanal: media de la ventana, porque
    # extrapolar una hora intensa a varios días daría avisos exagerados
    rate = history.recent_rate(lim["kind"], reset) if total <= 86400 else None
    if rate is None and elapsed > 300:
        rate = pct / elapsed
    if not rate or rate <= 0:
        return ("Sin consumo en la última hora" if pct else "Aún sin uso en esta ventana"), MUTED, marker
    unit, factor = ("%/h", 3600) if total <= 86400 else ("%/día", 86400)
    speed = f"Ritmo ≈ {rate * factor:.1f} {unit}"
    eta = now + (100 - pct) / rate
    if eta < reset:
        return f"{speed} → límite {fmt_when(eta)}", AMBER, marker
    return f"{speed} → no llegarás al límite", GREEN, marker


# ───────────────────────── Windows ─────────────────────────

def pyw_exe():
    launcher = Path(os.environ.get("WINDIR", r"C:\Windows")) / "pyw.exe"
    return launcher if launcher.exists() else Path(sys.executable).with_name("pythonw.exe")


def launch_command(*args):
    base = f'"{APP_FILE}"' if FROZEN else f'"{pyw_exe()}" "{APP_FILE}"'
    return " ".join((base, *args))


def get_autostart():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, launch_command("--startup"))
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass


def register_app_id():
    """Registra el AppUserModelID para que las notificaciones salgan con el nombre e icono de la app."""
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUMID_KEY) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, str(ICON_PNG))
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def toast(title, body):
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    xml = (f'<toast><visual><binding template="ToastGeneric"><text>{esc(title)}</text>'
           f'<text>{esc(body)}</text></binding></visual></toast>')
    ps = ("[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]>$null;"
          "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime]>$null;"
          "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;"
          f"$x.LoadXml('{xml.replace(chr(39), chr(39) * 2)}');"
          f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_ID}')"
          ".Show([Windows.UI.Notifications.ToastNotification]::new($x))")
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                          base64.b64encode(ps.encode("utf-16-le")).decode()],
                         creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError:
        pass


def claim_single_instance():
    """Devuelve el socket si somos la primera instancia; si no, pide a la existente que se muestre."""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", INSTANCE_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        try:
            with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=2) as c:
                c.sendall(b"show")
        except OSError:
            pass
        return None


# ───────────────────────── icono ─────────────────────────

def _png(size, rgba):
    raw = b"".join(b"\0" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _render_icon(size):
    """Medidor circular naranja sobre un cuadrado redondeado oscuro."""
    import math
    hexrgb = lambda h: tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
    bg, track, arc = hexrgb(BG), hexrgb(TRACK), hexrgb(ACCENT)
    px = bytearray()
    for y in range(size):
        for x in range(size):
            u, v = (x + 0.5) / size, (y + 0.5) / size
            # cuadrado redondeado
            qx, qy = max(abs(u - 0.5) - 0.5 + 0.03 + 0.2, 0), max(abs(v - 0.5) - 0.5 + 0.03 + 0.2, 0)
            sdf = math.hypot(qx, qy) - 0.2
            a = min(max(0.5 - sdf * size, 0), 1)
            r, g, b = bg
            # anillo
            dist = math.hypot(u - 0.5, v - 0.5)
            cov = min(max(0.5 - (abs(dist - 0.31) - 0.075) * size, 0), 1)
            if cov:
                ang = (math.degrees(math.atan2(u - 0.5, 0.5 - v)) + 360) % 360
                cr, cg, cb = arc if ang <= 250 else track
                r, g, b = (round(c0 + (c1 - c0) * cov) for c0, c1 in ((r, cr), (g, cg), (b, cb)))
            px += bytes((r, g, b, round(a * 255)))
    return bytes(px)


def ensure_icons():
    DATA_DIR.mkdir(exist_ok=True)
    if ICON_PNG.exists() and ICON_ICO.exists():
        return
    sizes = (16, 24, 32, 48, 64, 256)
    pngs = [_png(s, _render_icon(s)) for s in sizes]
    ICON_PNG.write_bytes(pngs[-1])
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, data in zip(sizes, pngs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    ICON_ICO.write_bytes(header + entries + b"".join(pngs))


# ───────────────────────── interfaz ─────────────────────────

def load_settings():
    try:
        return {**DEFAULTS, **json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULTS)


class LimitRow:
    def __init__(self, parent):
        self.frame = tk.Frame(parent, bg=CARD, padx=12, pady=10)
        top = tk.Frame(self.frame, bg=CARD)
        top.pack(fill="x")
        self.title = tk.Label(top, bg=CARD, fg=FG, font=(FONT_B, 10), anchor="w")
        self.title.pack(side="left")
        self.pct = tk.Label(top, bg=CARD, fg=FG, font=(FONT_B, 14))
        self.pct.pack(side="right")
        self.bar = tk.Canvas(self.frame, height=10, bg=CARD, highlightthickness=0)
        self.bar.pack(fill="x", pady=(6, 4))
        self.bar.bind("<Configure>", lambda e: self._draw())
        self.sub = tk.Label(self.frame, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w")
        self.sub.pack(fill="x")
        self.pace = tk.Label(self.frame, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w")
        self.pace.pack(fill="x")
        self.value, self.marker = 0.0, None

    def update(self, lim, history):
        self.value = lim["pct"]
        self.title.config(text=LABELS.get(lim["kind"], lim["kind"].replace("_", " ").capitalize()))
        self.pct.config(text=f"{self.value:.0f}%", fg=color_for(self.value))
        reset = fmt_reset(lim["reset"])
        self.sub.config(text=f"Te queda {max(0.0, 100 - self.value):.0f}%" + (f" · {reset}" if reset else ""))
        text, color, self.marker = pace_info(lim, history)
        self.pace.config(text=text, fg=color)
        self._draw()

    def _draw(self):
        c = self.bar
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        c.create_rectangle(0, 0, w, h, fill=TRACK, width=0)
        fill = int(w * min(self.value, 100) / 100)
        if fill > 0:
            c.create_rectangle(0, 0, fill, h, fill=color_for(self.value), width=0)
        if self.marker is not None:  # ritmo "ideal": tiempo transcurrido de la ventana
            x = int(w * self.marker / 100)
            c.create_line(x, 0, x, h, fill=FG, width=2)


SHORT = {"session": "Sesión", "weekly_all": "Semana", "weekly_opus": "Opus", "weekly_sonnet": "Sonnet"}


def fmt_short(epoch):
    if not epoch:
        return ""
    secs = int(epoch - time.time())
    if secs <= 0:
        return "ya"
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f"{d}d {h}h" if d else f"{h}h {m:02d}m" if h else f"{m}m"


def work_area_corner():
    """Esquina superior derecha del área de trabajo del monitor principal (sin barra de tareas)."""
    try:
        import ctypes
        from ctypes import wintypes as wt
        r = wt.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
        return r.right, r.top
    except Exception:
        return None


def virtual_screen():
    """(x, y, ancho, alto) del escritorio completo, con todos los monitores."""
    try:
        import ctypes
        gsm = ctypes.windll.user32.GetSystemMetrics
        return gsm(76), gsm(77), gsm(78), gsm(79)
    except Exception:
        return None


class Widget:
    """Ventana flotante sin bordes. Arrastrar para mover, doble clic: ventana completa,
    clic derecho: menú. Estilos: 'large' (barras grandes), 'rings' (anillos), 'compact'."""

    def __init__(self, app):
        self.app = app
        self.style = app.settings.get("widget_style", "large")
        w = self.win = tk.Toplevel(app.root)
        w.withdraw()
        w.overrideredirect(True)
        w.configure(bg=BORDER)  # borde de 1 px
        w.attributes("-alpha", app.settings["alpha"])
        pad = (10, 6) if self.style == "compact" else (14, 10)
        self.inner = tk.Frame(w, bg=BG, padx=pad[0], pady=pad[1])
        self.inner.pack(padx=1, pady=1)

        head = tk.Frame(self.inner, bg=BG)
        head.pack(fill="x")
        big = self.style != "compact"
        self.dot = tk.Label(head, text="●", bg=BG, fg=ACCENT, font=(FONT, 9 if big else 8))
        self.dot.pack(side="left")
        tk.Label(head, text="Claude", bg=BG, fg=FG, font=(FONT_B, 11 if big else 9)).pack(side="left", padx=(3, 0))
        btn = dict(bg=BG, fg=MUTED, activebackground=CARD, activeforeground=FG, bd=0, cursor="hand2",
                   font=(FONT, 11 if big else 9), padx=6, pady=0)
        tk.Button(head, text="—", command=app.hide, **btn).pack(side="right")
        tk.Button(head, text="⤢", command=lambda: app.show_mode("full"), **btn).pack(side="right")

        self.table = tk.Frame(self.inner, bg=BG)
        self.table.pack(fill="x", pady=(6 if big else 4, 0))
        self.placeholder = tk.Label(self.table, text="Consultando…", bg=BG, fg=MUTED, font=(FONT, 10))
        self.placeholder.grid(row=0, column=0)
        self.rows = []
        self._drag = None
        self._bind_tree(self.win)

    # ── interacción ──
    def _bind_tree(self, widget):
        if not isinstance(widget, tk.Button):
            widget.bind("<ButtonPress-1>", self._start)
            widget.bind("<B1-Motion>", self._move)
            widget.bind("<ButtonRelease-1>", self._end)
            widget.bind("<Double-Button-1>", lambda e: self.app.show_mode("full"))
            widget.bind("<Button-3>", lambda e: self.app.menu.tk_popup(e.x_root, e.y_root))
        for child in widget.winfo_children():
            self._bind_tree(child)

    def _start(self, e):
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _move(self, e):
        if self._drag:
            self.win.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _end(self, e):
        if self._drag:
            self._drag = None
            self.app.settings["widget_pos"] = [self.win.winfo_x(), self.win.winfo_y()]
            self.app.save()

    def show(self, anchor_right=None):
        """Muestra el widget en su posición guardada, sin salirse de la pantalla.
        anchor_right: mantener este borde derecho (al cambiar de estilo y de ancho)."""
        self.win.update_idletasks()
        pos, vs = self.app.settings.get("widget_pos"), virtual_screen()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        if pos and anchor_right is not None:
            pos = [anchor_right - w, pos[1]]
        if not pos or (vs and not (vs[0] - w + 40 <= pos[0] <= vs[0] + vs[2] - 40
                                   and vs[1] <= pos[1] <= vs[1] + vs[3] - 30)):
            right, top = work_area_corner() or (self.win.winfo_screenwidth(), 0)
            pos = [right - w - 24, top + 24]
        if vs:  # que quepa entero
            pos = [min(max(pos[0], vs[0]), vs[0] + vs[2] - w), min(max(pos[1], vs[1]), vs[1] + vs[3] - h)]
        self.app.settings["widget_pos"] = pos
        self.win.geometry(f"+{pos[0]}+{pos[1]}")
        self.win.deiconify()
        self.win.attributes("-topmost", self.app.on_top.get())
        self.win.lift()

    def set_alpha(self, alpha):
        self.win.attributes("-alpha", alpha)

    # ── dibujo ──
    def render(self, limits, history, error):
        self.dot.config(fg=AMBER if error else ACCENT)
        self.placeholder.grid() if not limits else self.placeholder.grid_remove()
        if len(self.rows) != len(limits):
            for row in self.rows:
                for cell in row.values():
                    cell.destroy()
            self.rows = [getattr(self, f"_make_{self.style}")(i) for i in range(len(limits))]
            self._bind_tree(self.table)
        for row, lim in zip(self.rows, limits):
            getattr(self, f"_update_{self.style}")(row, lim, pace_info(lim, history))

    @staticmethod
    def _bar(canvas, width, height, pct, marker):
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill=TRACK, width=0)
        if pct > 0:
            canvas.create_rectangle(0, 0, max(3, width * min(pct, 100) / 100), height,
                                    fill=color_for(pct), width=0)
        if marker is not None:  # tiempo transcurrido: línea blanca con contorno oscuro
            x = width * marker / 100
            canvas.create_line(x, 0, x, height, fill=BG, width=5)
            canvas.create_line(x, 0, x, height, fill=FG, width=2)

    # estilo grande: nombre + % enorme, barra gruesa, reinicio y ritmo
    LARGE_W, LARGE_H = 260, 14

    def _make_large(self, i):
        f = tk.Frame(self.table, bg=BG)
        f.grid(row=i + 1, column=0, sticky="we", pady=(0 if i == 0 else 10, 0))
        top = tk.Frame(f, bg=BG)
        top.pack(fill="x")
        row = {"name": tk.Label(top, bg=BG, fg=FG, font=(FONT_B, 11), anchor="sw"),
               "pct": tk.Label(top, bg=BG, fg=FG, font=(FONT_B, 24), anchor="e"),
               "bar": tk.Canvas(f, width=self.LARGE_W, height=self.LARGE_H, bg=BG, highlightthickness=0),
               "sub": tk.Label(f, bg=BG, fg=MUTED, font=(FONT, 10), anchor="w"),
               "pace": tk.Label(f, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w")}
        row["name"].pack(side="left", fill="y")
        row["pct"].pack(side="right")
        row["bar"].pack(fill="x", pady=(0, 4))
        row["sub"].pack(fill="x")
        row["pace"].pack(fill="x")
        row["frame"] = f  # al final: se destruye después de sus hijos
        return row

    def _update_large(self, row, lim, pace):
        text, color, marker = pace
        p = lim["pct"]
        row["name"].config(text=SHORT.get(lim["kind"], lim["kind"]).upper())
        row["pct"].config(text=f"{p:.0f}%", fg=color_for(p))
        when = fmt_short(lim["reset"])
        row["sub"].config(text=f"Quedan {max(0.0, 100 - p):.0f}%" + (f"  ·  reinicia en {when}" if when else ""))
        row["pace"].config(text=text, fg=color)
        self._bar(row["bar"], self.LARGE_W, self.LARGE_H, p, marker)

    # estilo anillos: un medidor circular por límite, lado a lado
    RING = 128

    def _make_rings(self, i):
        f = tk.Frame(self.table, bg=BG)
        f.grid(row=1, column=i, padx=(0 if i == 0 else 12, 0))
        row = {"ring": tk.Canvas(f, width=self.RING, height=self.RING, bg=BG, highlightthickness=0),
               "sub": tk.Label(f, bg=BG, fg=MUTED, font=(FONT, 10))}
        row["ring"].pack()
        row["sub"].pack()
        row["frame"] = f
        return row

    def _update_rings(self, row, lim, pace):
        import math
        _, color, marker = pace
        p, c, s = lim["pct"], row["ring"], self.RING
        thick, m = 14, 9
        c.delete("all")
        box = (m, m, s - m, s - m)
        c.create_oval(*box, outline=TRACK, width=thick)
        if p > 0:
            extent = -359.9 * min(p, 100) / 100
            c.create_arc(*box, start=90, extent=extent, style="arc", outline=color_for(p), width=thick)
        if marker is not None:
            a = math.radians(90 - 360 * marker / 100)
            r0, r1 = (s - 2 * m) / 2 - thick / 2 - 2, (s - 2 * m) / 2 + thick / 2 + 2
            cx = cy = s / 2
            pts = (cx + r0 * math.cos(a), cy - r0 * math.sin(a), cx + r1 * math.cos(a), cy - r1 * math.sin(a))
            c.create_line(*pts, fill=BG, width=5)
            c.create_line(*pts, fill=FG, width=2)
        warn = "⚠" if color in (AMBER, RED) else ""
        c.create_text(s / 2, s / 2 - 8, text=f"{p:.0f}%", fill=color_for(p), font=(FONT_B, 22))
        c.create_text(s / 2, s / 2 + 18, text=f"{warn} {SHORT.get(lim['kind'], lim['kind'])}".strip(),
                      fill=AMBER if warn else FG, font=(FONT_B, 10))
        when = fmt_short(lim["reset"])
        row["sub"].config(text=f"reinicia en {when}" if when else "")

    # estilo compacto: una línea por límite
    COMPACT_W, COMPACT_H = 110, 7

    def _make_compact(self, i):
        row = {"name": tk.Label(self.table, bg=BG, fg=FG, font=(FONT, 9), anchor="w", width=7),
               "bar": tk.Canvas(self.table, width=self.COMPACT_W, height=self.COMPACT_H, bg=BG,
                                highlightthickness=0),
               "pct": tk.Label(self.table, bg=BG, fg=FG, font=(FONT_B, 10), anchor="e", width=6),
               "reset": tk.Label(self.table, bg=BG, fg=MUTED, font=(FONT, 9), anchor="e", width=7)}
        for col, cell in enumerate(row.values()):
            cell.grid(row=i + 1, column=col, sticky="we", padx=(0, 4) if col < 3 else 0, pady=1)
        return row

    def _update_compact(self, row, lim, pace):
        _, color, marker = pace
        p = lim["pct"]
        warn = "⚠ " if color in (AMBER, RED) else ""
        row["name"].config(text=SHORT.get(lim["kind"], lim["kind"][:7]))
        row["pct"].config(text=f"{warn}{p:.0f}%", fg=color if warn else color_for(p))
        row["reset"].config(text=fmt_short(lim["reset"]))
        self._bar(row["bar"], self.COMPACT_W, self.COMPACT_H, p, marker)


class Tray:
    """Icono en la bandeja del sistema y atajo global Ctrl+Alt+U, con Win32 puro (ctypes).
    Corre en su propio hilo con su bucle de mensajes; avisa a la app con on_command(id)."""

    CALLBACK = 0x8000 + 1  # WM_APP + 1
    TOGGLE, FULL, REFRESH, QUIT = 1, 2, 3, 9

    def __init__(self, on_command):
        import ctypes
        from ctypes import wintypes as wt
        self.ct, self.wt = ctypes, wt
        self.on_command = on_command
        self.hwnd = self.icon = self.taskbar_created = None  # llegan mensajes antes de terminar _run
        self.tip = APP_NAME
        self.hotkey = False
        self.ready = threading.Event()

        LRESULT = ctypes.c_ssize_t
        self.WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wt.UINT), ("lpfnWndProc", self.WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                        ("lpszClassName", wt.LPCWSTR)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                        ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                        ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                        ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                        ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wt.HICON)]

        self.WNDCLASSW, self.NOTIFYICONDATAW = WNDCLASSW, NOTIFYICONDATAW
        u32 = self.u32 = ctypes.WinDLL("user32", use_last_error=True)
        s32 = self.s32 = ctypes.WinDLL("shell32", use_last_error=True)
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        P = ctypes.POINTER
        u32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        u32.DefWindowProcW.restype = LRESULT
        u32.RegisterClassW.argtypes = [P(WNDCLASSW)]
        u32.RegisterClassW.restype = wt.ATOM
        u32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
        u32.CreateWindowExW.restype = wt.HWND
        u32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
        u32.LoadImageW.restype = wt.HANDLE
        u32.GetMessageW.argtypes = [P(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
        u32.TranslateMessage.argtypes = [P(wt.MSG)]
        u32.DispatchMessageW.argtypes = [P(wt.MSG)]
        u32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        u32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
        u32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
        u32.CreatePopupMenu.restype = wt.HMENU
        u32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
        u32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND,
                                       wt.LPVOID]
        u32.DestroyMenu.argtypes = [wt.HMENU]
        u32.SetForegroundWindow.argtypes = [wt.HWND]
        u32.GetCursorPos.argtypes = [P(wt.POINT)]
        u32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
        s32.Shell_NotifyIconW.argtypes = [wt.DWORD, P(NOTIFYICONDATAW)]
        self.k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        self.k32.GetModuleHandleW.restype = wt.HMODULE

        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        ct, wt, u32 = self.ct, self.wt, self.u32
        self._proc = self.WNDPROC(self._wndproc)  # referencia viva: si se libera, crash
        hinst = self.k32.GetModuleHandleW(None)
        wc = self.WNDCLASSW(lpfnWndProc=self._proc, hInstance=hinst, lpszClassName="ClaudeUsageTray")
        u32.RegisterClassW(ct.byref(wc))
        self.hwnd = u32.CreateWindowExW(0, "ClaudeUsageTray", APP_NAME, 0, 0, 0, 0, 0, None, None, hinst, None)
        size = u32.GetSystemMetrics(49)  # SM_CXSMICON
        self.icon = u32.LoadImageW(None, str(ICON_ICO), 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        self.taskbar_created = u32.RegisterWindowMessageW("TaskbarCreated")
        self._notify(0)  # NIM_ADD
        self.hotkey = bool(u32.RegisterHotKey(self.hwnd, 1, 0x0001 | 0x0002 | 0x4000, ord("U")))
        self.ready.set()
        msg = wt.MSG()
        while u32.GetMessageW(ct.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(ct.byref(msg))
            u32.DispatchMessageW(ct.byref(msg))

    def _notify(self, action):
        nid = self.NOTIFYICONDATAW()
        nid.cbSize = self.ct.sizeof(nid)
        nid.hWnd, nid.uID = self.hwnd, 1
        nid.uFlags = 0x1 | 0x2 | 0x4  # NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = self.CALLBACK
        nid.hIcon = self.icon
        nid.szTip = self.tip[:127]
        self.s32.Shell_NotifyIconW(action, self.ct.byref(nid))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == self.CALLBACK:
            event = lparam & 0xFFFF
            if event == 0x0202:  # WM_LBUTTONUP
                self.on_command(self.TOGGLE)
            elif event == 0x0205:  # WM_RBUTTONUP
                self._menu()
            return 0
        if msg == 0x0312:  # WM_HOTKEY
            self.on_command(self.TOGGLE)
            return 0
        if msg == self.taskbar_created:  # el Explorador se reinició: volver a poner el icono
            self._notify(0)
            return 0
        if msg == 0x0002:  # WM_DESTROY
            self.u32.PostQuitMessage(0)
            return 0
        return self.u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _menu(self):
        u32 = self.u32
        items = ((self.TOGGLE, "Mostrar / ocultar widget" + ("\tCtrl+Alt+U" if self.hotkey else "")),
                 (self.FULL, "Ventana completa"), (self.REFRESH, "Actualizar ahora"),
                 (None, None), (self.QUIT, "Salir"))
        menu = u32.CreatePopupMenu()
        for cid, text in items:
            u32.AppendMenuW(menu, 0x800 if cid is None else 0, cid or 0, text)  # MF_SEPARATOR / MF_STRING
        pt = self.wt.POINT()
        u32.GetCursorPos(self.ct.byref(pt))
        u32.SetForegroundWindow(self.hwnd)  # necesario para que el menú se cierre al hacer clic fuera
        cmd = u32.TrackPopupMenu(menu, 0x0100 | 0x0002 | 0x0020, pt.x, pt.y, 0, self.hwnd, None)
        u32.PostMessageW(self.hwnd, 0, 0, 0)
        u32.DestroyMenu(menu)
        if cmd:
            self.on_command(cmd)

    def set_tip(self, text):
        self.tip = text
        if self.ready.is_set():
            self._notify(1)  # NIM_MODIFY

    def stop(self):
        if self.ready.is_set():
            self._notify(2)  # NIM_DELETE
            self.u32.PostMessageW(self.hwnd, 0x0010, 0, 0)  # WM_CLOSE


class App:
    def __init__(self, root, server):
        self.root = root
        self.settings = load_settings()
        self.history = History()
        self.events = queue.Queue()
        self.rows, self.limits = [], []
        self.data = self.error = self.updated_at = self.tokens = None
        if self.history.points:  # mostrar el último dato conocido mientras llega el primero
            last = self.history.points[-1]
            self.limits = [{"kind": k, "pct": v[0], "reset": v[1]} for k, v in last["l"].items()]
            self.updated_at = datetime.fromtimestamp(last["t"])
        self.busy, self._job = False, None

        root.withdraw()  # se muestra la vista elegida al final
        root.title(APP_NAME)
        root.configure(bg=BG)
        root.minsize(380, 0)
        root.resizable(False, False)
        if "position" in self.settings:
            root.geometry(self.settings["position"])
        try:
            self._icon = tk.PhotoImage(file=str(ICON_PNG))
            root.iconphoto(True, self._icon)
        except tk.TclError:
            pass
        root.protocol("WM_DELETE_WINDOW", lambda: self.show_mode("widget"))
        root.bind("<F5>", lambda e: self.refresh())

        self.on_top = tk.BooleanVar(value=self.settings["on_top"])
        self.notify = tk.BooleanVar(value=self.settings["notify"])
        self.autostart = tk.BooleanVar(value=get_autostart())
        if self.autostart.get():  # por si la app se movió o pasó de .pyw a .exe
            try:
                set_autostart(True)
            except OSError:
                pass
        self.interval = tk.IntVar(value=self.settings["interval"])
        root.attributes("-topmost", self.on_top.get())

        self._build()
        self.widget = Widget(self)
        self.tray = Tray(lambda cmd: self.events.put(("tray", cmd)))
        self.show_mode(self.settings["mode"])
        if server:
            threading.Thread(target=self._listen, args=(server,), daemon=True).start()
        self.refresh()
        self.scan_tokens()
        self._pump()
        self._tick()

    # ── construcción ──
    def _build(self):
        btn = dict(bg=BG, fg=MUTED, activebackground=CARD, activeforeground=FG, bd=0, cursor="hand2",
                   font=(FONT, 11), padx=6)
        header = tk.Frame(self.root, bg=BG, padx=12, pady=8)
        header.pack(fill="x")
        tk.Label(header, text="Uso de Claude", bg=BG, fg=FG, font=(FONT_B, 12)).pack(side="left")
        self.menu_btn = tk.Button(header, text="⚙", command=self._show_menu, **btn)
        self.menu_btn.pack(side="right")
        tk.Button(header, text="⟳", command=self.refresh, **btn).pack(side="right")

        self.body = tk.Frame(self.root, bg=BG, padx=12)
        self.body.pack(fill="x")
        self.extra = tk.Label(self.root, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", padx=12)

        self.toggle = tk.Button(self.root, command=self._toggle_details, anchor="w",
                                **{**btn, "font": (FONT, 9), "padx": 12})
        self.toggle.pack(fill="x")
        self.details = tk.Frame(self.root, bg=BG, padx=12)
        self._build_details()

        self.status = tk.Label(self.root, bg=BG, fg=MUTED, font=(FONT, 8), anchor="w", padx=12, pady=6,
                               justify="left")
        self.status.pack(fill="x", side="bottom")
        self._apply_details()
        self._build_menu()

    def _build_details(self):
        card = tk.Frame(self.details, bg=CARD, padx=10, pady=8)
        card.pack(fill="x", pady=(0, 8))
        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x")
        tk.Label(top, text="Historial", bg=CARD, fg=FG, font=(FONT_B, 10)).pack(side="left")
        self.range_btns = {}
        for hours, label in ((168, "7 d"), (24, "24 h")):
            b = tk.Button(top, text=label, bd=0, padx=6, cursor="hand2", font=(FONT, 8), bg=CARD,
                          activebackground=TRACK, command=lambda h=hours: self._set_range(h))
            b.pack(side="right")
            self.range_btns[hours] = b
        self.chart = tk.Canvas(card, height=130, bg=CARD, highlightthickness=0)
        self.chart.pack(fill="x", pady=(6, 0))
        self.chart.bind("<Configure>", lambda e: self._draw_chart())

        card = tk.Frame(self.details, bg=CARD, padx=10, pady=8)
        card.pack(fill="x", pady=(0, 8))
        tk.Label(card, text="Tokens en Claude Code (este PC)", bg=CARD, fg=FG, font=(FONT_B, 10),
                 anchor="w").pack(fill="x")
        self.tok_labels = []
        for _ in range(5):
            lbl = tk.Label(card, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w", justify="left")
            lbl.pack(fill="x")
            self.tok_labels.append(lbl)
        self.tok_labels[0].config(text="Analizando registros…")

    def _build_menu(self):
        m = self.menu = tk.Menu(self.root, tearoff=False)
        m.add_command(label="Ver como widget", command=lambda: self.show_mode("widget"))
        m.add_command(label="Ver ventana completa", command=lambda: self.show_mode("full"))
        m.add_command(label="Ocultar en la bandeja   Ctrl+Alt+U", command=self.hide)
        m.add_separator()
        m.add_checkbutton(label="Siempre encima", variable=self.on_top, command=self._on_top_changed)
        m.add_checkbutton(label="Iniciar con Windows", variable=self.autostart, command=self._autostart_changed)
        m.add_checkbutton(label="Notificaciones", variable=self.notify, command=self.save)
        sub = tk.Menu(m, tearoff=False)
        for secs, label in ((30, "30 s"), (60, "1 min"), (120, "2 min"), (300, "5 min")):
            sub.add_radiobutton(label=label, value=secs, variable=self.interval, command=self._interval_changed)
        m.add_cascade(label="Actualizar cada", menu=sub)
        self.style = tk.StringVar(value=self.settings["widget_style"])
        sub = tk.Menu(m, tearoff=False)
        for value, label in (("large", "Grande"), ("rings", "Anillos"), ("compact", "Compacto")):
            sub.add_radiobutton(label=label, value=value, variable=self.style, command=self._style_changed)
        m.add_cascade(label="Estilo del widget", menu=sub)
        self.alpha = tk.DoubleVar(value=self.settings["alpha"])
        sub = tk.Menu(m, tearoff=False)
        for alpha in (1.0, 0.95, 0.85, 0.75, 0.6):
            sub.add_radiobutton(label=f"{alpha:.0%}", value=alpha, variable=self.alpha, command=self._alpha_changed)
        m.add_cascade(label="Opacidad del widget", menu=sub)
        m.add_separator()
        m.add_command(label="Abrir uso en claude.ai", command=lambda: webbrowser.open(USAGE_PAGE))
        m.add_command(label="Exportar historial a CSV", command=self.export_csv)
        m.add_command(label="Probar notificación",
                      command=lambda: toast(APP_NAME, "Las notificaciones funcionan correctamente."))
        m.add_command(label="Abrir carpeta de la app", command=lambda: os.startfile(APP_DIR))
        m.add_separator()
        m.add_command(label="Salir", command=self.quit)

    # ── acciones ──
    def _show_menu(self):
        b = self.menu_btn
        self.menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def _on_top_changed(self):
        self.root.attributes("-topmost", self.on_top.get())
        self.widget.win.attributes("-topmost", self.on_top.get())
        self.save()

    def _style_changed(self):
        old = self.widget
        visible = old.win.state() == "normal"
        right = old.win.winfo_x() + old.win.winfo_width()
        self.settings["widget_style"] = self.style.get()
        old.win.destroy()
        self.widget = Widget(self)
        self.render()
        if visible:
            self.widget.show(anchor_right=right)
        self.save()

    def _alpha_changed(self):
        self.settings["alpha"] = self.alpha.get()
        self.widget.set_alpha(self.alpha.get())
        self.save()

    def _autostart_changed(self):
        try:
            set_autostart(self.autostart.get())
        except OSError as e:
            self.autostart.set(get_autostart())
            self.status.config(text=f"No se pudo cambiar el inicio automático: {e}", fg=AMBER)

    def _interval_changed(self):
        self.save()
        self.refresh()

    def _toggle_details(self):
        self.settings["details"] = not self.settings["details"]
        self._apply_details()
        self.save()

    def _apply_details(self):
        shown = self.settings["details"]
        self.toggle.config(text=("▾ " if shown else "▸ ") + "Historial y tokens")
        if shown:
            self.details.pack(fill="x", after=self.toggle)
        else:
            self.details.pack_forget()
        for hours, b in self.range_btns.items():
            active = hours == self.settings["chart_hours"]
            b.config(fg=FG if active else MUTED, bg=TRACK if active else CARD)
        self._draw_chart()

    def _set_range(self, hours):
        self.settings["chart_hours"] = hours
        self._apply_details()
        self.save()

    def export_csv(self):
        path = DATA_DIR / "historial.csv"
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["fecha", "limite", "porcentaje", "reinicio"])
                for p in self.history.points:
                    for kind, (pct, reset) in p["l"].items():
                        w.writerow([datetime.fromtimestamp(p["t"]).strftime("%Y-%m-%d %H:%M:%S"),
                                    LABELS.get(kind, kind), f"{pct:g}".replace(".", ","),
                                    datetime.fromtimestamp(reset).strftime("%Y-%m-%d %H:%M") if reset else ""])
            os.startfile(path)
        except OSError as e:
            self.status.config(text=f"No se pudo exportar: {e}", fg=AMBER)

    def save(self):
        self.settings.update(on_top=self.on_top.get(), notify=self.notify.get(), interval=self.interval.get())
        try:
            SETTINGS_FILE.write_text(json.dumps(self.settings, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _remember_window(self):
        if self.root.state() == "normal":
            self.settings["position"] = f"+{self.root.winfo_x()}+{self.root.winfo_y()}"

    def show_mode(self, mode):
        """mode: 'widget' (mini flotante) o 'full' (ventana completa)."""
        self._remember_window()
        self.settings["mode"] = mode
        if mode == "full":
            self.widget.win.withdraw()
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self._draw_chart()
        else:
            self.root.withdraw()
            self.widget.show()
        self.save()

    def hide(self):
        """Oculta todo; la app sigue en la bandeja del sistema."""
        self._remember_window()
        self.root.withdraw()
        self.widget.win.withdraw()
        if not self.settings.get("hint_shown"):
            self.settings["hint_shown"] = True
            self.save()
            toast(APP_NAME, "Sigo en la bandeja del sistema, junto al reloj. Haz clic en el icono"
                  + (" o pulsa Ctrl+Alt+U" if self.tray.hotkey else "") + " para volver a verme.")

    def toggle_visible(self):
        visible = self.root.state() == "normal" or self.widget.win.state() == "normal"
        self.hide() if visible else self.show_mode(self.settings["mode"])

    def quit(self):
        self._remember_window()
        self.save()
        self.tray.stop()
        self.root.destroy()

    # ── hilos de fondo ──
    def _listen(self, server):
        while True:
            try:
                conn, _ = server.accept()
                conn.settimeout(2)
                try:
                    msg = conn.recv(16)
                except OSError:
                    msg = b""
                conn.close()
                # "quit" lo envían el instalador y el desinstalador; cualquier otra cosa: mostrarse
                self.events.put(("quit" if msg == b"quit" else "show", None))
            except OSError:
                return

    def refresh(self):
        if not self.busy:
            self.busy = True
            threading.Thread(target=self._fetch_worker, daemon=True).start()
        if self._job:
            self.root.after_cancel(self._job)
        self._job = self.root.after(self.interval.get() * 1000, self.refresh)

    def _fetch_worker(self):
        try:
            self.events.put(("usage", fetch_usage()))
        except RateLimited as e:
            self.events.put(("ratelimit", str(e)))
        except AuthError as e:
            self.events.put(("error", str(e)))
        except Exception as e:  # red caída, respuesta inesperada…
            self.events.put(("error", f"No se pudo consultar el uso ({e.__class__.__name__})."))

    def scan_tokens(self):
        threading.Thread(target=lambda: self.events.put(("tokens", scan_tokens())), daemon=True).start()
        self.root.after(TOKEN_SCAN_MS, self.scan_tokens)

    def _pump(self):
        self.root.after(200, self._pump)  # primero: un error abajo no debe parar el bucle
        while not self.events.empty():
            kind, payload = self.events.get()
            if kind == "usage":
                self.busy = False
                self._on_usage(payload)
            elif kind in ("error", "ratelimit"):
                self.busy = False
                self.error = payload
                self.render()
                if kind == "ratelimit":  # no insistir: esperar antes del próximo intento
                    self.root.after_cancel(self._job)
                    self._job = self.root.after(RATE_LIMIT_WAIT * 1000, self.refresh)
            elif kind == "tokens":
                self.tokens = payload
                self._render_tokens()
            elif kind == "show":
                self.show_mode(self.settings["mode"])
            elif kind == "quit":
                self.quit()
            elif kind == "tray":
                {Tray.TOGGLE: self.toggle_visible, Tray.FULL: lambda: self.show_mode("full"),
                 Tray.REFRESH: self.refresh, Tray.QUIT: self.quit}.get(payload, lambda: None)()

    def _tick(self):
        """Refresca cuentas atrás y marcadores aunque no haya datos nuevos."""
        self.render()
        self.root.after(30_000, self._tick)

    # ── lógica de uso ──
    def _on_usage(self, data):
        limits = parse_limits(data)
        if self.notify.get():
            self._check_alerts(limits)
        self.history.add(limits)
        self.data, self.limits, self.error = data, limits, None
        self.updated_at = datetime.now()
        self.render()
        self._draw_chart()

    def _check_alerts(self, limits):
        notified = self.settings.setdefault("notified", {})
        for lim in limits:
            kind, pct, reset = lim["kind"], lim["pct"], lim["reset"]
            label = LABELS.get(kind, kind)
            prev = self.history.last(kind)
            if prev and prev[0] >= 50 and (pct < prev[0] - 5 or
                                           (reset and prev[1] and reset - prev[1] > 1800)):
                toast(f"{label}: reiniciado", f"Vuelves a tener disponible el {100 - pct:.0f}%.")
            window = round(reset / 3600) if reset else 0
            last_window, last_level = notified.get(kind, [None, 0])
            if last_window != window:
                last_level = 0
            level = max((t for t in THRESHOLDS if pct >= t), default=0)
            if level > last_level:
                if level >= 100:
                    toast(f"{label}: límite alcanzado", fmt_reset(reset).capitalize() or "Espera al reinicio.")
                else:
                    when = fmt_reset(reset)
                    toast(f"{label}: {pct:.0f}% usado",
                          f"Te queda un {100 - pct:.0f}%." + (f" {when.capitalize()}." if when else ""))
            notified[kind] = [window, max(level, last_level)]
        self.save()

    def render(self):
        limits = self.limits
        while len(self.rows) < len(limits):
            row = LimitRow(self.body)
            row.frame.pack(fill="x", pady=(0, 8))
            self.rows.append(row)
        for row, lim in zip(self.rows, limits):
            row.update(lim, self.history)
        for row in self.rows[len(limits):]:
            row.frame.destroy()
        del self.rows[len(limits):]

        extra = (self.data or {}).get("extra_usage") or {}
        if extra.get("is_enabled") and extra.get("utilization") is not None:
            self.extra.config(text=f"Créditos extra: {extra['utilization']:.0f}% del límite mensual")
            self.extra.pack(fill="x", after=self.body, pady=(0, 6))
        else:
            self.extra.pack_forget()

        if limits:
            worst = max(lim["pct"] for lim in limits)
            self.root.title("Claude " + " · ".join(f"{lim['pct']:.0f}%" for lim in limits))
            note = "Has alcanzado un límite." if worst >= 100 else \
                f"Margen: {100 - worst:.0f}% hasta el límite más cercano."
        elif self.updated_at:
            note = "La API no devolvió límites para esta cuenta."
        else:
            note = "Consultando…"

        self.widget.render(limits, self.history, self.error)
        tip = " · ".join(f"{SHORT.get(lim['kind'], lim['kind'])} {lim['pct']:.0f}%" for lim in limits)
        self.tray.set_tip(f"Claude · {tip}" if tip else APP_NAME)

        if self.error:
            stale = f"\nÚltimo dato: {self.updated_at:%H:%M}" if self.updated_at else ""
            self.status.config(text=self.error + stale, fg=AMBER)
        else:
            stamp = f"\nActualizado {self.updated_at:%H:%M:%S} · la línea blanca marca el tiempo transcurrido" \
                if self.updated_at else ""
            self.status.config(text=note + stamp, fg=MUTED)

    def _draw_chart(self):
        c = self.chart
        if not self.settings["details"]:
            return
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50:
            return
        left, right, top, bottom = 32, w - 6, 18, h - 16
        hours = self.settings["chart_hours"]
        now = time.time()
        start = now - hours * 3600
        y_of = lambda p: bottom - (bottom - top) * min(p, 100) / 100
        for v in (0, 50, 100):
            c.create_line(left, y_of(v), right, y_of(v), fill=TRACK)
            c.create_text(left - 4, y_of(v), text=f"{v}%", anchor="e", fill=MUTED, font=(FONT, 7))
        for frac, anchor in ((0, "sw"), (0.5, "s"), (1, "se")):
            t = datetime.fromtimestamp(start + hours * 3600 * frac)
            label = f"{t:%H:%M}" if hours <= 24 else f"{DAYS[t.weekday()]} {t:%d}"
            c.create_text(left + (right - left) * frac, h - 2, text=label, anchor=anchor, fill=MUTED, font=(FONT, 7))
        gap = max(600, 3 * self.interval.get())
        legend_x = right
        for lim in reversed(self.limits):
            kind = lim["kind"]
            color = SERIES.get(kind, MUTED)
            seg, prev_t = [], None
            for t, p in self.history.series(kind, start):
                if prev_t is not None and t - prev_t > gap:
                    if len(seg) >= 4:
                        c.create_line(*seg, fill=color, width=2)
                    seg = []
                seg += [left + (right - left) * (t - start) / (hours * 3600), y_of(p)]
                prev_t = t
            if len(seg) >= 4:
                c.create_line(*seg, fill=color, width=2)
            elif len(seg) == 2:
                c.create_oval(seg[0] - 2, seg[1] - 2, seg[0] + 2, seg[1] + 2, fill=color, outline="")
            name = "Sesión" if kind == "session" else LABELS.get(kind, kind).split("·")[-1].strip().capitalize()
            item = c.create_text(legend_x, 2, text=f"● {name}", anchor="ne", fill=color, font=(FONT, 7))
            legend_x = c.bbox(item)[0] - 8

    def _render_tokens(self):
        s = self.tokens
        if not s or not s["week"]:
            self.tok_labels[0].config(text="Sin actividad de Claude Code en los últimos 7 días.")
            return
        t, wk = s["today"], s["week"]
        top = lambda d: " · ".join(f"{name} {100 * v / sum(d.values()):.0f}%"
                                   for name, v in sorted(d.items(), key=lambda kv: -kv[1])[:3]
                                   if v / sum(d.values()) >= 0.005)
        models = defaultdict(int)
        for m, v in s["models"].items():
            models[pretty_model(m)] += v
        lines = [
            f"Hoy: {fmt_num(sum(t.values()))}   ·   7 días: {fmt_num(sum(wk.values()))}",
            f"Hoy → entrada {fmt_num(t['input'])} · salida {fmt_num(t['output'])} · "
            f"caché {fmt_num(t['cache_write'])} escr. / {fmt_num(t['cache_read'])} lect.",
            f"Modelos (7 d): {top(models)}",
            f"Proyectos (7 d): {top(s['projects'])}",
            "La lectura de caché cuenta mucho menos para el límite que la salida.",
        ]
        for lbl, text in zip(self.tok_labels, lines):
            lbl.config(text=text)


def _folder(csidl):
    import ctypes
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return Path(buf.value)


def _reg_value(key, name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return str(winreg.QueryValueEx(k, name)[0])
    except OSError:
        return ""


def uninstall(silent):
    """Quita accesos directos, inicio automático, registro y archivos de esta instalación."""
    from tkinter import messagebox
    if not silent:
        r = tk.Tk()
        r.withdraw()
        ok = messagebox.askyesno(APP_NAME, "¿Desinstalar Claude Usage?\n\nSe quitarán los accesos directos, "
                                           "el inicio automático y el historial de uso.", parent=r)
        r.destroy()
        if not ok:
            return
    try:  # cerrar la instancia abierta
        with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=1) as c:
            c.sendall(b"quit")
        time.sleep(1.5)
    except OSError:
        pass

    if str(APP_FILE).lower() in _reg_value(RUN_KEY, APP_NAME).lower():
        set_autostart(False)
    for key in (AUMID_KEY, UNINSTALL_KEY if Path(_reg_value(UNINSTALL_KEY, "InstallLocation") or "?")
                == APP_DIR else None):
        if key:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except OSError:
                pass
    for folder in (_folder(0x02), _folder(0x10)):  # CSIDL_PROGRAMS, CSIDL_DESKTOPDIRECTORY
        try:
            (folder / f"{APP_NAME}.lnk").unlink()
        except OSError:
            pass

    # los archivos se borran cuando este proceso ya ha terminado; solo los nuestros, y la
    # carpeta solo si queda vacía
    steps = ["ping -n 3 127.0.0.1 >nul", f'rmdir /s /q "{DATA_DIR}"']
    if FROZEN:
        steps.append(f'del /f /q "{APP_FILE}"')
    steps.append(f'rmdir "{APP_DIR}"')
    subprocess.Popen(f'cmd /c {" & ".join(steps)}', creationflags=subprocess.CREATE_NO_WINDOW, cwd=Path.home())
    if not silent:
        r = tk.Tk()
        r.withdraw()
        messagebox.showinfo(APP_NAME, "Claude Usage se ha desinstalado.", parent=r)
        r.destroy()


def main():
    if "--uninstall" in sys.argv:
        uninstall("--silent" in sys.argv)
        return
    ensure_icons()
    if "--make-icons" in sys.argv:
        return
    server = claim_single_instance()
    if server is None:
        return
    register_app_id()
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root, server)
    root.mainloop()


if __name__ == "__main__":
    main()
