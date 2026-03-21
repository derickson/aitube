import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/aitube/",
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    allowedHosts: ["smaug"],
    port: 8103,
    proxy: {
      "/aitube/api": {
        target: "http://localhost:3103",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/aitube/, ""),
      },
    },
  },
});
