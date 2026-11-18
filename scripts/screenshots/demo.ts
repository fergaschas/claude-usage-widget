// Monta las vistas reales de la app con datos de ejemplo, sin Rust, para hacer las capturas de docs/.
// Uso: /scripts/screenshots/?view=widget|full&style=large|rings|compact&lang=es|en&collapsed=1
import { mockIPC, mockWindows } from "@tauri-apps/api/mocks";
import type { State } from "../../src/lib/types";

const q = new URLSearchParams(location.search);
const view = q.get("view") ?? "widget";
const lang = q.get("lang") ?? "en";
const now = Date.now() / 1000;
const H = 3600;

const state: State = {
  lang,
  version: "3.0.0",
  hotkey: true,
  settings: {
    on_top: true,
    notify: true,
    interval: 60,
    details: true,
    chart_hours: 24,
    check_updates: true,
    mode: view === "full" ? "full" : "widget",
    alpha: 1,
    widget_style: (q.get("style") as State["settings"]["widget_style"]) ?? "large",
    autostart: true,
    show_all_limits: false,
    collapsed: q.get("collapsed") === "1",
    fade_idle: false,
    codex: true,
  },
  limits: [
    {
      kind: "session",
      label: lang === "es" ? "Sesión (5 h)" : "Session (5 h)",
      short: lang === "es" ? "Sesión" : "Session",
      pct: 38,
      reset: now + 2 * H + 13 * 60 + 40,
      pace: { state: "ok", speed: 13.7, unit: "h", eta: null, marker: 55.7 },
      primary: true,
      provider: "claude",
    },
    {
      kind: "weekly_all",
      label: lang === "es" ? "Semanal · todos los modelos" : "Weekly · all models",
      short: lang === "es" ? "Semana" : "Week",
      pct: 21,
      reset: now + 4 * 24 * H + 5 * H + 1800,
      pace: { state: "ok", speed: 7.6, unit: "day", eta: null, marker: 39.9 },
      primary: true,
      provider: "claude",
    },
    {
      kind: "weekly_scoped",
      label: lang === "es" ? "Semanal · Fable" : "Weekly · Fable",
      short: "Fable",
      pct: 4,
      reset: now + 4 * 24 * H + 5 * H + 1800,
      pace: { state: "ok", speed: 1.4, unit: "day", eta: null, marker: 39.9 },
      primary: false,
      provider: "claude",
    },
  ],
  extra: null,
  error: null,
  error_key: null,
  retry_at: null,
  updated_at: now - 22,
  account: "you@example.com · Pro",
  update: null,
  tokens: {
    today: { input: 1_200, output: 312_000, cache_write: 846_000, cache_read: 24_600_000 },
    week: { input: 9_800, output: 2_140_000, cache_write: 6_900_000, cache_read: 188_200_000 },
    models: [
      ["Opus 5.5", 162_000_000],
      ["Sonnet 5.5", 35_000_000],
    ],
    projects: [
      ["my-app", 118_000_000],
      ["website", 51_000_000],
      ["scripts", 28_000_000],
    ],
    daily: [6, 5, 4, 3, 2, 1, 0].map((d, i) => {
      const date = new Date((now - d * 24 * H) * 1000);
      const iso = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
      return [iso, [31, 24, 3, 0, 46, 38, 26][i] * 1_000_000] as [string, number];
    }),
  },
  connect: null,
  codex_at: null,
  codex_plan: null,
  tokens_codex: null,
};

// ?codex=1: añade Codex (OpenAI) con datos de ejemplo
if (q.get("codex") === "1") {
  const es = lang === "es";
  state.limits.push(
    {
      kind: "codex_session",
      label: es ? "Sesión (5 h)" : "Session (5 h)",
      short: es ? "Sesión" : "Session",
      pct: 64,
      reset: now + 1 * H + 41 * 60 + 40,
      pace: { state: "hit", speed: 19.8, unit: "h", eta: now + 1.4 * H, marker: 66.3 },
      primary: true,
      provider: "codex",
    },
    {
      kind: "codex_weekly",
      label: es ? "Semanal" : "Weekly",
      short: es ? "Semana" : "Week",
      pct: 33,
      reset: now + 2 * 24 * H + 9 * H + 1800,
      pace: { state: "ok", speed: 7.1, unit: "day", eta: null, marker: 66.2 },
      primary: true,
      provider: "codex",
    },
  );
  state.codex_at = now - 7 * 60;
  state.codex_plan = "Plus";
  state.tokens_codex = {
    today: { input: 210_000, output: 96_000, cache_write: 0, cache_read: 3_400_000 },
    week: { input: 1_300_000, output: 610_000, cache_write: 0, cache_read: 21_000_000 },
    models: [["GPT-5.5 Codex", 22_910_000]],
    projects: [["my-app", 15_000_000], ["scripts", 7_910_000]],
    daily: state.tokens!.daily.map(([d], i) => [d, [2, 4, 0, 0, 6, 5, 3.7][i] * 1_000_000] as [string, number]),
  };
}

/** Historial de ejemplo: cada ventana de 5 h de la sesión sube a su propio ritmo y se para cuando
 *  se deja de trabajar; la actual (empezó hace 2,8 h) va al 13,6 %/h y llega al 38 %. El semanal sube despacio. */
function history(hours: number) {
  const start = now - hours * H;
  const step = hours <= 24 ? 300 : 1800;
  const rates = [13.6, 9, 0, 11, 7, 15, 4, 12, 0, 10]; // %/h por ventana, de la actual hacia atrás
  const stops = [5, 3.2, 0, 4.1, 2.5, 3.8, 1.5, 4.4, 0, 3]; // horas de trabajo dentro de cada ventana
  const session: [number, number][] = [];
  const weekly: [number, number][] = [];
  for (let t = start; t <= now; t += step) {
    const sinceReset = (t - (now - 2.8 * H)) / H;
    const cycle = ((sinceReset % 5) + 5) % 5;
    const k = Math.abs(Math.floor(sinceReset / 5)) % rates.length;
    session.push([t, Math.min(100, rates[k] * Math.min(cycle, stops[k]))]);
    weekly.push([t, Math.max(0, 21 - ((now - t) / (7 * 24 * H)) * 19)]);
  }
  return { series: { session, weekly_all: weekly }, start, end: now, gap: 900 };
}

mockWindows(view === "full" ? "main" : "widget");
mockIPC(
  (cmd, args) => {
    if (cmd === "get_state") return state;
    if (cmd === "history_series") return history((args as { hours: number }).hours);
    return null;
  },
  { shouldMockEvents: true },
);

const mod = view === "full" ? await import("../../src/views/full") : await import("../../src/views/widget");
mod.mount(document.getElementById("app")!);
