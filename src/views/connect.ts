import { invoke } from "@tauri-apps/api/core";
import { autoFit, h, logo, onState, svgIcon } from "../lib/dom";
import { tr } from "../lib/i18n";
import type { Step } from "../lib/types";

/** Asistente de conexión: comprueba Claude Code, la sesión de Claude y el acceso al uso,
 *  y se actualiza solo (Rust lo comprueba cada 3 s mientras está abierto). */
export function mount(root: HTMLElement) {
  const app = h("div.dialog-root.fade-in");
  root.append(app);
  const heading = h("h1");
  const intro = h("p");
  const steps = h("div.steps");
  const tips = h("div.tips.hidden");
  const diag = h("button.text-btn", { onclick: copyDiagnostics });
  const main = h("button.btn");
  app.append(
    h("div.dialog-head", {}, logo(), h("div", {}, heading, intro)),
    steps,
    tips,
    h("div.dialog-foot", {}, diag, main),
  );

  let done = false;
  main.addEventListener("click", () => invoke("connect_action", { action: done ? "finish" : "close" }));

  async function copyDiagnostics() {
    const text = await invoke<string>("diagnostics");
    await navigator.clipboard.writeText(text);
    diag.textContent = tr("diag.copied");
    diag.classList.add("ok");
    setTimeout(() => {
      diag.textContent = tr("btn.diag");
      diag.classList.remove("ok");
    }, 2500);
  }

  onState((s) => {
    heading.textContent = tr("connect.heading");
    intro.textContent = tr("connect.intro");
    if (!diag.classList.contains("ok")) diag.textContent = tr("btn.diag");
    const view = s.connect;
    if (!view) return;
    done = view.done;
    steps.replaceChildren(...view.steps.map((st, i) => step(st, i + 1)));
    tips.textContent = tr("connect.tips");
    tips.classList.toggle("hidden", !done);
    main.textContent = done ? tr("btn.start") : tr("btn.close");
    main.className = done ? "btn primary" : "btn";
  });

  autoFit(app, { width: 500 });
}

function step(st: Step, n: number): HTMLElement {
  const badge =
    st.state === "ok"
      ? svgIcon("check")
      : st.state === "wait"
        ? h("div.spinner")
        : st.state === "error"
          ? svgIcon("alert")
          : String(n);
  return h(
    `div.step.${st.state}`,
    {},
    h("div.badge", {}, badge),
    h("div.text", {}, h("div.title", {}, st.title), st.detail ? h("div.detail", {}, st.detail) : null),
    st.action
      ? h(
          `button.btn${st.state === "ok" ? "" : ".primary"}`,
          { onclick: () => invoke("connect_action", { action: st.action![1] }) },
          st.action[0],
        )
      : null,
  );
}
