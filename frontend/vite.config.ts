import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev proxy keeps the browser same-origin with the API, so the httpOnly session cookie just works.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
});
