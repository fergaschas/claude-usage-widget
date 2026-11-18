import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { animateNumber, autoFit, connectBanner, h, onState, setColor, setText, svgIcon, updateBanner } from "../lib/dom";
import { colorClass, fmtShort, groupLimits, left, paceText } from "../lib/format";
import { tr } from "../lib/i18n";
import type { Limit, State } from "../lib/types";

/** Ventana flotante sin bordes, pensada para verse sin molestar:
 *  - arrastrar para moverla (al soltarla cerca de un borde se pega a él);
 *  - "—" la minimiza a una píldora con los porcentajes; clic en la píldora: expandir;
 *  - doble clic: ventana completa; clic derecho: menú;
 *  - se atenúa cuando el ratón no está encima (salvo si algún límite está cerca de agotarse).
 *  Estilos: "large" (barras grandes), "rings" (anillos), "compact". */
export function mount(root: HTMLElement) {
  document.documentElement.classList.add("transparent");
  document.body.classList.add("widget");
  const frame = h("div.widget-frame");
  const card = h("div.widget-card.fade-in");
  const pill = h("div.pill-widget.hidden");
  frame.append(card, pill);
  root.append(frame);

  const dot = h("span.dot.live");
  const collapseBtn = h("button.icon-btn", { onclick: () => setCollapsed(true) }, svgIcon("minus"));
  const fullBtn = h("button.icon-btn", { onclick: () => invoke("show_mode_cmd", { mode: "full" }) }, svgIcon("expand"));
  const brandName = h("span", {}, "Claude");
  const head = h("div.w-head", {}, h("div.brand", {}, dot, brandName), fullBtn, collapseBtn);
  const banners = h("div.w-banners");
  const body = h("div");
  const more = h("button.more-btn", { onclick: () => invoke("set_setting", { key: "show_all_limits", value: !showAll }) });
  card.append(head, banners, body, more);
  let showAll = false;

  function setCollapsed(v: boolean) {
    invoke("set_setting", { key: "collapsed", value: v });
  }

  // arrastrar solo si el ratón se mueve con el botón pulsado: así un clic sigue siendo un clic
  for (const el of [card, pill]) {
    let down: { x: number; y: number } | null = null;
    el.addEventListener("mousedown", (e) => {
      if (e.button === 0 && !(e.target as HTMLElement).closest("button")) down = { x: e.screenX, y: e.screenY };
    });
    el.addEventListener("mousemove", (e) => {
      if (down && e.buttons & 1 && Math.hypot(e.screenX - down.x, e.screenY - down.y) > 4) {
        down = null;
        getCurrentWindow().startDragging();
      }
    });
    el.addEventListener("mouseup", () => (down = null));
    el.addEventListener("dblclick", (e) => {
      if (!(e.target as HTMLElement).closest("button")) invoke("show_mode_cmd", { mode: "full" });
    });
    el.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      invoke("open_menu");
    });
  }
  // clic en la píldora: expandir (con una pequeña espera para no confundirlo con el doble clic)
  let clickTimer = 0;
  pill.addEventListener("click", (e) => {
    clearTimeout(clickTimer);
    if (e.detail === 1) clickTimer = window.setTimeout(() => setCollapsed(false), 230);
  });

  // atenuado cuando el ratón no está encima
  let hover = false;
  let alpha = 1;
  let fadeIdle = true;
  let urgent = false;
  let leaveTimer = 0;
  const applyOpacity = () => {
    const dim = fadeIdle && !hover && !urgent;
    frame.style.opacity = String(dim ? Math.min(alpha, 0.65) : alpha);
  };
  frame.addEventListener("mouseenter", () => {
    clearTimeout(leaveTimer);
    hover = true;
    applyOpacity();
  });
  frame.addEventListener("mouseleave", () => {
    leaveTimer = window.setTimeout(() => {
      hover = false;
      applyOpacity();
    }, 1200);
  });

  // al volver a mostrarse (bandeja, Ctrl+Alt+U, fin de pantalla completa): entrada suave
  listen("appear", () => {
    for (const el of [card, pill]) {
      el.classList.remove("fade-in");
      void el.offsetWidth;
      el.classList.add("fade-in");
    }
  });

  let style = "";
  let rows = new Map<string, Row>();
  let titles = new Map<string, HTMLElement>();
  let fitting = false;

  onState((s) => {
    alpha = s.settings.alpha;
    fadeIdle = s.settings.fade_idle;
    collapseBtn.title = tr("btn.collapse");
    fullBtn.title = tr("menu.full");
    const st = s.settings.widget_style;
    if (st !== style) {
      style = st;
      card.className = `widget-card ${st}`;
    }
    dot.classList.toggle("warn", !!s.error);
    banners.replaceChildren(...[updateBanner(s), connectBanner(s)].filter((x): x is HTMLElement => !!x));
    render(s);

    // un límite casi agotado, un aviso de ritmo o algo que hacer: siempre bien visible
    const shown = s.limits.filter((l) => l.primary || s.settings.show_all_limits);
    urgent =
      shown.some((l) => l.pct >= 90 || l.pace.state === "hit" || l.pace.state === "limit") ||
      !!s.update ||
      !!connectBanner(s);
    applyOpacity();

    const collapsed = s.settings.collapsed;
    card.classList.toggle("hidden", collapsed);
    pill.classList.toggle("hidden", !collapsed);
    if (collapsed) renderPill(s);

    if (!fitting) {
      fitting = true;
      requestAnimationFrame(() => autoFit(frame));
    }
  });

  function renderPill(s: State) {
    const limits = s.limits.filter((l) => l.primary);
    const groups = groupLimits(limits, s.codex_at);
    const warn = !!s.error || !!s.update || !!connectBanner(s);
    const item = (l: Limit) =>
      h("span.pill-item", {}, miniRing(l), h(`span.num.${colorClass(l.pct)}`, {}, `${l.pct.toFixed(0)}%`));
    pill.replaceChildren(
      h(`span.dot${warn ? ".warn" : ""}`),
      ...(groups.length
        ? groups.flatMap((g) => [...(groups.length > 1 ? [h("span.pill-group", {}, g.name)] : []), ...g.limits.map(item)])
        : [h("span.muted", {}, s.retry_at ?? "…")]),
    );
    pill.title = [
      tr("btn.expand"),
      ...groups.flatMap((g) => g.limits.map((l) => `${g.name} · ${l.label}: ${l.pct.toFixed(0)}% · ${fmtShort(l.reset)}`)),
    ].join("\n");
  }

  function render(full: State) {
    // los límites secundarios (p. ej. Fable) solo si el usuario lo pide
    showAll = full.settings.show_all_limits;
    const hidden = full.limits.filter((l) => !l.primary);
    const s = { ...full, limits: showAll ? full.limits : full.limits.filter((l) => l.primary) };
    more.classList.toggle("hidden", !hidden.length);
    const names = hidden.map((l) => l.short).join(", ");
    more.textContent = showAll ? tr("more.hide", { names }) : tr("more.show", { n: hidden.length, names });
    more.title = tr("more.title");
    if (!s.limits.length) {
      const text = connectBanner(s) ? "" : s.retry_at ? tr("wait.ratelimit", { time: s.retry_at }) : tr("loading");
      body.replaceChildren(text ? h("div.w-placeholder", {}, text) : "");
      body.dataset.key = "";
      rows = new Map();
      return;
    }
    // una sección por proveedor (Claude, Codex); con un solo proveedor, sin títulos
    const groups = groupLimits(s.limits, s.codex_at);
    const multi = groups.length > 1;
    setText(brandName, multi ? "TokenPace" : (groups[0]?.name ?? "Claude"));
    const cls = { large: "large-list", rings: "rings-list", compact: "compact-list" }[style] ?? "large-list";
    const key = `${style}|${multi}|${s.limits.map((l) => l.kind).join(",")}`;
    if (body.dataset.key !== key) {
      body.dataset.key = key;
      rows = new Map();
      titles = new Map();
      body.replaceChildren(
        ...groups.map((g) => {
          const list = h(`div.${cls}`);
          for (const l of g.limits) {
            const row = makeRow(style);
            row.nodes.forEach((n) => list.append(n));
            rows.set(l.kind, row);
          }
          if (!multi) return list;
          const meta = h("span.group-meta");
          titles.set(g.provider, meta);
          return h("div.group", {}, h("div.group-title", {}, h("span", {}, g.name), meta), list);
        }),
      );
    }
    for (const g of groups) {
      const meta = titles.get(g.provider);
      if (meta) setText(meta, g.meta);
    }
    for (const l of s.limits) rows.get(l.kind)!.update(l);
  }
}

/** Anillo diminuto para la píldora. */
function miniRing(l: Limit): SVGSVGElement {
  const r = 6;
  const c = 2 * Math.PI * r;
  const p = Math.min(Math.max(l.pct, 0), 100);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("class", `mini-ring ${colorClass(l.pct)}`);
  svg.innerHTML = `<circle cx="8" cy="8" r="${r}" fill="none" stroke="var(--track)" stroke-width="3"/>
    <circle cx="8" cy="8" r="${r}" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"
      stroke-dasharray="${(c * p) / 100} ${c}" transform="rotate(-90 8 8)" ${p ? "" : 'visibility="hidden"'}/>`;
  return svg;
}

interface Row {
  nodes: HTMLElement[];
  update: (l: Limit) => void;
}

function makeRow(style: string): Row {
  if (style === "rings") return ringRow();
  if (style === "compact") return compactRow();
  return largeRow();
}

function bar(cls = "bar") {
  const fill = h("div.fill");
  const marker = h("div.marker");
  const el = h(`div.${cls.split(" ").join(".")}`, {}, fill, marker);
  return {
    el,
    set(l: Limit) {
      setColor(el, colorClass(l.pct));
      fill.style.width = `${Math.max(l.pct > 0 ? 2 : 0, Math.min(l.pct, 100))}%`;
      const m = l.pace.marker;
      marker.classList.toggle("hidden", m === null);
      if (m !== null) marker.style.left = `${m}%`;
    },
  };
}

function paceLine() {
  const el = h("div.pace");
  return {
    el,
    set(l: Limit) {
      const p = paceText(l.pace);
      setColor(el, p.cls);
      el.replaceChildren(...(p.icon ? [svgIcon(p.icon)] : []), h("span", {}, p.text));
      el.classList.toggle("hidden", !p.text);
    },
  };
}

// estilo grande: nombre + % enorme, barra gruesa, reinicio y ritmo
function largeRow(): Row {
  const name = h("div.name");
  const pct = h("div.pct.num");
  const b = bar("bar thick");
  const sub = h("div.sub.num");
  const pace = paceLine();
  const el = h("div.large-row", {}, h("div.top", {}, name, pct), b.el, sub, pace.el);
  return {
    nodes: [el],
    update(l) {
      setText(name, l.short);
      setColor(pct, colorClass(l.pct));
      animateNumber(pct, l.pct);
      b.set(l);
      const when = fmtShort(l.reset);
      setText(sub, tr("widget.left", { pct: left(l) }) + (when ? `  ·  ${tr("widget.resets", { when })}` : ""));
      pace.set(l);
    },
  };
}

// estilo anillos: un medidor circular por límite, lado a lado
function ringRow(): Row {
  const NS = "http://www.w3.org/2000/svg";
  const size = 124;
  const stroke = 12;
  const r = (size - stroke) / 2 - 4;
  const c = 2 * Math.PI * r;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
  const mk = (tag: string, attrs: Record<string, string | number>) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
    svg.append(e);
    return e;
  };
  const center = size / 2;
  mk("circle", { class: "track", cx: center, cy: center, r, fill: "none", "stroke-width": stroke });
  const arc = mk("circle", {
    class: "arc",
    cx: center,
    cy: center,
    r,
    fill: "none",
    "stroke-width": stroke,
    "stroke-linecap": "round",
    "stroke-dasharray": c,
    "stroke-dashoffset": c,
    transform: `rotate(-90 ${center} ${center})`,
  });
  // marca del tiempo transcurrido: un trazo radial que gira alrededor del centro
  const y0 = center - r - stroke / 2 - 3;
  const y1 = center - r + stroke / 2 + 3;
  const tickBg = mk("line", { class: "tick-bg", x1: center, x2: center, y1: y0, y2: y1 });
  const tick = mk("line", { class: "tick", x1: center, x2: center, y1: y0, y2: y1 });
  for (const t of [tickBg, tick]) (t as SVGElement).style.transformOrigin = `${center}px ${center}px`;

  const pct = h("div.pct.num");
  const lbl = h("div.lbl");
  const ring = h("div.ring", {}, h("div.center", {}, pct, lbl));
  ring.prepend(svg as unknown as HTMLElement);
  const sub = h("div.sub.num");
  const el = h("div.ring-cell", {}, ring, sub);
  return {
    nodes: [el],
    update(l) {
      const cls = colorClass(l.pct);
      setColor(ring, cls);
      setColor(pct, cls);
      animateNumber(pct, l.pct);
      const p = Math.min(Math.max(l.pct, 0), 100);
      // con extremos redondeados, un arco casi vacío se ve como un punto: mínimo visible pequeño
      arc.setAttribute("stroke-dashoffset", String(c * (1 - (p > 0 ? Math.max(p, 1.5) : 0) / 100)));
      arc.classList.toggle("hidden", p <= 0);
      const m = l.pace.marker;
      for (const t of [tickBg, tick]) {
        t.classList.toggle("hidden", m === null);
        (t as SVGElement).style.transform = `rotate(${((m ?? 0) / 100) * 360}deg)`;
      }
      const warn = paceText(l.pace).cls;
      const warned = warn === "c-amber" || warn === "c-red";
      setColor(lbl, warned ? warn : "");
      setText(lbl, (warned ? "⚠ " : "") + l.short);
      const when = fmtShort(l.reset);
      setText(sub, when ? tr("widget.resets", { when }) : "");
    },
  };
}

// estilo compacto: una línea por límite
function compactRow(): Row {
  const name = h("div.name");
  const b = bar("bar thin");
  const pct = h("div.pct.num");
  const reset = h("div.reset.num");
  return {
    nodes: [name, b.el, pct, reset],
    update(l) {
      setText(name, l.short);
      b.set(l);
      const p = paceText(l.pace);
      const warned = p.cls === "c-amber" || p.cls === "c-red";
      setColor(pct, warned ? p.cls : colorClass(l.pct));
      setText(pct, `${warned ? "⚠ " : ""}${l.pct.toFixed(0)}%`);
      setText(reset, fmtShort(l.reset));
      b.el.title = p.text;
    },
  };
}
