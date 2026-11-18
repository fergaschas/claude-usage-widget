import { dayName, tr } from "./i18n";
import type { Limit, Pace } from "./types";

const pad = (n: number) => String(n).padStart(2, "0");
export const hhmm = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
export const hhmmss = (d: Date) => `${hhmm(d)}:${pad(d.getSeconds())}`;
const now = () => Date.now() / 1000;

export function colorClass(pct: number): string {
  return pct >= 90 ? "c-red" : pct >= 70 ? "c-amber" : "c-green";
}

export function fmtNum(n: number): string {
  for (const [div, suf] of [
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "k"],
  ] as const) {
    if (n >= div) return `${(n / div).toFixed(1).replace(/\.0$/, "")}${suf}`;
  }
  return String(Math.round(n));
}

export function fmtWhen(epoch: number): string {
  const d = new Date(epoch * 1000);
  if (d.toDateString() === new Date().toDateString()) return tr("when.today", { time: hhmm(d) });
  return tr("when.other", { day: dayName(d), time: hhmm(d) });
}

/** "reinicia en 2 h 13 min (17:44)" */
export function fmtReset(epoch: number | null): string {
  if (!epoch) return "";
  const secs = Math.floor(epoch - now());
  if (secs <= 0) return tr("reset.done");
  const d = Math.floor(secs / 86400);
  const h = Math.floor((secs % 86400) / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const left = d ? `${d} d ${h} h` : h ? `${h} h ${pad(m)} min` : `${m} min`;
  const local = new Date(epoch * 1000);
  const stamp = d ? `${dayName(local)} ${hhmm(local)}` : hhmm(local);
  return tr("reset.in", { left, stamp });
}

/** "2h 13m" */
export function fmtShort(epoch: number | null): string {
  if (!epoch) return "";
  const secs = Math.floor(epoch - now());
  if (secs <= 0) return tr("reset.short_done");
  const d = Math.floor(secs / 86400);
  const h = Math.floor((secs % 86400) / 3600);
  const m = Math.floor((secs % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${pad(m)}m` : `${m}m`;
}

export interface PaceText {
  text: string;
  cls: string;
  icon: "check" | "alert" | "stop" | "pause" | "";
}

export function paceText(p: Pace): PaceText {
  const speed = () =>
    tr("pace.speed", {
      value: (p.speed ?? 0).toFixed(1),
      unit: p.unit === "h" ? "%/h" : tr("pace.day"),
    });
  switch (p.state) {
    case "limit":
      return { text: tr("pace.limit"), cls: "c-red", icon: "stop" };
    case "idle":
      return { text: tr("pace.idle"), cls: "c-muted", icon: "pause" };
    case "unused":
      return { text: tr("pace.unused"), cls: "c-muted", icon: "pause" };
    case "hit":
      return { text: tr("pace.hit", { speed: speed(), when: fmtWhen(p.eta ?? 0) }), cls: "c-amber", icon: "alert" };
    case "ok":
      return { text: tr("pace.ok", { speed: speed() }), cls: "c-green", icon: "check" };
    default:
      return { text: "", cls: "c-muted", icon: "" };
  }
}

export const left = (l: Limit) => Math.max(0, 100 - l.pct).toFixed(0);
