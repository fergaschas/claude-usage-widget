//! Windows: inicio automático, notificaciones, instancia única, accesos directos y desinstalación.

use crate::connect::NO_WINDOW;
use crate::core::{
    self, app_dir, app_file, data_dir, t, APP_ID, APP_NAME, INSTANCE_PORT, LEGACY_APP_ID, LEGACY_NAME, RUN_KEY, UNINSTALL_KEY,
};
use std::io::Write;
use std::net::{TcpListener, TcpStream};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;
use winreg::enums::{HKEY_CURRENT_USER, KEY_SET_VALUE};
use winreg::RegKey;

fn aumid_key(id: &str) -> String {
    format!(r"Software\Classes\AppUserModelId\{id}")
}
pub const ICON_PNG: &[u8] = include_bytes!("../icons/128x128@2x.png");

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

fn hkcu() -> RegKey {
    RegKey::predef(HKEY_CURRENT_USER)
}

pub fn reg_value(key: &str, name: &str) -> String {
    hkcu().open_subkey(key).and_then(|k| k.get_value::<String, _>(name)).unwrap_or_default()
}

// ── inicio con Windows ──

/// También cuenta la entrada de la versión 2 ("Claude Usage"), que se migra al volver a activarlo.
pub fn get_autostart() -> bool {
    let run = hkcu().open_subkey(RUN_KEY);
    [APP_NAME, LEGACY_NAME].iter().any(|n| run.as_ref().is_ok_and(|k| k.get_value::<String, _>(n).is_ok()))
}

pub fn set_autostart(enabled: bool) -> std::io::Result<()> {
    set_autostart_for(&app_file(), enabled)
}

pub fn set_autostart_for(exe: &Path, enabled: bool) -> std::io::Result<()> {
    let k = hkcu().open_subkey_with_flags(RUN_KEY, KEY_SET_VALUE)?;
    let _ = k.delete_value(LEGACY_NAME);
    if enabled {
        k.set_value(APP_NAME, &format!("\"{}\" --startup", exe.display()))
    } else {
        let _ = k.delete_value(APP_NAME);
        Ok(())
    }
}

// ── notificaciones ──

/// Registra el AppUserModelID para que las notificaciones salgan con el nombre e icono de la app.
pub fn register_app_id() {
    let icon = data_dir().join("icon.png");
    let _ = std::fs::create_dir_all(data_dir());
    if !icon.exists() {
        let _ = std::fs::write(&icon, ICON_PNG);
    }
    let _ = hkcu().delete_subkey_all(aumid_key(LEGACY_APP_ID));
    if let Ok((k, _)) = hkcu().create_subkey(aumid_key(APP_ID)) {
        let _ = k.set_value("DisplayName", &APP_NAME);
        let _ = k.set_value("IconUri", &icon.display().to_string());
    }
    // SAFETY: cadena terminada en cero que vive durante la llamada
    unsafe {
        windows_sys::Win32::UI::Shell::SetCurrentProcessExplicitAppUserModelID(wide(APP_ID).as_ptr());
    }
}

pub fn toast(title: &str, body: &str) {
    let (title, body) = (title.to_string(), body.to_string());
    std::thread::spawn(move || {
        let r = tauri_winrt_notification::Toast::new(APP_ID).title(&title).text1(&body).show();
        if let Err(e) = r {
            crate::warn!("notificación fallida: {e:?}");
        }
    });
}

// ── pantalla ──

/// ¿Hay una app a pantalla completa (juego, vídeo, presentación) en primer plano?
pub fn fullscreen_app() -> bool {
    let mut state = 0;
    // SAFETY: puntero a un entero local
    let ok = unsafe { windows_sys::Win32::UI::Shell::SHQueryUserNotificationState(&mut state) } == 0;
    // QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE
    ok && matches!(state, 2..=4)
}

/// ¿Está pulsado el botón izquierdo del ratón? (para saber si se sigue arrastrando una ventana)
pub fn mouse_down() -> bool {
    // SAFETY: llamada sin punteros
    (unsafe { windows_sys::Win32::UI::Input::KeyboardAndMouse::GetAsyncKeyState(0x01) } as u16 & 0x8000) != 0
}

// ── instancia única (mismo protocolo que la versión 2: el instalador envía "quit") ──

/// El socket si somos la primera instancia; si no, pide a la existente que se muestre.
pub fn claim_single_instance() -> Option<TcpListener> {
    match TcpListener::bind(("127.0.0.1", INSTANCE_PORT)) {
        Ok(l) => Some(l),
        Err(_) => {
            send_instance(b"show");
            None
        }
    }
}

pub fn send_instance(msg: &[u8]) -> bool {
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], INSTANCE_PORT));
    match TcpStream::connect_timeout(&addr, Duration::from_secs(2)) {
        Ok(mut c) => c.write_all(msg).is_ok(),
        Err(_) => false,
    }
}

// ── utilidades ──

/// Abre una ruta o URL con el programa predeterminado.
pub fn shell_open(target: &str) {
    let (op, file) = (wide("open"), wide(target));
    // SAFETY: cadenas terminadas en cero que viven durante la llamada
    unsafe {
        windows_sys::Win32::UI::Shell::ShellExecuteW(
            std::ptr::null_mut(),
            op.as_ptr(),
            file.as_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            1,
        );
    }
}

/// Cuadro de mensaje nativo. `ask`: Sí/No. Devuelve true si se acepta.
pub fn message_box(text: &str, ask: bool) -> bool {
    use windows_sys::Win32::UI::WindowsAndMessaging::*;
    let flags = if ask { MB_YESNO | MB_ICONQUESTION } else { MB_OK | MB_ICONINFORMATION };
    // SAFETY: cadenas terminadas en cero que viven durante la llamada
    let r = unsafe {
        MessageBoxW(std::ptr::null_mut(), wide(text).as_ptr(), wide(APP_NAME).as_ptr(), flags | MB_SETFOREGROUND | MB_TOPMOST)
    };
    r == IDYES || r == IDOK
}

pub fn powershell(script: &str, envs: &[(&str, String)]) -> std::io::Result<std::process::Output> {
    let mut c = Command::new("powershell");
    c.args(["-NoProfile", "-NonInteractive", "-Command", script]).creation_flags(NO_WINDOW);
    for (k, v) in envs {
        c.env(k, v);
    }
    c.output()
}

pub fn known_folder(csidl: u32) -> PathBuf {
    let mut buf = [0u16; 260];
    // SAFETY: búfer de MAX_PATH caracteres como pide la API
    unsafe {
        windows_sys::Win32::UI::Shell::SHGetFolderPathW(std::ptr::null_mut(), csidl as i32, std::ptr::null_mut(), 0, buf.as_mut_ptr());
    }
    let len = buf.iter().position(|&c| c == 0).unwrap_or(0);
    PathBuf::from(String::from_utf16_lossy(&buf[..len]))
}

pub const CSIDL_PROGRAMS: u32 = 0x02;
pub const CSIDL_DESKTOPDIRECTORY: u32 = 0x10;

// ── desinstalación (TokenPace.exe --uninstall [--silent]) ──

pub fn uninstall(silent: bool) {
    if !silent && !message_box(&t("uninstall.ask"), true) {
        return;
    }
    // cerrar la instancia abierta
    if send_instance(b"quit") {
        std::thread::sleep(Duration::from_millis(1500));
    }
    let exe = app_file();
    let dir = app_dir().display().to_string().to_lowercase();
    if [APP_NAME, LEGACY_NAME].iter().any(|n| reg_value(RUN_KEY, n).to_lowercase().contains(&dir)) {
        let _ = set_autostart(false);
    }
    for id in [APP_ID, LEGACY_APP_ID] {
        let _ = hkcu().delete_subkey_all(aumid_key(id));
    }
    let location = reg_value(UNINSTALL_KEY, "InstallLocation");
    if !location.is_empty() && Path::new(&location) == app_dir() {
        let _ = hkcu().delete_subkey_all(UNINSTALL_KEY);
    }
    for folder in [CSIDL_PROGRAMS, CSIDL_DESKTOPDIRECTORY] {
        for name in [APP_NAME, LEGACY_NAME] {
            let _ = std::fs::remove_file(known_folder(folder).join(format!("{name}.lnk")));
        }
    }
    // los archivos se borran cuando este proceso ya ha terminado; solo los nuestros, y la
    // carpeta solo si queda vacía
    let steps = [
        "ping -n 3 127.0.0.1 >nul".to_string(),
        format!("rmdir /s /q \"{}\"", data_dir().display()),
        format!("rmdir /s /q \"{}\"", core::webview_dir().display()),
        format!("del /f /q \"{}\"", exe.display()),
        format!("rmdir \"{}\"", app_dir().display()),
    ];
    let _ = Command::new("cmd")
        .raw_arg(format!("/c {}", steps.join(" & ")))
        .current_dir(core::home())
        .creation_flags(NO_WINDOW)
        .spawn();
    if !silent {
        message_box(&t("uninstall.done"), false);
    }
}
