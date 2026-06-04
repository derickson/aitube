import { useEffect, useState, useCallback } from "react";
import {
  getPredictions,
  listSubscriptions,
  batchPlaybackProgress,
  setInterest as apiSetInterest,
  setConsumed as apiSetConsumed,
  type PredictionResponse,
  type ContentItemSummary,
  type Subscription,
  type PlaybackProgress,
} from "../api/client";
import { ContentView } from "./ContentView";
import { ErrorBanner } from "./ErrorBanner";
import { ContentCard } from "./ContentCard";

function scoreClass(item: ContentItemSummary): "pos" | "neg" {
  const s = item.engagement?.score;
  return s != null && s >= 0.5 ? "pos" : "neg";
}

function scoreLabel(item: ContentItemSummary): string | null {
  const s = item.engagement?.score;
  if (s == null) return null;
  return `${Math.round(s * 100)}%`;
}

export function Prediction() {
  const [data, setData] = useState<PredictionResponse | null>(null);
  const [subs, setSubs] = useState<Record<string, Subscription>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [consumedIds, setConsumedIds] = useState<Set<string>>(new Set());
  // Items acted on (consumed) that should drop out of the watchlist view until
  // the next refetch makes the server authoritative.
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [progress, setProgress] = useState<Record<string, PlaybackProgress>>({});

  const fetchData = useCallback(async (showSpinner = true) => {
    try {
      if (showSpinner) setLoading(true);
      const [pred, subData] = await Promise.all([
        getPredictions(),
        listSubscriptions(),
      ]);
      setData(pred);
      setHidden(new Set());
      setConsumedIds(
        new Set(
          [...pred.interesting, ...pred.not_interesting]
            .filter((i) => i.consumed)
            .map((i) => i.id),
        ),
      );
      const subMap: Record<string, Subscription> = {};
      for (const s of subData) subMap[s.id] = s;
      setSubs(subMap);
      setError("");

      const ids = [...pred.interesting, ...pred.not_interesting].map((i) => i.id);
      if (ids.length > 0) {
        batchPlaybackProgress(ids).then(setProgress).catch(() => {});
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load predictions");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useEffect(() => {
    if (selectedId && window.innerWidth <= 768) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => { document.body.style.overflow = ""; };
  }, [selectedId]);

  const handleInterestChange = useCallback((itemId: string, value: "up" | "down" | "none") => {
    apiSetInterest(itemId, value).catch(() => {});
    // Optimistically reflect the new interest on the card (+/- active state).
    // The item only migrates between sections on the next intentional refetch
    // (Refresh / remount); refetching here would race the ES refresh and clobber
    // the optimistic state with stale data.
    setData((prev) => {
      if (!prev) return prev;
      const apply = (items: ContentItemSummary[]) =>
        items.map((i) => (i.id === itemId ? { ...i, user_interest: value === "none" ? null : value } : i));
      return { ...prev, interesting: apply(prev.interesting), not_interesting: apply(prev.not_interesting) };
    });
  }, []);

  const handleConsumedChange = useCallback((itemId: string, consumed: boolean, callApi = false) => {
    if (callApi) {
      apiSetConsumed(itemId, consumed).catch(() => {});
    }
    setConsumedIds((prev) => {
      const next = new Set(prev);
      if (consumed) next.add(itemId);
      else next.delete(itemId);
      return next;
    });
    // A consumed item leaves the unwatched watchlist — hide it optimistically.
    // Server becomes authoritative on the next intentional refetch (Refresh /
    // remount), by which point the ES write has been refreshed. Do NOT refetch
    // here: ES hasn't yet refreshed the consumed write, so it would return the
    // item as still-unwatched and reset the hidden set, undoing this change.
    setHidden((prev) => {
      const next = new Set(prev);
      if (consumed) next.add(itemId);
      else next.delete(itemId);
      return next;
    });
  }, []);

  const renderGrid = (items: ContentItemSummary[], emptyText: string) => {
    const visible = items.filter((i) => !hidden.has(i.id));
    if (visible.length === 0) {
      return <p className="empty-text prediction-empty">{emptyText}</p>;
    }
    return (
      <div className="content-grid">
        {visible.map((item) => {
          const label = scoreLabel(item);
          return (
            <div key={item.id} className="prediction-card-wrap">
              {label && (
                <span
                  className={`prediction-score prediction-score-${scoreClass(item)}`}
                  title="Predicted likelihood you'll watch (model score)"
                >
                  {label}
                </span>
              )}
              <ContentCard
                item={item}
                subName={subs[item.subscription_id]?.name ?? ""}
                isActive={item.id === selectedId}
                isConsumed={consumedIds.has(item.id)}
                progress={progress[item.id]}
                onSelect={() => setSelectedId(item.id === selectedId ? null : item.id)}
                onInterest={handleInterestChange}
                onToggleConsumed={handleConsumedChange}
              />
            </div>
          );
        })}
      </div>
    );
  };

  const interesting = data?.interesting ?? [];
  const notInteresting = data?.not_interesting ?? [];
  const selectedSubName =
    subs[
      [...interesting, ...notInteresting].find((i) => i.id === selectedId)?.subscription_id ?? ""
    ]?.name ?? "";

  return (
    <div className="timeline">
      <div className="timeline-header">
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <h2>Prediction</h2>
          {data && (
            <span className="prediction-subtitle">
              {data.scored} of {data.total_unwatched} unwatched videos scored
              {data.unscored > 0 ? ` · ${data.unscored} pending` : ""}
            </span>
          )}
        </div>
        <button className="btn" onClick={() => fetchData()}>Refresh</button>
      </div>

      {error && <ErrorBanner error={error} />}

      <div className={`timeline-layout${selectedId ? " flyout-open" : ""}`}>
        <div className="timeline-main">
          {loading && !data ? (
            <p className="loading-text">Loading predictions...</p>
          ) : (
            <>
              <section className="prediction-section">
                <h3 className="prediction-heading prediction-heading-pos">
                  <span className="prediction-heading-icon">▲</span>
                  Predicted Interesting
                </h3>
                <p className="prediction-section-note">
                  All unwatched videos the model rates above 50% likely to watch, plus anything you marked Interesting (+).
                </p>
                {renderGrid(interesting, "No interesting predictions yet.")}
              </section>

              <hr className="prediction-divider" />

              <section className="prediction-section">
                <h3 className="prediction-heading prediction-heading-neg">
                  <span className="prediction-heading-icon">▼</span>
                  Predicted Not Interested
                </h3>
                <p className="prediction-section-note">
                  All unwatched videos the model rates above 50% likely to skip, plus anything you marked Not interested (−).
                </p>
                {renderGrid(notInteresting, "No not-interested predictions yet.")}
              </section>
            </>
          )}
        </div>

        {selectedId && (
          <ContentView
            itemId={selectedId}
            subName={selectedSubName}
            onClose={() => setSelectedId(null)}
            onConsumedChange={handleConsumedChange}
          />
        )}
      </div>
    </div>
  );
}
