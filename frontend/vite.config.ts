import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds straight into the backend package so FastAPI serves the dashboard.
// Target older iPhone Safari (iOS 14+) so newer syntax is rewritten instead of blanking the page.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../backend/ventctl/static", emptyOutDir: true, target: ["es2020", "safari14"], cssTarget: ["safari14"] },
  server: { proxy: { "/api": "http://localhost:8000" } },
});
