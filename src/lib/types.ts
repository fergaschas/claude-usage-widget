export interface Pace {
  state: "none" | "limit" | "idle" | "unused" | "hit" | "ok";
  speed: number | null;
  unit: "h" | "day";
  eta: number | null;
  marker: number | null;
}

export interface Limit {
  kind: string;
  label: string;
  short: string;
  pct: number;
  reset: number | null;
  pace: Pace;
  primary: boolean;
  provider: "claude" | "codex";
}

export interface Split {
  input: number;
  output: number;
  cache_write: number;
  cache_read: number;
}

export interface Tokens {
  today: Split;
  week: Split;
  models: [string, number][];
  projects: [string, number][];
  daily: [string, number][];
}

export interface Step {
  state: "ok" | "todo" | "wait" | "error";
  title: string;
  detail: string;
  action: [string, string] | null;
}

export interface State {
  lang: string;
  version: string;
  hotkey: boolean;
  settings: {
    on_top: boolean;
    notify: boolean;
    interval: number;
    details: boolean;
    chart_hours: number;
    check_updates: boolean;
    mode: string;
    alpha: number;
    widget_style: "large" | "rings" | "compact";
    autostart: boolean;
    show_all_limits: boolean;
    collapsed: boolean;
    fade_idle: boolean;
    codex: boolean;
  };
  limits: Limit[];
  extra: number | null;
  error: string | null;
  error_key: string | null;
  retry_at: string | null;
  updated_at: number | null;
  account: string | null;
  update: { state: "available" | "downloading" | "installing"; version?: string; pct?: number } | null;
  tokens: Tokens | null;
  connect: { steps: Step[]; done: boolean } | null;
  codex_at: number | null;
  codex_plan: string | null;
  tokens_codex: Tokens | null;
}
