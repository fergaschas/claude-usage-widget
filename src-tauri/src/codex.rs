//! Codex (OpenAI): límites del plan y tokens a partir de sus registros locales
//! (`%CODEX_HOME%` o `~\.codex\sessions\**\*.jsonl`). No hace ninguna petición de red.
//!
//! Tras cada respuesta Codex guarda una línea `{"timestamp", "type": "event_msg", "payload":
//! {"type": "token_count", "info": {...}, "rate_limits": {...}}}`. Formato en el código de Codex,
//! codex-rs/protocol/src/protocol.rs: `TokenCountEvent`, `RateLimitSnapshot` (limit_id, limit_name,
//! primary, secondary, plan_type) y `RateLimitWindow` (used_percent, window_minutes, resets_at).
//! El dato solo se actualiza cuando se usa Codex.

use crate::core::{capitalize, home, now, t, tr, TOKEN_DAYS};
use crate::tokens::{jsonl_files, Acc, Split, TokenStats};
use crate::usage::Limit;
use chrono::{DateTime, Local};
use serde_json::Value;
use std::collections::BTreeMap;
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::SystemTime;

pub fn codex_home() -> PathBuf {
    match std::env::var_os("CODEX_HOME") {
        Some(p) if !p.is_empty() => PathBuf::from(p),
        _ => home().join(".codex"),
    }
}

fn sessions_dir() -> PathBuf {
    codex_home().join("sessions")
}

/// ¿Hay rastro de Codex en este PC?
pub fn installed() -> bool {
    sessions_dir().is_dir()
}

#[derive(Debug, Clone, Default)]
pub struct CodexLimits {
    pub limits: Vec<Limit>,
    /// cuándo se tomó el dato (lo escribió Codex)
    pub at: Option<f64>,
    pub plan: Option<String>,
}

fn epoch(ts: &str) -> Option<f64> {
    DateTime::parse_from_rfc3339(ts).ok().map(|d| d.timestamp_millis() as f64 / 1000.0)
}

/// Archivos de sesión ordenados del más reciente al más antiguo.
fn newest_sessions(since: f64) -> Vec<(PathBuf, SystemTime)> {
    let mut files = Vec::new();
    jsonl_files(&sessions_dir(), since, &mut files);
    let mut with_time: Vec<(PathBuf, SystemTime)> = files
        .into_iter()
        .filter_map(|p| fs::metadata(&p).and_then(|m| m.modified()).ok().map(|m| (p, m)))
        .collect();
    with_time.sort_by(|a, b| b.1.cmp(&a.1));
    with_time
}

fn lines(path: &PathBuf) -> impl Iterator<Item = Value> {
    let file = fs::File::open(path).ok();
    file.into_iter().flat_map(|f| {
        BufReader::new(f).split(b'\n').map_while(Result::ok).filter_map(|l| {
            // solo las líneas que nos interesan: evita analizar mensajes largos
            let s = String::from_utf8_lossy(&l);
            let wanted = s.contains("\"token_count\"") || s.contains("\"session_meta\"") || s.contains("\"turn_context\"");
            if wanted {
                serde_json::from_str::<Value>(&s).ok()
            } else {
                None
            }
        })
    })
}

/// Nombre de la ventana según su duración, como hace Codex: 5h, daily, weekly, monthly.
fn window_key(minutes: i64) -> String {
    let near = |target: i64| (minutes - target).abs() * 10 <= target;
    if near(300) {
        "session".into()
    } else if near(1440) {
        "daily".into()
    } else if near(10080) {
        "weekly".into()
    } else if near(43200) {
        "monthly".into()
    } else {
        format!("{}h", (minutes as f64 / 60.0).round() as i64)
    }
}

fn window_names(key: &str, minutes: i64) -> (String, String) {
    match key {
        "session" | "daily" | "weekly" | "monthly" => (t(&format!("codex.window.{key}")), t(&format!("codex.short.{key}"))),
        _ => {
            let n = tr("codex.window.hours", &[("n", (minutes / 60).to_string())]);
            (n.clone(), n)
        }
    }
}

/// "gpt-5.5-codex" → "GPT-5.5 Codex"; "o4-mini" → "O4 Mini"
pub fn pretty_model(model: &str) -> String {
    let (prefix, rest) = match model.strip_prefix("gpt-") {
        Some(rest) => {
            let mut parts = rest.splitn(2, '-');
            let version = parts.next().unwrap_or("");
            (format!("GPT-{version}"), parts.next().unwrap_or("").to_string())
        }
        None => (String::new(), model.to_string()),
    };
    let words: Vec<String> = rest.split(['-', '_']).filter(|p| !p.is_empty()).map(capitalize).collect();
    [prefix, words.join(" ")].into_iter().filter(|s| !s.is_empty()).collect::<Vec<_>>().join(" ")
}

/// "codex-other" → "Other"; "gpt-5.5-codex" → "Gpt 5.5 Codex"
fn pretty_limit(name: &str) -> String {
    let base = name.strip_prefix("codex-").unwrap_or(name);
    base.split(['-', '_']).filter(|p| !p.is_empty()).map(capitalize).collect::<Vec<_>>().join(" ")
}

fn snapshot_limits(rl: &Value, now: f64) -> Vec<Limit> {
    let id = rl["limit_id"].as_str().unwrap_or("codex");
    let main = id == "codex";
    let model = pretty_limit(rl["limit_name"].as_str().unwrap_or(id));
    ["primary", "secondary"]
        .iter()
        .filter_map(|which| {
            let w = &rl[*which];
            let pct = w["used_percent"].as_f64()?;
            let minutes = w["window_minutes"].as_i64().unwrap_or(if *which == "primary" { 300 } else { 10080 });
            let key = window_key(minutes);
            let (name, short) = window_names(&key, minutes);
            let reset = w["resets_at"].as_f64();
            // si la ventana ya se reinició desde el último dato, se empieza de cero
            let expired = reset.is_some_and(|r| r <= now);
            Some(Limit {
                kind: if main { format!("codex_{key}") } else { format!("codex_{}_{key}", id.replace('-', "_")) },
                pct: if expired { 0.0 } else { pct },
                reset: if expired { None } else { reset },
                name: Some(if main { name } else { format!("{model} · {name}") }),
                short_name: Some(if main { short } else { model.clone() }),
                window: Some(minutes as f64 * 60.0),
                provider: "codex",
                primary: Some(main),
            })
        })
        .collect()
}

static CACHE: Mutex<Option<(PathBuf, SystemTime, CodexLimits)>> = Mutex::new(None);

/// Último dato de límites que escribió Codex (de su sesión más reciente que lo tenga).
pub fn scan_limits() -> CodexLimits {
    let now = now();
    for (path, mtime) in newest_sessions(now - 30.0 * 86400.0) {
        {
            let cache = CACHE.lock().unwrap();
            if let Some((p, m, data)) = cache.as_ref() {
                if *p == path && *m == mtime {
                    let mut data = data.clone();
                    // recalcular por si alguna ventana ha caducado desde entonces
                    data.limits.iter_mut().filter(|l| l.reset.is_some_and(|r| r <= now)).for_each(|l| {
                        l.pct = 0.0;
                        l.reset = None;
                    });
                    return data;
                }
            }
        }
        // por cada limit_id, su último dato en este archivo
        let mut latest: BTreeMap<String, (f64, Value)> = BTreeMap::new();
        for d in lines(&path) {
            let p = &d["payload"];
            if p["type"] != "token_count" || !p["rate_limits"].is_object() {
                continue;
            }
            let at = d["timestamp"].as_str().and_then(epoch).unwrap_or(0.0);
            let id = p["rate_limits"]["limit_id"].as_str().unwrap_or("codex").to_string();
            if latest.get(&id).is_none_or(|(t, _)| at >= *t) {
                latest.insert(id, (at, p["rate_limits"].clone()));
            }
        }
        if latest.is_empty() {
            continue;
        }
        // el límite principal primero
        let mut snaps: Vec<&(f64, Value)> = latest.values().collect();
        snaps.sort_by_key(|(_, rl)| rl["limit_id"].as_str().unwrap_or("codex") != "codex");
        let data = CodexLimits {
            limits: snaps.iter().flat_map(|(_, rl)| snapshot_limits(rl, now)).collect(),
            at: latest.values().map(|(t, _)| *t).reduce(f64::max),
            plan: snaps.iter().find_map(|(_, rl)| rl["plan_type"].as_str()).map(capitalize),
        };
        *CACHE.lock().unwrap() = Some((path, mtime, data.clone()));
        return data;
    }
    CodexLimits::default()
}

/// Tokens de Codex de los últimos días, por día, modelo y proyecto.
/// Cada `token_count` lleva el total acumulado de la sesión: se suman las diferencias.
pub fn scan_tokens() -> TokenStats {
    let since = now() - (TOKEN_DAYS * 86400) as f64;
    let mut acc = Acc::new();
    for (path, _) in newest_sessions(since) {
        let (mut cwd, mut model) = (String::new(), String::from("?"));
        let mut prev = [0u64; 4];
        for d in lines(&path) {
            let p = &d["payload"];
            match d["type"].as_str() {
                Some("session_meta") => cwd = p["cwd"].as_str().unwrap_or_default().to_string(),
                Some("turn_context") => {
                    if let Some(c) = p["cwd"].as_str() {
                        cwd = c.to_string();
                    }
                    if let Some(m) = p["model"].as_str() {
                        model = m.to_string();
                    }
                }
                _ if p["type"] == "token_count" => {
                    let u = &p["info"]["total_token_usage"];
                    if !u.is_object() {
                        continue;
                    }
                    let n = |k: &str| u[k].as_u64().unwrap_or(0);
                    let (cached, input) = (n("cached_input_tokens"), n("input_tokens"));
                    let cur = [input.saturating_sub(cached), n("output_tokens"), n("cache_write_input_tokens"), cached];
                    // si el total baja (sesión nueva o compactada), se cuenta desde cero
                    let restarted = cur.iter().zip(prev.iter()).any(|(c, p)| c < p);
                    let delta: Vec<u64> = cur.iter().zip(prev.iter()).map(|(c, p)| if restarted { *c } else { c - p }).collect();
                    prev = cur;
                    let Some(when) = d["timestamp"].as_str().and_then(|s| DateTime::parse_from_rfc3339(s).ok()) else {
                        continue;
                    };
                    let parts = Split { input: delta[0], output: delta[1], cache_write: delta[2], cache_read: delta[3] };
                    acc.add(when.with_timezone(&Local).date_naive(), &parts, &pretty_model(&model), &cwd);
                }
                _ => {}
            }
        }
    }
    acc.finish()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Registro de ejemplo con el formato de codex-rs/protocol (TokenCountEvent).
    #[test]
    fn reads_limits_and_tokens_from_session_log() {
        let dir = std::env::temp_dir().join(format!("tokenpace-codex-test-{}", std::process::id()));
        let day = dir.join("sessions").join("2026").join("11").join("18");
        fs::create_dir_all(&day).unwrap();
        let now = now();
        let ts = |offset: f64| {
            DateTime::from_timestamp((now + offset) as i64, 0).unwrap().to_rfc3339_opts(chrono::SecondsFormat::Millis, true)
        };
        let usage = |input: u64, cached: u64, output: u64| {
            json!({"input_tokens": input, "cached_input_tokens": cached, "cache_write_input_tokens": 0,
                   "output_tokens": output, "reasoning_output_tokens": 0, "total_tokens": input + output})
        };
        let window = |pct: f64, minutes: i64, reset_in: f64| {
            json!({"used_percent": pct, "window_minutes": minutes, "resets_at": (now + reset_in) as i64})
        };
        let lines = [
            json!({"timestamp": ts(-120.0), "type": "session_meta", "payload": {"id": "x", "cwd": "C:/work/my-app"}}),
            json!({"timestamp": ts(-110.0), "type": "turn_context", "payload": {"cwd": "C:/work/my-app", "model": "gpt-5.5-codex"}}),
            json!({"timestamp": ts(-100.0), "type": "event_msg", "payload": {"type": "token_count",
                "info": {"total_token_usage": usage(1000, 600, 200), "last_token_usage": usage(1000, 600, 200)},
                "rate_limits": {"limit_id": "codex", "limit_name": null, "plan_type": "plus",
                    "primary": window(30.0, 300, 3600.0), "secondary": window(10.0, 10080, 86400.0)}}}),
            json!({"timestamp": ts(-50.0), "type": "event_msg", "payload": {"type": "token_count",
                "info": {"total_token_usage": usage(1500, 900, 350), "last_token_usage": usage(500, 300, 150)},
                "rate_limits": {"limit_id": "codex", "plan_type": "plus",
                    "primary": window(42.0, 300, 3500.0), "secondary": window(18.0, 10080, 86000.0)}}}),
            json!({"timestamp": ts(-40.0), "type": "event_msg", "payload": {"type": "token_count", "info": null,
                "rate_limits": {"limit_id": "codex-other", "limit_name": "codex-other",
                    "primary": null, "secondary": window(5.0, 10080, 86000.0)}}}),
        ];
        let text: String = lines.iter().map(|l| l.to_string() + "\n").collect();
        fs::write(day.join("rollout-test.jsonl"), text).unwrap();
        std::env::set_var("CODEX_HOME", &dir);

        let data = scan_limits();
        let kinds: Vec<(&str, f64, Option<bool>)> = data.limits.iter().map(|l| (l.kind.as_str(), l.pct, l.primary)).collect();
        assert_eq!(
            kinds,
            [("codex_session", 42.0, Some(true)), ("codex_weekly", 18.0, Some(true)), ("codex_codex_other_weekly", 5.0, Some(false))]
        );
        assert_eq!(data.plan.as_deref(), Some("Plus"));
        assert_eq!(data.limits[0].window, Some(300.0 * 60.0));
        assert_eq!(data.limits[2].short_name.as_deref(), Some("Other"));
        assert!(data.at.is_some_and(|t| (t - (now - 40.0)).abs() < 2.0));

        let tokens = scan_tokens();
        // se suman las diferencias del total acumulado: entrada sin caché 600, caché 900, salida 350
        assert_eq!((tokens.week.input, tokens.week.cache_read, tokens.week.output), (600, 900, 350));
        assert_eq!(tokens.models, vec![("GPT-5.5 Codex".to_string(), 1850)]);
        assert_eq!(tokens.projects, vec![("my-app".to_string(), 1850)]);

        std::env::remove_var("CODEX_HOME");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn model_names() {
        assert_eq!(pretty_model("gpt-5.5-codex"), "GPT-5.5 Codex");
        assert_eq!(pretty_model("o4-mini"), "O4 Mini");
        assert_eq!(pretty_limit("codex-other"), "Other");
    }
}
