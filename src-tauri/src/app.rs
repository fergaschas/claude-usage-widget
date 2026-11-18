//! Estado de la app, bucles en segundo plano, ventanas, bandeja, menú y comandos de la interfaz.

use crate::connect::{self, ConnInfo};
use crate::core::{self, label, now, short, t, tr, Settings, APP_NAME, APP_VERSION, RATE_LIMIT_STEPS, THRESHOLDS};
use crate::tokens::{self, TokenStats};
use crate::update::{self, Release};
use crate::usage::{self, FetchError, History, Limit, Pace};
use crate::winsys;
use crate::{info, warn};
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::io::Read;
use std::net::TcpListener;
use std::path::PathBuf;
use std::sync::{Condvar, Mutex, MutexGuard};
use std::time::Duration;
use tauri::menu::{CheckMenuItem, IsMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{
    AppHandle, Emitter, LogicalSize, Manager, PhysicalPosition, Runtime, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder, Window, WindowEvent,
};

const TOKEN_SCAN_SECS: u64 = 5 * 60;
const AUTH_ERRORS: [&str; 2] = ["err.no_session", "err.token"];

#[derive(Clone, PartialEq)]
enum UpdateState {
    Downloading(u64),
    Installing,
}

pub struct Inner {
    settings: Settings,
    history: History,
    limits: Vec<Limit>,
    data: Value,
    error: Option<(String, Vec<(String, String)>)>,
    updated_at: Option<f64>,
    tokens: Option<TokenStats>,
    account: Option<String>,
    credentials: PathBuf,
    update_info: Option<Release>,
    update_state: Option<UpdateState>,
    backoff_until: f64,
    backoff_step: usize,
    next_refresh: f64,
    conn: Option<ConnInfo>,
    connect_open: bool,
    install_started: bool,
    login_started: bool,
    last_connect_refresh: f64,
    hotkey: bool,
    autostart: bool,
    pending_show: bool,
    widget_ready: bool,
    shown_at: Option<f64>,
    /// el widget se movió (arrastre): al soltar se pega a los bordes cercanos
    moved_at: Option<f64>,
    /// oculto porque hay una app a pantalla completa; se vuelve a mostrar al salir
    hidden_by_fullscreen: bool,
    dirty_since: Option<f64>,
}

pub struct AppState {
    inner: Mutex<Inner>,
    wake: Condvar,
}

impl AppState {
    pub fn new() -> Self {
        let settings = Settings::load();
        let history = History::load();
        let (mut limits, mut updated_at) = (Vec::new(), None);
        // mostrar el último dato conocido mientras llega el primero
        if let Some((t, l)) = history.last_limits() {
            limits = l;
            updated_at = Some(t);
        }
        AppState {
            inner: Mutex::new(Inner {
                settings,
                history,
                limits,
                data: Value::Null,
                error: None,
                updated_at,
                tokens: None,
                account: None,
                credentials: core::home().join(r".claude\.credentials.json"),
                update_info: None,
                update_state: None,
                backoff_until: 0.0,
                backoff_step: 0,
                next_refresh: 0.0,
                conn: None,
                connect_open: false,
                install_started: false,
                login_started: false,
                last_connect_refresh: 0.0,
                hotkey: false,
                autostart: winsys::get_autostart(),
                pending_show: false,
                widget_ready: false,
                shown_at: None,
                moved_at: None,
                hidden_by_fullscreen: false,
                dirty_since: None,
            }),
            wake: Condvar::new(),
        }
    }
}

fn lock(app: &AppHandle) -> MutexGuard<'_, Inner> {
    let st: &AppState = app.state::<AppState>().inner();
    st.inner.lock().unwrap_or_else(|e| e.into_inner())
}

// ───────────────────────── instantánea para la interfaz ─────────────────────────

#[derive(Serialize)]
struct LimitView {
    kind: String,
    label: String,
    short: String,
    pct: f64,
    reset: Option<f64>,
    pace: Pace,
    /// sesión y semanal; el resto solo se muestra si el usuario lo pide
    primary: bool,
}

/// Límites principales: la sesión y el semanal general (si la API no devuelve ninguno, todos).
fn is_primary(kind: &str, limits: &[Limit]) -> bool {
    const MAIN: [&str; 2] = ["session", "weekly_all"];
    MAIN.contains(&kind) || !limits.iter().any(|l| MAIN.contains(&l.kind.as_str()))
}

#[derive(Serialize)]
struct StepView {
    state: &'static str,
    title: String,
    detail: String,
    action: Option<(String, &'static str)>,
}

#[derive(Serialize)]
struct ConnectView {
    steps: Vec<StepView>,
    done: bool,
}

#[derive(Serialize)]
struct Snapshot {
    lang: String,
    version: &'static str,
    hotkey: bool,
    settings: Value,
    limits: Vec<LimitView>,
    extra: Option<f64>,
    error: Option<String>,
    error_key: Option<String>,
    /// hora del próximo intento mientras Anthropic limita las consultas
    retry_at: Option<String>,
    updated_at: Option<f64>,
    account: Option<String>,
    update: Option<Value>,
    tokens: Option<TokenStats>,
    connect: Option<ConnectView>,
}

fn error_text(err: &Option<(String, Vec<(String, String)>)>) -> Option<String> {
    err.as_ref().map(|(k, a)| tr(k, &a.iter().map(|(x, y)| (x.as_str(), y.clone())).collect::<Vec<_>>()))
}

fn logged_in(i: &Inner) -> (bool, bool) {
    let Some(info) = &i.conn else { return (false, false) };
    let status = &info.status;
    let cli = info.claude.is_some() && status["loggedIn"] == true && status["authMethod"] == "claude.ai";
    let logged = cli || (info.creds_ok && !(info.claude.is_some() && status["loggedIn"] == false));
    (cli, logged)
}

fn connect_view(i: &Inner) -> ConnectView {
    let step = |state, title: String, detail: String, action: Option<(String, &'static str)>| StepView {
        state,
        title,
        detail,
        action,
    };
    let Some(info) = &i.conn else {
        let checking = || step("wait", t("connect.checking"), String::new(), None);
        return ConnectView { steps: vec![checking(), checking(), checking()], done: false };
    };
    let status = &info.status;
    let mut steps = Vec::new();

    steps.push(if info.claude.is_some() {
        let mut parts = Vec::new();
        if !info.version.is_empty() {
            parts.push(tr("step.install.version", &[("version", info.version.clone())]));
        }
        if info.source == "desktop" {
            parts.push(t("step.install.desktop"));
        }
        step("ok", t("step.install.ok"), parts.join(" · "), None)
    } else if let Some(d) = &info.wsl {
        step("ok", t("step.install.ok"), tr("step.install.wsl", &[("distro", d.clone())]), None)
    } else if info.creds_ok {
        // no se encuentra el ejecutable, pero sí su sesión: basta para leer el uso
        step("ok", t("step.install.ok"), t("step.install.found"), None)
    } else if i.install_started {
        step("wait", t("step.install"), t("step.install.wait"), Some((t("btn.install"), "install")))
    } else {
        step("todo", t("step.install"), t("step.install.detail"), Some((t("btn.install"), "install")))
    });

    let (cli, logged) = logged_in(i);
    steps.push(if cli {
        step("ok", t("step.login.ok"), connect::account_text(status), Some((t("btn.switch"), "login")))
    } else if logged {
        let detail = match &info.wsl {
            Some(d) => tr("step.login.wsl", &[("distro", d.clone())]),
            None => t("step.login.found"),
        };
        step("ok", t("step.login.ok"), detail, info.claude.as_ref().map(|_| (t("btn.switch"), "login")))
    } else if info.claude.is_none() {
        step("todo", t("step.login"), t("step.login.need_install"), None)
    } else if status["loggedIn"] == true {
        step("error", t("step.login.api"), t("step.login.api_detail"), Some((t("btn.login"), "login")))
    } else if i.login_started {
        step("wait", t("step.login"), t("step.login.wait"), Some((t("btn.login"), "login")))
    } else {
        step("todo", t("step.login"), t("step.login.detail"), Some((t("btn.login"), "login")))
    });

    let rate_limited = matches!(&i.error, Some((k, _)) if k == "err.ratelimit");
    let mut done = false;
    steps.push(if !logged {
        step("todo", t("step.usage"), String::new(), None)
    } else if !i.limits.is_empty() && (i.error.is_none() || rate_limited) {
        // con límite de consultas la conexión es correcta: se muestra el último dato
        done = true;
        let summary = i.limits.iter().map(|l| format!("{} {:.0}%", short(&l.kind), l.pct)).collect::<Vec<_>>().join(" · ");
        let extra = error_text(&i.error).map(|e| format!("\n{e}")).unwrap_or_default();
        step("ok", t("step.usage.ok"), summary + &extra, None)
    } else if rate_limited {
        // la conexión está bien; solo hay que esperar. No volver a consultar: alargaría el límite
        done = true;
        let args = &i.error.as_ref().unwrap().1;
        step(
            "wait",
            t("step.usage"),
            tr("step.usage.ratelimit", &args.iter().map(|(a, b)| (a.as_str(), b.clone())).collect::<Vec<_>>()),
            None,
        )
    } else if let Some(e) = error_text(&i.error) {
        step("error", t("step.usage"), e, None)
    } else {
        step("wait", t("step.usage"), t("loading"), None)
    });
    ConnectView { steps, done }
}

fn snapshot(i: &Inner) -> Snapshot {
    let s = &i.settings;
    let settings = json!({
        "on_top": s.bool("on_top"), "notify": s.bool("notify"), "interval": s.f64("interval"),
        "details": s.bool("details"), "chart_hours": s.f64("chart_hours"), "check_updates": s.bool("check_updates"),
        "mode": s.str("mode"), "alpha": s.f64("alpha"), "widget_style": s.str("widget_style"),
        "autostart": i.autostart, "show_all_limits": s.bool("show_all_limits"),
        "collapsed": s.bool("collapsed"), "fade_idle": s.bool("fade_idle"),
    });
    let extra = &i.data["extra_usage"];
    let update = match (&i.update_state, &i.update_info) {
        (Some(UpdateState::Downloading(p)), _) => Some(json!({"state": "downloading", "pct": p})),
        (Some(UpdateState::Installing), _) => Some(json!({"state": "installing"})),
        (None, Some(r)) => Some(json!({"state": "available", "version": r.version})),
        _ => None,
    };
    Snapshot {
        lang: core::lang(),
        version: APP_VERSION,
        hotkey: i.hotkey,
        settings,
        limits: i
            .limits
            .iter()
            .map(|l| LimitView {
                kind: l.kind.clone(),
                label: l.name.clone().unwrap_or_else(|| label(&l.kind)),
                short: l.name.clone().unwrap_or_else(|| short(&l.kind)),
                pct: l.pct,
                reset: l.reset,
                pace: usage::pace_info(l, &i.history),
                primary: is_primary(&l.kind, &i.limits),
            })
            .collect(),
        extra: if extra["is_enabled"] == true { extra["utilization"].as_f64() } else { None },
        error: error_text(&i.error),
        error_key: i.error.as_ref().map(|e| e.0.clone()),
        retry_at: i.error.as_ref().filter(|e| e.0 == "err.ratelimit").and_then(|e| e.1.first()).map(|a| a.1.clone()),
        updated_at: i.updated_at,
        account: i.account.clone(),
        update,
        tokens: i.tokens.clone(),
        connect: i.connect_open.then(|| connect_view(i)),
    }
}

/// Envía el estado a todas las ventanas y actualiza la bandeja y el título.
pub fn emit(app: &AppHandle) {
    let (snap, tip, title) = {
        let i = lock(app);
        let shown: Vec<&Limit> = i.limits.iter().filter(|l| is_primary(&l.kind, &i.limits)).collect();
        let tip = shown.iter().map(|l| format!("{} {:.0}%", short(&l.kind), l.pct)).collect::<Vec<_>>().join(" · ");
        let title = shown.iter().map(|l| format!("{:.0}%", l.pct)).collect::<Vec<_>>().join(" · ");
        (snapshot(&i), tip, title)
    };
    let _ = app.emit("state", &snap);
    if let Some(tray) = app.tray_by_id("main") {
        let _ = tray.set_tooltip(Some(if tip.is_empty() { APP_NAME.to_string() } else { format!("Claude · {tip}") }));
    }
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.set_title(&if title.is_empty() { APP_NAME.to_string() } else { format!("{APP_NAME} · {title}") });
    }
}

// ───────────────────────── consultas ─────────────────────────

pub fn kick_refresh(app: &AppHandle, manual: bool) {
    let st = app.state::<AppState>();
    let mut i = st.inner.lock().unwrap();
    // mientras Anthropic limite las consultas, solo se adelanta si lo pide el usuario
    if !manual && i.backoff_until > now() {
        return;
    }
    i.next_refresh = 0.0;
    st.wake.notify_all();
}

fn fetch_renewing(creds: &PathBuf) -> Result<Value, FetchError> {
    match usage::fetch_usage(creds) {
        Err(FetchError::Auth("err.token")) => match connect::find_claude() {
            Some(claude) => {
                connect::auth_status(&claude); // al ejecutarse, Claude Code renueva su token si ha caducado
                usage::fetch_usage(creds)
            }
            None => Err(FetchError::Auth("err.token")),
        },
        r => r,
    }
}

fn refresh_loop(app: AppHandle) {
    // en desarrollo, CLAUDE_USAGE_OFFLINE=1 no consulta a Anthropic (para probar la interfaz sin gastar consultas)
    if cfg!(debug_assertions) && std::env::var("CLAUDE_USAGE_OFFLINE").is_ok() {
        return;
    }
    let st = app.state::<AppState>();
    loop {
        let creds = {
            let mut i = st.inner.lock().unwrap();
            loop {
                let wait = i.next_refresh - now();
                if wait <= 0.0 {
                    break;
                }
                i = st.wake.wait_timeout(i, Duration::from_secs_f64(wait.min(3600.0))).unwrap().0;
            }
            i.next_refresh = now() + i.settings.f64("interval").max(30.0);
            i.credentials.clone()
        };
        let result = fetch_renewing(&creds);
        match result {
            Ok(data) => on_usage(&app, data),
            Err(FetchError::RateLimited(retry_after)) => on_rate_limited(&app, retry_after),
            Err(FetchError::Auth(key)) => on_error(&app, key, vec![]),
            Err(FetchError::Other(name)) => {
                warn!("usage error: {name}");
                on_error(&app, "err.fetch", vec![("name".into(), name)]);
            }
        }
    }
}

fn on_usage(app: &AppHandle, data: Value) {
    let limits = usage::parse_limits(&data);
    {
        let mut i = lock(app);
        if i.settings.bool("notify") {
            check_alerts(&mut i, &limits);
        }
        i.history.add(&limits);
        if i.backoff_step > 0 {
            info!("límite de consultas superado tras {} esperas", i.backoff_step);
        }
        let summary = limits.iter().map(|l| format!("{} {:.0}%", l.kind, l.pct)).collect::<Vec<_>>().join(" · ");
        info!("usage ok: {}", if summary.is_empty() { "sin límites".into() } else { summary });
        i.data = data;
        i.limits = limits;
        i.error = None;
        i.backoff_until = 0.0;
        i.backoff_step = 0;
        i.updated_at = Some(now());
    }
    emit(app);
}

fn on_rate_limited(app: &AppHandle, retry_after: u64) {
    {
        let mut i = lock(app);
        let wait = retry_after.max(RATE_LIMIT_STEPS[i.backoff_step.min(RATE_LIMIT_STEPS.len() - 1)]);
        i.backoff_step += 1;
        i.backoff_until = now() + wait as f64;
        i.next_refresh = i.backoff_until;
        let at = chrono::Local::now() + chrono::Duration::seconds(wait as i64);
        warn!("rate limited ({} seguidos): próximo intento a las {}", i.backoff_step, at.format("%H:%M:%S"));
        i.error = Some(("err.ratelimit".into(), vec![("time".into(), at.format("%H:%M").to_string())]));
    }
    emit(app);
}

fn on_error(app: &AppHandle, key: &str, args: Vec<(String, String)>) {
    let open = {
        let mut i = lock(app);
        info!("error: {key}");
        i.error = Some((key.into(), args));
        AUTH_ERRORS.contains(&key) && !i.settings.bool("onboarded") && !i.connect_open
    };
    emit(app);
    if open {
        open_connect(app);
    }
}

pub fn fmt_reset(epoch: Option<f64>) -> String {
    let Some(epoch) = epoch else { return String::new() };
    let secs = (epoch - now()) as i64;
    if secs <= 0 {
        return t("reset.done");
    }
    let (d, h, m) = (secs / 86400, secs % 86400 / 3600, secs % 3600 / 60);
    let left = if d > 0 {
        format!("{d} d {h} h")
    } else if h > 0 {
        format!("{h} h {m:02} min")
    } else {
        format!("{m} min")
    };
    let local = chrono::DateTime::from_timestamp(epoch as i64, 0).unwrap_or_default().with_timezone(&chrono::Local);
    use chrono::Datelike;
    let days = core::tr_list("days");
    let day = days.get(local.weekday().num_days_from_monday() as usize).cloned().unwrap_or_default();
    let stamp = if d > 0 { format!("{day} {}", local.format("%H:%M")) } else { local.format("%H:%M").to_string() };
    tr("reset.in", &[("left", left), ("stamp", stamp)])
}

fn check_alerts(i: &mut Inner, limits: &[Limit]) {
    let mut notified = i.settings.get("notified").as_object().cloned().unwrap_or_default();
    for lim in limits {
        let name = label(&lim.kind);
        if let Some((prev_pct, prev_reset)) = i.history.last(&lim.kind) {
            let new_window = matches!((lim.reset, prev_reset), (Some(r), Some(p)) if r - p > 1800.0);
            if prev_pct >= 50.0 && (lim.pct < prev_pct - 5.0 || new_window) {
                winsys::toast(
                    &tr("alert.reset", &[("label", name.clone())]),
                    &tr("alert.reset_body", &[("pct", format!("{:.0}", 100.0 - lim.pct))]),
                );
            }
        }
        let window = lim.reset.map(|r| (r / 3600.0).round() as i64).unwrap_or(0);
        let prev = notified.get(&lim.kind).cloned().unwrap_or(json!([null, 0]));
        let mut last_level = prev[1].as_f64().unwrap_or(0.0);
        if prev[0].as_i64() != Some(window) {
            last_level = 0.0;
        }
        let level = THRESHOLDS.iter().copied().filter(|t| lim.pct >= *t).fold(0.0, f64::max);
        if level > last_level {
            let when = fmt_reset(lim.reset);
            if level >= 100.0 {
                let body = if when.is_empty() { t("alert.wait") } else { core::capitalize(&when) };
                winsys::toast(&tr("alert.limit", &[("label", name)]), &body);
            } else {
                let mut body = tr("alert.left", &[("pct", format!("{:.0}", 100.0 - lim.pct))]);
                if !when.is_empty() {
                    body += &format!(" {}.", core::capitalize(&when));
                }
                winsys::toast(&tr("alert.used", &[("label", name), ("pct", format!("{:.0}", lim.pct))]), &body);
            }
        }
        notified.insert(lim.kind.clone(), json!([window, level.max(last_level)]));
    }
    i.settings.set("notified", Value::Object(notified));
    i.settings.save();
}

// ───────────────────────── conexión ─────────────────────────

fn on_connection(app: &AppHandle, info: ConnInfo) {
    let kick = {
        let mut i = lock(app);
        let mut kick = false;
        if info.creds_ok && info.creds != i.credentials {
            // p. ej. en WSL o en CLAUDE_CONFIG_DIR
            i.credentials = info.creds.clone();
            kick = matches!(&i.error, Some((k, _)) if AUTH_ERRORS.contains(&k.as_str()));
        }
        i.account = (info.status["loggedIn"] == true).then(|| connect::account_text(&info.status));
        i.conn = Some(info);
        // recién conectado desde el asistente: pedir el uso ya (sin saltarse las esperas)
        if i.connect_open && logged_in(&i).1 && !connect_view(&i).done && now() - i.last_connect_refresh > 30.0 {
            i.last_connect_refresh = now();
            kick = true;
        }
        kick
    };
    if kick {
        kick_refresh(app, false);
    }
    emit(app);
}

pub fn open_connect(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("connect") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
        return;
    }
    {
        let mut i = lock(app);
        i.connect_open = true;
        i.install_started = false;
        i.login_started = false;
    }
    let handle = app.clone();
    // las ventanas no se pueden crear desde el hilo principal dentro de un comando síncrono
    std::thread::spawn(move || {
        let built = WebviewWindowBuilder::new(&handle, "connect", WebviewUrl::App("index.html".into()))
            .title(t("connect.title"))
            .inner_size(500.0, 520.0)
            .resizable(false)
            .maximizable(false)
            .minimizable(false)
            .always_on_top(true)
            .theme(Some(tauri::Theme::Dark))
            .background_color(tauri::window::Color(17, 17, 20, 255))
            .center()
            .visible(false)
            .build();
        if let Err(e) = built {
            warn!("no se pudo abrir el asistente: {e}");
            lock(&handle).connect_open = false;
            return;
        }
        emit(&handle);
        // comprobar cada 3 s mientras el asistente esté abierto
        while lock(&handle).connect_open {
            let info = connect::check_connection();
            if !lock(&handle).connect_open {
                break;
            }
            on_connection(&handle, info);
            std::thread::sleep(Duration::from_secs(3));
        }
    });
}

fn close_connect(app: &AppHandle) {
    lock(app).connect_open = false;
    if let Some(w) = app.get_webview_window("connect") {
        let _ = w.destroy();
    }
}

// ───────────────────────── actualizaciones ─────────────────────────

fn check_updates(app: &AppHandle, manual: bool) {
    let app = app.clone();
    std::thread::spawn(move || {
        let result = update::fetch_latest_release();
        match &result {
            Ok(r) => info!("update check: {}", r.as_ref().map(|r| r.version.as_str()).unwrap_or("sin instalador")),
            Err(e) => info!("update check: fallo ({e})"),
        }
        match result {
            Ok(Some(info)) if update::version_tuple(&info.version) > update::version_tuple(APP_VERSION) => {
                let notify = {
                    let mut i = lock(&app);
                    let notify = !manual && i.settings.str("update_notified") != info.version;
                    if notify {
                        i.settings.set("update_notified", json!(info.version));
                        i.settings.save();
                    }
                    i.update_info = Some(info.clone());
                    notify
                };
                if notify {
                    winsys::toast(
                        &tr("update.toast_title", &[("version", info.version.clone())]),
                        &t("update.toast_body"),
                    );
                }
                emit(&app);
                if manual {
                    start_update(&app);
                }
            }
            Ok(_) if manual => {
                winsys::message_box(&tr("update.latest", &[("version", APP_VERSION.into())]), false);
            }
            Err(_) if manual => {
                winsys::message_box(&t("update.check_failed"), false);
            }
            _ => {}
        }
    });
}

fn start_update(app: &AppHandle) {
    let app = app.clone();
    std::thread::spawn(move || {
        let info = {
            let i = lock(&app);
            match (&i.update_info, &i.update_state) {
                (Some(r), None) => r.clone(),
                _ => return,
            }
        };
        if cfg!(debug_assertions) {
            // ejecutando en desarrollo: no hay .exe instalado que sustituir
            winsys::shell_open(&info.page);
            return;
        }
        let ask = tr(
            "update.ask",
            &[
                ("version", info.version.clone()),
                ("current", APP_VERSION.into()),
                ("notes", update::plain_notes(&info.notes)),
            ],
        );
        if !winsys::message_box(&ask, true) {
            return;
        }
        set_update_state(&app, Some(UpdateState::Downloading(0)));
        let last = Mutex::new(u64::MAX);
        let progress = |pct: u64| {
            let mut l = last.lock().unwrap();
            if *l != pct {
                *l = pct;
                set_update_state(&app, Some(UpdateState::Downloading(pct)));
            }
        };
        match update::download_update(&info, progress) {
            Ok(setup) => install_update(&app, setup),
            Err(e) => update_failed(&app, &e),
        }
    });
}

fn set_update_state(app: &AppHandle, state: Option<UpdateState>) {
    lock(app).update_state = state;
    emit(app);
}

/// Lanza el instalador en modo silencioso; él cierra esta app, sustituye el .exe y la vuelve a abrir.
fn install_update(app: &AppHandle, setup: PathBuf) {
    use std::os::windows::process::CommandExt;
    info!("instalando actualización: {}", setup.display());
    set_update_state(app, Some(UpdateState::Installing));
    let spawned = std::process::Command::new(&setup)
        .args(["--silent", "--update", "--dir"])
        .arg(core::app_dir())
        .current_dir(setup.parent().unwrap_or(&setup))
        .creation_flags(0x0000_0008 | 0x0000_0200) // DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        .spawn();
    if let Err(e) = spawned {
        update_failed(app, &e.to_string());
        return;
    }
    // si el instalador no ha cerrado la app en 90 s, algo ha ido mal: no quedarse en "Instalando…"
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(90));
        if lock(&app).update_state == Some(UpdateState::Installing) {
            update_failed(&app, "timeout");
        }
    });
}

fn update_failed(app: &AppHandle, error: &str) {
    warn!("actualización fallida: {error}");
    set_update_state(app, None);
    winsys::toast(APP_NAME, &tr("update.failed", &[("e", error.into())]));
}

// ───────────────────────── ventanas ─────────────────────────

fn widget(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window("widget")
}

fn main_win(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window("main")
}

fn create_windows(app: &AppHandle) -> tauri::Result<()> {
    let on_top = lock(app).settings.bool("on_top");
    WebviewWindowBuilder::new(app, "widget", WebviewUrl::App("index.html".into()))
        .title(APP_NAME)
        .inner_size(320.0, 180.0)
        .decorations(false)
        .transparent(true)
        .shadow(false)
        .resizable(false)
        .maximizable(false)
        .skip_taskbar(true)
        .always_on_top(on_top)
        .focused(false)
        .visible(false)
        .build()?;
    let pos = lock(app).settings.str("position");
    let mut b = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title(APP_NAME)
        .inner_size(440.0, 560.0)
        .resizable(false)
        .maximizable(false)
        .always_on_top(on_top)
        .theme(Some(tauri::Theme::Dark))
        .background_color(tauri::window::Color(17, 17, 20, 255))
        .visible(false);
    // "+x+y" (mismo formato que la versión 2)
    let xy: Vec<f64> = pos.split('+').filter_map(|s| s.parse().ok()).collect();
    if xy.len() == 2 {
        b = b.position(xy[0], xy[1]);
    } else {
        b = b.center();
    }
    b.build()?;
    Ok(())
}

/// Monitores como rectángulos (x, y, ancho, alto) del área de trabajo, en píxeles físicos.
fn work_areas(w: &WebviewWindow) -> Vec<(i32, i32, i32, i32)> {
    w.available_monitors()
        .unwrap_or_default()
        .iter()
        .map(|m| {
            let a = m.work_area();
            (a.position.x, a.position.y, a.size.width as i32, a.size.height as i32)
        })
        .collect()
}

/// Coloca el widget en su posición guardada, sin salirse de la pantalla.
fn place_widget(app: &AppHandle, w: &WebviewWindow) {
    let size = w.outer_size().unwrap_or_default();
    let (sw, sh) = (size.width as i32, size.height as i32);
    let saved: Option<(i32, i32)> = {
        let i = lock(app);
        let p = i.settings.get("widget_pos");
        p[0].as_f64().zip(p[1].as_f64()).map(|(x, y)| (x as i32, y as i32))
    };
    let areas = work_areas(w);
    let inside = |x: i32, y: i32| {
        areas.iter().any(|(ax, ay, aw, ah)| x + sw - 40 >= *ax && x + 40 <= ax + aw && y >= *ay && y + 30 <= ay + ah)
    };
    let (mut x, mut y) = match saved {
        Some((x, y)) if inside(x, y) => (x, y),
        _ => {
            // esquina superior derecha del monitor principal
            let a = w
                .primary_monitor()
                .ok()
                .flatten()
                .map(|m| {
                    let a = m.work_area();
                    (a.position.x, a.position.y, a.size.width as i32, a.size.height as i32)
                })
                .or_else(|| areas.first().copied())
                .unwrap_or((0, 0, 1920, 1080));
            let margin = (16.0 * w.scale_factor().unwrap_or(1.0)) as i32;
            (a.0 + a.2 - sw - margin, a.1 + margin)
        }
    };
    // que quepa entero en el monitor donde está
    if let Some((ax, ay, aw, ah)) = areas.iter().find(|(ax, ay, aw, ah)| x + sw / 2 >= *ax && x + sw / 2 < ax + aw && y >= *ay - sh && y < ay + ah) {
        x = x.clamp(*ax, (ax + aw - sw).max(*ax));
        y = y.clamp(*ay, (ay + ah - sh).max(*ay));
    }
    let _ = w.set_position(PhysicalPosition::new(x, y));
}

/// La ventana completa, siempre visible entera: si no está en ninguna pantalla, junto al widget.
fn place_main(app: &AppHandle, m: &WebviewWindow) {
    let (Ok(p), Ok(s)) = (m.outer_position(), m.outer_size()) else { return };
    let areas = work_areas(m);
    let visible = areas.iter().any(|(ax, ay, aw, ah)| p.x >= *ax - 8 && p.x + s.width as i32 <= ax + aw + 8 && p.y >= *ay && p.y < ay + ah);
    if visible {
        return;
    }
    let anchor = widget(app).and_then(|w| w.outer_position().ok());
    let area = anchor
        .and_then(|a| areas.iter().find(|(ax, ay, aw, ah)| a.x >= *ax && a.x < ax + aw && a.y >= *ay && a.y < ay + ah).copied())
        .or_else(|| areas.first().copied());
    if let Some((ax, ay, aw, ah)) = area {
        let x = ax + (aw - s.width as i32).max(0) / 2;
        let y = ay + (ah - s.height as i32).max(0) / 3;
        let _ = m.set_position(PhysicalPosition::new(x, y));
    }
}

/// Área de trabajo del monitor que contiene el centro de la ventana.
fn area_of(w: &WebviewWindow) -> Option<(i32, i32, i32, i32)> {
    let (p, s) = (w.outer_position().ok()?, w.outer_size().ok()?);
    let (cx, cy) = (p.x + s.width as i32 / 2, p.y + s.height as i32 / 2);
    let areas = work_areas(w);
    areas
        .iter()
        .find(|(ax, ay, aw, ah)| cx >= *ax && cx < ax + aw && cy >= *ay && cy < ay + ah)
        .or(areas.first())
        .copied()
}

/// Al soltar el widget cerca de un borde de la pantalla, se pega a él (y nunca queda fuera).
fn snap_widget(app: &AppHandle) {
    let Some(w) = widget(app) else { return };
    let (Ok(p), Ok(s), Some((ax, ay, aw, ah))) = (w.outer_position(), w.outer_size(), area_of(&w)) else { return };
    let scale = w.scale_factor().unwrap_or(1.0);
    // la ventana tiene un margen transparente para la sombra: se pega la tarjeta, no la ventana
    let (pad_x, pad_top, pad_bottom) = ((16.0 * scale) as i32, (12.0 * scale) as i32, (20.0 * scale) as i32);
    let gap = (8.0 * scale) as i32;
    let near = (36.0 * scale) as i32;
    let (sw, sh) = (s.width as i32, s.height as i32);
    let (left, right) = (ax + gap - pad_x, ax + aw - gap + pad_x - sw);
    let (top, bottom) = (ay + gap - pad_top, ay + ah - gap + pad_bottom - sh);
    let mut x = p.x.clamp(left.min(right), right.max(left));
    let mut y = p.y.clamp(top.min(bottom), bottom.max(top));
    if (x - left).abs() < near {
        x = left;
    } else if (x - right).abs() < near {
        x = right;
    }
    if (y - top).abs() < near {
        y = top;
    } else if (y - bottom).abs() < near {
        y = bottom;
    }
    if (x, y) != (p.x, p.y) {
        let _ = w.set_position(PhysicalPosition::new(x, y));
    }
    let mut i = lock(app);
    i.settings.set("widget_pos", json!([x, y]));
    i.dirty_since = Some(now());
}

fn show_widget(app: &AppHandle) {
    let Some(w) = widget(app) else { return };
    {
        let mut i = lock(app);
        if !w.is_visible().unwrap_or(false) && !i.widget_ready {
            // la interfaz aún no ha medido su tamaño: se mostrará al hacerlo
            i.pending_show = true;
            return;
        }
    }
    place_widget(app, &w);
    if !w.is_visible().unwrap_or(false) {
        lock(app).shown_at = Some(now());
        let _ = app.emit_to("widget", "appear", ());
    }
    let _ = w.show();
    let _ = w.set_always_on_top(lock(app).settings.bool("on_top"));
}

/// Oculta el widget mientras haya una app a pantalla completa y lo vuelve a mostrar al salir.
fn check_fullscreen(app: &AppHandle) {
    let Some(w) = widget(app) else { return };
    let (enabled, hidden, mode) = {
        let i = lock(app);
        (i.settings.bool("hide_fullscreen"), i.hidden_by_fullscreen, i.settings.str("mode"))
    };
    if !enabled || mode != "widget" {
        return;
    }
    let busy = winsys::fullscreen_app();
    if busy && !hidden && w.is_visible().unwrap_or(false) {
        lock(app).hidden_by_fullscreen = true;
        let _ = w.hide();
    } else if !busy && hidden {
        lock(app).hidden_by_fullscreen = false;
        show_widget(app);
    }
}

pub fn show_mode(app: &AppHandle, mode: &str) {
    {
        let mut i = lock(app);
        i.hidden_by_fullscreen = false;
        i.settings.set("mode", json!(mode));
        i.settings.save();
    }
    if mode == "full" {
        if let Some(w) = widget(app) {
            let _ = w.hide();
        }
        if let Some(m) = main_win(app) {
            place_main(app, &m);
            let _ = m.unminimize();
            let _ = m.show();
            let _ = m.set_focus();
        }
    } else {
        if let Some(m) = main_win(app) {
            let _ = m.hide();
        }
        show_widget(app);
    }
}

pub fn hide_all(app: &AppHandle) {
    lock(app).hidden_by_fullscreen = false;
    for w in [widget(app), main_win(app)].into_iter().flatten() {
        let _ = w.hide();
    }
    let hint = {
        let mut i = lock(app);
        let first = !i.settings.bool("hint_shown");
        if first {
            i.settings.set("hint_shown", json!(true));
            i.settings.save();
        }
        first.then_some(i.hotkey)
    };
    if let Some(hotkey) = hint {
        let hk = if hotkey { t("hint.hotkey") } else { String::new() };
        winsys::toast(APP_NAME, &tr("hint", &[("hotkey", hk)]));
    }
}

pub fn toggle_visible(app: &AppHandle) {
    let visible = [widget(app), main_win(app)].into_iter().flatten().any(|w| w.is_visible().unwrap_or(false));
    if visible {
        hide_all(app);
    } else {
        let mode = lock(app).settings.str("mode");
        show_mode(app, &mode);
    }
}

pub fn quit(app: &AppHandle) {
    remember_main(app);
    lock(app).settings.save();
    app.exit(0);
}

fn remember_main(app: &AppHandle) {
    if let Some(m) = main_win(app) {
        if m.is_visible().unwrap_or(false) {
            if let Ok(p) = m.outer_position() {
                lock(app).settings.set("position", json!(format!("+{}+{}", p.x, p.y)));
            }
        }
    }
}

pub fn on_window_event(window: &Window, event: &WindowEvent) {
    let app = window.app_handle();
    match (window.label(), event) {
        ("main", WindowEvent::CloseRequested { api, .. }) => {
            api.prevent_close();
            remember_main(app);
            show_mode(app, "widget");
        }
        ("connect", WindowEvent::CloseRequested { .. }) => {
            lock(app).connect_open = false;
        }
        ("widget", WindowEvent::Moved(p)) if window.is_visible().unwrap_or(false) => {
            let mut i = lock(app);
            i.settings.set("widget_pos", json!([p.x, p.y]));
            i.dirty_since = Some(now());
            i.moved_at = Some(now());
        }
        ("main", WindowEvent::Moved(p)) if window.is_visible().unwrap_or(false) => {
            let mut i = lock(app);
            i.settings.set("position", json!(format!("+{}+{}", p.x, p.y)));
            i.dirty_since = Some(now());
        }
        _ => {}
    }
}

// ───────────────────────── menú y bandeja ─────────────────────────

fn build_menu<R: Runtime>(app: &AppHandle<R>, i: &Inner) -> tauri::Result<Menu<R>> {
    let s = &i.settings;
    let item = |id: &str, text: String| MenuItem::with_id(app, id, text, true, None::<&str>);
    let check = |id: &str, text: String, on: bool| CheckMenuItem::with_id(app, id, text, true, on, None::<&str>);
    let sep = || PredefinedMenuItem::separator(app);

    let interval = s.f64("interval") as i64;
    let intervals: Vec<CheckMenuItem<R>> = [(30, "30 s"), (60, "1 min"), (120, "2 min"), (300, "5 min")]
        .iter()
        .map(|(v, l)| check(&format!("interval:{v}"), l.to_string(), interval == *v))
        .collect::<Result<_, _>>()?;
    let style = s.str("widget_style");
    let styles: Vec<CheckMenuItem<R>> = ["large", "rings", "compact"]
        .iter()
        .map(|v| check(&format!("style:{v}"), t(&format!("style.{v}")), style == *v))
        .collect::<Result<_, _>>()?;
    let alpha = s.f64("alpha");
    let alphas: Vec<CheckMenuItem<R>> = [1.0, 0.95, 0.85, 0.75, 0.6]
        .iter()
        .map(|v: &f64| check(&format!("alpha:{v}"), format!("{:.0}%", v * 100.0), (alpha - v).abs() < 0.001))
        .collect::<Result<_, _>>()?;
    let lang = core::lang();
    let langs: Vec<CheckMenuItem<R>> = [("es", "Español"), ("en", "English")]
        .iter()
        .map(|(c, n)| check(&format!("lang:{c}"), n.to_string(), lang == *c))
        .collect::<Result<_, _>>()?;
    let sub = |text: String, items: &Vec<CheckMenuItem<R>>| {
        let refs: Vec<&dyn IsMenuItem<R>> = items.iter().map(|x| x as &dyn IsMenuItem<R>).collect();
        Submenu::with_items(app, text, true, &refs)
    };

    let hide_text = t("menu.hide");
    // menú corto: lo del widget y las herramientas, en submenús
    let style_sub = sub(t("menu.style"), &styles)?;
    let alpha_sub = sub(t("menu.alpha"), &alphas)?;
    let on_top = check("on_top", t("menu.on_top"), s.bool("on_top"))?;
    let fade = check("fade_idle", t("menu.fade_idle"), s.bool("fade_idle"))?;
    let fullscreen = check("hide_fullscreen", t("menu.hide_fullscreen"), s.bool("hide_fullscreen"))?;
    let (sep1, sep2) = (sep()?, sep()?);
    let widget_sub = Submenu::with_items(
        app,
        t("menu.widget_group"),
        true,
        &[&style_sub, &alpha_sub, &sep1, &on_top, &fade, &fullscreen],
    )?;
    let tools = [
        item("export", t("menu.export"))?,
        item("test", t("menu.test"))?,
        item("folder", t("menu.folder"))?,
        item("log", t("menu.log"))?,
        item("check_updates", t("menu.check_updates"))?,
    ];
    let auto_updates = check("auto_updates", t("menu.auto_updates"), s.bool("check_updates"))?;
    let tools_sub = Submenu::with_items(
        app,
        t("menu.tools"),
        true,
        &[&tools[0], &tools[1], &tools[2], &tools[3], &sep2, &tools[4], &auto_updates],
    )?;
    let entries: Vec<Box<dyn IsMenuItem<R>>> = vec![
        Box::new(item("widget", t("menu.widget"))?),
        Box::new(item("full", t("menu.full"))?),
        Box::new(item("hide", if i.hotkey { hide_text } else { hide_text.split("   ").next().unwrap_or("").into() })?),
        Box::new(sep()?),
        Box::new(widget_sub),
        Box::new(check("notify", t("menu.notify"), s.bool("notify"))?),
        Box::new(check("autostart", t("menu.autostart"), i.autostart)?),
        Box::new(sub(t("menu.interval"), &intervals)?),
        Box::new(sub(t("menu.lang"), &langs)?),
        Box::new(sep()?),
        Box::new(item("account", t("menu.account"))?),
        Box::new(item("open_web", t("menu.open_web"))?),
        Box::new(tools_sub),
        Box::new(sep()?),
        Box::new(item("quit", t("quit"))?),
    ];
    let refs: Vec<&dyn IsMenuItem<R>> = entries.iter().map(|b| b.as_ref()).collect();
    Menu::with_items(app, &refs)
}

fn tray_menu(app: &AppHandle, hotkey: bool) -> tauri::Result<Menu<tauri::Wry>> {
    let toggle = t("tray.toggle") + if hotkey { "\tCtrl+Alt+U" } else { "" };
    Menu::with_items(
        app,
        &[
            &MenuItem::with_id(app, "toggle", toggle, true, None::<&str>)?,
            &MenuItem::with_id(app, "full", t("tray.full"), true, None::<&str>)?,
            &MenuItem::with_id(app, "refresh", t("tray.refresh"), true, None::<&str>)?,
            &PredefinedMenuItem::separator(app)?,
            &MenuItem::with_id(app, "quit", t("quit"), true, None::<&str>)?,
        ],
    )
}

fn update_setting(app: &AppHandle, key: &str, value: Value) {
    let mut i = lock(app);
    i.settings.set(key, value);
    i.settings.save();
}

pub fn on_menu(app: &AppHandle, id: &str) {
    let (kind, arg) = id.split_once(':').unwrap_or((id, ""));
    match kind {
        "widget" => show_mode(app, "widget"),
        "full" => show_mode(app, "full"),
        "hide" => hide_all(app),
        "toggle" => toggle_visible(app),
        "refresh" => kick_refresh(app, true),
        "account" => open_connect(app),
        "on_top" => {
            let on = !lock(app).settings.bool("on_top");
            update_setting(app, "on_top", json!(on));
            for w in [widget(app), main_win(app)].into_iter().flatten() {
                let _ = w.set_always_on_top(on);
            }
        }
        "autostart" => {
            let want = !lock(app).autostart;
            if let Err(e) = winsys::set_autostart(want) {
                winsys::toast(APP_NAME, &tr("err.autostart", &[("e", e.to_string())]));
            }
            lock(app).autostart = winsys::get_autostart();
        }
        "notify" => {
            let on = !lock(app).settings.bool("notify");
            update_setting(app, "notify", json!(on));
        }
        "fade_idle" | "hide_fullscreen" => {
            let on = !lock(app).settings.bool(kind);
            update_setting(app, kind, json!(on));
            if !on && kind == "hide_fullscreen" && std::mem::take(&mut lock(app).hidden_by_fullscreen) {
                show_widget(app);
            }
        }
        "auto_updates" => {
            let on = !lock(app).settings.bool("check_updates");
            update_setting(app, "check_updates", json!(on));
        }
        "interval" => {
            update_setting(app, "interval", json!(arg.parse::<i64>().unwrap_or(60)));
            kick_refresh(app, false);
        }
        "style" => update_setting(app, "widget_style", json!(arg)),
        "alpha" => update_setting(app, "alpha", json!(arg.parse::<f64>().unwrap_or(1.0))),
        "lang" => {
            core::set_lang(arg);
            update_setting(app, "lang", json!(arg));
            let hotkey = lock(app).hotkey;
            if let (Some(tray), Ok(menu)) = (app.tray_by_id("main"), tray_menu(app, hotkey)) {
                let _ = tray.set_menu(Some(menu));
            }
            if let Some(w) = app.get_webview_window("connect") {
                let _ = w.set_title(&t("connect.title"));
            }
        }
        "open_web" => winsys::shell_open(core::USAGE_PAGE),
        "export" => export_csv(app),
        "test" => winsys::toast(APP_NAME, &t("test.body")),
        "folder" => winsys::shell_open(&core::app_dir().display().to_string()),
        "log" => {
            let log = core::data_dir().join("claude-usage.log");
            if log.exists() {
                winsys::shell_open(&log.display().to_string());
            }
        }
        "check_updates" => check_updates(app, true),
        "quit" => quit(app),
        _ => {}
    }
    emit(app);
}

fn export_csv(app: &AppHandle) {
    let es = core::lang() == "es";
    let path = core::data_dir().join(t("csv.file"));
    // Excel en español espera ';' y coma decimal; en inglés, ',' y punto
    let (sep, dec) = if es { (";", ",") } else { (",", ".") };
    let mut out = String::from("\u{feff}");
    out += &core::tr_list("csv.header").join(sep);
    out += "\r\n";
    let fmt_t = |t: f64, f: &str| {
        chrono::DateTime::from_timestamp(t as i64, 0)
            .map(|d| d.with_timezone(&chrono::Local).format(f).to_string())
            .unwrap_or_default()
    };
    {
        let i = lock(app);
        for (t, l) in &i.history.points {
            for (kind, v) in l {
                let pct = v[0].as_f64().unwrap_or(0.0);
                let reset = v[1].as_f64().map(|r| fmt_t(r, "%Y-%m-%d %H:%M")).unwrap_or_default();
                let row = [fmt_t(*t, "%Y-%m-%d %H:%M:%S"), label(kind), format!("{pct}").replace('.', dec), reset];
                out += &row.iter().map(|c| if c.contains(sep) { format!("\"{c}\"") } else { c.clone() }).collect::<Vec<_>>().join(sep);
                out += "\r\n";
            }
        }
    }
    match std::fs::write(&path, out) {
        Ok(()) => winsys::shell_open(&path.display().to_string()),
        Err(e) => winsys::toast(APP_NAME, &tr("err.export", &[("e", e.to_string())])),
    }
}

// ───────────────────────── comandos de la interfaz ─────────────────────────

#[tauri::command]
pub fn get_state(app: AppHandle) -> Value {
    serde_json::to_value(snapshot(&lock(&app))).unwrap_or_default()
}

#[tauri::command]
pub fn i18n() -> &'static str {
    core::I18N
}

#[tauri::command]
pub fn refresh(app: AppHandle) {
    kick_refresh(&app, true);
}

#[tauri::command]
pub fn set_setting(app: AppHandle, key: String, value: Value) {
    if ["details", "chart_hours", "show_all_limits", "collapsed"].contains(&key.as_str()) {
        update_setting(&app, &key, value);
        emit(&app);
    }
}

#[tauri::command]
pub fn menu_action(app: AppHandle, id: String) {
    on_menu(&app, &id);
}

#[tauri::command]
pub fn open_menu(window: WebviewWindow) {
    let app = window.app_handle().clone();
    let menu = {
        let i = lock(&app);
        build_menu(&app, &i)
    };
    if let Ok(menu) = menu {
        let _ = window.popup_menu(&menu);
    }
}

#[derive(Serialize)]
pub struct Series {
    series: BTreeMap<String, Vec<[f64; 2]>>,
    start: f64,
    end: f64,
    gap: f64,
}

#[tauri::command]
pub fn history_series(app: AppHandle, hours: f64) -> Series {
    let i = lock(&app);
    let end = now();
    let start = end - hours * 3600.0;
    let series = i.limits.iter().map(|l| (l.kind.clone(), i.history.series(&l.kind, start))).collect();
    Series { series, start, end, gap: (3.0 * i.settings.f64("interval")).max(600.0) }
}

/// La interfaz pide el tamaño que necesita su contenido (en píxeles lógicos).
#[tauri::command]
pub fn fit_window(window: WebviewWindow, width: f64, height: f64) {
    let app = window.app_handle().clone();
    let scale = window.scale_factor().unwrap_or(1.0);
    let max_h = area_of(&window).map(|a| a.3).unwrap_or(1080) as f64 / scale * 0.85;
    let (width, height) = (width.ceil(), height.ceil().min(max_h));
    let old_pos = window.outer_position().ok();
    let old_size = window.outer_size().ok();
    let visible = window.is_visible().unwrap_or(false);
    let _ = window.set_size(LogicalSize::new(width, height));
    match window.label() {
        "widget" => {
            // justo después de mostrarse se respeta la posición guardada; luego (cambio de estilo,
            // límites que aparecen) en la mitad derecha de la pantalla se crece hacia la izquierda
            let settled = lock(&app).shown_at.is_some_and(|t| now() - t > 1.5);
            if visible && settled {
                // mantener la esquina más cercana: en la mitad derecha crece hacia la izquierda,
                // en la mitad inferior crece hacia arriba
                if let (Some(p), Some(s), Some((ax, ay, aw, ah))) = (old_pos, old_size, area_of(&window)) {
                    let (new_w, new_h) = ((width * scale).round() as i32, (height * scale).round() as i32);
                    let (ow, oh) = (s.width as i32, s.height as i32);
                    let x = if p.x + ow / 2 > ax + aw / 2 { p.x + ow - new_w } else { p.x };
                    let y = if p.y + oh / 2 > ay + ah / 2 { p.y + oh - new_h } else { p.y };
                    if (x, y) != (p.x, p.y) {
                        let _ = window.set_position(PhysicalPosition::new(x, y));
                    }
                }
            }
            let show = {
                let mut i = lock(&app);
                i.widget_ready = true;
                std::mem::take(&mut i.pending_show)
            };
            if show {
                show_widget(&app);
            }
        }
        "connect" | "installer" if !visible => {
            let _ = window.center();
            let _ = window.show();
            let _ = window.set_focus();
        }
        _ => {}
    }
}

#[tauri::command]
pub fn show_mode_cmd(app: AppHandle, mode: String) {
    show_mode(&app, &mode);
}

#[tauri::command]
pub fn hide(app: AppHandle) {
    hide_all(&app);
}

#[tauri::command]
pub fn start_update_cmd(app: AppHandle) {
    start_update(&app);
}

#[tauri::command]
pub fn open_connect_cmd(app: AppHandle) {
    open_connect(&app);
}

#[tauri::command]
pub fn connect_action(app: AppHandle, action: String) {
    match action.as_str() {
        "install" => {
            lock(&app).install_started = true;
            if connect::has_winget() {
                connect::run_in_console("winget install --id Anthropic.ClaudeCode -e");
            } else {
                winsys::shell_open(core::CLAUDE_CODE_PAGE);
            }
        }
        "login" => {
            let claude = lock(&app).conn.as_ref().and_then(|c| c.claude.clone()).or_else(connect::find_claude);
            if let Some(claude) = claude {
                lock(&app).login_started = true;
                connect::run_in_console(&format!("\"{}\" auth login --claudeai", claude.display()));
            }
        }
        "finish" => {
            update_setting(&app, "onboarded", json!(true));
            close_connect(&app);
            let mode = lock(&app).settings.str("mode");
            show_mode(&app, &mode);
        }
        "close" => close_connect(&app),
        _ => {}
    }
    emit(&app);
}

#[tauri::command]
pub fn diagnostics(app: AppHandle) -> String {
    let conn = lock(&app).conn.clone();
    connect::diagnostics(conn.as_ref())
}

// ───────────────────────── arranque ─────────────────────────

fn listen(app: AppHandle, server: TcpListener) {
    for conn in server.incoming() {
        let Ok(mut conn) = conn else { continue };
        let _ = conn.set_read_timeout(Some(Duration::from_secs(2)));
        let mut buf = [0u8; 16];
        let n = conn.read(&mut buf).unwrap_or(0);
        // "quit" lo envían el instalador y el desinstalador; cualquier otra cosa: mostrarse
        if &buf[..n] == b"quit" {
            quit(&app);
        } else {
            let mode = lock(&app).settings.str("mode");
            show_mode(&app, &mode);
        }
    }
}

pub fn setup(app: &AppHandle, server: TcpListener) -> tauri::Result<()> {
    use tauri_plugin_global_shortcut::GlobalShortcutExt;
    let hotkey = app.global_shortcut().register("ctrl+alt+u").is_ok();
    lock(app).hotkey = hotkey;

    let autostart = lock(app).autostart;
    if autostart {
        // por si la app se movió o pasó de la versión Python a esta
        let _ = winsys::set_autostart(true);
    }

    TrayIconBuilder::with_id("main")
        .icon(app.default_window_icon().cloned().expect("icono"))
        .tooltip(APP_NAME)
        .menu(&tray_menu(app, hotkey)?)
        .show_menu_on_left_click(false)
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                toggle_visible(tray.app_handle());
            }
        })
        .build(app)?;

    create_windows(app)?;

    // primer arranque tras actualizar: avisar y limpiar el instalador descargado
    update::cleanup_old_setups();
    let (last, onboarded, empty, mode) = {
        let i = lock(app);
        (i.settings.str("last_version"), i.settings.bool("onboarded"), i.history.points.is_empty(), i.settings.str("mode"))
    };
    if !last.is_empty() && update::version_tuple(APP_VERSION) > update::version_tuple(&last) {
        winsys::toast(APP_NAME, &tr("update.done", &[("version", APP_VERSION.into())]));
    }
    if last != APP_VERSION {
        update_setting(app, "last_version", json!(APP_VERSION));
    }

    show_mode(app, if mode == "full" { "full" } else { "widget" });
    if !onboarded && empty {
        open_connect(app); // instalación nueva
    }

    let h = app.clone();
    std::thread::spawn(move || listen(h, server));
    let h = app.clone();
    std::thread::spawn(move || refresh_loop(h));
    let h = app.clone();
    std::thread::spawn(move || on_connection(&h, connect::check_connection()));
    // tokens cada 5 min
    let h = app.clone();
    std::thread::spawn(move || loop {
        let stats = tokens::scan_tokens();
        lock(&h).tokens = Some(stats);
        emit(&h);
        std::thread::sleep(Duration::from_secs(TOKEN_SCAN_SECS));
    });
    // actualizaciones: a los 8 s y luego cada 6 h
    let h = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(8));
        loop {
            if lock(&h).settings.bool("check_updates") {
                check_updates(&h, false);
            }
            std::thread::sleep(Duration::from_secs(update::UPDATE_EVERY_SECS));
        }
    });
    // reloj: pega el widget a los bordes al soltarlo, lo oculta con apps a pantalla completa,
    // guarda la posición tras moverlo y refresca las cuentas atrás
    let h = app.clone();
    std::thread::spawn(move || {
        let mut ticks = 0u64;
        loop {
            std::thread::sleep(Duration::from_millis(250));
            ticks += 1;
            let snap = {
                let mut i = lock(&h);
                let released = i.moved_at.is_some_and(|t| now() - t > 0.15) && !winsys::mouse_down();
                if released {
                    i.moved_at = None;
                }
                released
            };
            if snap {
                snap_widget(&h);
            }
            if ticks % 8 == 0 {
                check_fullscreen(&h);
            }
            {
                let mut i = lock(&h);
                if i.dirty_since.is_some_and(|t| now() - t > 1.0) {
                    i.dirty_since = None;
                    i.settings.save();
                }
            }
            if ticks % 120 == 0 {
                emit(&h);
            }
        }
    });
    Ok(())
}
