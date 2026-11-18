//! Rutas, constantes, registro (log), idiomas y ajustes compartidos por toda la app.

use serde_json::{json, Map, Value};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock, RwLock};

pub const APP_NAME: &str = "TokenPace";
pub const APP_VERSION: &str = env!("CARGO_PKG_VERSION");
pub const APP_ID: &str = "TokenPace.App";
pub const EXE_NAME: &str = "TokenPace.exe";
pub const IDENTIFIER: &str = "io.github.fergaschas.tokenpace";
/// nombre de la app hasta la versión 2.5 (accesos directos, inicio con Windows, ejecutable)
pub const LEGACY_NAME: &str = "Claude Usage";
pub const LEGACY_EXE: &str = "ClaudeUsage.exe";
pub const LEGACY_APP_ID: &str = "ClaudeUsage.Tracker";
pub const USAGE_URL: &str = "https://api.anthropic.com/api/oauth/usage";
pub const USAGE_PAGE: &str = "https://claude.ai/settings/usage";
pub const CLAUDE_CODE_PAGE: &str = "https://claude.com/claude-code";
pub const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
/// misma clave que la versión 2, para que el instalador encuentre y actualice la instalación existente
pub const UNINSTALL_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ClaudeUsage";
/// en desarrollo, otro puerto: así se puede probar con la versión instalada abierta
pub const INSTANCE_PORT: u16 = if cfg!(debug_assertions) { 47824 } else { 47823 };
pub const HISTORY_DAYS: f64 = 8.0;
pub const TOKEN_DAYS: i64 = 7;
pub const THRESHOLDS: [f64; 3] = [75.0, 90.0, 100.0];
/// esperas tras un 429 de Anthropic (no envía Retry-After): crecen mientras dure el límite
pub const RATE_LIMIT_STEPS: [u64; 4] = [300, 600, 1200, 1800];

pub fn user_agent() -> String {
    format!("tokenpace/{APP_VERSION}")
}

// ───────────────────────── rutas ─────────────────────────

pub fn app_file() -> PathBuf {
    std::env::current_exe().ok().and_then(|p| p.canonicalize().ok().or(Some(p))).map(strip_unc).unwrap_or_default()
}

/// canonicalize() en Windows devuelve rutas \\?\C:\...; para el registro y los mensajes, sin prefijo
fn strip_unc(p: PathBuf) -> PathBuf {
    let s = p.to_string_lossy();
    match s.strip_prefix(r"\\?\") {
        Some(rest) if !rest.starts_with("UNC") => PathBuf::from(rest),
        _ => p,
    }
}

pub fn app_dir() -> PathBuf {
    app_file().parent().map(Path::to_path_buf).unwrap_or_default()
}

pub fn data_dir() -> PathBuf {
    app_dir().join("data")
}

pub fn home() -> PathBuf {
    std::env::var_os("USERPROFILE").map(PathBuf::from).unwrap_or_default()
}

pub fn env_path(name: &str) -> PathBuf {
    std::env::var_os(name).map(PathBuf::from).unwrap_or_default()
}

/// carpeta de datos de WebView2 (caché del navegador integrado); se borra al desinstalar
pub fn webview_dir() -> PathBuf {
    env_path("LOCALAPPDATA").join(IDENTIFIER)
}

// ───────────────────────── registro ─────────────────────────

static LOG_LOCK: Mutex<()> = Mutex::new(());

pub fn log(level: &str, msg: &str) {
    let _guard = LOG_LOCK.lock();
    let path = data_dir().join("claude-usage.log");
    if fs::metadata(&path).map(|m| m.len() > 256 * 1024).unwrap_or(false) {
        let _ = fs::rename(&path, path.with_extension("log.1"));
    }
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(f, "{} {level} {msg}", chrono::Local::now().format("%Y-%m-%d %H:%M:%S"));
    }
}

#[macro_export]
macro_rules! info { ($($t:tt)*) => { $crate::core::log("INFO", &format!($($t)*)) } }
#[macro_export]
macro_rules! warn { ($($t:tt)*) => { $crate::core::log("WARNING", &format!($($t)*)) } }

// ───────────────────────── idiomas ─────────────────────────

pub const I18N: &str = include_str!("../../src/i18n.json");

fn strings() -> &'static Value {
    static S: OnceLock<Value> = OnceLock::new();
    S.get_or_init(|| serde_json::from_str(I18N).expect("i18n.json"))
}

static LANG: RwLock<String> = RwLock::new(String::new());

pub fn system_lang() -> &'static str {
    // SAFETY: llamada Win32 sin parámetros
    let id = unsafe { windows_sys::Win32::Globalization::GetUserDefaultUILanguage() };
    if id & 0x3FF == 0x0A {
        "es"
    } else {
        "en"
    }
}

pub fn lang() -> String {
    let l = LANG.read().unwrap();
    if l.is_empty() {
        system_lang().into()
    } else {
        l.clone()
    }
}

pub fn set_lang(lang: &str) {
    *LANG.write().unwrap() = if lang == "es" || lang == "en" { lang.into() } else { String::new() };
}

fn raw(key: &str) -> Value {
    let s = strings();
    s[lang()].get(key).or_else(|| s["es"].get(key)).cloned().unwrap_or(Value::String(key.into()))
}

/// Texto traducido; {nombre} se sustituye por el valor de `args`.
pub fn tr(key: &str, args: &[(&str, String)]) -> String {
    let mut text = raw(key).as_str().unwrap_or(key).to_string();
    for (k, v) in args {
        text = text.replace(&format!("{{{k}}}"), v);
    }
    text
}

pub fn t(key: &str) -> String {
    tr(key, &[])
}

pub fn tr_list(key: &str) -> Vec<String> {
    raw(key).as_array().map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect()).unwrap_or_default()
}

/// Nombre de un límite que no conocemos: "weekly_scoped" → ("Semanal", "Scoped")
fn unknown_kind(kind: &str) -> (Option<String>, String) {
    match kind.strip_prefix("weekly_") {
        Some(rest) => {
            let weekly = t("label.weekly_all").split('·').next().unwrap_or("").trim().to_string();
            (Some(weekly), capitalize(&rest.replace('_', " ")))
        }
        None => (None, capitalize(&kind.replace('_', " "))),
    }
}

pub fn label(kind: &str) -> String {
    let v = raw(&format!("label.{kind}"));
    v.as_str().filter(|s| !s.starts_with("label.")).map(String::from).unwrap_or_else(|| match unknown_kind(kind) {
        (Some(group), name) => format!("{group} · {name}"),
        (None, name) => name,
    })
}

pub fn short(kind: &str) -> String {
    let v = raw(&format!("short.{kind}"));
    v.as_str().filter(|s| !s.starts_with("short.")).map(String::from).unwrap_or_else(|| unknown_kind(kind).1)
}

pub fn capitalize(s: &str) -> String {
    let mut c = s.chars();
    c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default()
}

// ───────────────────────── ajustes ─────────────────────────

/// data\settings.json. Se guarda como objeto JSON libre para conservar claves de otras versiones.
pub struct Settings(pub Map<String, Value>);

impl Settings {
    pub fn defaults() -> Map<String, Value> {
        json!({"on_top": true, "notify": true, "interval": 60, "details": false, "chart_hours": 24,
               "check_updates": true, "mode": "widget", "alpha": 1.0, "widget_style": "large", "lang": null,
               "collapsed": false, "fade_idle": true, "hide_fullscreen": true, "codex": true})
        .as_object()
        .unwrap()
        .clone()
    }

    pub fn load() -> Self {
        let mut map = Self::defaults();
        if let Ok(text) = fs::read_to_string(data_dir().join("settings.json")) {
            if let Ok(Value::Object(saved)) = serde_json::from_str::<Value>(&text) {
                map.extend(saved);
            }
        }
        Settings(map)
    }

    pub fn save(&self) {
        let _ = fs::create_dir_all(data_dir());
        if let Ok(text) = serde_json::to_string_pretty(&self.0) {
            let _ = fs::write(data_dir().join("settings.json"), text);
        }
    }

    pub fn get(&self, key: &str) -> &Value {
        self.0.get(key).unwrap_or(&Value::Null)
    }
    pub fn bool(&self, key: &str) -> bool {
        self.get(key).as_bool().unwrap_or(false)
    }
    pub fn f64(&self, key: &str) -> f64 {
        self.get(key).as_f64().unwrap_or(0.0)
    }
    pub fn str(&self, key: &str) -> String {
        self.get(key).as_str().unwrap_or_default().to_string()
    }
    pub fn set(&mut self, key: &str, value: Value) {
        self.0.insert(key.into(), value);
    }
}

pub fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}
