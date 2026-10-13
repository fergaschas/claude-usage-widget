"""Instalador de Claude Usage.

Se empaqueta con PyInstaller junto a ClaudeUsage.exe (como datos) para obtener un único
ClaudeUsage-Setup.exe. Instala por usuario, sin permisos de administrador.

Uso silencioso: ClaudeUsage-Setup.exe --silent [--dir RUTA] [--no-desktop] [--no-autostart] [--no-launch]
  --update: lo usa la app al actualizarse; conserva el acceso directo y el inicio con Windows actuales.
Idioma: el de Windows; se puede forzar con --lang es|en.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
import tkinter as tk
import winreg
from pathlib import Path
from tkinter import filedialog

APP_NAME = "Claude Usage"
VERSION = "2.5.1"
EXE = "ClaudeUsage.exe"
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
PAYLOAD, ICON = BUNDLE / EXE, BUNDLE / "icon.png"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ClaudeUsage"
DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / APP_NAME
CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
INSTANCE_PORT = 47823
NO_WINDOW = subprocess.CREATE_NO_WINDOW

BG, CARD, FG, MUTED, BORDER = "#111114", "#202026", "#ffffff", "#c8c8d2", "#4a4a55"
GREEN, AMBER, ACCENT = "#34d17a", "#ffb224", "#e8825f"
FONT, FONT_B = "Segoe UI", "Segoe UI Semibold"

STRINGS = {
    "es": {
        "title": "Instalar {app}", "tagline": "Widget con el % de uso de tu plan de Claude",
        "no_session": "ℹ  Aún no has conectado Claude en este PC. No pasa nada: al abrir Claude Usage, un "
                      "asistente te guiará para conectar tu cuenta de Claude en un par de clics.",
        "folder": "Carpeta de instalación", "change": "Cambiar…",
        "opt.desktop": "Crear acceso directo en el escritorio", "opt.autostart": "Iniciar con Windows",
        "opt.open": "Abrir Claude Usage al terminar",
        "note": "Se instala solo para tu usuario; no requiere permisos de administrador.",
        "install": "Instalar", "update": "Actualizar", "cancel": "Cancelar", "close": "Cerrar",
        "step.stop": "Cerrando Claude Usage si está abierto…", "step.copy": "Copiando archivos…",
        "step.links": "Creando accesos directos…", "step.register": "Registrando la aplicación…",
        "failed": "No se pudo completar la instalación: {e}",
        "done": "✓  Instalado. Búscalo en el menú Inicio como «Claude Usage». Pulsa Ctrl+Alt+U para "
                "mostrar u ocultar el widget.",
        "browse": "Elige dónde instalar Claude Usage", "link_desc": "Porcentaje de uso de tu plan de Claude",
    },
    "en": {
        "title": "Install {app}", "tagline": "Widget showing how much of your Claude plan you've used",
        "no_session": "ℹ  Claude isn't connected on this PC yet. No problem: when you open Claude Usage, a "
                      "short wizard will help you connect your Claude account in a couple of clicks.",
        "folder": "Install folder", "change": "Change…",
        "opt.desktop": "Create a desktop shortcut", "opt.autostart": "Start with Windows",
        "opt.open": "Open Claude Usage when done",
        "note": "Installs for your user only; no admin rights needed.",
        "install": "Install", "update": "Update", "cancel": "Cancel", "close": "Close",
        "step.stop": "Closing Claude Usage if it's running…", "step.copy": "Copying files…",
        "step.links": "Creating shortcuts…", "step.register": "Registering the app…",
        "failed": "Installation failed: {e}",
        "done": "✓  Installed. Find it in the Start menu as “Claude Usage”. Press Ctrl+Alt+U to show or "
                "hide the widget.",
        "browse": "Choose where to install Claude Usage", "link_desc": "How much of your Claude plan you've used",
    },
}


def _system_lang():
    try:
        import ctypes
        return "es" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x0A else "en"
    except Exception:
        return "en"


LANG = _system_lang()


def tr(key, **kw):
    text = STRINGS[LANG][key]
    return text.format(**kw) if kw else text


def existing_dir():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
            loc = Path(winreg.QueryValueEx(k, "InstallLocation")[0])
            return loc if loc.exists() else None
    except OSError:
        return None


def stop_running():
    try:
        with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=1) as c:
            c.sendall(b"quit")
        time.sleep(2)
    except OSError:
        pass
    subprocess.run(["taskkill", "/f", "/im", EXE], capture_output=True, creationflags=NO_WINDOW)
    time.sleep(0.5)


def make_shortcuts(exe, desktop):
    ps = r"""
$sh = New-Object -ComObject WScript.Shell
function Link($folder) {
    $s = $sh.CreateShortcut((Join-Path ([Environment]::GetFolderPath($folder)) 'Claude Usage.lnk'))
    $s.TargetPath = $env:CU_EXE; $s.WorkingDirectory = $env:CU_DIR; $s.IconLocation = "$env:CU_EXE,0"
    $s.Description = $env:CU_DESC; $s.Save()
}
Link 'Programs'
if ($env:CU_DESK -eq '1') { Link 'Desktop' }
else { Remove-Item (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Claude Usage.lnk') -ErrorAction SilentlyContinue }
"""
    env = {**os.environ, "CU_EXE": str(exe), "CU_DIR": str(exe.parent), "CU_DESK": "1" if desktop else "0",
           "CU_DESC": tr("link_desc")}
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env, check=True,
                   capture_output=True, creationflags=NO_WINDOW)


def register(exe, autostart):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if autostart:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, f'"{exe}" --startup')
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, value in (("DisplayName", APP_NAME), ("DisplayVersion", VERSION), ("Publisher", APP_NAME),
                            ("DisplayIcon", f"{exe},0"), ("InstallLocation", str(exe.parent)),
                            ("UninstallString", f'"{exe}" --uninstall'),
                            ("QuietUninstallString", f'"{exe}" --uninstall --silent')):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        for name, value in (("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", exe.stat().st_size // 1024)):
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, value)


def current_shortcut_and_autostart():
    """(¿hay acceso directo en el escritorio?, ¿arranca con Windows?) en la instalación actual."""
    import ctypes
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY
    desktop = (Path(buf.value) / f"{APP_NAME}.lnk").exists()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            autostart = True
    except OSError:
        autostart = False
    return desktop, autostart


def launch(exe):
    # un .exe de PyInstaller lanzado desde otro debe empezar con su propio entorno
    env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    subprocess.Popen([str(exe)], cwd=exe.parent, env=env, creationflags=subprocess.DETACHED_PROCESS)


def install(target, desktop, autostart, log=print):
    log(tr("step.stop"))
    stop_running()
    log(tr("step.copy"))
    target.mkdir(parents=True, exist_ok=True)
    exe = target / EXE
    for attempt in range(20):  # el .exe anterior puede tardar un momento en liberarse al cerrarse
        try:
            shutil.copy2(PAYLOAD, exe)
            break
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.5)
    log(tr("step.links"))
    make_shortcuts(exe, desktop)
    log(tr("step.register"))
    register(exe, autostart)
    return exe


class Wizard:
    def __init__(self, root):
        self.root = root
        root.title(tr("title", app=APP_NAME))
        root.configure(bg=BG)
        root.resizable(False, False)
        try:
            self._icon = tk.PhotoImage(file=str(ICON))
            root.iconphoto(True, self._icon)
        except tk.TclError:
            self._icon = None
        prev = existing_dir()
        self.target = tk.StringVar(value=str(prev or DEFAULT_DIR))
        self.desktop = tk.BooleanVar(value=True)
        self.autostart = tk.BooleanVar(value=True)
        self.open_after = tk.BooleanVar(value=True)

        body = tk.Frame(root, bg=BG, padx=24, pady=20)
        body.pack(fill="both")
        head = tk.Frame(body, bg=BG)
        head.pack(fill="x")
        if self._icon:
            small = self._icon.subsample(max(1, self._icon.width() // 56))
            self._small = small
            tk.Label(head, image=small, bg=BG).pack(side="left", padx=(0, 14))
        titles = tk.Frame(head, bg=BG)
        titles.pack(side="left", fill="x")
        tk.Label(titles, text=APP_NAME, bg=BG, fg=FG, font=(FONT_B, 18), anchor="w").pack(fill="x")
        tk.Label(titles, text=tr("tagline"), bg=BG, fg=MUTED,
                 font=(FONT, 10), anchor="w").pack(fill="x")

        if not CREDENTIALS.exists():
            tk.Label(body, bg=CARD, fg=FG, font=(FONT, 9), justify="left", anchor="w", padx=10, pady=8,
                     wraplength=440,
                     text=tr("no_session")).pack(fill="x", pady=(16, 0))

        tk.Label(body, text=tr("folder"), bg=BG, fg=FG, font=(FONT_B, 10),
                 anchor="w").pack(fill="x", pady=(18, 4))
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x")
        tk.Entry(row, textvariable=self.target, bg=CARD, fg=FG, insertbackground=FG, relief="flat",
                 font=(FONT, 10), width=44).pack(side="left", fill="x", expand=True, ipady=5)
        self._button(row, tr("change"), self.browse, primary=False).pack(side="left", padx=(8, 0))

        opts = tk.Frame(body, bg=BG)
        opts.pack(fill="x", pady=(14, 0))
        for var, text in ((self.desktop, tr("opt.desktop")), (self.autostart, tr("opt.autostart")),
                          (self.open_after, tr("opt.open"))):
            tk.Checkbutton(opts, text=text, variable=var, bg=BG, fg=FG, selectcolor=CARD, activebackground=BG,
                           activeforeground=FG, font=(FONT, 10), anchor="w", bd=0,
                           highlightthickness=0).pack(fill="x", pady=1)

        self.status = tk.Label(body, text=tr("note"),
                               bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left", wraplength=440)
        self.status.pack(fill="x", pady=(16, 0))

        buttons = tk.Frame(body, bg=BG)
        buttons.pack(fill="x", pady=(16, 0))
        self.main_btn = self._button(buttons, tr("update" if prev else "install"), self.run)
        self.main_btn.pack(side="right")
        self.cancel_btn = self._button(buttons, tr("cancel"), root.destroy, primary=False)
        self.cancel_btn.pack(side="right", padx=(0, 8))

        root.update_idletasks()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        root.geometry(f"+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 3}")

    @staticmethod
    def _button(parent, text, command, primary=True):
        return tk.Button(parent, text=text, command=command, bd=0, cursor="hand2", padx=16, pady=6,
                         font=(FONT_B if primary else FONT, 10),
                         bg=ACCENT if primary else CARD, fg="#111114" if primary else FG,
                         activebackground="#f09a7a" if primary else BORDER,
                         activeforeground="#111114" if primary else FG)

    def browse(self):
        chosen = filedialog.askdirectory(parent=self.root, initialdir=self.target.get(),
                                         title=tr("browse"))
        if chosen:
            path = Path(chosen)
            self.target.set(str(path if path.name == APP_NAME else path / APP_NAME))

    def log(self, text, color=MUTED):
        self.status.config(text=text, fg=color)
        self.root.update()

    def run(self):
        self.main_btn.config(state="disabled")
        self.cancel_btn.config(state="disabled")
        try:
            exe = install(Path(self.target.get()).expanduser(), self.desktop.get(), self.autostart.get(), self.log)
        except Exception as e:
            self.log(tr("failed", e=e), AMBER)
            self.main_btn.config(state="normal")
            self.cancel_btn.config(state="normal")
            return
        if self.open_after.get():
            launch(exe)
        self.log(tr("done"), GREEN)
        self.cancel_btn.pack_forget()
        self.main_btn.config(text=tr("close"), state="normal", command=self.root.destroy)


def main():
    global LANG
    args = sys.argv[1:]
    if "--lang" in args and args[args.index("--lang") + 1:][:1] in (["es"], ["en"]):
        LANG = args[args.index("--lang") + 1]
    if "--silent" in args:
        target = Path(args[args.index("--dir") + 1]) if "--dir" in args else (existing_dir() or DEFAULT_DIR)
        if "--update" in args:  # actualización desde la app: respetar lo que el usuario ya tiene
            desktop, autostart = current_shortcut_and_autostart()
        else:
            desktop, autostart = "--no-desktop" not in args, "--no-autostart" not in args
        try:
            exe = install(target, desktop, autostart, log=lambda _: None)
        except Exception:
            if "--update" in args and (target / EXE).exists():  # no dejar al usuario sin la app
                launch(target / EXE)
            sys.exit(1)
        if "--no-launch" not in args:
            launch(exe)
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    Wizard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
