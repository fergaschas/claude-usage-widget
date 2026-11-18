import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { autoFit, h, logo, svgIcon } from "../lib/dom";
import { setLang, tr } from "../lib/i18n";

interface Info {
  lang: string;
  version: string;
  dir: string;
  update: boolean;
  has_session: boolean;
}

/** Instalador: carpeta, accesos directos, inicio con Windows y abrir al terminar. */
export async function mount(root: HTMLElement) {
  const info = await invoke<Info>("installer_info");
  setLang(info.lang);

  const app = h("div.dialog-root.fade-in", { style: "width:520px" });
  root.append(app);
  const input = h("input.input", { value: info.dir, spellcheck: "false" }) as HTMLInputElement;
  const toggle = (text: string) => {
    const box = h("input", { type: "checkbox", checked: true }) as HTMLInputElement;
    return { box, el: h("label.switch", {}, box, h("span.knob"), h("span", {}, text)) };
  };
  const desktop = toggle(tr("setup.opt.desktop"));
  const autostart = toggle(tr("setup.opt.autostart"));
  const open = toggle(tr("setup.opt.open"));
  const status = h("div.note", {}, tr("setup.note"));
  const progress = h("div.progress-line.hidden", {}, h("div"));
  const cancel = h("button.btn", { onclick: () => getCurrentWindow().close() }, tr("setup.cancel"));
  const main = h("button.btn.primary", { onclick: run }, tr(info.update ? "setup.update" : "setup.install"));
  const change = h("button.btn", { onclick: browse }, svgIcon("folder"), tr("setup.change"));
  const form = h(
    "div",
    {},
    info.has_session ? null : h("div.callout", {}, svgIcon("info"), h("span", {}, tr("setup.no_session").replace(/^ℹ\s+/, ""))),
    h("div.field-label", {}, tr("setup.folder")),
    h("div.field", {}, input, change),
    h("div.options", {}, desktop.el, autostart.el, open.el),
  );

  app.append(
    h(
      "div.dialog-head",
      {},
      logo(),
      h("div", {}, h("h1", {}, "TokenPace"), h("p", {}, `${tr("setup.tagline")} · ${tr("setup.version", { version: info.version })}`)),
    ),
    form,
    progress,
    status,
    h("div.dialog-foot", {}, h("span"), h("div", { style: "display:flex;gap:8px" }, cancel, main)),
  );

  async function browse() {
    const chosen = await invoke<string | null>("installer_browse", { current: input.value });
    if (chosen) input.value = chosen;
  }

  const unlisten = await listen<string>("installer-step", (e) => {
    status.className = "note";
    status.textContent = e.payload;
  });

  async function run() {
    for (const b of [main, cancel, change]) (b as HTMLButtonElement).disabled = true;
    input.disabled = true;
    progress.classList.remove("hidden");
    status.textContent = tr("setup.installing");
    try {
      await invoke("installer_run", {
        dir: input.value,
        desktop: desktop.box.checked,
        autostart: autostart.box.checked,
        open: open.box.checked,
      });
      unlisten();
      progress.classList.add("hidden");
      form.classList.add("hidden");
      status.className = "callout done-msg";
      status.replaceChildren(svgIcon("check"), h("span", {}, tr("setup.done").replace(/^✓\s+/, "")));
      cancel.classList.add("hidden");
      main.textContent = tr("setup.close");
      (main as HTMLButtonElement).disabled = false;
      main.removeEventListener("click", run);
      main.addEventListener("click", () => getCurrentWindow().close());
    } catch (e) {
      progress.classList.add("hidden");
      status.className = "note err-msg";
      status.textContent = String(e);
      for (const b of [main, cancel, change]) (b as HTMLButtonElement).disabled = false;
      input.disabled = false;
    }
  }

  autoFit(app, { width: 520 });
}
