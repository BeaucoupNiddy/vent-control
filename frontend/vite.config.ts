import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds straight into the backend package so FastAPI serves the dashboard.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../backend/ventctl/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://localhost:8000" } },
});
