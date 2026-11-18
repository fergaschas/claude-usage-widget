import { invoke } from "@tauri-apps/api/core";
import {
  animateNumber,
  autoFit,
  connectBanner,
  h,
  logo,
  onState,
  setColor,
  setText,
  svgIcon,
  updateBanner,
} from "../lib/dom";
import { colorClass, fmtNum, fmtReset, hhmm, hhmmss, left, paceText } from "../lib/format";
import { dayName, tr } from "../lib/i18n";
import type { Limit, State, Tokens } from "../lib/types";

const SERIES: Record<string, string> = {
  session: "var(--accent)",
  weekly_all: "var(--blue)",
  weekly_opus: "var(--violet)",
  weekly_sonnet: "var(--teal)",
};
const SERIES_HEX: Record<string, string> = {
  session: "#e8825f",
  weekly_all: "#6ea8fe",
  weekly_opus: "#b48ead",
  weekly_sonnet: "#88c0d0",
};

// límites nuevos que aún no tienen color propio
const EXTRA_HEX = ["#34d17a", "#f0c674", "#c792ea", "#7fdbca"];
const seriesHex = (kind: string, i = 0) => SERIES_HEX[kind] ?? EXTRA_HEX[i % EXTRA_HEX.length];
const seriesColor = (kind: string, i = 0) => SERIES[kind] ?? seriesHex(kind, i);

export function mount(root: HTMLElement) {
  document.body.classList.add("full");
  const app = h("div.full-root.fade-in");
  root.append(app);

  const refreshBtn = h("button.icon-btn", { onclick: refresh }, svgIcon("refresh"));
  const menuBtn = h("button.icon-btn", { onclick: () => invoke("open_menu") }, svgIcon("gear"));
  const title = h("h1");
  const account = h("div.account");
  const head = h("div.f-head", {}, logo(), h("div.titles", {}, title, account), refreshBtn, menuBtn);
  const banners = h("div.details");
  const cards = h("div.details");
  const extra = h("div.pill.hidden");
  const more = h("button.more-btn", { onclick: () => invoke("set_setting", { key: "show_all_limits", value: !showAll }) });
  let showAll = false;
  const toggle = h("button.section-toggle", { onclick: () => invoke("set_setting", { key: "details", value: !details }) });
  const detailsBox = h("div.details");
  const status = h("div.status");
  app.append(head, banners, cards, more, extra, toggle, detailsBox, status);

  const chart = historyCard();
  const tokens = tokensCard();
  detailsBox.append(chart.el, tokens.el);

  function refresh() {
    refreshBtn.classList.remove("spin");
    void refreshBtn.offsetWidth;
    refreshBtn.classList.add("spin");
    invoke("refresh");
  }
  window.addEventListener("keydown", (e) => {
    if (e.key === "F5") {
      e.preventDefault();
      refresh();
    }
  });

  let details = false;
  const rows = new Map<string, ReturnType<typeof limitCard>>();

  onState((full) => {
    showAll = full.settings.show_all_limits;
    const hidden = full.limits.filter((l) => !l.primary);
    const s = { ...full, limits: showAll ? full.limits : full.limits.filter((l) => l.primary) };
    const names = hidden.map((l) => l.short).join(", ");
    more.classList.toggle("hidden", !hidden.length);
    more.textContent = showAll ? tr("more.hide", { names }) : tr("more.show", { n: hidden.length, names });
    more.title = tr("more.title");
    setText(title, tr("title"));
    refreshBtn.title = tr("btn.refresh");
    menuBtn.title = tr("btn.settings");
    setText(account, s.account ?? "");
    account.classList.toggle("hidden", !s.account);

    banners.replaceChildren(...[updateBanner(s), connectBanner(s)].filter((x): x is HTMLElement => !!x));
    banners.classList.toggle("hidden", !banners.childElementCount);

    // tarjetas de límites
    const kinds = s.limits.map((l) => l.kind).join(",");
    if ([...rows.keys()].join(",") !== kinds) {
      cards.replaceChildren();
      rows.clear();
      for (const l of s.limits) {
        const c = limitCard();
        rows.set(l.kind, c);
        cards.append(c.el);
      }
    }
    for (const l of s.limits) rows.get(l.kind)!.update(l);
    if (!s.limits.length) {
      cards.replaceChildren(h("div.card.empty", {}, s.updated_at ? tr("status.no_limits") : tr("loading")));
      rows.clear();
    }

    extra.classList.toggle("hidden", s.extra === null);
    if (s.extra !== null) extra.replaceChildren(svgIcon("bolt"), tr("extra", { pct: s.extra.toFixed(0) }));

    details = s.settings.details;
    toggle.classList.toggle("open", details);
    toggle.replaceChildren(svgIcon("chevron"), tr("details"));
    detailsBox.classList.toggle("hidden", !details);
    if (details) {
      chart.update(s);
      tokens.update(s.tokens);
    }

    renderStatus(s);
  });

  function renderStatus(s: State) {
    const lines: string[] = [];
    let warn = false;
    if (s.error) {
      warn = true;
      lines.push(s.error);
      if (s.updated_at) lines.push(tr("status.last", { time: hhmm(new Date(s.updated_at * 1000)) }));
    } else {
      if (s.limits.length) {
        const worst = Math.max(...s.limits.map((l) => l.pct));
        lines.push(worst >= 100 ? tr("status.limit") : tr("status.margin", { pct: (100 - worst).toFixed(0) }));
      }
      if (s.updated_at) lines.push(tr("status.updated_short", { time: hhmmss(new Date(s.updated_at * 1000)) }));
      if (s.limits.some((l) => l.pace.marker !== null)) lines.push(tr("legend.marker"));
    }
    status.classList.toggle("warn", warn);
    status.replaceChildren(...lines.map((l) => h("div", {}, l)));
  }

  autoFit(app, { width: 440 });
}

function limitCard() {
  const name = h("div.name");
  const pct = h("div.pct.num");
  const fill = h("div.fill");
  const marker = h("div.marker");
  const bar = h("div.bar.thick", {}, fill, marker);
  const leftEl = h("span.num");
  const reset = h("span.reset.num");
  const pace = h("div.pace");
  const el = h("div.card.limit-card", {}, h("div.top", {}, name, pct), bar, h("div.meta", {}, leftEl, reset), pace);
  return {
    el,
    update(l: Limit) {
      setText(name, l.label);
      const cls = colorClass(l.pct);
      setColor(pct, cls);
      setColor(bar, cls);
      animateNumber(pct, l.pct);
      fill.style.width = `${Math.max(l.pct > 0 ? 1.5 : 0, Math.min(l.pct, 100))}%`;
      marker.classList.toggle("hidden", l.pace.marker === null);
      if (l.pace.marker !== null) marker.style.left = `${l.pace.marker}%`;
      setText(leftEl, tr("row.left", { pct: left(l) }));
      setText(reset, fmtReset(l.reset));
      const p = paceText(l.pace);
      setColor(pace, p.cls);
      pace.replaceChildren(...(p.icon ? [svgIcon(p.icon)] : []), h("span", {}, p.text));
      pace.classList.toggle("hidden", !p.text);
    },
  };
}

// ───────────── historial ─────────────

interface SeriesData {
  series: Record<string, [number, number][]>;
  start: number;
  end: number;
  gap: number;
}

function historyCard() {
  const seg = h("div.segmented");
  const chart = h("div.chart");
  const legend = h("div.legend");
  const heading = h("h2");
  const el = h("div.card", {}, h("div.card-head", {}, heading, seg), chart, legend);
  let hours = 24;
  let labels: Record<string, string> = {};
  let pending = false;

  async function draw() {
    if (pending) return;
    pending = true;
    try {
      const data = await invoke<SeriesData>("history_series", { hours });
      for (const k of Object.keys(data.series)) if (!(k in labels)) delete data.series[k];
      paint(data);
    } finally {
      pending = false;
    }
  }

  function paint(d: SeriesData) {
    const W = 406;
    const H = 150;
    const L = 30;
    const R = 6;
    const T = 8;
    const B = 20;
    const span = d.end - d.start;
    const x = (t: number) => L + ((W - L - R) * (t - d.start)) / span;
    const y = (p: number) => T + (H - T - B) * (1 - Math.min(Math.max(p, 0), 100) / 100);
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    let html = '<defs>';
    for (const kind of Object.keys(d.series)) {
      const c = seriesHex(kind, Object.keys(d.series).indexOf(kind));
      html += `<linearGradient id="g-${kind}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="${c}" stop-opacity=".28"/><stop offset="1" stop-color="${c}" stop-opacity="0"/></linearGradient>`;
    }
    html += '</defs><g class="grid">';
    for (const v of [0, 50, 100]) html += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>`;
    html += '</g><g class="axis">';
    for (const v of [0, 50, 100]) html += `<text x="${L - 6}" y="${y(v) + 3.5}" text-anchor="end">${v}%</text>`;
    const fmtT = (t: number) => {
      const dt = new Date(t * 1000);
      return hours <= 24 ? hhmm(dt) : `${dayName(dt)} ${dt.getDate()}`;
    };
    const ticks = hours <= 24 ? 4 : 7;
    for (let i = 0; i <= ticks; i++) {
      const t = d.start + (span * i) / ticks;
      const anchor = i === 0 ? "start" : i === ticks ? "end" : "middle";
      html += `<text x="${x(t)}" y="${H - 4}" text-anchor="${anchor}">${fmtT(t)}</text>`;
    }
    html += "</g>";

    // series en orden inverso: la sesión queda por encima
    let any = false;
    const kinds = Object.keys(d.series).reverse();
    for (const kind of kinds) {
      const pts = d.series[kind];
      const segs: [number, number][][] = [];
      let cur: [number, number][] = [];
      for (let i = 0; i < pts.length; i++) {
        if (i && pts[i][0] - pts[i - 1][0] > d.gap) {
          segs.push(cur);
          cur = [];
        }
        cur.push(pts[i]);
      }
      if (cur.length) segs.push(cur);
      const color = seriesColor(kind, Object.keys(d.series).indexOf(kind));
      html += `<g class="series">`;
      for (const s of segs) {
        any = true;
        if (s.length === 1) {
          html += `<circle cx="${x(s[0][0])}" cy="${y(s[0][1])}" r="2.5" fill="${color}"/>`;
          continue;
        }
        const line = s.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
        const area = `${line}L${x(s[s.length - 1][0]).toFixed(1)},${y(0)}L${x(s[0][0]).toFixed(1)},${y(0)}Z`;
        html += `<path d="${area}" fill="url(#g-${kind})"/>`;
        html += `<path class="line" d="${line}" stroke="${color}"/>`;
      }
      html += "</g>";
    }
    html += `<g class="hover" visibility="hidden"><line class="cursor" y1="${T}" y2="${H - B}"/></g>`;
    svg.innerHTML = html;

    const tooltip = h("div.tooltip");
    chart.replaceChildren(svg as unknown as HTMLElement, tooltip);
    if (!any) {
      chart.append(h("div.empty", { style: "position:absolute;inset:30px 0 0 30px" }, tr("chart.empty")));
      return;
    }

    // cursor: valores de cada serie en el instante bajo el ratón
    const hover = svg.querySelector(".hover") as SVGGElement;
    const cursor = hover.querySelector("line")!;
    svg.addEventListener("mousemove", (e) => {
      const rect = svg.getBoundingClientRect();
      const px = ((e.clientX - rect.left) / rect.width) * W;
      if (px < L || px > W - R) return hide();
      const t = d.start + ((px - L) / (W - L - R)) * span;
      const rows: HTMLElement[] = [];
      hover.querySelectorAll("circle").forEach((c) => c.remove());
      for (const kind of Object.keys(d.series)) {
        let best: [number, number] | null = null;
        for (const p of d.series[kind]) if (!best || Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
        if (!best || Math.abs(best[0] - t) > d.gap) continue;
        const dot = document.createElementNS(NS, "circle");
        dot.setAttribute("cx", String(x(best[0])));
        dot.setAttribute("cy", String(y(best[1])));
        dot.setAttribute("r", "3.5");
        dot.setAttribute("fill", seriesHex(kind, Object.keys(d.series).indexOf(kind)));
        dot.setAttribute("stroke", "#1d1d23");
        dot.setAttribute("stroke-width", "2");
        hover.append(dot);
        rows.push(
          h(
            "div",
            {},
            h("span.swatch", { style: `--c:${seriesColor(kind, Object.keys(d.series).indexOf(kind))};display:inline-block;margin-right:6px` }),
            `${labels[kind] ?? kind}: `,
            h("b", {}, `${best[1].toFixed(0)}%`),
          ),
        );
      }
      if (!rows.length) return hide();
      cursor.setAttribute("x1", String(px));
      cursor.setAttribute("x2", String(px));
      hover.setAttribute("visibility", "visible");
      const dt = new Date(t * 1000);
      tooltip.replaceChildren(h("div.t", {}, hours <= 24 ? hhmm(dt) : `${dayName(dt)} ${dt.getDate()} · ${hhmm(dt)}`), ...rows);
      const left = Math.min(Math.max((px / W) * rect.width, 70), rect.width - 70);
      tooltip.style.left = `${left}px`;
      tooltip.classList.add("show");
    });
    svg.addEventListener("mouseleave", hide);
    function hide() {
      hover.setAttribute("visibility", "hidden");
      tooltip.classList.remove("show");
    }
  }

  return {
    el,
    update(s: State) {
      setText(heading, tr("history"));
      hours = s.settings.chart_hours;
      seg.replaceChildren(
        ...[
          [24, "24 h"],
          [168, "7 d"],
        ].map(([v, text]) =>
          h(`button${v === hours ? ".active" : ""}`, { onclick: () => invoke("set_setting", { key: "chart_hours", value: v }) }, String(text)),
        ),
      );
      labels = Object.fromEntries(
        s.limits.map((l) => {
          const name = l.kind === "session" ? l.short : (l.label.split("·").pop() ?? l.label).trim();
          return [l.kind, name.charAt(0).toUpperCase() + name.slice(1)];
        }),
      );
      legend.replaceChildren(
        ...s.limits.map((l, i) => h("span", {}, h("span.swatch", { style: `--c:${seriesColor(l.kind, i)}` }), labels[l.kind])),
      );
      draw();
    },
  };
}

// ───────────── tokens ─────────────

function tokensCard() {
  const heading = h("h2");
  const body = h("div");
  const el = h("div.card", {}, h("div.card-head", {}, heading), body);
  return {
    el,
    update(t: Tokens | null) {
      setText(heading, tr("tokens.title"));
      if (!t) {
        body.replaceChildren(h("div.empty", {}, tr("tokens.scanning")));
        return;
      }
      const sum = (s: Tokens["today"]) => s.input + s.output + s.cache_write + s.cache_read;
      if (!sum(t.week)) {
        body.replaceChildren(h("div.empty", {}, tr("tokens.none")));
        return;
      }
      const kpis = h(
        "div.kpis",
        {},
        h("div.kpi", {}, h("div.k", {}, tr("tokens.today")), h("div.v.num", {}, fmtNum(sum(t.today)))),
        h("div.kpi", {}, h("div.k", {}, tr("tokens.week")), h("div.v.num", {}, fmtNum(sum(t.week)))),
      );

      // hoy por tipo: barra apilada con leyenda
      const parts: [keyof Tokens["today"], string][] = [
        ["output", "var(--accent)"],
        ["input", "var(--blue)"],
        ["cache_write", "var(--violet)"],
        ["cache_read", "var(--teal)"],
      ];
      const total = sum(t.today) || 1;
      const stack = h(
        "div.stack",
        {},
        ...parts
          .filter(([k]) => t.today[k] > 0)
          .map(([k, c]) => h("div", { style: `--c:${c};flex-grow:${Math.max(t.today[k] / total, 0.004)}`, title: `${tr(`tokens.${k}`)}: ${fmtNum(t.today[k])}` })),
      );
      const legend = h(
        "div.legend",
        {},
        ...parts.map(([k, c]) => h("span", {}, h("span.swatch", { style: `--c:${c}` }), `${tr(`tokens.${k}`)} `, h("b.num", {}, fmtNum(t.today[k])))),
      );

      // por día (7 días)
      const max = Math.max(...t.daily.map((d) => d[1]), 1);
      const daily = h(
        "div.daily",
        {},
        ...t.daily.map(([date, v], i) => {
          const d = new Date(`${date}T12:00:00`);
          return h(
            `div.col${i === t.daily.length - 1 ? ".today" : ""}`,
            { title: `${dayName(d)} ${d.getDate()}: ${fmtNum(v)}` },
            h("div.b", { style: `height:${Math.max((v / max) * 100, v ? 3 : 0)}%` }),
            h("div.d", {}, dayName(d)),
          );
        }),
      );

      const rank = (items: [string, number][], color: string) => {
        const tot = items.reduce((a, b) => a + b[1], 0) || 1;
        return h(
          "div.rank",
          {},
          ...items
            .filter(([, v]) => v / tot >= 0.005)
            .slice(0, 4)
            .map(([name, v]) =>
              h(
                "div.r",
                {},
                h("span.n", { title: name }, name),
                h("span.p.num", {}, `${((100 * v) / tot).toFixed(0)}%`),
                h("div.bar", { style: `--c:${color}` }, h("div.fill", { style: `width:${(100 * v) / tot}%` })),
              ),
            ),
        );
      };

      body.replaceChildren(
        kpis,
        h("div.sub-title", {}, tr("tokens.daily")),
        daily,
        h("div.sub-title", {}, tr("tokens.by_type")),
        stack,
        legend,
        h(
          "div.two-col",
          {},
          h("div", {}, h("div.sub-title", {}, tr("tokens.models_title")), rank(t.models, "var(--accent)")),
          h("div", {}, h("div.sub-title", {}, tr("tokens.projects_title")), rank(t.projects, "var(--blue)")),
        ),
        h("div.note", {}, tr("tokens.note")),
      );
    },
  };
}
