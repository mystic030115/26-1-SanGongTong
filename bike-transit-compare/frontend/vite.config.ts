import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 이 환경은 fsevents 기반 감시가 변경을 놓쳐 HMR이 멈추는 일이 있어 폴링으로 강제 감시
    watch: { usePolling: true, interval: 300 },
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        timeout: 180_000,
        proxyTimeout: 180_000,
      },
    },
  },
});
