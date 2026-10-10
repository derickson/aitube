import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { setApiDemoMode } from "../api/client";

// Demo Mode restricts the whole site to the work-safe "AI and Software" category
// (the backend does the filtering — backend/app/services/demo_mode.py). It's a
// per-browser setting, so turning it on for a work laptop doesn't change what the
// site shows anywhere else.
const STORAGE_KEY = "aitube-demo-mode";

function readStored(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

// Applied at module load, before any component mounts, so the very first API
// requests (fired from child effects, which run before a provider's) already
// carry the header.
const initial = readStored();
setApiDemoMode(initial);

interface DemoModeValue {
  demoMode: boolean;
  setDemoMode: (on: boolean) => void;
}

const DemoModeContext = createContext<DemoModeValue>({ demoMode: false, setDemoMode: () => {} });

export function DemoModeProvider({ children }: { children: ReactNode }) {
  const [demoMode, setState] = useState(initial);

  const setDemoMode = useCallback((on: boolean) => {
    setApiDemoMode(on);
    try {
      localStorage.setItem(STORAGE_KEY, on ? "1" : "0");
    } catch {
      // Storage unavailable (private window etc.) — the toggle still works for this tab.
    }
    setState(on);
  }, []);

  return <DemoModeContext.Provider value={{ demoMode, setDemoMode }}>{children}</DemoModeContext.Provider>;
}

export function useDemoMode(): DemoModeValue {
  return useContext(DemoModeContext);
}
