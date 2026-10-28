import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev the API is proxied so the app calls a relative /api path, exactly as nginx does in production.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_PROXY_TARGET || "http://localhost:8010",
        rewrite: (p) => p.replace(/^\/api/, ""),
        changeOrigin: true,
      },
    },
  },
});
