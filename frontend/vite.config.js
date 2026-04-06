import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks: {
          "vendor-react":    ["react", "react-dom"],
          "vendor-recharts": ["recharts"],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Dev only — in production Vercel rewrites handle /api/*
      "/api": {
        target: "https://crypto-trader-production-8ef4.up.railway.app",
        changeOrigin: true,
        secure: true,
      },
      "/ws": {
        target: "ws://localhost:3001",
        ws: true,
      },
    },
  },
});
