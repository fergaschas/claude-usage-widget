//! Conexión con Claude: dónde está Claude Code, su sesión y sus credenciales (también en WSL).

use crate::core::{self, env_path, home, now, APP_VERSION};
use serde_json::Value;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Mutex;
use std::time::Duration;
use winreg::enums::{HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE};
use winreg::RegKey;

pub const NO_WINDOW: u32 = 0x0800_0000;
const NEW_CONSOLE: u32 = 0x0000_0010;

fn env_keys() -> [(RegKey, &'static str); 2] {
    [
        (RegKey::predef(HKEY_CURRENT_USER), "Environment"),
        (RegKey::predef(HKEY_LOCAL_MACHINE), r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ]
}

fn expand(s: &str) -> String {
    // %VAR% → valor (las variables del registro vienen sin expandir)
    let mut out = String::new();
    let mut rest = s;
    while let Some(start) = rest.find('%') {
        out.push_str(&rest[..start]);
        let after = &rest[start + 1..];
        match after.find('%') {
            Some(end) => {
                let name = &after[..end];
                out.push_str(&std::env::var(name).unwrap_or_else(|_| format!("%{name}%")));
                rest = &after[end + 1..];
            }
            None => {
                out.push_str(&rest[start..]);
                rest = "";
            }
        }
    }
    out + rest
}

fn reg_env(name: &str) -> Vec<String> {
    env_keys()
        .iter()
        .filter_map(|(hive, key)| {
            let k = hive.open_subkey(key).ok()?;
            k.get_value::<String, _>(name).ok().map(|v| expand(&v))
        })
        .collect()
}

/// Variable de entorno actual o, si no está, la guardada en el registro (p. ej. recién creada).
pub fn env(name: &str) -> String {
    match std::env::var(name) {
        Ok(v) if !v.is_empty() => v,
        _ => reg_env(name).into_iter().next().unwrap_or_default(),
    }
}

/// PATH del proceso más el del registro, por si Claude Code se acaba de instalar.
pub fn search_path() -> Vec<PathBuf> {
    let mut all = vec![std::env::var("PATH").unwrap_or_default()];
    all.extend(reg_env("Path"));
    all.iter().flat_map(|p| p.split(';')).filter(|s| !s.is_empty()).map(PathBuf::from).collect()
}

fn which(name: &str) -> Option<PathBuf> {
    let exts = ["exe", "cmd", "bat"];
    search_path().into_iter().find_map(|dir| exts.iter().map(|e| dir.join(format!("{name}.{e}"))).find(|p| p.is_file()))
}

/// Ubicaciones conocidas de Claude Code en Windows, por orden de preferencia.
pub fn claude_candidates() -> Vec<PathBuf> {
    let (h, local, roaming) = (home(), env_path("LOCALAPPDATA"), env_path("APPDATA"));
    let pf = std::env::var_os("ProgramFiles").map(PathBuf::from).unwrap_or(PathBuf::from(r"C:\Program Files"));
    let mut out = vec![
        local.join(r"Microsoft\WinGet\Links\claude.exe"),
        h.join(r".local\bin\claude.exe"),
        roaming.join(r"npm\claude.cmd"),
        pf.join(r"nodejs\claude.cmd"),
        h.join(r"scoop\shims\claude.exe"),
        h.join(r".bun\bin\claude.exe"),
        local.join(r"Volta\bin\claude.exe"),
    ];
    let nvm = env("NVM_SYMLINK");
    if !nvm.is_empty() {
        out.push(Path::new(&nvm).join("claude.cmd"));
    }
    // copia incluida en la app de escritorio de Claude (pestaña Code): la versión más reciente
    let mut bundled: Vec<(std::time::SystemTime, PathBuf)> = Vec::new();
    if let Ok(a) = std::fs::read_dir(roaming.join(r"Claude\claude-code")) {
        for d1 in a.flatten() {
            if let Ok(b) = std::fs::read_dir(d1.path()) {
                for d2 in b.flatten() {
                    let exe = d2.path().join("claude.exe");
                    if let Ok(m) = exe.metadata().and_then(|m| m.modified()) {
                        bundled.push((m, exe));
                    }
                }
            }
        }
    }
    bundled.sort_by(|a, b| b.0.cmp(&a.0));
    out.extend(bundled.into_iter().map(|(_, p)| p));
    out
}

pub fn find_claude() -> Option<PathBuf> {
    which("claude").or_else(|| claude_candidates().into_iter().find(|c| c.exists()))
}

pub fn claude_source(claude: &Option<PathBuf>) -> &'static str {
    match claude {
        Some(c) if c.to_string_lossy().contains(r"\Claude\claude-code") => "desktop",
        _ => "cli",
    }
}

static WSL_CACHE: Mutex<(f64, Option<(PathBuf, String)>)> = Mutex::new((0.0, None));

/// (ruta, distro) de unas credenciales de Claude Code dentro de WSL. Se cachea 2 min
/// porque consultar WSL puede arrancar la distribución.
pub fn wsl_credentials() -> Option<(PathBuf, String)> {
    {
        let c = WSL_CACHE.lock().unwrap();
        if now() - c.0 < 120.0 {
            return c.1.clone();
        }
    }
    let mut result = None;
    if let Some(out) = run_with_timeout(Command::new("wsl.exe").args(["-l", "-q"]), 15) {
        let utf16: Vec<u16> = out.chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
        let text = String::from_utf16_lossy(&utf16).replace('\0', "");
        for distro in text.lines().map(str::trim).filter(|d| !d.is_empty()) {
            let root = PathBuf::from(format!(r"\\wsl.localhost\{distro}"));
            let mut hits: Vec<PathBuf> = std::fs::read_dir(root.join("home"))
                .into_iter()
                .flatten()
                .flatten()
                .map(|u| u.path().join(r".claude\.credentials.json"))
                .filter(|p| p.exists())
                .collect();
            let root_creds = root.join(r"root\.claude\.credentials.json");
            if root_creds.exists() {
                hits.push(root_creds);
            }
            if let Some(hit) = hits.into_iter().next() {
                result = Some((hit, distro.to_string()));
                break;
            }
        }
    }
    *WSL_CACHE.lock().unwrap() = (now(), result.clone());
    result
}

/// (ruta de .credentials.json, distro WSL). Si no hay ninguna, la ruta por defecto.
pub fn find_credentials(status: &Value) -> (PathBuf, Option<String>) {
    let mut dirs: Vec<PathBuf> = [env("CLAUDE_CONFIG_DIR"), status["configDirectory"].as_str().unwrap_or("").to_string()]
        .into_iter()
        .filter(|d| !d.is_empty())
        .map(PathBuf::from)
        .collect();
    dirs.push(home().join(".claude"));
    for d in dirs {
        if d.join(".credentials.json").exists() {
            return (d.join(".credentials.json"), None);
        }
    }
    match wsl_credentials() {
        Some((p, distro)) => (p, Some(distro)),
        None => (home().join(r".claude\.credentials.json"), None),
    }
}

/// Ejecuta un comando sin ventana y devuelve su salida estándar, o None si falla o tarda demasiado.
fn run_with_timeout(cmd: &mut Command, secs: u64) -> Option<Vec<u8>> {
    let child = cmd
        .creation_flags(NO_WINDOW)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
        .ok()?;
    let (tx, rx) = std::sync::mpsc::channel();
    let id = child.id();
    std::thread::spawn(move || {
        let _ = tx.send(child.wait_with_output());
    });
    match rx.recv_timeout(Duration::from_secs(secs)) {
        Ok(Ok(out)) => Some(out.stdout),
        Ok(Err(_)) => None,
        Err(_) => {
            let _ = Command::new("taskkill").args(["/f", "/t", "/pid", &id.to_string()]).creation_flags(NO_WINDOW).output();
            None
        }
    }
}

/// Rust ejecuta también los .cmd de npm (a través de cmd.exe, con el escapado correcto)
fn claude_cmd(claude: &Path) -> Command {
    Command::new(claude)
}

/// `claude auth status --json`: {loggedIn, authMethod, email, orgName, subscriptionType, ...}
pub fn auth_status(claude: &Path) -> Value {
    run_with_timeout(claude_cmd(claude).args(["auth", "status", "--json"]), 30)
        .and_then(|out| serde_json::from_slice(&out).ok())
        .unwrap_or(Value::Null)
}

fn claude_version(claude: &Path) -> String {
    run_with_timeout(claude_cmd(claude).arg("--version"), 30)
        .map(|o| String::from_utf8_lossy(&o).split_whitespace().next().unwrap_or("").to_string())
        .unwrap_or_default()
}

#[derive(Debug, Clone)]
pub struct ConnInfo {
    pub claude: Option<PathBuf>,
    pub source: &'static str,
    pub status: Value,
    pub version: String,
    pub creds: PathBuf,
    pub wsl: Option<String>,
    pub creds_ok: bool,
}

static LAST_SUMMARY: Mutex<String> = Mutex::new(String::new());

pub fn check_connection() -> ConnInfo {
    let claude = find_claude();
    let status = claude.as_deref().map(auth_status).unwrap_or(Value::Null);
    let (creds, wsl) = find_credentials(&status);
    let summary = format!(
        "claude={:?} ({}) loggedIn={} authMethod={} credenciales={} existe={} wsl={:?}",
        claude,
        claude_source(&claude),
        status["loggedIn"],
        status["authMethod"],
        creds.display(),
        creds.exists(),
        wsl
    );
    {
        let mut last = LAST_SUMMARY.lock().unwrap();
        if *last != summary {
            crate::info!("conexión: {summary}");
            *last = summary;
        }
    }
    ConnInfo {
        source: claude_source(&claude),
        version: claude.as_deref().map(claude_version).unwrap_or_default(),
        creds_ok: creds.exists(),
        claude,
        status,
        creds,
        wsl,
    }
}

pub fn account_text(status: &Value) -> String {
    let plan = core::capitalize(status["subscriptionType"].as_str().unwrap_or(""));
    [status["email"].as_str().unwrap_or(""), status["orgName"].as_str().unwrap_or(""), &plan]
        .iter()
        .filter(|s| !s.is_empty())
        .copied()
        .collect::<Vec<_>>()
        .join(" · ")
}

/// Texto para pegar en una incidencia: qué se ha buscado y qué se ha encontrado (sin datos de la cuenta).
pub fn diagnostics(info: Option<&ConnInfo>) -> String {
    let os = Command::new("cmd")
        .args(["/c", "ver"])
        .creation_flags(NO_WINDOW)
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default();
    let mut lines = vec![format!("{} {APP_VERSION} (Tauri) · {os}", core::APP_NAME)];
    if let Some(i) = info {
        lines.push(format!("claude: {:?} ({}) {}", i.claude, i.source, i.version));
        lines.push(format!("auth status: loggedIn={} authMethod={}", i.status["loggedIn"], i.status["authMethod"]));
        lines.push(format!("credentials: {} exists={} wsl={:?}", i.creds.display(), i.creds_ok, i.wsl));
    }
    let cfg = env("CLAUDE_CONFIG_DIR");
    lines.push(format!("CLAUDE_CONFIG_DIR={}", if cfg.is_empty() { "-" } else { &cfg }));
    lines.push("candidates:".into());
    for c in claude_candidates() {
        lines.push(format!("  [{}] {}", if c.exists() { "x" } else { " " }, c.display()));
    }
    lines.push("PATH (claude/node/npm):".into());
    for p in search_path() {
        let l = p.to_string_lossy().to_lowercase();
        if ["claude", "node", "npm", "bun", "scoop", "volta", ".local"].iter().any(|k| l.contains(k)) {
            lines.push(format!("  {}", p.display()));
        }
    }
    if let Ok(text) = std::fs::read_to_string(core::data_dir().join("claude-usage.log")) {
        let all: Vec<&str> = text.lines().collect();
        lines.push("log (últimas líneas):".into());
        lines.extend(all[all.len().saturating_sub(40)..].iter().map(|l| format!("  {l}")));
    }
    lines.join("\n")
}

/// Abre una consola visible; solo se queda abierta si el comando falla, para poder leer el error.
pub fn run_in_console(command: &str) {
    let _ = Command::new("cmd").raw_arg(format!("/c \"{command} || pause\"")).creation_flags(NEW_CONSOLE).spawn();
}

pub fn has_winget() -> bool {
    which("winget").is_some()
}
