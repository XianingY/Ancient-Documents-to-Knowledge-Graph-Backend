import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export const manualChunks = {
  react: ["react", "react-dom"],
  echarts: ["echarts"],
  icons: ["lucide-react"],
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:3000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks,
      },
    },
    chunkSizeWarningLimit: 1600,
  },
});
