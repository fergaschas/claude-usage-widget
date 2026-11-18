//! Límites del plan: endpoint de `/usage` de Claude Code, historial local y previsión de ritmo.

use crate::core::{self, now, HISTORY_DAYS};
use serde::Serialize;
use serde_json::{json, Map, Value};
use std::fs::{self, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

#[derive(Debug, Clone)]
pub enum FetchError {
    /// clave de i18n de un error de sesión ("err.no_session" / "err.token")
    Auth(&'static str),
    RateLimited(u64),
    Other(String),
}

pub fn agent(timeout: u64) -> ureq::Agent {
    let mut b = ureq::AgentBuilder::new().timeout(Duration::from_secs(timeout)).user_agent(&core::user_agent());
    if let Ok(tls) = native_tls::TlsConnector::new() {
        b = b.tls_connector(Arc::new(tls));
    }
    b.build()
}

pub fn fetch_usage(credentials: &PathBuf) -> Result<Value, FetchError> {
    let token = fs::read_to_string(credentials)
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .and_then(|v| v["claudeAiOauth"]["accessToken"].as_str().map(String::from))
        .ok_or(FetchError::Auth("err.no_session"))?;
    let resp = agent(15)
        .get(core::USAGE_URL)
        .set("Authorization", &format!("Bearer {token}"))
        .set("anthropic-beta", "oauth-2025-04-20")
        .call();
    match resp {
        Ok(r) => r.into_json::<Value>().map_err(|e| FetchError::Other(format!("JSON: {e}"))),
        Err(ureq::Error::Status(code, r)) => {
            let retry = r.header("retry-after").unwrap_or("").to_string();
            crate::warn!(
                "usage HTTP {code} (request-id {}, retry-after {})",
                r.header("request-id").unwrap_or("-"),
                if retry.is_empty() { "-" } else { &retry }
            );
            match code {
                401 | 403 => Err(FetchError::Auth("err.token")),
                429 => Err(FetchError::RateLimited(retry.parse().unwrap_or(0))),
                _ => Err(FetchError::Other(format!("HTTPError {code}"))),
            }
        }
        Err(e) => Err(FetchError::Other(match e {
            ureq::Error::Transport(t) => format!("{:?}", t.kind()),
            other => other.to_string(),
        })),
    }
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct Limit {
    pub kind: String,
    pub pct: f64,
    pub reset: Option<f64>,
    /// nombre completo; si falta, el traducido según `kind` (o el que da la API para límites nuevos)
    pub name: Option<String>,
    /// nombre corto (widget); si falta, `name` o el traducido según `kind`
    pub short_name: Option<String>,
    /// duración de la ventana en segundos, si la fuente la indica (Codex)
    pub window: Option<f64>,
    /// "claude" (por defecto) o "codex"
    pub provider: &'static str,
    /// si la fuente dice si es un límite principal (Codex); para Claude se decide por `kind`
    pub primary: Option<bool>,
}

impl Limit {
    pub fn claude(kind: String, pct: f64, reset: Option<f64>, name: Option<String>) -> Self {
        Limit { kind, pct, reset, name, provider: "claude", ..Default::default() }
    }
}

fn to_epoch(v: &Value) -> Option<f64> {
    let s = v.as_str()?;
    chrono::DateTime::parse_from_rfc3339(s).ok().map(|d| d.timestamp_millis() as f64 / 1000.0)
}

const LEGACY_KEYS: [(&str, &str); 4] = [
    ("five_hour", "session"),
    ("seven_day", "weekly_all"),
    ("seven_day_opus", "weekly_opus"),
    ("seven_day_sonnet", "weekly_sonnet"),
];

const KNOWN: [&str; 4] = ["session", "weekly_all", "weekly_opus", "weekly_sonnet"];

/// Anota una vez los campos de un límite desconocido (sin valores), para poder nombrarlo bien.
fn log_unknown(kind: &str, item: &Value) {
    static SEEN: std::sync::Mutex<Vec<String>> = std::sync::Mutex::new(Vec::new());
    let mut seen = SEEN.lock().unwrap();
    if !seen.iter().any(|k| k == kind) {
        seen.push(kind.to_string());
        let fields: Vec<String> = item
            .as_object()
            .map(|o| o.iter().map(|(k, v)| if v.is_string() && k != "resets_at" { format!("{k}={v}") } else { k.clone() }).collect())
            .unwrap_or_default();
        crate::info!("límite nuevo '{kind}': {}", fields.join(", "));
    }
}

/// Límites a partir de `limits` o, si no existe, de los campos clásicos.
pub fn parse_limits(data: &Value) -> Vec<Limit> {
    let mut out: Vec<Limit> = data["limits"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|item| {
            let kind = item["kind"].as_str()?.to_string();
            if !KNOWN.contains(&kind.as_str()) {
                log_unknown(&kind, item);
            }
            let name = ["display_name", "label", "name", "title", "model_display_name"]
                .iter()
                .find_map(|k| item[*k].as_str().filter(|s| !s.is_empty()).map(String::from));
            Some(Limit::claude(kind, item["percent"].as_f64()?, to_epoch(&item["resets_at"]), name))
        })
        .collect();
    if out.is_empty() {
        for (key, kind) in LEGACY_KEYS {
            if let Some(pct) = data[key]["utilization"].as_f64() {
                out.push(Limit::claude(kind.into(), pct, to_epoch(&data[key]["resets_at"]), None));
            }
        }
    }
    out
}

pub fn window_seconds(kind: &str) -> Option<f64> {
    if kind == "session" {
        Some(5.0 * 3600.0)
    } else if kind.starts_with("weekly") {
        Some(7.0 * 86400.0)
    } else {
        None
    }
}

pub fn same_window(a: Option<f64>, b: Option<f64>) -> bool {
    matches!((a, b), (Some(a), Some(b)) if (a - b).abs() < 1800.0)
}

/// Puntos {t, l: {kind: [pct, reset]}} guardados en data\history.jsonl (mismo formato que la versión 2).
pub struct History {
    pub points: Vec<(f64, Map<String, Value>)>,
}

impl History {
    fn file() -> PathBuf {
        core::data_dir().join("history.jsonl")
    }

    pub fn load() -> Self {
        let cutoff = now() - HISTORY_DAYS * 86400.0;
        let mut points = Vec::new();
        if let Ok(f) = fs::File::open(Self::file()) {
            for line in BufReader::new(f).lines().map_while(Result::ok) {
                if let Ok(p) = serde_json::from_str::<Value>(&line) {
                    let t = p["t"].as_f64().unwrap_or(0.0);
                    if let (true, Some(l)) = (t >= cutoff, p["l"].as_object()) {
                        points.push((t, l.clone()));
                    }
                }
            }
            let text: String = points.iter().map(|(t, l)| json!({"t": t, "l": l}).to_string() + "\n").collect();
            let _ = fs::write(Self::file(), text);
        }
        History { points }
    }

    pub fn add(&mut self, limits: &[Limit]) {
        let l: Map<String, Value> = limits.iter().map(|x| (x.kind.clone(), json!([x.pct, x.reset]))).collect();
        let t = now();
        if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(Self::file()) {
            let _ = writeln!(f, "{}", json!({"t": t, "l": l}));
        }
        self.points.push((t, l));
    }

    fn entry(l: &Map<String, Value>, kind: &str) -> Option<(f64, Option<f64>)> {
        let v = l.get(kind)?;
        Some((v[0].as_f64()?, v[1].as_f64()))
    }

    pub fn last(&self, kind: &str) -> Option<(f64, Option<f64>)> {
        self.points.iter().rev().find_map(|(_, l)| Self::entry(l, kind))
    }

    pub fn last_limits(&self) -> Option<(f64, Vec<Limit>)> {
        let (t, l) = self.points.last()?;
        let limits = l
            .iter()
            .filter_map(|(k, v)| Some(Limit::claude(k.clone(), v[0].as_f64()?, v[1].as_f64(), None)))
            .collect();
        Some((*t, limits))
    }

    pub fn series(&self, kind: &str, since: f64) -> Vec<[f64; 2]> {
        self.points
            .iter()
            .filter(|(t, _)| *t >= since)
            .filter_map(|(t, l)| Self::entry(l, kind).map(|(p, _)| [*t, p]))
            .collect()
    }

    /// % por segundo en la última hora dentro de la misma ventana, o None si hay pocos datos.
    pub fn recent_rate(&self, kind: &str, reset: Option<f64>, span: f64) -> Option<f64> {
        let since = now() - span;
        let pts: Vec<(f64, f64)> = self
            .points
            .iter()
            .filter(|(t, _)| *t >= since)
            .filter_map(|(t, l)| Self::entry(l, kind).filter(|(_, r)| same_window(*r, reset)).map(|(p, _)| (*t, p)))
            .collect();
        let (first, last) = (pts.first()?, pts.last()?);
        if pts.len() < 2 || last.0 - first.0 < 900.0 {
            return None;
        }
        Some((last.1 - first.1) / (last.0 - first.0))
    }
}

/// Previsión de ritmo de un límite. La interfaz la convierte en texto.
#[derive(Debug, Clone, Serialize)]
pub struct Pace {
    /// "none" | "limit" | "idle" | "unused" | "hit" | "ok"
    pub state: &'static str,
    /// ritmo en %/h (sesión) o %/día (semanal)
    pub speed: Option<f64>,
    /// "h" | "day"
    pub unit: &'static str,
    /// cuándo se alcanzaría el límite (si state == "hit")
    pub eta: Option<f64>,
    /// tiempo transcurrido de la ventana, 0-100
    pub marker: Option<f64>,
}

pub fn pace_info(lim: &Limit, history: &History) -> Pace {
    let (Some(total), Some(reset)) = (lim.window.or_else(|| window_seconds(&lim.kind)), lim.reset) else {
        return Pace { state: "none", speed: None, unit: "h", eta: None, marker: None };
    };
    let now = now();
    let elapsed = (now - (reset - total)).clamp(0.0, total);
    let marker = Some(100.0 * elapsed / total);
    let mk = |state, speed, unit, eta| Pace { state, speed, unit, eta, marker };
    if lim.pct >= 100.0 {
        return mk("limit", None, "h", None);
    }
    // sesión: ritmo de la última hora (reacciona rápido); semanal: media de la ventana, porque
    // extrapolar una hora intensa a varios días daría avisos exagerados
    let short_window = total <= 86400.0;
    let mut rate = if short_window { history.recent_rate(&lim.kind, Some(reset), 3600.0) } else { None };
    if rate.is_none() && elapsed > 300.0 {
        rate = Some(lim.pct / elapsed);
    }
    let rate = match rate {
        Some(r) if r > 0.0 => r,
        _ => return mk(if lim.pct > 0.0 { "idle" } else { "unused" }, None, "h", None),
    };
    let (unit, factor) = if short_window { ("h", 3600.0) } else { ("day", 86400.0) };
    let eta = now + (100.0 - lim.pct) / rate;
    if eta < reset {
        mk("hit", Some(rate * factor), unit, Some(eta))
    } else {
        mk("ok", Some(rate * factor), unit, None)
    }
}
