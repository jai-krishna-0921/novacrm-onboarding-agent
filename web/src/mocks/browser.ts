import { setupWorker } from "msw/browser";
import { makeHandlers } from "./handlers";

export const worker = setupWorker(...makeHandlers(undefined, { latency: 250 }));
