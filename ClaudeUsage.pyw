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
import re
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
APP_VERSION = "2.5.1"
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
CLAUDE_CODE_PAGE = "https://claude.com/claude-code"
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
            "check_updates": True,
            "mode": "widget", "alpha": 1.0, "widget_style": "large", "lang": None}

LEGACY_KEYS = {
    "five_hour": "session",
    "seven_day": "weekly_all",
    "seven_day_opus": "weekly_opus",
    "seven_day_sonnet": "weekly_sonnet",
}

# ───────────────────────── idiomas ─────────────────────────

STRINGS = {
    "es": {
        "days": ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"],
        "label.session": "Sesión (5 h)", "label.weekly_all": "Semanal · todos los modelos",
        "label.weekly_opus": "Semanal · Opus", "label.weekly_sonnet": "Semanal · Sonnet",
        "short.session": "Sesión", "short.weekly_all": "Semana",
        "short.weekly_opus": "Opus", "short.weekly_sonnet": "Sonnet",
        "err.no_session": "No estás conectado a Claude. Abre «Cuenta de Claude…» para conectarte.",
        "err.token": "La sesión de Claude ha caducado. Abre «Cuenta de Claude…» para reconectar.",
        "err.ratelimit": "Anthropic limita las consultas por ahora; reintento en {min} min.",
        "err.fetch": "No se pudo consultar el uso ({name}).",
        "when.today": "a las {time}", "when.other": "el {day} a las {time}",
        "reset.done": "reiniciado", "reset.in": "reinicia en {left} ({stamp})", "reset.short_done": "ya",
        "pace.limit": "Límite alcanzado: espera al reinicio", "pace.idle": "Sin consumo en la última hora",
        "pace.unused": "Aún sin uso en esta ventana", "pace.day": "%/día",
        "pace.speed": "Ritmo ≈ {value} {unit}", "pace.hit": "{speed} → límite {when}",
        "pace.ok": "{speed} → no llegarás al límite",
        "row.left": "Te queda {pct}%", "widget.left": "Quedan {pct}%", "widget.resets": "reinicia en {when}",
        "loading": "Consultando…",
        "tray.toggle": "Mostrar / ocultar widget", "tray.full": "Ventana completa",
        "tray.refresh": "Actualizar ahora", "quit": "Salir",
        "title": "Uso de Claude", "history": "Historial", "tokens.title": "Tokens en Claude Code (este PC)",
        "tokens.scanning": "Analizando registros…", "details": "Historial y tokens",
        "menu.widget": "Ver como widget", "menu.full": "Ver ventana completa",
        "menu.hide": "Ocultar en la bandeja   Ctrl+Alt+U", "menu.on_top": "Siempre encima",
        "menu.autostart": "Iniciar con Windows", "menu.notify": "Notificaciones",
        "menu.interval": "Actualizar cada", "menu.style": "Estilo del widget",
        "style.large": "Grande", "style.rings": "Anillos", "style.compact": "Compacto",
        "menu.alpha": "Opacidad del widget", "menu.lang": "Idioma / Language",
        "menu.open_web": "Abrir uso en claude.ai", "menu.export": "Exportar historial a CSV",
        "menu.test": "Probar notificación", "test.body": "Las notificaciones funcionan correctamente.",
        "menu.folder": "Abrir carpeta de la app",
        "err.autostart": "No se pudo cambiar el inicio automático: {e}", "err.export": "No se pudo exportar: {e}",
        "csv.file": "historial.csv", "csv.header": ["fecha", "limite", "porcentaje", "reinicio"],
        "hint": "Sigo en la bandeja del sistema, junto al reloj. Haz clic en el icono{hotkey} para volver a verme.",
        "hint.hotkey": " o pulsa Ctrl+Alt+U",
        "alert.reset": "{label}: reiniciado", "alert.reset_body": "Vuelves a tener disponible el {pct}%.",
        "alert.limit": "{label}: límite alcanzado", "alert.wait": "Espera al reinicio.",
        "alert.used": "{label}: {pct}% usado", "alert.left": "Te queda un {pct}%.",
        "extra": "Créditos extra: {pct}% del límite mensual", "status.limit": "Has alcanzado un límite.",
        "status.margin": "Margen: {pct}% hasta el límite más cercano.",
        "status.no_limits": "La API no devolvió límites para esta cuenta.",
        "status.last": "Último dato: {time}",
        "status.updated": "Actualizado {time} · la línea blanca marca el tiempo transcurrido",
        "tokens.none": "Sin actividad de Claude Code en los últimos 7 días.",
        "tokens.totals": "Hoy: {today}   ·   7 días: {week}",
        "tokens.split": "Hoy → entrada {input} · salida {output} · caché {write} escr. / {read} lect.",
        "tokens.models": "Modelos (7 d): {top}", "tokens.projects": "Proyectos (7 d): {top}",
        "tokens.note": "La lectura de caché cuenta mucho menos para el límite que la salida.",
        "uninstall.ask": "¿Desinstalar Claude Usage?\n\nSe quitarán los accesos directos, el inicio automático "
                         "y el historial de uso.",
        "uninstall.done": "Claude Usage se ha desinstalado.",
        "menu.account": "Cuenta de Claude…", "status.account": "Cuenta: {account}",
        "menu.check_updates": "Buscar actualizaciones", "menu.auto_updates": "Avisar de nuevas versiones",
        "update.available": "⬆  Nueva versión {version} disponible · Actualizar",
        "update.ask_title": "Actualizar Claude Usage",
        "update.ask": "¿Instalar la versión {version} ahora? (tienes la {current})\n\nLa app se cerrará un "
                      "momento y volverá a abrirse. Tu historial y tus ajustes se conservan.\n\nNovedades:\n{notes}",
        "update.downloading": "Descargando actualización… {pct}%",
        "update.installing": "Instalando la actualización…",
        "update.failed": "No se pudo actualizar: {e}",
        "update.latest": "Tienes la última versión ({version}).",
        "update.check_failed": "No se pudo comprobar si hay actualizaciones.",
        "update.toast_title": "Claude Usage {version} disponible",
        "update.toast_body": "Abre la app y pulsa «Actualizar» para instalarla en un clic.",
        "update.done": "Claude Usage se ha actualizado a la versión {version}.",
        "cta.connect": "⚠  Sin conexión con Claude · Conectar →",
        "connect.title": "Conectar con Claude", "connect.heading": "Conecta tu cuenta de Claude",
        "connect.intro": "Claude Usage lee tu uso a través de Claude Code, la herramienta oficial de Anthropic. "
                         "Solo tienes que conectarlo una vez.",
        "connect.checking": "Comprobando…",
        "step.install": "Instalar Claude Code", "step.install.ok": "Claude Code instalado",
        "step.install.version": "Versión {version}",
        "step.install.desktop": "incluido en la app de escritorio de Claude",
        "step.install.wsl": "Instalado en WSL ({distro})",
        "step.install.found": "Se ha encontrado su sesión en este PC",
        "step.login.found": "Sesión de Claude Code encontrada en este PC",
        "step.login.wsl": "Sesión de Claude Code en WSL ({distro})",
        "btn.diag": "Copiar diagnóstico", "diag.copied": "Diagnóstico copiado ✓",
        "step.install.detail": "Es gratuito y se instala en un minuto.",
        "step.install.wait": "Sigue los pasos de la ventana de instalación. Esto se actualizará solo.",
        "btn.install": "Instalar",
        "step.login": "Iniciar sesión en Claude", "step.login.ok": "Sesión iniciada",
        "step.login.detail": "Se abrirá el navegador para que autorices el acceso con tu cuenta de Claude "
                             "(Pro, Max, Team…).",
        "step.login.wait": "Completa el inicio de sesión en el navegador. Esto se actualizará solo.",
        "step.login.need_install": "Primero instala Claude Code.",
        "step.login.api": "Claude Code usa una clave de API",
        "step.login.api_detail": "Los límites de uso solo existen con una suscripción de Claude. "
                                 "Inicia sesión con tu cuenta de Claude.",
        "btn.login": "Conectar", "btn.switch": "Cambiar de cuenta",
        "step.usage": "Leer tu uso", "step.usage.ok": "Uso conectado",
        "connect.tips": "Todo listo. Arrastra el widget a donde quieras · doble clic: ventana completa · "
                        "clic derecho: ajustes e idioma · Ctrl+Alt+U: mostrar u ocultar.",
        "btn.start": "Empezar", "btn.close": "Cerrar",
    },
    "en": {
        "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "label.session": "Session (5 h)", "label.weekly_all": "Weekly · all models",
        "label.weekly_opus": "Weekly · Opus", "label.weekly_sonnet": "Weekly · Sonnet",
        "short.session": "Session", "short.weekly_all": "Week",
        "short.weekly_opus": "Opus", "short.weekly_sonnet": "Sonnet",
        "err.no_session": "Not connected to Claude. Open “Claude account…” to connect.",
        "err.token": "Your Claude session expired. Open “Claude account…” to reconnect.",
        "err.ratelimit": "Anthropic is rate-limiting requests; retrying in {min} min.",
        "err.fetch": "Couldn't fetch usage ({name}).",
        "when.today": "at {time}", "when.other": "on {day} at {time}",
        "reset.done": "reset", "reset.in": "resets in {left} ({stamp})", "reset.short_done": "now",
        "pace.limit": "Limit reached: wait for the reset", "pace.idle": "No usage in the last hour",
        "pace.unused": "No usage yet in this window", "pace.day": "%/day",
        "pace.speed": "Pace ≈ {value} {unit}", "pace.hit": "{speed} → limit {when}",
        "pace.ok": "{speed} → you won't hit the limit",
        "row.left": "{pct}% left", "widget.left": "{pct}% left", "widget.resets": "resets in {when}",
        "loading": "Loading…",
        "tray.toggle": "Show / hide widget", "tray.full": "Full window",
        "tray.refresh": "Refresh now", "quit": "Quit",
        "title": "Claude usage", "history": "History", "tokens.title": "Claude Code tokens (this PC)",
        "tokens.scanning": "Scanning logs…", "details": "History and tokens",
        "menu.widget": "Show as widget", "menu.full": "Show full window",
        "menu.hide": "Hide to tray   Ctrl+Alt+U", "menu.on_top": "Always on top",
        "menu.autostart": "Start with Windows", "menu.notify": "Notifications",
        "menu.interval": "Refresh every", "menu.style": "Widget style",
        "style.large": "Large", "style.rings": "Rings", "style.compact": "Compact",
        "menu.alpha": "Widget opacity", "menu.lang": "Idioma / Language",
        "menu.open_web": "Open usage on claude.ai", "menu.export": "Export history to CSV",
        "menu.test": "Test notification", "test.body": "Notifications are working.",
        "menu.folder": "Open app folder",
        "err.autostart": "Couldn't change start with Windows: {e}", "err.export": "Couldn't export: {e}",
        "csv.file": "history.csv", "csv.header": ["date", "limit", "percent", "reset"],
        "hint": "I'm still in the system tray, next to the clock. Click the icon{hotkey} to bring me back.",
        "hint.hotkey": " or press Ctrl+Alt+U",
        "alert.reset": "{label}: reset", "alert.reset_body": "You have {pct}% available again.",
        "alert.limit": "{label}: limit reached", "alert.wait": "Wait for the reset.",
        "alert.used": "{label}: {pct}% used", "alert.left": "{pct}% left.",
        "extra": "Extra credits: {pct}% of the monthly limit", "status.limit": "You've reached a limit.",
        "status.margin": "Headroom: {pct}% to the nearest limit.",
        "status.no_limits": "The API returned no limits for this account.",
        "status.last": "Last data: {time}",
        "status.updated": "Updated {time} · the white line marks elapsed time",
        "tokens.none": "No Claude Code activity in the last 7 days.",
        "tokens.totals": "Today: {today}   ·   7 days: {week}",
        "tokens.split": "Today → input {input} · output {output} · cache {write} write / {read} read",
        "tokens.models": "Models (7 d): {top}", "tokens.projects": "Projects (7 d): {top}",
        "tokens.note": "Cache reads count much less toward the limit than output.",
        "uninstall.ask": "Uninstall Claude Usage?\n\nThe shortcuts, start with Windows and usage history "
                         "will be removed.",
        "uninstall.done": "Claude Usage has been uninstalled.",
        "menu.account": "Claude account…", "status.account": "Account: {account}",
        "menu.check_updates": "Check for updates", "menu.auto_updates": "Notify about new versions",
        "update.available": "⬆  Version {version} available · Update",
        "update.ask_title": "Update Claude Usage",
        "update.ask": "Install version {version} now? (you have {current})\n\nThe app will close for a moment "
                      "and reopen. Your history and settings are kept.\n\nWhat's new:\n{notes}",
        "update.downloading": "Downloading update… {pct}%",
        "update.installing": "Installing the update…",
        "update.failed": "Update failed: {e}",
        "update.latest": "You're on the latest version ({version}).",
        "update.check_failed": "Couldn't check for updates.",
        "update.toast_title": "Claude Usage {version} available",
        "update.toast_body": "Open the app and click “Update” to install it in one click.",
        "update.done": "Claude Usage was updated to version {version}.",
        "cta.connect": "⚠  Not connected to Claude · Connect →",
        "connect.title": "Connect to Claude", "connect.heading": "Connect your Claude account",
        "connect.intro": "Claude Usage reads your usage through Claude Code, Anthropic's official tool. "
                         "You only need to connect once.",
        "connect.checking": "Checking…",
        "step.install": "Install Claude Code", "step.install.ok": "Claude Code installed",
        "step.install.version": "Version {version}",
        "step.install.desktop": "bundled with the Claude desktop app",
        "step.install.wsl": "Installed in WSL ({distro})",
        "step.install.found": "Its session was found on this PC",
        "step.login.found": "Claude Code session found on this PC",
        "step.login.wsl": "Claude Code session in WSL ({distro})",
        "btn.diag": "Copy diagnostics", "diag.copied": "Diagnostics copied ✓",
        "step.install.detail": "It's free and installs in a minute.",
        "step.install.wait": "Follow the steps in the installer window. This will update by itself.",
        "btn.install": "Install",
        "step.login": "Sign in to Claude", "step.login.ok": "Signed in",
        "step.login.detail": "Your browser will open so you can authorize access with your Claude account "
                             "(Pro, Max, Team…).",
        "step.login.wait": "Finish signing in in your browser. This will update by itself.",
        "step.login.need_install": "Install Claude Code first.",
        "step.login.api": "Claude Code is using an API key",
        "step.login.api_detail": "Usage limits only apply to Claude subscriptions. Sign in with your Claude account.",
        "btn.login": "Connect", "btn.switch": "Switch account",
        "step.usage": "Read your usage", "step.usage.ok": "Usage connected",
        "connect.tips": "All set. Drag the widget anywhere · double-click: full window · "
                        "right-click: settings and language · Ctrl+Alt+U: show or hide.",
        "btn.start": "Get started", "btn.close": "Close",
    },
}
LANGUAGES = (("es", "Español"), ("en", "English"))


def system_lang():
    """'es' si Windows está en español; si no, 'en'."""
    try:
        import ctypes
        return "es" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x0A else "en"
    except Exception:
        return "en"


LANG = system_lang()


def tr(key, **kw):
    text = STRINGS[LANG].get(key, STRINGS["es"][key])
    return text.format(**kw) if kw else text


def label(kind):
    return STRINGS[LANG].get(f"label.{kind}", kind.replace("_", " ").capitalize())


def short(kind):
    return STRINGS[LANG].get(f"short.{kind}", kind[:7])


def day(i):
    return tr("days")[i]

# alto contraste: fondo casi negro, texto blanco, colores de estado saturados
BG, CARD, FG, MUTED, TRACK, BORDER = "#111114", "#202026", "#ffffff", "#c8c8d2", "#34343e", "#4a4a55"
GREEN, AMBER, RED, ACCENT = "#34d17a", "#ffb224", "#ff5a52", "#e8825f"
SERIES = {"session": ACCENT, "weekly_all": "#6ea8fe", "weekly_opus": "#b48ead", "weekly_sonnet": "#88c0d0"}
FONT, FONT_B = "Segoe UI", "Segoe UI Semibold"


# ───────────────────────── datos ─────────────────────────

class AuthError(Exception):
    def __init__(self, key, **kw):
        super().__init__(key)
        self.key, self.kw = key, kw


class RateLimited(AuthError):
    pass


def fetch_usage():
    try:
        oauth = json.loads(CREDENTIALS.read_text(encoding="utf-8"))["claudeAiOauth"]
    except (OSError, KeyError, ValueError):
        raise AuthError("err.no_session")
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {oauth['accessToken']}",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": f"claude-usage-tracker/{APP_VERSION}",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthError("err.token")
        if e.code == 429:
            raise RateLimited("err.ratelimit", min=RATE_LIMIT_WAIT // 60)
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
        return tr("when.today", time=f"{local:%H:%M}")
    return tr("when.other", day=day(local.weekday()), time=f"{local:%H:%M}")


def fmt_reset(epoch):
    if not epoch:
        return ""
    secs = int(epoch - time.time())
    if secs <= 0:
        return tr("reset.done")
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    left = f"{d} d {h} h" if d else f"{h} h {m:02d} min" if h else f"{m} min"
    local = datetime.fromtimestamp(epoch)
    stamp = f"{day(local.weekday())} {local:%H:%M}" if d else f"{local:%H:%M}"
    return tr("reset.in", left=left, stamp=stamp)


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
        return tr("pace.limit"), RED, marker
    # sesión: ritmo de la última hora (reacciona rápido); semanal: media de la ventana, porque
    # extrapolar una hora intensa a varios días daría avisos exagerados
    rate = history.recent_rate(lim["kind"], reset) if total <= 86400 else None
    if rate is None and elapsed > 300:
        rate = pct / elapsed
    if not rate or rate <= 0:
        return tr("pace.idle" if pct else "pace.unused"), MUTED, marker
    unit, factor = ("%/h", 3600) if total <= 86400 else (tr("pace.day"), 86400)
    speed = tr("pace.speed", value=f"{rate * factor:.1f}", unit=unit)
    eta = now + (100 - pct) / rate
    if eta < reset:
        return tr("pace.hit", speed=speed, when=fmt_when(eta)), AMBER, marker
    return tr("pace.ok", speed=speed), GREEN, marker


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
        self.title.config(text=label(lim["kind"]))
        self.pct.config(text=f"{self.value:.0f}%", fg=color_for(self.value))
        reset = fmt_reset(lim["reset"])
        self.sub.config(text=tr("row.left", pct=f"{max(0.0, 100 - self.value):.0f}") + (f" · {reset}" if reset else ""))
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


def fmt_short(epoch):
    if not epoch:
        return ""
    secs = int(epoch - time.time())
    if secs <= 0:
        return tr("reset.short_done")
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

        head = self.head = tk.Frame(self.inner, bg=BG)
        head.pack(fill="x")
        big = self.style != "compact"
        self.dot = tk.Label(head, text="●", bg=BG, fg=ACCENT, font=(FONT, 9 if big else 8))
        self.dot.pack(side="left")
        tk.Label(head, text="Claude", bg=BG, fg=FG, font=(FONT_B, 11 if big else 9)).pack(side="left", padx=(3, 0))
        btn = dict(bg=BG, fg=MUTED, activebackground=CARD, activeforeground=FG, bd=0, cursor="hand2",
                   font=(FONT, 11 if big else 9), padx=6, pady=0)
        tk.Button(head, text="—", command=app.hide, **btn).pack(side="right")
        tk.Button(head, text="⤢", command=lambda: app.show_mode("full"), **btn).pack(side="right")

        # botón con aspecto de enlace (los botones no inician el arrastre)
        self.cta = tk.Button(self.inner, text=tr("cta.connect"), command=app.open_connect, bd=0, cursor="hand2",
                             bg=BG, fg=AMBER, activebackground=CARD, activeforeground=AMBER, anchor="w",
                             font=(FONT_B, 10 if big else 9), padx=0)
        self.update_btn = tk.Button(self.inner, command=app.start_update, bd=0, cursor="hand2", bg=BG, fg=ACCENT,
                                    activebackground=CARD, activeforeground=ACCENT, anchor="w",
                                    font=(FONT_B, 10 if big else 9), padx=0)
        self.table = tk.Frame(self.inner, bg=BG)
        self.table.pack(fill="x", pady=(6 if big else 4, 0))
        self.placeholder = tk.Label(self.table, text=tr("loading"), bg=BG, fg=MUTED, font=(FONT, 10))
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
    def render(self, limits, history, error, update_text=None):
        if update_text:
            self.update_btn.config(text=update_text)
            self.update_btn.pack(fill="x", before=self.table, pady=(6, 0))
        else:
            self.update_btn.pack_forget()
        self.dot.config(fg=AMBER if error else ACCENT)
        disconnected = bool(error and error[0] in AUTH_ERRORS)
        if disconnected:
            self.cta.pack(fill="x", after=self.head, pady=(6, 0))
        else:
            self.cta.pack_forget()
        self.placeholder.config(text="" if disconnected else tr("loading"))
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
        row["name"].config(text=short(lim["kind"]).upper())
        row["pct"].config(text=f"{p:.0f}%", fg=color_for(p))
        when = fmt_short(lim["reset"])
        row["sub"].config(text=tr("widget.left", pct=f"{max(0.0, 100 - p):.0f}")
                          + (f"  ·  {tr('widget.resets', when=when)}" if when else ""))
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
        c.create_text(s / 2, s / 2 + 18, text=f"{warn} {short(lim['kind'])}".strip(),
                      fill=AMBER if warn else FG, font=(FONT_B, 10))
        when = fmt_short(lim["reset"])
        row["sub"].config(text=tr("widget.resets", when=when) if when else "")

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
        row["name"].config(text=short(lim["kind"]))
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
        items = ((self.TOGGLE, tr("tray.toggle") + ("\tCtrl+Alt+U" if self.hotkey else "")),
                 (self.FULL, tr("tray.full")), (self.REFRESH, tr("tray.refresh")),
                 (None, None), (self.QUIT, tr("quit")))
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


# ───────────────────────── conexión con Claude ─────────────────────────

AUTH_ERRORS = ("err.no_session", "err.token")


ENV_KEYS = ((winreg.HKEY_CURRENT_USER, "Environment"),
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"))


def _env(name):
    """Variable de entorno actual o, si no está, la guardada en el registro (p. ej. recién creada)."""
    if os.environ.get(name):
        return os.environ[name]
    for hive, key in ENV_KEYS:
        try:
            with winreg.OpenKey(hive, key) as k:
                return os.path.expandvars(winreg.QueryValueEx(k, name)[0])
        except OSError:
            pass
    return ""


def _search_path():
    """PATH del proceso más el del registro, por si Claude Code se acaba de instalar."""
    paths = [os.environ.get("PATH", "")]
    for hive, key in ENV_KEYS:
        try:
            with winreg.OpenKey(hive, key) as k:
                paths.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
        except OSError:
            pass
    return os.pathsep.join(paths)


def claude_candidates():
    """Ubicaciones conocidas de Claude Code en Windows, por orden de preferencia."""
    home = Path.home()
    local, roaming = Path(os.environ.get("LOCALAPPDATA", "")), Path(os.environ.get("APPDATA", ""))
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    out = [local / "Microsoft" / "WinGet" / "Links" / "claude.exe",  # winget
           home / ".local" / "bin" / "claude.exe",                    # instalador nativo
           roaming / "npm" / "claude.cmd",                            # npm
           program_files / "nodejs" / "claude.cmd",
           home / "scoop" / "shims" / "claude.exe",
           home / ".bun" / "bin" / "claude.exe",
           local / "Volta" / "bin" / "claude.exe"]
    if _env("NVM_SYMLINK"):
        out.append(Path(_env("NVM_SYMLINK")) / "claude.cmd")
    # copia incluida en la app de escritorio de Claude (pestaña Code): la versión más reciente
    bundled = sorted((roaming / "Claude" / "claude-code").glob("*/*/claude.exe"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    return out + bundled


def find_claude():
    """Ruta de Claude Code (CLI o la incluida en la app de escritorio), o None."""
    import shutil
    found = shutil.which("claude", path=_search_path())
    if found:
        return found
    return next((str(c) for c in claude_candidates() if c.exists()), None)


def claude_source(claude):
    return "desktop" if claude and r"\Claude\claude-code" in claude else "cli"


_wsl_cache = [0.0, None]


def wsl_credentials():
    """(ruta, distro) de unas credenciales de Claude Code dentro de WSL, o None. Se cachea 2 min
    porque consultar WSL puede arrancar la distribución."""
    if time.time() - _wsl_cache[0] < 120:
        return _wsl_cache[1]
    result = None
    try:
        out = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=15,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        distros = [d.strip() for d in out.stdout.decode("utf-16-le", "ignore").replace("\0", "").splitlines()
                   if d.strip()]
        for distro in distros:
            root = Path(rf"\\wsl.localhost\{distro}")
            hits = list((root / "home").glob("*/.claude/.credentials.json"))
            hits += [p for p in (root / "root" / ".claude" / ".credentials.json",) if p.exists()]
            if hits:
                result = (hits[0], distro)
                break
    except (OSError, subprocess.TimeoutExpired):
        pass
    _wsl_cache[:] = [time.time(), result]
    return result


def find_credentials(status=None):
    """(ruta de .credentials.json, distro WSL o None). Si no hay ninguna, la ruta por defecto."""
    dirs = [Path(d) for d in (_env("CLAUDE_CONFIG_DIR"), (status or {}).get("configDirectory")) if d]
    dirs.append(Path.home() / ".claude")
    for d in dirs:
        if (d / ".credentials.json").exists():
            return d / ".credentials.json", None
    return wsl_credentials() or (Path.home() / ".claude" / ".credentials.json", None)


def auth_status(claude):
    """`claude auth status --json`: {loggedIn, authMethod, email, orgName, subscriptionType, ...} o None."""
    try:
        out = subprocess.run([claude, "auth", "status", "--json"], capture_output=True, timeout=30,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        return json.loads(out.stdout.decode("utf-8", "replace"))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def claude_version(claude):
    try:
        out = subprocess.run([claude, "--version"], capture_output=True, timeout=30,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        return out.stdout.decode("utf-8", "replace").split()[0]  # "2.1.280 (Claude Code)"
    except (OSError, IndexError, subprocess.TimeoutExpired):
        return ""


def check_connection():
    claude = find_claude()
    status = auth_status(claude) if claude else None
    creds, wsl = find_credentials(status)
    return {"claude": claude, "source": claude_source(claude), "status": status,
            "version": claude_version(claude) if claude else "", "creds": creds, "wsl": wsl,
            "creds_ok": creds.exists()}


def diagnostics(info):
    """Texto para pegar en una incidencia: qué se ha buscado y qué se ha encontrado (sin datos de la cuenta)."""
    import platform
    status = (info or {}).get("status") or {}
    lines = [f"Claude Usage {APP_VERSION} · Windows {platform.version()} · {'exe' if FROZEN else 'py'}",
             f"claude: {(info or {}).get('claude')} ({(info or {}).get('source')}) {(info or {}).get('version')}",
             f"auth status: loggedIn={status.get('loggedIn')} authMethod={status.get('authMethod')}",
             f"credentials: {(info or {}).get('creds')} exists={(info or {}).get('creds_ok')} "
             f"wsl={(info or {}).get('wsl')}",
             f"CLAUDE_CONFIG_DIR={_env('CLAUDE_CONFIG_DIR') or '-'}",
             "candidates:"]
    lines += [f"  [{'x' if c.exists() else ' '}] {c}" for c in claude_candidates()]
    lines.append("PATH (claude/node/npm):")
    lines += [f"  {p}" for p in _search_path().split(os.pathsep)
              if any(k in p.lower() for k in ("claude", "node", "npm", "bun", "scoop", "volta", ".local"))]
    return "\n".join(lines)


def account_text(status):
    plan = (status.get("subscriptionType") or "").capitalize()
    return " · ".join(x for x in (status.get("email"), status.get("orgName"), plan) if x)


def run_in_console(command):
    """Abre una consola visible; solo se queda abierta si el comando falla, para poder leer el error."""
    subprocess.Popen(f'cmd /c "{command} || pause"', creationflags=subprocess.CREATE_NEW_CONSOLE)


class StepRow:
    ICONS = {"ok": ("✓", GREEN), "todo": (None, MUTED), "wait": ("…", ACCENT), "error": ("!", AMBER)}

    def __init__(self, parent, number):
        self.number = str(number)  # pasos pendientes: se muestra su número
        self.frame = tk.Frame(parent, bg=CARD, padx=12, pady=10)
        self.frame.pack(fill="x", pady=(0, 8))
        self.icon = tk.Label(self.frame, text=str(number), bg=CARD, fg=MUTED, font=(FONT_B, 14), width=2)
        self.icon.pack(side="left", anchor="n")
        text = tk.Frame(self.frame, bg=CARD)
        text.pack(side="left", fill="x", expand=True, padx=(8, 8))
        self.title = tk.Label(text, bg=CARD, fg=FG, font=(FONT_B, 10), anchor="w")
        self.title.pack(fill="x")
        self.detail = tk.Label(text, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w", justify="left", wraplength=300)
        self.detail.pack(fill="x")
        self.button = tk.Button(self.frame, bd=0, cursor="hand2", padx=12, pady=4, font=(FONT_B, 9),
                                bg=ACCENT, fg=BG, activebackground="#f09a7a", activeforeground=BG)

    def set(self, state, title, detail="", action=None, command=None):
        icon, color = self.ICONS[state]
        self.icon.config(text=icon or self.number, fg=color)
        self.title.config(text=title, fg=FG if state != "todo" else MUTED)
        self.detail.config(text=detail)
        if action:
            # acción principal en naranja; en pasos ya completados (p. ej. cambiar de cuenta), discreta
            primary = state != "ok"
            self.button.config(text=action, command=command, bg=ACCENT if primary else BORDER,
                               fg=BG if primary else FG, activebackground="#f09a7a" if primary else TRACK,
                               activeforeground=BG if primary else FG)
            self.button.pack(side="right", anchor="center")
        else:
            self.button.pack_forget()


class ConnectWindow:
    """Asistente de conexión: comprueba Claude Code, la sesión de Claude y el acceso al uso,
    y se actualiza solo mientras el usuario instala o inicia sesión."""

    POLL_MS = 3000

    def __init__(self, app):
        self.app = app
        self.info = None
        self.checking = self.install_started = self.login_started = False
        self.last_refresh = 0
        self._job = None
        w = self.win = tk.Toplevel(app.root)
        w.title(tr("connect.title"))
        w.configure(bg=BG)
        w.resizable(False, False)
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", self.close)

        body = tk.Frame(w, bg=BG, padx=24, pady=20)
        body.pack(fill="both")
        head = tk.Frame(body, bg=BG)
        head.pack(fill="x", pady=(0, 14))
        if getattr(app, "_icon", None):
            self._small = app._icon.subsample(max(1, app._icon.width() // 48))
            tk.Label(head, image=self._small, bg=BG).pack(side="left", padx=(0, 12), anchor="n")
        titles = tk.Frame(head, bg=BG)
        titles.pack(side="left", fill="x")
        tk.Label(titles, text=tr("connect.heading"), bg=BG, fg=FG, font=(FONT_B, 15), anchor="w").pack(fill="x")
        tk.Label(titles, text=tr("connect.intro"), bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left",
                 wraplength=360).pack(fill="x")

        self.steps = [StepRow(body, i) for i in (1, 2, 3)]
        self.tips = tk.Label(body, text=tr("connect.tips"), bg=BG, fg=GREEN, font=(FONT, 9), anchor="w",
                             justify="left", wraplength=420)
        footer = tk.Frame(body, bg=BG)
        footer.pack(fill="x", side="bottom", pady=(8, 0))
        self.main_btn = tk.Button(footer, bd=0, cursor="hand2", padx=16, pady=6, command=self.finish)
        self.main_btn.pack(side="right")
        self.diag_btn = tk.Button(footer, text=tr("btn.diag"), command=self.copy_diagnostics, bd=0, cursor="hand2",
                                  bg=BG, fg=MUTED, activebackground=BG, activeforeground=FG, font=(FONT, 8), padx=0)
        self.diag_btn.pack(side="left", anchor="s")
        self._set_done(False)
        for row in self.steps:
            row.set("wait", tr("connect.checking"))

        w.update_idletasks()
        x = (w.winfo_screenwidth() - w.winfo_reqwidth()) // 2
        y = (w.winfo_screenheight() - w.winfo_reqheight()) // 3
        w.geometry(f"+{x}+{y}")
        self._poll()

    # ── ciclo de comprobación ──
    def _poll(self):
        if not self.checking:
            self.checking = True
            threading.Thread(target=lambda: self.app.events.put(("connect", check_connection())),
                             daemon=True).start()
        self._job = self.win.after(self.POLL_MS, self._poll)

    def update(self, info):
        """Recibe el resultado de check_connection() y redibuja los pasos."""
        self.checking = False
        self.info = info
        claude, status = info["claude"], info["status"] or {}
        wsl, creds_ok = info.get("wsl"), info.get("creds_ok")
        install, login, usage = self.steps

        if claude:
            version = info.get("version")
            parts = [tr("step.install.version", version=version) if version else "",
                     tr("step.install.desktop") if info.get("source") == "desktop" else ""]
            install.set("ok", tr("step.install.ok"), " · ".join(p for p in parts if p))
        elif wsl:
            install.set("ok", tr("step.install.ok"), tr("step.install.wsl", distro=wsl))
        elif creds_ok:  # no se encuentra el ejecutable, pero sí su sesión: basta para leer el uso
            install.set("ok", tr("step.install.ok"), tr("step.install.found"))
        elif self.install_started:
            install.set("wait", tr("step.install"), tr("step.install.wait"), tr("btn.install"), self.install)
        else:
            install.set("todo", tr("step.install"), tr("step.install.detail"), tr("btn.install"), self.install)

        cli_logged = bool(claude and status.get("loggedIn") and status.get("authMethod") == "claude.ai")
        logged = cli_logged or bool(creds_ok and not (claude and status.get("loggedIn") is False))
        if cli_logged:
            login.set("ok", tr("step.login.ok"), account_text(status), tr("btn.switch"), self.login)
        elif logged:
            login.set("ok", tr("step.login.ok"),
                      tr("step.login.wsl", distro=wsl) if wsl else tr("step.login.found"),
                      tr("btn.switch") if claude else None, self.login)
        elif not claude:
            login.set("todo", tr("step.login"), tr("step.login.need_install"))
        elif status.get("loggedIn"):
            login.set("error", tr("step.login.api"), tr("step.login.api_detail"), tr("btn.login"), self.login)
        elif self.login_started:
            login.set("wait", tr("step.login"), tr("step.login.wait"), tr("btn.login"), self.login)
        else:
            login.set("todo", tr("step.login"), tr("step.login.detail"), tr("btn.login"), self.login)

        app = self.app
        done = False
        if not logged:
            usage.set("todo", tr("step.usage"))
        elif app.limits and (not app.error or app.error[0] == "err.ratelimit"):
            # con límite de consultas la conexión es correcta: se muestra el último dato
            summary = " · ".join(f"{short(lim['kind'])} {lim['pct']:.0f}%" for lim in app.limits)
            usage.set("ok", tr("step.usage.ok"),
                      summary + (f"\n{tr(app.error[0], **app.error[1])}" if app.error else ""))
            done = True
        else:
            if app.error:
                usage.set("error", tr("step.usage"), tr(app.error[0], **app.error[1]))
            else:
                usage.set("wait", tr("step.usage"), tr("loading"))
            if time.time() - self.last_refresh > 10:  # recién conectado: pedir el uso ya
                self.last_refresh = time.time()
                app.refresh()
        self._set_done(done)

    def _set_done(self, done):
        if done:
            self.tips.pack(fill="x", pady=(4, 0))
            self.main_btn.config(text=tr("btn.start"), font=(FONT_B, 10), bg=ACCENT, fg=BG,
                                 activebackground="#f09a7a", activeforeground=BG)
        else:
            self.tips.pack_forget()
            self.main_btn.config(text=tr("btn.close"), font=(FONT, 10), bg=CARD, fg=FG,
                                 activebackground=BORDER, activeforeground=FG)

    # ── acciones ──
    def install(self):
        import shutil
        self.install_started = True
        if shutil.which("winget"):
            run_in_console("winget install --id Anthropic.ClaudeCode -e")
        else:
            webbrowser.open(CLAUDE_CODE_PAGE)
        self.steps[0].set("wait", tr("step.install"), tr("step.install.wait"), tr("btn.install"), self.install)

    def login(self):
        claude = (self.info or {}).get("claude") or find_claude()
        if not claude:
            return
        self.login_started = True
        run_in_console(f'"{claude}" auth login --claudeai')
        self.steps[1].set("wait", tr("step.login"), tr("step.login.wait"), tr("btn.login"), self.login)

    def copy_diagnostics(self):
        self.win.clipboard_clear()
        self.win.clipboard_append(diagnostics(self.info))
        self.diag_btn.config(text=tr("diag.copied"), fg=GREEN)
        self.win.after(2500, lambda: self.diag_btn.winfo_exists() and self.diag_btn.config(text=tr("btn.diag"),
                                                                                           fg=MUTED))

    def finish(self):
        self.app.settings["onboarded"] = True
        self.app.save()
        self.close()
        self.app.show_mode(self.app.settings["mode"])

    def close(self):
        if self._job:
            self.win.after_cancel(self._job)
        self.win.destroy()
        self.app.connect_win = None


# ───────────────────────── actualizaciones ─────────────────────────

GITHUB_REPO = "fergaschas/claude-usage-widget"
# se puede apuntar a otro servidor para probar el flujo de actualización
UPDATE_API = os.environ.get("CLAUDE_USAGE_UPDATE_URL") or f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
UPDATE_EVERY_MS = 6 * 3600 * 1000
SETUP_ASSET = "claudeusage-setup.exe"


def version_tuple(version):
    """"v2.5.0" → (2, 5). Sin ceros finales, para que v2.5 y v2.5.0 se consideren la misma versión."""
    parts = [int(n) for n in re.findall(r"\d+", version)[:4]]
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def fetch_latest_release():
    """Última Release publicada: {version, page, url, size, digest, notes}, o None si no tiene instalador."""
    req = urllib.request.Request(UPDATE_API, headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": f"claude-usage-tracker/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    asset = next((a for a in data.get("assets", []) if a.get("name", "").lower() == SETUP_ASSET), None)
    if not asset:
        return None
    return {"version": data["tag_name"].lstrip("vV"), "page": data.get("html_url", ""),
            "url": asset["browser_download_url"], "size": asset.get("size") or 0,
            "digest": asset.get("digest") or "", "notes": data.get("body") or ""}


def download_update(info, progress):
    """Descarga el instalador y comprueba tamaño y SHA-256 (si GitHub lo publica). Devuelve su ruta."""
    import hashlib
    import tempfile
    dest = Path(tempfile.gettempdir()) / f"ClaudeUsage-Setup-{info['version']}.exe"
    sha, done = hashlib.sha256(), 0
    req = urllib.request.Request(info["url"], headers={"User-Agent": f"claude-usage-tracker/{APP_VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            while chunk := resp.read(256 * 1024):
                f.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                progress(min(99, done * 100 // max(info["size"], 1)))
        if info["size"] and done != info["size"]:
            raise ValueError(f"size {done} != {info['size']}")
        if info["digest"].startswith("sha256:") and sha.hexdigest() != info["digest"][7:].lower():
            raise ValueError("SHA-256")
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return dest


def cleanup_old_setups():
    import tempfile
    for f in Path(tempfile.gettempdir()).glob("ClaudeUsage-Setup-*.exe"):
        try:
            f.unlink()
        except OSError:
            pass  # el instalador que nos acaba de actualizar puede seguir abierto


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
        self.connect_win = self.account = None

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
        self.auto_updates = tk.BooleanVar(value=self.settings["check_updates"])
        self.update_info = self.update_state = None
        root.attributes("-topmost", self.on_top.get())

        self._build()
        self.widget = Widget(self)
        self.tray = Tray(lambda cmd: self.events.put(("tray", cmd)))
        self.show_mode(self.settings["mode"])
        threading.Thread(target=lambda: self.events.put(("connect", check_connection())), daemon=True).start()
        self._after_update()
        self.root.after(8000, self._auto_check_updates)
        if not self.settings.get("onboarded") and not self.history.points:  # instalación nueva
            self.open_connect()
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
        tk.Label(header, text=tr("title"), bg=BG, fg=FG, font=(FONT_B, 12)).pack(side="left")
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
        self.update_btn = tk.Button(self.root, command=self.start_update, bd=0, cursor="hand2", bg=BG, fg=ACCENT,
                                    activebackground=CARD, activeforeground=ACCENT, anchor="w",
                                    font=(FONT_B, 10), padx=12)
        self.connect_btn = tk.Button(self.root, text=tr("cta.connect"), command=self.open_connect, bd=0,
                                     cursor="hand2", bg=BG, fg=AMBER, activebackground=CARD,
                                     activeforeground=AMBER, anchor="w", font=(FONT_B, 10), padx=12)
        self._apply_details()
        self._build_menu()

    def _build_details(self):
        card = tk.Frame(self.details, bg=CARD, padx=10, pady=8)
        card.pack(fill="x", pady=(0, 8))
        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x")
        tk.Label(top, text=tr("history"), bg=CARD, fg=FG, font=(FONT_B, 10)).pack(side="left")
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
        tk.Label(card, text=tr("tokens.title"), bg=CARD, fg=FG, font=(FONT_B, 10),
                 anchor="w").pack(fill="x")
        self.tok_labels = []
        for _ in range(5):
            lbl = tk.Label(card, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w", justify="left")
            lbl.pack(fill="x")
            self.tok_labels.append(lbl)
        self.tok_labels[0].config(text=tr("tokens.scanning"))

    def _build_menu(self):
        m = self.menu = tk.Menu(self.root, tearoff=False)
        m.add_command(label=tr("menu.widget"), command=lambda: self.show_mode("widget"))
        m.add_command(label=tr("menu.full"), command=lambda: self.show_mode("full"))
        m.add_command(label=tr("menu.hide"), command=self.hide)
        m.add_command(label=tr("menu.account"), command=self.open_connect)
        m.add_separator()
        m.add_checkbutton(label=tr("menu.on_top"), variable=self.on_top, command=self._on_top_changed)
        m.add_checkbutton(label=tr("menu.autostart"), variable=self.autostart, command=self._autostart_changed)
        m.add_checkbutton(label=tr("menu.notify"), variable=self.notify, command=self.save)
        sub = tk.Menu(m, tearoff=False)
        for secs, label in ((30, "30 s"), (60, "1 min"), (120, "2 min"), (300, "5 min")):
            sub.add_radiobutton(label=label, value=secs, variable=self.interval, command=self._interval_changed)
        m.add_cascade(label=tr("menu.interval"), menu=sub)
        self.style = tk.StringVar(value=self.settings["widget_style"])
        sub = tk.Menu(m, tearoff=False)
        for value in ("large", "rings", "compact"):
            sub.add_radiobutton(label=tr(f"style.{value}"), value=value, variable=self.style,
                                command=self._style_changed)
        m.add_cascade(label=tr("menu.style"), menu=sub)
        self.alpha = tk.DoubleVar(value=self.settings["alpha"])
        sub = tk.Menu(m, tearoff=False)
        for alpha in (1.0, 0.95, 0.85, 0.75, 0.6):
            sub.add_radiobutton(label=f"{alpha:.0%}", value=alpha, variable=self.alpha, command=self._alpha_changed)
        m.add_cascade(label=tr("menu.alpha"), menu=sub)
        self.lang = tk.StringVar(value=LANG)
        sub = tk.Menu(m, tearoff=False)
        for code, name in LANGUAGES:
            sub.add_radiobutton(label=name, value=code, variable=self.lang,
                                command=lambda: self.root.after(50, self._lang_changed))
        m.add_cascade(label=tr("menu.lang"), menu=sub)
        m.add_separator()
        m.add_command(label=tr("menu.open_web"), command=lambda: webbrowser.open(USAGE_PAGE))
        m.add_command(label=tr("menu.export"), command=self.export_csv)
        m.add_command(label=tr("menu.test"), command=lambda: toast(APP_NAME, tr("test.body")))
        m.add_command(label=tr("menu.folder"), command=lambda: os.startfile(APP_DIR))
        m.add_separator()
        m.add_command(label=tr("menu.check_updates"), command=lambda: self.check_updates(manual=True))
        m.add_checkbutton(label=tr("menu.auto_updates"), variable=self.auto_updates, command=self.save)
        m.add_separator()
        m.add_command(label=tr("quit"), command=self.quit)

    # ── acciones ──
    def _show_menu(self):
        b = self.menu_btn
        self.menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def _on_top_changed(self):
        self.root.attributes("-topmost", self.on_top.get())
        self.widget.win.attributes("-topmost", self.on_top.get())
        self.save()

    def _rebuild_widget(self):
        old = self.widget
        visible = old.win.state() == "normal"
        right = old.win.winfo_x() + old.win.winfo_width()
        old.win.destroy()
        self.widget = Widget(self)
        self.render()
        if visible:
            self.widget.show(anchor_right=right)

    def _style_changed(self):
        self.settings["widget_style"] = self.style.get()
        self._rebuild_widget()
        self.save()

    # ── actualizaciones ──
    def _after_update(self):
        """Primer arranque tras actualizar: avisar y limpiar el instalador descargado."""
        cleanup_old_setups()
        last = self.settings.get("last_version")
        if last and version_tuple(APP_VERSION) > version_tuple(last):
            toast(APP_NAME, tr("update.done", version=APP_VERSION))
        if last != APP_VERSION:
            self.settings["last_version"] = APP_VERSION
            self.save()

    def _auto_check_updates(self):
        if self.auto_updates.get():
            self.check_updates()
        self.root.after(UPDATE_EVERY_MS, self._auto_check_updates)

    def check_updates(self, manual=False):
        def work():
            try:
                self.events.put(("update_check", (fetch_latest_release(), manual, False)))
            except Exception:
                self.events.put(("update_check", (None, manual, True)))
        threading.Thread(target=work, daemon=True).start()

    def _on_update_check(self, info, manual, failed):
        if info and version_tuple(info["version"]) > version_tuple(APP_VERSION):
            self.update_info = info
            if not manual and self.settings.get("update_notified") != info["version"]:
                self.settings["update_notified"] = info["version"]
                self.save()
                toast(tr("update.toast_title", version=info["version"]), tr("update.toast_body"))
            self.render()
            if manual:
                self.start_update()
        elif manual:
            from tkinter import messagebox
            self._dialog(messagebox.showinfo, APP_NAME,
                         tr("update.check_failed") if failed else tr("update.latest", version=APP_VERSION))

    def _update_text(self):
        if self.update_state and self.update_state[0] == "downloading":
            return tr("update.downloading", pct=self.update_state[1])
        if self.update_state:
            return tr("update.installing")
        if self.update_info:
            return tr("update.available", version=self.update_info["version"])
        return None

    def _dialog(self, fn, title, message):
        """Muestra un cuadro de diálogo por delante del widget (que está siempre encima)."""
        self.widget.win.attributes("-topmost", False)
        try:
            return fn(title, message, parent=self.root if self.root.state() == "normal" else self.widget.win)
        finally:
            self.widget.win.attributes("-topmost", self.on_top.get())

    def start_update(self):
        from tkinter import messagebox
        info = self.update_info
        if not info or self.update_state:
            return
        if not FROZEN:  # ejecutando desde el código: no hay .exe que sustituir
            webbrowser.open(info["page"])
            return
        notes = re.sub(r"[*_`#>]", "", info["notes"]).strip()
        notes = notes[:600] + ("…" if len(notes) > 600 else "")
        if not self._dialog(messagebox.askyesno, tr("update.ask_title"),
                            tr("update.ask", version=info["version"], current=APP_VERSION, notes=notes)):
            return
        self.update_state = ("downloading", 0)
        self.render()

        def work():
            last = [-1]

            def progress(pct):
                if pct != last[0]:
                    last[0] = pct
                    self.events.put(("update_progress", pct))
            try:
                self.events.put(("update_ready", str(download_update(info, progress))))
            except Exception as e:
                self.events.put(("update_failed", str(e) or e.__class__.__name__))
        threading.Thread(target=work, daemon=True).start()

    def _install_update(self, setup):
        """Lanza el instalador en modo silencioso; él cierra esta app, sustituye el .exe y la vuelve a abrir."""
        self.update_state = ("installing",)
        self.render()
        env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
        try:
            subprocess.Popen([setup, "--silent", "--update", "--dir", str(APP_DIR)], env=env, cwd=Path(setup).parent,
                             creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        except OSError as e:
            self._update_failed(e)
            return
        # si el instalador no ha cerrado la app en 90 s, algo ha ido mal: no quedarse en "Instalando…"
        self.root.after(90_000, lambda: self.update_state == ("installing",) and self._update_failed("timeout"))

    def _update_failed(self, error):
        self.update_state = None
        self.render()
        toast(APP_NAME, tr("update.failed", e=error))

    def open_connect(self):
        if self.connect_win:
            self.connect_win.win.deiconify()
            self.connect_win.win.lift()
            self.connect_win.win.focus_force()
        else:
            self.connect_win = ConnectWindow(self)

    def _on_connection(self, info):
        global CREDENTIALS
        if info.get("creds_ok") and info["creds"] != CREDENTIALS:  # p. ej. en WSL o en CLAUDE_CONFIG_DIR
            CREDENTIALS = info["creds"]
            if self.error and self.error[0] in AUTH_ERRORS:
                self.refresh()
        status = info["status"] or {}
        self.account = account_text(status) if status.get("loggedIn") else None
        if self.connect_win:
            self.connect_win.update(info)
        self.render()

    def _lang_changed(self):
        """Cambia el idioma reconstruyendo la interfaz, sin reiniciar la app."""
        global LANG
        LANG = self.settings["lang"] = self.lang.get()
        self.save()
        reopen = self.connect_win is not None
        if reopen:
            self.connect_win.close()
        for child in self.root.winfo_children():
            if child is not self.widget.win:
                child.destroy()
        self.rows = []
        self._build()
        self._rebuild_widget()
        if self.tokens:
            self._render_tokens()
        self._draw_chart()
        if reopen:
            self.open_connect()

    def _alpha_changed(self):
        self.settings["alpha"] = self.alpha.get()
        self.widget.set_alpha(self.alpha.get())
        self.save()

    def _autostart_changed(self):
        try:
            set_autostart(self.autostart.get())
        except OSError as e:
            self.autostart.set(get_autostart())
            self.status.config(text=tr("err.autostart", e=e), fg=AMBER)

    def _interval_changed(self):
        self.save()
        self.refresh()

    def _toggle_details(self):
        self.settings["details"] = not self.settings["details"]
        self._apply_details()
        self.save()

    def _apply_details(self):
        shown = self.settings["details"]
        self.toggle.config(text=("▾ " if shown else "▸ ") + tr("details"))
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
        path = DATA_DIR / tr("csv.file")
        # Excel en español espera ';' y coma decimal; en inglés, ',' y punto
        sep, dec = (";", ",") if LANG == "es" else (",", ".")
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=sep)
                w.writerow(tr("csv.header"))
                for p in self.history.points:
                    for kind, (pct, reset) in p["l"].items():
                        w.writerow([datetime.fromtimestamp(p["t"]).strftime("%Y-%m-%d %H:%M:%S"),
                                    label(kind), f"{pct:g}".replace(".", dec),
                                    datetime.fromtimestamp(reset).strftime("%Y-%m-%d %H:%M") if reset else ""])
            os.startfile(path)
        except OSError as e:
            self.status.config(text=tr("err.export", e=e), fg=AMBER)

    def save(self):
        self.settings.update(on_top=self.on_top.get(), notify=self.notify.get(), interval=self.interval.get(),
                             check_updates=self.auto_updates.get())
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
            toast(APP_NAME, tr("hint", hotkey=tr("hint.hotkey") if self.tray.hotkey else ""))

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

    def _fetch_usage_renewing(self):
        try:
            return fetch_usage()
        except AuthError as e:
            claude = find_claude() if e.key == "err.token" else None
            if not claude:
                raise
            auth_status(claude)  # al ejecutarse, Claude Code renueva su token si ha caducado
            return fetch_usage()

    def _fetch_worker(self):
        try:
            self.events.put(("usage", self._fetch_usage_renewing()))
        except RateLimited as e:
            self.events.put(("ratelimit", (e.key, e.kw)))
        except AuthError as e:
            self.events.put(("error", (e.key, e.kw)))
        except Exception as e:  # red caída, respuesta inesperada…
            self.events.put(("error", ("err.fetch", {"name": e.__class__.__name__})))

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
                if payload[0] in AUTH_ERRORS and not self.settings.get("onboarded") and not self.connect_win:
                    self.open_connect()
                if kind == "ratelimit":  # no insistir: esperar antes del próximo intento
                    self.root.after_cancel(self._job)
                    self._job = self.root.after(RATE_LIMIT_WAIT * 1000, self.refresh)
            elif kind == "tokens":
                self.tokens = payload
                self._render_tokens()
            elif kind == "connect":
                self._on_connection(payload)
            elif kind == "show":
                self.show_mode(self.settings["mode"])
            elif kind == "update_check":
                self._on_update_check(*payload)
            elif kind == "update_progress":
                self.update_state = ("downloading", payload)
                self.render()
            elif kind == "update_ready":
                self._install_update(payload)
            elif kind == "update_failed":
                self._update_failed(payload)
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
        if self.connect_win and self.connect_win.info:  # marcar "leer tu uso" en cuanto llega el dato
            self.connect_win.update(self.connect_win.info)
        self.updated_at = datetime.now()
        self.render()
        self._draw_chart()

    def _check_alerts(self, limits):
        notified = self.settings.setdefault("notified", {})
        for lim in limits:
            kind, pct, reset = lim["kind"], lim["pct"], lim["reset"]
            name = label(kind)
            prev = self.history.last(kind)
            if prev and prev[0] >= 50 and (pct < prev[0] - 5 or
                                           (reset and prev[1] and reset - prev[1] > 1800)):
                toast(tr("alert.reset", label=name), tr("alert.reset_body", pct=f"{100 - pct:.0f}"))
            window = round(reset / 3600) if reset else 0
            last_window, last_level = notified.get(kind, [None, 0])
            if last_window != window:
                last_level = 0
            level = max((t for t in THRESHOLDS if pct >= t), default=0)
            if level > last_level:
                if level >= 100:
                    toast(tr("alert.limit", label=name), fmt_reset(reset).capitalize() or tr("alert.wait"))
                else:
                    when = fmt_reset(reset)
                    toast(tr("alert.used", label=name, pct=f"{pct:.0f}"),
                          tr("alert.left", pct=f"{100 - pct:.0f}") + (f" {when.capitalize()}." if when else ""))
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
            self.extra.config(text=tr("extra", pct=f"{extra['utilization']:.0f}"))
            self.extra.pack(fill="x", after=self.body, pady=(0, 6))
        else:
            self.extra.pack_forget()

        if limits:
            worst = max(lim["pct"] for lim in limits)
            self.root.title("Claude " + " · ".join(f"{lim['pct']:.0f}%" for lim in limits))
            note = tr("status.limit") if worst >= 100 else tr("status.margin", pct=f"{100 - worst:.0f}")
        elif self.updated_at:
            note = tr("status.no_limits")
        else:
            note = tr("loading")

        update_text = self._update_text()
        self.widget.render(limits, self.history, self.error, update_text)
        if update_text:
            self.update_btn.config(text=update_text)
            self.update_btn.pack(fill="x", side="bottom")
        else:
            self.update_btn.pack_forget()
        tip = " · ".join(f"{short(lim['kind'])} {lim['pct']:.0f}%" for lim in limits)
        self.tray.set_tip(f"Claude · {tip}" if tip else APP_NAME)

        if self.error and self.error[0] in AUTH_ERRORS:
            self.connect_btn.pack(fill="x", side="bottom")
        else:
            self.connect_btn.pack_forget()
        if self.error:
            stale = "\n" + tr("status.last", time=f"{self.updated_at:%H:%M}") if self.updated_at else ""
            self.status.config(text=tr(self.error[0], **self.error[1]) + stale, fg=AMBER)
        else:
            stamp = "\n" + tr("status.updated", time=f"{self.updated_at:%H:%M:%S}") if self.updated_at else ""
            if self.account:
                stamp += "\n" + tr("status.account", account=self.account)
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
            text = f"{t:%H:%M}" if hours <= 24 else f"{day(t.weekday())} {t:%d}"
            c.create_text(left + (right - left) * frac, h - 2, text=text, anchor=anchor, fill=MUTED, font=(FONT, 7))
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
            name = short(kind) if kind == "session" else label(kind).split("·")[-1].strip().capitalize()
            item = c.create_text(legend_x, 2, text=f"● {name}", anchor="ne", fill=color, font=(FONT, 7))
            legend_x = c.bbox(item)[0] - 8

    def _render_tokens(self):
        s = self.tokens
        if not s or not s["week"]:
            self.tok_labels[0].config(text=tr("tokens.none"))
            return
        t, wk = s["today"], s["week"]
        top = lambda d: " · ".join(f"{name} {100 * v / sum(d.values()):.0f}%"
                                   for name, v in sorted(d.items(), key=lambda kv: -kv[1])[:3]
                                   if v / sum(d.values()) >= 0.005)
        models = defaultdict(int)
        for m, v in s["models"].items():
            models[pretty_model(m)] += v
        lines = [
            tr("tokens.totals", today=fmt_num(sum(t.values())), week=fmt_num(sum(wk.values()))),
            tr("tokens.split", input=fmt_num(t["input"]), output=fmt_num(t["output"]),
               write=fmt_num(t["cache_write"]), read=fmt_num(t["cache_read"])),
            tr("tokens.models", top=top(models)),
            tr("tokens.projects", top=top(s["projects"])),
            tr("tokens.note"),
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
        ok = messagebox.askyesno(APP_NAME, tr("uninstall.ask"), parent=r)
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
        messagebox.showinfo(APP_NAME, tr("uninstall.done"), parent=r)
        r.destroy()


def main():
    global LANG
    LANG = load_settings().get("lang") or LANG
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
