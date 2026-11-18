import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { setLang, tr } from "./i18n";
import type { State } from "./types";

type Attrs = Record<string, string | number | boolean | EventListener | undefined>;
type Child = Node | string | null | undefined | false;

/** Crea un elemento: h("div.card.big", {onclick}, hijos…) */
export function h(tag: string, attrs: Attrs = {}, ...children: (Child | Child[])[]): HTMLElement {
  const [name, ...classes] = tag.split(".");
  const el = document.createElement(name || "div");
  if (classes.length) el.className = classes.join(" ");
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = String(v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c);
  }
  return el;
}

/** Cambia el texto solo si es distinto (evita parpadeos y reflujo). */
export function setText(el: Element, text: string) {
  if (el.textContent !== text) el.textContent = text;
}

/** Sustituye las clases de color (c-*) de un elemento. */
export function setColor(el: Element, cls: string) {
  el.classList.remove("c-green", "c-amber", "c-red", "c-muted");
  if (cls) el.classList.add(cls);
}

/** Pide a Rust que la ventana tenga el tamaño del contenido. */
export function autoFit(el: HTMLElement, opts: { width?: number } = {}) {
  let last = "";
  const fit = () => {
    const width = opts.width ?? el.offsetWidth;
    const height = el.offsetHeight;
    const key = `${width}x${height}`;
    if (key !== last && width > 0 && height > 0) {
      last = key;
      invoke("fit_window", { width, height });
    }
  };
  new ResizeObserver(fit).observe(el);
  fit();
}

/** Escucha el estado que envía Rust y pide el inicial. */
export function onState(render: (s: State) => void) {
  const apply = (s: State) => {
    setLang(s.lang);
    render(s);
  };
  listen<State>("state", (e) => apply(e.payload));
  invoke<State>("get_state").then(apply);
}

/** Número que se anima al cambiar de valor. */
export function animateNumber(el: HTMLElement, value: number, suffix = "%") {
  const from = Number(el.dataset.value ?? NaN);
  el.dataset.value = String(value);
  const paint = (v: number) => {
    el.innerHTML = `${Math.round(v)}<small>${suffix}</small>`;
  };
  if (Number.isNaN(from) || Math.round(from) === Math.round(value)) {
    paint(value);
    return;
  }
  const start = performance.now();
  const dur = 700;
  const step = (t: number) => {
    const k = Math.min(1, (t - start) / dur);
    const e = 1 - Math.pow(1 - k, 3);
    paint(from + (value - from) * e);
    if (k < 1 && el.dataset.value === String(value)) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

export function updateBanner(s: State, cls = "link-btn accent"): HTMLElement | null {
  if (!s.update) return null;
  const text =
    s.update.state === "downloading"
      ? tr("update.downloading", { pct: s.update.pct ?? 0 })
      : s.update.state === "installing"
        ? tr("update.installing")
        : tr("update.available", { version: s.update.version ?? "" }).replace(/^⬆\s+/, "");
  return h(
    `button.${cls.split(" ").join(".")}`,
    { onclick: () => invoke("start_update_cmd") },
    svgIcon("download"),
    h("span.grow", {}, text),
  );
}

export function connectBanner(s: State): HTMLElement | null {
  if (!s.error_key || !["err.no_session", "err.token"].includes(s.error_key)) return null;
  return h(
    "button.link-btn.warn",
    { onclick: () => invoke("open_connect_cmd") },
    svgIcon("plug"),
    h("span.grow", {}, tr("cta.connect").replace(/^⚠\s+/, "")),
  );
}

// ───────────── iconos (trazos SVG propios) ─────────────
const PATHS: Record<string, string> = {
  gear:
    '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
  expand: '<path d="M15 3h6v6"/><path d="M9 21H3v-6"/><path d="M21 3l-7 7"/><path d="M3 21l7-7"/>',
  minus: '<path d="M5 12h14"/>',
  chevron: '<path d="M9 6l6 6-6 6"/>',
  check: '<path d="M20 6L9 17l-5-5"/>',
  alert: '<path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  stop: '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
  pause: '<circle cx="12" cy="12" r="9"/><path d="M10 9v6M14 9v6"/>',
  download: '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M5 21h14"/>',
  plug: '<path d="M9 2v6M15 2v6"/><path d="M6 8h12v4a6 6 0 0 1-12 0z"/><path d="M12 18v4"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
};

export function svgIcon(name: string): SVGSVGElement {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  svg.innerHTML = PATHS[name] ?? "";
  return svg;
}

/** Logotipo: medidor circular naranja sobre un cuadrado redondeado oscuro (como el icono de la app). */
export function logo(cls = "logo"): SVGSVGElement {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 100 100");
  svg.setAttribute("class", cls);
  const r = 31;
  const c = 2 * Math.PI * r;
  svg.innerHTML = `
    <rect x="3" y="3" width="94" height="94" rx="22" fill="#18181d" stroke="rgba(255,255,255,.08)"/>
    <circle cx="50" cy="50" r="${r}" fill="none" stroke="#34343e" stroke-width="15"/>
    <circle cx="50" cy="50" r="${r}" fill="none" stroke="#e8825f" stroke-width="15"
      stroke-dasharray="${(c * 250) / 360} ${c}" transform="rotate(-90 50 50)"/>`;
  return svg;
}
