import { useEffect, useState } from "react";
import { ErrorBanner } from "./ErrorBanner";
import { formatRemaining, useDetox } from "../detox/Detox";
import type { DetoxDuration } from "../api/client";

const OPTIONS: { duration: DetoxDuration; label: string; detail: string }[] = [
  { duration: "3h", label: "3 hours", detail: "Ends 240 minutes from now" },
  { duration: "1d", label: "1 day", detail: "Ends at 2am Eastern tomorrow" },
  { duration: "3d", label: "3 days", detail: "Ends at 2am Eastern after three midnights" },
];

export function DetoxPage() {
  const { active, endsAt, start } = useDetox();
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);

  const begin = async (duration: DetoxDuration, label: string) => {
    if (!window.confirm(`Start a ${label} Dopamine Detox? Videos won't play until it ends, and it can't be cancelled.`)) return;
    setBusy(true);
    setError(null);
    try {
      await start(duration);
      setNow(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="detox-page">
      <h2>Dopamine Detox</h2>
      {error && <ErrorBanner error={error} />}
      {active && endsAt != null ? (
        <div className="detox-countdown">
          <div className="detox-countdown-label">Videos unlock in</div>
          <div className="detox-countdown-time">{formatRemaining(endsAt - now)}</div>
          <div className="detox-countdown-until">
            {new Date(endsAt).toLocaleString(undefined, {
              weekday: "short",
              hour: "numeric",
              minute: "2-digit",
              timeZoneName: "short",
            })}
          </div>
        </div>
      ) : (
        <>
          <p className="detox-intro">
            Videos won't play during a detox. Podcasts, articles and Corridor videos still work.
          </p>
          <div className="detox-options">
            {OPTIONS.map((o) => (
              <button
                key={o.duration}
                className="btn detox-option"
                disabled={busy}
                onClick={() => begin(o.duration, o.label)}
              >
                <span className="detox-option-label">{o.label}</span>
                <span className="detox-option-detail">{o.detail}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
