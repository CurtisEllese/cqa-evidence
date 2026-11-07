import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const proxy = { "/api": { target: env.BACKEND_URL || "http://127.0.0.1:8000", changeOrigin: true } };
  return {
    plugins: [react()],
    build: { rollupOptions: { input: { main: "index.html", auth: "auth-redirect.html" } } },
    server: {
      proxy,
      // Test databases and Python runtimes change independently of the UI.
      watch: { ignored: ["**/tmp/**", "**/.venv*/**", "**/local-data/**", "**/.hypothesis/**", "**/__pycache__/**"] },
    },
    preview: { proxy },
    base: "./",
  };
});
