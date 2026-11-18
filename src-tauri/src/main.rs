//! TokenPace — widget de escritorio con el % de uso de tu plan de Claude y su ritmo de consumo.
//!
//! Fuentes de datos:
//!   * Límites del plan: el endpoint que usa `/usage` en Claude Code, autenticado con el
//!     token OAuth que Claude Code guarda en ~/.claude/.credentials.json.
//!   * Tokens: los registros locales de Claude Code (~/.claude/projects/**/*.jsonl).
//!
//! El mismo ejecutable es la app, su instalador (si se llama *setup*.exe) y su desinstalador (--uninstall).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod app;
mod codex;
mod connect;
mod core;
mod installer;
mod tokens;
mod update;
mod usage;
mod winsys;

use serde_json::{json, Value};
use std::path::PathBuf;
use tauri::{Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

fn arg_value(args: &[String], flag: &str) -> Option<String> {
    args.iter().position(|a| a == flag).and_then(|i| args.get(i + 1)).cloned()
}

fn main() {
    let args: Vec<String> = std::env::args().collect();

    if installer::is_setup() {
        core::set_lang(&arg_value(&args, "--lang").unwrap_or_default());
        if args.iter().any(|a| a == "--silent") {
            std::process::exit(installer::run_silent(&args));
        }
        run_installer();
        return;
    }

    let settings = core::Settings::load();
    core::set_lang(&settings.str("lang"));
    if args.iter().any(|a| a == "--uninstall") {
        winsys::uninstall(args.iter().any(|a| a == "--silent"));
        return;
    }
    let _ = std::fs::create_dir_all(core::data_dir());
    let Some(server) = winsys::claim_single_instance() else { return };
    winsys::register_app_id();
    info!("inicio: {} {} (tauri, idioma {})", core::APP_NAME, core::APP_VERSION, core::lang());

    let server = std::sync::Mutex::new(Some(server));
    tauri::Builder::default()
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    if event.state() == tauri_plugin_global_shortcut::ShortcutState::Pressed {
                        app::toggle_visible(app);
                    }
                })
                .build(),
        )
        .plugin(tauri_plugin_dialog::init())
        .manage(app::AppState::new())
        .on_menu_event(|app, event| app::on_menu(app, event.id().as_ref()))
        .on_window_event(app::on_window_event)
        .invoke_handler(tauri::generate_handler![
            app::get_state,
            app::i18n,
            app::refresh,
            app::set_setting,
            app::menu_action,
            app::open_menu,
            app::history_series,
            app::fit_window,
            app::show_mode_cmd,
            app::hide,
            app::start_update_cmd,
            app::open_connect_cmd,
            app::connect_action,
            app::diagnostics,
        ])
        .setup(move |a| {
            let server = server.lock().unwrap().take().expect("socket");
            app::setup(a.handle(), server)?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error al iniciar la app");
}

// ───────────────────────── ventana del instalador ─────────────────────────

#[tauri::command]
fn installer_info() -> Value {
    let existing = installer::existing_dir();
    json!({
        "lang": core::lang(),
        "version": core::APP_VERSION,
        "dir": existing.clone().unwrap_or_else(installer::default_dir),
        "update": existing.is_some(),
        "has_session": installer::has_session(),
    })
}

#[tauri::command]
async fn installer_browse(app: tauri::AppHandle, current: String) -> Option<String> {
    use tauri_plugin_dialog::DialogExt;
    let chosen = app
        .dialog()
        .file()
        .set_title(core::t("setup.browse"))
        .set_directory(&current)
        .blocking_pick_folder()?;
    let path = chosen.into_path().ok()?;
    let path = if path.file_name().is_some_and(|n| n == core::APP_NAME) { path } else { path.join(core::APP_NAME) };
    Some(path.display().to_string())
}

#[tauri::command]
async fn installer_run(app: tauri::AppHandle, dir: String, desktop: bool, autostart: bool, open: bool) -> Result<(), String> {
    let target = PathBuf::from(dir.trim());
    let emit = |key: &str| {
        let _ = app.emit("installer-step", core::t(key));
    };
    let exe = installer::install(&target, desktop, autostart, emit)
        .map_err(|e| core::tr("setup.failed", &[("e", e)]))?;
    if open {
        installer::launch(&exe);
    }
    Ok(())
}

#[tauri::command]
fn installer_i18n() -> &'static str {
    core::I18N
}

fn run_installer() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            installer_info,
            installer_browse,
            installer_run,
            installer_i18n,
            app::fit_window
        ])
        .setup(|a| {
            let title = core::tr("setup.title", &[("app", core::APP_NAME.into())]);
            WebviewWindowBuilder::new(a, "installer", WebviewUrl::App("index.html".into()))
                .title(title)
                .inner_size(520.0, 460.0)
                .resizable(false)
                .maximizable(false)
                .theme(Some(tauri::Theme::Dark))
                .background_color(tauri::window::Color(17, 17, 20, 255))
                .center()
                .visible(false)
                .build()?;
            let _ = a.get_webview_window("installer");
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error al iniciar el instalador");
}
