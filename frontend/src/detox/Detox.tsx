import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { getDetox, startDetox, type DetoxDuration, type DetoxStatus } from "../api/client";

// Dopamine Detox: a timed lock (held in backend memory — backend/app/services/detox.py)
// during which YouTube videos don't play. It applies to every browser, and the only
// way to end it early is restarting the backend, so there's no "stop" here.
const POLL_MS = 60_000;

interface DetoxValue {
  active: boolean;
  /** Local-clock ms timestamp when the detox ends (derived from the server's
   *  remaining_seconds, so client clock skew doesn't matter). */
  endsAt: number | null;
  start: (duration: DetoxDuration) => Promise<void>;
}

const DetoxContext = createContext<DetoxValue>({
  active: false,
  endsAt: null,
  start: async () => {},
});

function toEndsAt(s: DetoxStatus): number | null {
  return s.active ? Date.now() + s.remaining_seconds * 1000 : null;
}

export function DetoxProvider({ children }: { children: ReactNode }) {
  const [endsAt, setEndsAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const refresh = useCallback(() => {
    getDetox().then((s) => setEndsAt(toEndsAt(s))).catch(() => {});
  }, []);

  // Poll so a detox started (or cleared by a restart) elsewhere shows up here.
  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  // Tick once a second while active so it releases exactly on time.
  useEffect(() => {
    if (endsAt == null) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [endsAt]);

  const start = useCallback(async (duration: DetoxDuration) => {
    setEndsAt(toEndsAt(await startDetox(duration)));
    setNow(Date.now());
  }, []);

  const active = endsAt != null && endsAt > now;
  return (
    <DetoxContext.Provider value={{ active, endsAt: active ? endsAt : null, start }}>
      {children}
    </DetoxContext.Provider>
  );
}

export function useDetox(): DetoxValue {
  return useContext(DetoxContext);
}

export function formatRemaining(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const d = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const hms = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return d > 0 ? `${d}d ${hms}` : hms;
}
