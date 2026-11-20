import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { setApiDemoMode, setApiZenMode } from "../api/client";
import { useFeature } from "../features/Features";

// Demo Mode restricts the whole site to the work-safe "AI and Software" category;
// Zen Mode is the inverse and hides that category (the backend does the filtering —
// backend/app/services/demo_mode.py). They're per-browser settings, so turning one
// on doesn't change what the site shows anywhere else, and they're mutually exclusive.
const DEMO_KEY = "aitube-demo-mode";
const ZEN_KEY = "aitube-zen-mode";

function readStored(key: string): boolean {
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

function store(key: string, on: boolean): void {
  try {
    localStorage.setItem(key, on ? "1" : "0");
  } catch {
    // Storage unavailable (private window etc.) — the toggle still works for this tab.
  }
}

// Applied at module load, before any component mounts, so the very first API
// requests (fired from child effects, which run before a provider's) already
// carry the header. Demo wins if both somehow got stored.
const initialDemo = readStored(DEMO_KEY);
const initialZen = !initialDemo && readStored(ZEN_KEY);
setApiDemoMode(initialDemo);
setApiZenMode(initialZen);

interface DemoModeValue {
  demoMode: boolean;
  zenMode: boolean;
  setDemoMode: (on: boolean) => void;
  setZenMode: (on: boolean) => void;
}

const DemoModeContext = createContext<DemoModeValue>({
  demoMode: false,
  zenMode: false,
  setDemoMode: () => {},
  setZenMode: () => {},
});

export function DemoModeProvider({ children }: { children: ReactNode }) {
  // A deployment without these toggles (e.g. work) must not apply a stale stored
  // value it offers no way to turn off. The feature set is fixed for the page's
  // lifetime and this provider mounts before any page, so resolving it in the
  // state initializers covers the first API request.
  const demoAllowed = useFeature("demo_mode");
  const zenAllowed = useFeature("zen_mode");
  const [demoMode, setDemoState] = useState(() => {
    const on = initialDemo && demoAllowed;
    setApiDemoMode(on);
    return on;
  });
  const [zenMode, setZenState] = useState(() => {
    const on = initialZen && zenAllowed;
    setApiZenMode(on);
    return on;
  });

  const setDemoMode = useCallback((on: boolean) => {
    setApiDemoMode(on);
    store(DEMO_KEY, on);
    setDemoState(on);
    if (on) {
      setApiZenMode(false);
      store(ZEN_KEY, false);
      setZenState(false);
    }
  }, []);

  const setZenMode = useCallback((on: boolean) => {
    setApiZenMode(on);
    store(ZEN_KEY, on);
    setZenState(on);
    if (on) {
      setApiDemoMode(false);
      store(DEMO_KEY, false);
      setDemoState(false);
    }
  }, []);

  return (
    <DemoModeContext.Provider value={{ demoMode, zenMode, setDemoMode, setZenMode }}>
      {children}
    </DemoModeContext.Provider>
  );
}

export function useDemoMode(): DemoModeValue {
  return useContext(DemoModeContext);
}
