// Regenera las capturas de docs/ con la interfaz real y datos de ejemplo.
// Uso: node scripts/screenshots/take.mjs   (necesita Microsoft Edge instalado)
import { chromium } from "playwright-core";
import { createServer } from "vite";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const out = path.join(root, "docs");

const shots = [
  { name: "widget-large", query: "view=widget&style=large", selector: ".widget-frame" },
  { name: "widget-rings", query: "view=widget&style=rings", selector: ".widget-frame" },
  { name: "widget-compact", query: "view=widget&style=compact", selector: ".widget-frame" },
  { name: "widget-pill", query: "view=widget&style=large&collapsed=1", selector: ".widget-frame" },
  { name: "full-window", query: "view=full", selector: ".full-root", width: 440 },
];

const server = await createServer({ root, logLevel: "error", server: { port: 1430, strictPort: true } });
await server.listen();
const browser = await chromium.launch({ channel: "msedge" });
try {
  for (const lang of ["en", "es"]) {
    for (const shot of shots) {
      const page = await browser.newPage({
        viewport: { width: shot.width ?? 700, height: 1600 },
        deviceScaleFactor: 2,
        locale: lang,
      });
      await page.goto(`http://localhost:1430/scripts/screenshots/?${shot.query}&lang=${lang}`);
      await page.waitForSelector(shot.selector);
      await page.waitForTimeout(1600); // barras, anillos y entrada animada
      const file = path.join(out, `${shot.name}-${lang}.png`);
      await page.locator(shot.selector).screenshot({ path: file, omitBackground: shot.selector === ".widget-frame" });
      console.log(path.relative(root, file));
      await page.close();
    }
  }
} finally {
  await browser.close();
  await server.close();
}
