import { init as initApm } from "@elastic/apm-rum";

const serverUrl = import.meta.env.VITE_ELASTIC_APM_SERVER_URL;

const apm = serverUrl
  ? initApm({
      serviceName: "aitube-frontend",
      serverUrl,
      environment:
        import.meta.env.VITE_ELASTIC_APM_ENVIRONMENT || "development",
      distributedTracingOrigins: [window.location.origin],
    })
  : null;

export default apm;
