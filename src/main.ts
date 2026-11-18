import { getCurrentWindow } from "@tauri-apps/api/window";

// cada ventana carga la misma página; la vista depende de su etiqueta
const label = getCurrentWindow().label;
const root = document.getElementById("app")!;

// sin menú contextual del navegador (el widget abre el menú nativo de la app)
document.addEventListener("contextmenu", (e) => e.preventDefault());

const views: Record<string, () => Promise<{ mount: (root: HTMLElement) => void }>> = {
  widget: () => import("./views/widget"),
  main: () => import("./views/full"),
  connect: () => import("./views/connect"),
  installer: () => import("./views/installer"),
};

(views[label] ?? views.main)().then((v) => v.mount(root));
