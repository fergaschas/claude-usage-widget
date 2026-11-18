import { defineConfig } from "vite";

// https://v2.tauri.app/start/frontend/vite/
export default defineConfig({
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  envPrefix: ["VITE_", "TAURI_ENV_*"],
  build: {
    outDir: "dist-web",
    target: "chrome120",
    minify: "esbuild",
    sourcemap: false,
  },
});
