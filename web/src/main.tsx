import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "./index.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App, makeQueryClient } from "./App";

async function start() {
  // `npm run dev:mock`: answer the API in the browser with MSW (never in a production build).
  if (import.meta.env.MODE === "mock") {
    const { worker } = await import("./mocks/browser");
    await worker.start({
      serviceWorker: { url: "/ui/mockServiceWorker.js" },
      onUnhandledRequest: "bypass",
      quiet: true,
    });
  }
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App client={makeQueryClient()} />
    </StrictMode>,
  );
}

void start();
