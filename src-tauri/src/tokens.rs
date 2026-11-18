//! Tokens de Claude Code a partir de sus registros locales (~/.claude/projects/**/*.jsonl).

use crate::core::{home, now, TOKEN_DAYS};
use chrono::{DateTime, Duration, Local, NaiveDate};
use serde::Serialize;
use serde_json::Value;
use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Default, Serialize)]
pub struct Split {
    pub input: u64,
    pub output: u64,
    pub cache_write: u64,
    pub cache_read: u64,
}

impl Split {
    pub fn total(&self) -> u64 {
        self.input + self.output + self.cache_write + self.cache_read
    }
    fn add(&mut self, o: &Split) {
        self.input += o.input;
        self.output += o.output;
        self.cache_write += o.cache_write;
        self.cache_read += o.cache_read;
    }
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct TokenStats {
    pub today: Split,
    pub week: Split,
    /// [(nombre, total)] ordenados de mayor a menor
    pub models: Vec<(String, u64)>,
    pub projects: Vec<(String, u64)>,
    /// total por día, del más antiguo al de hoy: [(fecha "YYYY-MM-DD", total)]
    pub daily: Vec<(String, u64)>,
}

pub fn jsonl_files(dir: &Path, since: f64, out: &mut Vec<PathBuf>) {
    let Ok(entries) = fs::read_dir(dir) else { return };
    for e in entries.flatten() {
        let path = e.path();
        let Ok(meta) = e.metadata() else { continue };
        if meta.is_dir() {
            jsonl_files(&path, since, out);
        } else if path.extension().is_some_and(|x| x == "jsonl") {
            let mtime = meta
                .modified()
                .ok()
                .and_then(|m| m.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs_f64())
                .unwrap_or(0.0);
            if mtime >= since {
                out.push(path);
            }
        }
    }
}

/// claude-opus-5-5-20260101 → Opus 5.5
pub fn pretty_model(model: &str) -> String {
    let parts: Vec<&str> = model
        .strip_prefix("claude-")
        .unwrap_or(model)
        .split('-')
        .filter(|p| !(p.len() == 8 && p.chars().all(|c| c.is_ascii_digit())))
        .collect();
    let is_num = |p: &&str| !p.is_empty() && p.chars().all(|c| c.is_ascii_digit());
    let mut words: Vec<String> = parts.iter().filter(|p| !is_num(p)).map(|p| crate::core::capitalize(p)).collect();
    let nums: Vec<&str> = parts.iter().filter(|p| is_num(p)).copied().collect();
    if !nums.is_empty() {
        words.push(nums.join("."));
    }
    let s = words.join(" ");
    if s.is_empty() {
        model.to_string()
    } else {
        s
    }
}

fn sorted(map: HashMap<String, u64>) -> Vec<(String, u64)> {
    let mut v: Vec<_> = map.into_iter().collect();
    v.sort_by(|a, b| b.1.cmp(&a.1));
    v
}

pub fn scan_tokens() -> TokenStats {
    let since = now() - (TOKEN_DAYS * 86400) as f64;
    let mut files = Vec::new();
    jsonl_files(&home().join(".claude").join("projects"), since, &mut files);

    // cada respuesta se escribe varias veces mientras se genera: deduplicar por id + requestId
    let mut seen: HashMap<(String, String), (String, String, String, Value)> = HashMap::new();
    for f in files {
        let Ok(file) = fs::File::open(&f) else { continue };
        for line in BufReader::new(file).split(b'\n').map_while(Result::ok) {
            let line = String::from_utf8_lossy(&line);
            if !line.contains("\"usage\"") {
                continue;
            }
            let Ok(d) = serde_json::from_str::<Value>(&line) else { continue };
            let m = &d["message"];
            if !m["usage"].is_object() {
                continue;
            }
            let id = m["id"].as_str().or(d["uuid"].as_str()).unwrap_or_default().to_string();
            let req = d["requestId"].as_str().unwrap_or_default().to_string();
            seen.insert(
                (id, req),
                (
                    d["timestamp"].as_str().unwrap_or_default().to_string(),
                    m["model"].as_str().unwrap_or("?").to_string(),
                    d["cwd"].as_str().unwrap_or_default().to_string(),
                    m["usage"].clone(),
                ),
            );
        }
    }

    let mut acc = Acc::new();
    for (ts, model, cwd, u) in seen.into_values() {
        let Ok(when) = DateTime::parse_from_rfc3339(&ts) else { continue };
        if (when.timestamp() as f64) < since {
            continue;
        }
        let n = |k: &str| u[k].as_u64().unwrap_or(0);
        let parts = Split {
            input: n("input_tokens"),
            output: n("output_tokens"),
            cache_write: n("cache_creation_input_tokens"),
            cache_read: n("cache_read_input_tokens"),
        };
        acc.add(when.with_timezone(&Local).date_naive(), &parts, &pretty_model(&model), &cwd);
    }
    acc.finish()
}

/// Suma tokens por día, modelo y proyecto (lo usan Claude Code y Codex).
pub struct Acc {
    today: NaiveDate,
    stats: TokenStats,
    models: HashMap<String, u64>,
    projects: HashMap<String, u64>,
    names: HashMap<String, String>,
    daily: HashMap<NaiveDate, u64>,
}

impl Acc {
    pub fn new() -> Self {
        Acc {
            today: Local::now().date_naive(),
            stats: TokenStats::default(),
            models: HashMap::new(),
            projects: HashMap::new(),
            names: HashMap::new(),
            daily: HashMap::new(),
        }
    }

    pub fn add(&mut self, date: NaiveDate, parts: &Split, model: &str, cwd: &str) {
        let total = parts.total();
        if total == 0 || date < self.today - Duration::days(TOKEN_DAYS - 1) {
            return;
        }
        self.stats.week.add(parts);
        if date == self.today {
            self.stats.today.add(parts);
        }
        *self.daily.entry(date).or_default() += total;
        *self.models.entry(model.to_string()).or_default() += total;
        let name = Path::new(cwd).file_name().map(|s| s.to_string_lossy().to_string()).unwrap_or("?".into());
        // rutas de Windows: sin distinguir mayúsculas
        let shown = self.names.entry(name.to_lowercase()).or_insert(name).clone();
        *self.projects.entry(shown).or_default() += total;
    }

    pub fn finish(mut self) -> TokenStats {
        self.stats.models = sorted(self.models);
        self.stats.projects = sorted(self.projects);
        let (today, daily) = (self.today, self.daily);
        self.stats.daily = (0..TOKEN_DAYS)
            .rev()
            .map(|i| {
                let d = today - Duration::days(i);
                (d.format("%Y-%m-%d").to_string(), daily.get(&d).copied().unwrap_or(0))
            })
            .collect();
        self.stats
    }
}
