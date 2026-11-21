import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// API_PORT lets several checkouts run side by side (default 8000).
const api = `http://localhost:${process.env.API_PORT ?? "8000"}`;

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.WEB_PORT ?? 5173),
    proxy: {
      "/api": { target: api, changeOrigin: true, ws: true },
    },
  },
});
