import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { getAppConfig, setApiUserStateEnabled, type AppConfig, type FeatureInfo } from "../api/client";

// Which pages, menu items and capabilities this deployment has. The backend owns
// the table (backend/app/features.py) and enforces it on the API; this provider
// fetches GET /api/config/ once and gates the UI from the same answer, so prod
// and the passive "work" demo run the same bundle.

// If the config can't be fetched, fall back to the read-only set rather than
// showing controls whose endpoints may not exist.
const FALLBACK: AppConfig = {
  deployment: "unknown",
  features: [
    { id: "core", label: "Core", kind: "core", route: null },
    { id: "timeline", label: "Timeline", kind: "nav", route: "/" },
    { id: "search", label: "Search", kind: "nav", route: "/search/" },
  ],
};

interface FeaturesValue {
  deployment: string;
  features: FeatureInfo[];
  has: (id: string) => boolean;
}

const FeaturesContext = createContext<FeaturesValue | null>(null);

function toValue(config: AppConfig): FeaturesValue {
  const ids = new Set(config.features.map((f) => f.id));
  return { deployment: config.deployment, features: config.features, has: (id) => ids.has(id) };
}

export function FeaturesProvider({ children }: { children: ReactNode }) {
  const [value, setValue] = useState<FeaturesValue | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAppConfig()
      .catch((err) => {
        console.error("Could not load /api/config/, using read-only features", err);
        return FALLBACK;
      })
      .then((config) => {
        if (cancelled) return;
        // Set before children mount: their first effects may already write state.
        setApiUserStateEnabled(config.features.some((f) => f.id === "user_state"));
        setValue(toValue(config));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Children (and their API calls) wait for the feature set: the config call is
  // tiny, and rendering first would flash controls that then disappear.
  if (!value) return null;
  return <FeaturesContext.Provider value={value}>{children}</FeaturesContext.Provider>;
}

export function useFeatures(): FeaturesValue {
  const ctx = useContext(FeaturesContext);
  if (!ctx) throw new Error("useFeatures must be used inside <FeaturesProvider>");
  return ctx;
}

export function useFeature(id: string): boolean {
  return useFeatures().has(id);
}
