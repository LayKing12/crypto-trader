import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Forward all /api calls to the backend during dev
      "/api": {
        target: "http://localhost:3001",
        changeOrigin: true,
      },
      // WebSocket proxy
      "/ws": {
        target: "ws://localhost:3001",
        ws: true,
      },
    },
  },
});
