import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const target = env.LIVABILITY_API_TARGET || "http://127.0.0.1:8091";

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ""),
          ...(env.LIVABILITY_API_KEY
            ? { headers: { Authorization: `Bearer ${env.LIVABILITY_API_KEY}` } }
            : {}),
        },
      },
    },
  };
});
