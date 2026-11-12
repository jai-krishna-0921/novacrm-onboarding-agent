/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";

const BACKEND = "http://127.0.0.1:8000";
const API_PREFIXES = [
  "/auth",
  "/runs",
  "/escalations",
  "/metrics",
  "/llm-calls",
  "/tool-calls",
  "/aes",
  "/migration",
  "/ui/config",
  "/healthz",
  "/agents",
];

export default defineConfig(({ mode }) => {
  const mock = mode === "mock";
  return {
    base: "/ui/",
    plugins: [react(), tailwindcss()],
    // The MSW worker lives in mock-public/ so it never ships in web/dist.
    publicDir: mock ? "mock-public" : false,
    resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
    server: {
      port: 5173,
      strictPort: true,
      // With mocks on, MSW answers in the browser and nothing is proxied.
      proxy: mock
        ? undefined
        : Object.fromEntries(API_PREFIXES.map((p) => [p, { target: BACKEND, changeOrigin: false }])),
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
      sourcemap: false,
      rollupOptions: {
        output: {
          manualChunks: {
            react: ["react", "react-dom", "react-router"],
            charts: ["recharts"],
            ui: [
              "@radix-ui/react-dialog",
              "@radix-ui/react-dropdown-menu",
              "@radix-ui/react-popover",
              "@radix-ui/react-select",
              "@radix-ui/react-tabs",
              "@radix-ui/react-toast",
              "@radix-ui/react-tooltip",
              "cmdk",
            ],
            data: ["@tanstack/react-query", "zod"],
          },
        },
      },
    },
    test: {
      environment: "jsdom",
      globals: false,
      setupFiles: ["./src/test/setup.ts"],
      css: false,
      include: ["src/**/*.test.{ts,tsx}"],
    },
  };
});
