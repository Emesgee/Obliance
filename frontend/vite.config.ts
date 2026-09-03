import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// Dev only: the API is proxied so the browser never needs a cross-origin call.
// In prod Caddy serves dist/ and proxies /api (infra/caddy/Caddyfile).
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    // Explicit IPv4: on Node ≥17 "localhost" resolves to ::1, so the dev server
    // would only listen on [::1] and http://127.0.0.1:5173 would refuse.
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  build: { sourcemap: false, target: "es2022" },
});
