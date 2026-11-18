//! Actualizaciones desde GitHub Releases. Cada versión publica TokenPace-Setup.exe y, para que las
//! instalaciones 2.x ("Claude Usage") se actualicen solas, el mismo archivo como ClaudeUsage-Setup.exe.

use crate::usage::agent;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::io::{Read, Write};
use std::path::PathBuf;

const GITHUB_REPO: &str = "fergaschas/claude-usage-widget";
const SETUP_ASSETS: [&str; 2] = ["tokenpace-setup.exe", "claudeusage-setup.exe"];
pub const UPDATE_EVERY_SECS: u64 = 6 * 3600;

#[derive(Debug, Clone)]
pub struct Release {
    pub version: String,
    pub page: String,
    pub url: String,
    pub size: u64,
    pub digest: String,
    pub notes: String,
}

fn update_api() -> String {
    // se puede apuntar a otro servidor para probar el flujo de actualización
    std::env::var("CLAUDE_USAGE_UPDATE_URL")
        .unwrap_or_else(|_| format!("https://api.github.com/repos/{GITHUB_REPO}/releases/latest"))
}

/// "v2.5.0" → [2, 5]. Sin ceros finales, para que v2.5 y v2.5.0 se consideren la misma versión.
pub fn version_tuple(v: &str) -> Vec<u64> {
    let mut parts: Vec<u64> = v
        .split(|c: char| !c.is_ascii_digit())
        .filter(|s| !s.is_empty())
        .take(4)
        .filter_map(|s| s.parse().ok())
        .collect();
    while parts.last() == Some(&0) {
        parts.pop();
    }
    parts
}

/// Última Release publicada, o None si no tiene instalador.
pub fn fetch_latest_release() -> Result<Option<Release>, String> {
    let data: Value = agent(15)
        .get(&update_api())
        .set("Accept", "application/vnd.github+json")
        .call()
        .map_err(|e| e.to_string())?
        .into_json()
        .map_err(|e| e.to_string())?;
    let assets: Vec<&Value> = data["assets"].as_array().into_iter().flatten().collect();
    let asset = SETUP_ASSETS
        .iter()
        .find_map(|want| assets.iter().find(|a| a["name"].as_str().unwrap_or("").to_lowercase() == *want).copied());
    Ok(asset.map(|a| Release {
        version: data["tag_name"].as_str().unwrap_or("").trim_start_matches(['v', 'V']).to_string(),
        page: data["html_url"].as_str().unwrap_or("").into(),
        url: a["browser_download_url"].as_str().unwrap_or("").into(),
        size: a["size"].as_u64().unwrap_or(0),
        digest: a["digest"].as_str().unwrap_or("").into(),
        notes: data["body"].as_str().unwrap_or("").into(),
    }))
}

/// Descarga el instalador y comprueba tamaño y SHA-256 (si GitHub lo publica). Devuelve su ruta.
pub fn download_update(info: &Release, progress: impl Fn(u64)) -> Result<PathBuf, String> {
    let dest = std::env::temp_dir().join(format!("TokenPace-Setup-{}.exe", info.version));
    let result = (|| -> Result<(), String> {
        let resp = agent(60).get(&info.url).call().map_err(|e| e.to_string())?;
        let mut reader = resp.into_reader();
        let mut file = std::fs::File::create(&dest).map_err(|e| e.to_string())?;
        let (mut sha, mut done, mut buf) = (Sha256::new(), 0u64, vec![0u8; 256 * 1024]);
        loop {
            let n = reader.read(&mut buf).map_err(|e| e.to_string())?;
            if n == 0 {
                break;
            }
            file.write_all(&buf[..n]).map_err(|e| e.to_string())?;
            sha.update(&buf[..n]);
            done += n as u64;
            progress((done * 100 / info.size.max(1)).min(99));
        }
        if info.size > 0 && done != info.size {
            return Err(format!("size {done} != {}", info.size));
        }
        let hex: String = sha.finalize().iter().map(|b| format!("{b:02x}")).collect();
        if let Some(expected) = info.digest.strip_prefix("sha256:") {
            if hex != expected.to_lowercase() {
                return Err("SHA-256".into());
            }
        }
        Ok(())
    })();
    match result {
        Ok(()) => Ok(dest),
        Err(e) => {
            let _ = std::fs::remove_file(&dest);
            Err(e)
        }
    }
}

pub fn cleanup_old_setups() {
    if let Ok(entries) = std::fs::read_dir(std::env::temp_dir()) {
        for e in entries.flatten() {
            let name = e.file_name().to_string_lossy().to_string();
            if (name.starts_with("TokenPace-Setup-") || name.starts_with("ClaudeUsage-Setup-")) && name.ends_with(".exe") {
                // el instalador que nos acaba de actualizar puede seguir abierto
                let _ = std::fs::remove_file(e.path());
            }
        }
    }
}

/// Notas de la versión en texto plano, recortadas.
pub fn plain_notes(notes: &str) -> String {
    let clean: String = notes.chars().filter(|c| !"*_`#>".contains(*c)).collect();
    let clean = clean.trim();
    if clean.chars().count() > 600 {
        clean.chars().take(600).collect::<String>() + "…"
    } else {
        clean.to_string()
    }
}
