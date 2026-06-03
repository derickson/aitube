import { init as initApm } from "@elastic/apm-rum";

const serverUrl = import.meta.env.VITE_ELASTIC_APM_SERVER_URL;
// RUM is opt-in: it stays disabled unless explicitly enabled at build time,
// even when a server URL is present. This avoids the browser-side intake
// failures (HTTP status 0 / CORS) we hit in prod while keeping backend
// Python APM (ELASTIC_APM_*) — which is configured separately — untouched.
const enabled = import.meta.env.VITE_ELASTIC_APM_RUM_ENABLED === "true";

const apm =
  enabled && serverUrl
    ? initApm({
        serviceName: "aitube-frontend",
        serverUrl,
        environment:
          import.meta.env.VITE_ELASTIC_APM_ENVIRONMENT || "development",
        distributedTracingOrigins: [window.location.origin],
      })
    : null;

export default apm;
