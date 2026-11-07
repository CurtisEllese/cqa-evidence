import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
// @ts-expect-error The small server adapter is native JavaScript, excluded from the browser bundle.
import { organizerMiddleware } from "./server/organizer.mjs";
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const middleware = organizerMiddleware({
    organizerUrl: env.ORGANIZER_DEV_URL,
  });
  return {
    plugins: [
      react(),
      {
        name: "organizer-api",
        configureServer(server) {
          server.middlewares.use(middleware);
        },
        configurePreviewServer(server) {
          server.middlewares.use(middleware);
        },
      },
    ],
    base: "./",
  };
});
