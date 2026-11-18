//! Instalador. El mismo ejecutable hace de instalador cuando su nombre contiene "setup"
//! (TokenPace-Setup.exe, o ClaudeUsage-Setup.exe para las versiones 2.x que se actualizan
//! solas): se copia a sí mismo en la carpeta elegida como TokenPace.exe.
//! Al actualizar desde "Claude Usage" (2.x) quita sus accesos directos, su inicio con Windows
//! y su ejecutable, y conserva la carpeta y los datos (data\).
//!
//! Uso silencioso (compatible con la versión 2):
//!   TokenPace-Setup.exe --silent [--dir RUTA] [--no-desktop] [--no-autostart] [--no-launch]
//!   --update: lo usa la app al actualizarse; conserva el acceso directo y el inicio con Windows actuales.
//! Idioma: el de Windows; se puede forzar con --lang es|en.

use crate::connect::NO_WINDOW;
use crate::core::{
    app_file, env_path, home, t, APP_NAME, APP_VERSION, EXE_NAME, LEGACY_EXE, LEGACY_NAME, RUN_KEY, UNINSTALL_KEY,
};
use crate::winsys::{self, known_folder, CSIDL_DESKTOPDIRECTORY, CSIDL_PROGRAMS};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;
use winreg::enums::HKEY_CURRENT_USER;
use winreg::RegKey;

pub fn is_setup() -> bool {
    let name = app_file().file_name().map(|n| n.to_string_lossy().to_lowercase()).unwrap_or_default();
    name.contains("setup") || std::env::args().any(|a| a == "--install")
}

pub fn default_dir() -> PathBuf {
    let local = env_path("LOCALAPPDATA");
    let base = if local.as_os_str().is_empty() { home() } else { local };
    base.join("Programs").join(APP_NAME)
}

pub fn existing_dir() -> Option<PathBuf> {
    let loc = winsys::reg_value(UNINSTALL_KEY, "InstallLocation");
    let p = PathBuf::from(loc);
    (!p.as_os_str().is_empty() && p.exists()).then_some(p)
}

pub fn has_session() -> bool {
    home().join(r".claude\.credentials.json").exists()
}

fn stop_running() {
    if winsys::send_instance(b"quit") {
        std::thread::sleep(Duration::from_secs(2));
    }
    for exe in [EXE_NAME, LEGACY_EXE] {
        let _ = Command::new("taskkill").args(["/f", "/im", exe]).creation_flags(NO_WINDOW).output();
    }
    std::thread::sleep(Duration::from_millis(500));
}

fn make_shortcuts(exe: &Path, desktop: bool) -> Result<(), String> {
    let ps = r#"
$sh = New-Object -ComObject WScript.Shell
function Link($folder) {
    $s = $sh.CreateShortcut((Join-Path ([Environment]::GetFolderPath($folder)) "$env:CU_NAME.lnk"))
    $s.TargetPath = $env:CU_EXE; $s.WorkingDirectory = $env:CU_DIR; $s.IconLocation = "$env:CU_EXE,0"
    $s.Description = $env:CU_DESC; $s.Save()
}
Link 'Programs'
if ($env:CU_DESK -eq '1') { Link 'Desktop' }
else { Remove-Item (Join-Path ([Environment]::GetFolderPath('Desktop')) "$env:CU_NAME.lnk") -ErrorAction SilentlyContinue }
"#;
    let out = winsys::powershell(
        ps,
        &[
            ("CU_EXE", exe.display().to_string()),
            ("CU_DIR", exe.parent().unwrap_or(exe).display().to_string()),
            ("CU_DESK", if desktop { "1" } else { "0" }.into()),
            ("CU_DESC", t("setup.link_desc")),
            ("CU_NAME", APP_NAME.to_string()),
        ],
    )
    .map_err(|e| e.to_string())?;
    if out.status.success() {
        Ok(())
    } else {
        Err(String::from_utf8_lossy(&out.stderr).trim().to_string())
    }
}

fn register(exe: &Path, autostart: bool) -> Result<(), String> {
    winsys::set_autostart_for(exe, autostart).map_err(|e| e.to_string())?;
    let (k, _) = RegKey::predef(HKEY_CURRENT_USER).create_subkey(UNINSTALL_KEY).map_err(|e| e.to_string())?;
    let dir = exe.parent().unwrap_or(exe).display().to_string();
    for (name, value) in [
        ("DisplayName", APP_NAME.to_string()),
        ("DisplayVersion", APP_VERSION.to_string()),
        ("Publisher", APP_NAME.to_string()),
        ("DisplayIcon", format!("{},0", exe.display())),
        ("InstallLocation", dir),
        ("UninstallString", format!("\"{}\" --uninstall", exe.display())),
        ("QuietUninstallString", format!("\"{}\" --uninstall --silent", exe.display())),
    ] {
        k.set_value(name, &value).map_err(|e| e.to_string())?;
    }
    let size = exe.metadata().map(|m| (m.len() / 1024) as u32).unwrap_or(0);
    for (name, value) in [("NoModify", 1u32), ("NoRepair", 1), ("EstimatedSize", size)] {
        k.set_value(name, &value).map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// (¿hay acceso directo en el escritorio?, ¿arranca con Windows?) en la instalación actual,
/// con el nombre nuevo o con el de la versión 2.
fn current_shortcut_and_autostart() -> (bool, bool) {
    let names = [APP_NAME, LEGACY_NAME];
    let desktop = names.iter().any(|n| known_folder(CSIDL_DESKTOPDIRECTORY).join(format!("{n}.lnk")).exists());
    let autostart = names.iter().any(|n| !winsys::reg_value(RUN_KEY, n).is_empty());
    (desktop, autostart)
}

/// Quita lo que dejó "Claude Usage" (2.x): sus accesos directos y su ejecutable en la carpeta de destino.
fn remove_legacy(target: &Path) {
    for folder in [CSIDL_PROGRAMS, CSIDL_DESKTOPDIRECTORY] {
        let _ = std::fs::remove_file(known_folder(folder).join(format!("{LEGACY_NAME}.lnk")));
    }
    let old = target.join(LEGACY_EXE);
    if old.exists() && old != app_file() {
        let _ = std::fs::remove_file(old);
    }
}

pub fn launch(exe: &Path) {
    const DETACHED: u32 = 0x0000_0008;
    let _ = Command::new(exe).current_dir(exe.parent().unwrap_or(exe)).creation_flags(DETACHED).spawn();
}

/// Instala en `target`. `step` recibe la clave de texto de cada paso.
pub fn install(target: &Path, desktop: bool, autostart: bool, step: impl Fn(&str)) -> Result<PathBuf, String> {
    step("setup.step.stop");
    stop_running();
    step("setup.step.copy");
    std::fs::create_dir_all(target).map_err(|e| e.to_string())?;
    let exe = target.join(EXE_NAME);
    let me = app_file();
    if me != exe {
        // el .exe anterior puede tardar un momento en liberarse al cerrarse
        let mut attempt = 0;
        loop {
            match std::fs::copy(&me, &exe) {
                Ok(_) => break,
                Err(e) if attempt < 19 => {
                    let _ = e;
                    attempt += 1;
                    std::thread::sleep(Duration::from_millis(500));
                }
                Err(e) => return Err(e.to_string()),
            }
        }
    }
    step("setup.step.links");
    remove_legacy(target);
    make_shortcuts(&exe, desktop)?;
    step("setup.step.register");
    register(&exe, autostart)?;
    Ok(exe)
}

fn arg_after(args: &[String], flag: &str) -> Option<String> {
    args.iter().position(|a| a == flag).and_then(|i| args.get(i + 1)).cloned()
}

/// Modo silencioso. Devuelve el código de salida.
pub fn run_silent(args: &[String]) -> i32 {
    let target = arg_after(args, "--dir").map(PathBuf::from).or_else(existing_dir).unwrap_or_else(default_dir);
    let has = |f: &str| args.iter().any(|a| a == f);
    let (desktop, autostart) = if has("--update") {
        // actualización desde la app: respetar lo que el usuario ya tiene
        current_shortcut_and_autostart()
    } else {
        (!has("--no-desktop"), !has("--no-autostart"))
    };
    match install(&target, desktop, autostart, |_| {}) {
        Ok(exe) => {
            if !has("--no-launch") {
                launch(&exe);
            }
            0
        }
        Err(e) => {
            crate::warn!("instalación silenciosa fallida: {e}");
            // no dejar al usuario sin la app
            let old = [EXE_NAME, LEGACY_EXE].iter().map(|n| target.join(n)).find(|p| p.exists());
            if let (true, Some(old)) = (has("--update"), old) {
                launch(&old);
            }
            1
        }
    }
}
