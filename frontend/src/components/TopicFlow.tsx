import { useEffect, useMemo, useRef, useState } from "react";
import {
  getTopicFlowLatest,
  getTopicFlowOverTime,
  getTopicFlowClusterItems,
  listSubscriptions,
  setInterest as apiSetInterest,
  setConsumed as apiSetConsumed,
  type ContentItemSummary,
  type Subscription,
  type TopicFlowResponse,
  type TopicFlowOverTime,
} from "../api/client";
import { ContentCard } from "./ContentCard";
import { ContentView } from "./ContentView";
import { ErrorBanner } from "./ErrorBanner";
import { TopicStoryChains, storyChainsNaturalHeight } from "./TopicStoryChains";

const NOISE_COLOR = "#9ca3af";

const CLUSTER_PALETTE = [
  "#4f46e5", "#e11d48", "#16a34a", "#d97706", "#0ea5e9",
  "#a21caf", "#0891b2", "#ca8a04", "#dc2626", "#7c3aed",
  "#059669", "#db2777", "#2563eb", "#65a30d", "#ea580c",
  "#0d9488", "#9333ea", "#b91c1c", "#1d4ed8", "#15803d",
];

function clusterColor(idx: number): string {
  return CLUSTER_PALETTE[idx % CLUSTER_PALETTE.length];
}

/** "3 hours ago" style relative time; falls back to the raw string on parse failure. */
function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const sec = Math.round((Date.now() - then) / 1000);
  if (sec < 0) return "just now";
  if (sec < 45) return "just now";
  const min = Math.round(sec / 60);
  if (min < 60) return `${min} minute${min === 1 ? "" : "s"} ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} hour${hr === 1 ? "" : "s"} ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day} day${day === 1 ? "" : "s"} ago`;
  const mon = Math.round(day / 30);
  if (mon < 12) return `${mon} month${mon === 1 ? "" : "s"} ago`;
  const yr = Math.round(mon / 12);
  return `${yr} year${yr === 1 ? "" : "s"} ago`;
}

function absoluteTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function TopicFlow() {
  const [data, setData] = useState<TopicFlowResponse | null>(null);
  const [flow, setFlow] = useState<TopicFlowOverTime | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedCluster, setSelectedCluster] = useState<string | null>(null);
  const [clusterItems, setClusterItems] = useState<ContentItemSummary[] | null>(null);
  const [itemsLoading, setItemsLoading] = useState(false);
  const [subs, setSubs] = useState<Record<string, Subscription>>({});
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [consumedIds, setConsumedIds] = useState<Set<string>>(new Set());
  const [chartsOpen, setChartsOpen] = useState(true);

  const plotRef = useRef<HTMLDivElement | null>(null);
  const topicsRef = useRef<HTMLDivElement | null>(null);

  // Initial load: cluster run, daily flow, and subscriptions.
  useEffect(() => {
    let cancel = false;
    setLoading(true);
    Promise.all([getTopicFlowLatest(), getTopicFlowOverTime(), listSubscriptions()])
      .then(([flowData, overTime, subList]) => {
        if (cancel) return;
        setData(flowData);
        setFlow(overTime);
        const subMap: Record<string, Subscription> = {};
        for (const s of subList) subMap[s.id] = s;
        setSubs(subMap);
      })
      .catch((e) => {
        if (cancel) return;
        const msg = String(e?.message || e);
        if (msg.includes("404")) {
          setError("No clustering run found yet. Run `uv run python -m backend.scripts.rebuild_clusters` to generate one.");
        } else {
          setError(msg);
        }
      })
      .finally(() => !cancel && setLoading(false));
    return () => { cancel = true; };
  }, []);

  // Stable cluster → color map (clusters are pre-sorted size desc, matching the cards).
  const clusterColorById = useMemo(() => {
    const map = new Map<string, string>();
    if (!data) return map;
    data.clusters.forEach((c, i) => map.set(c.id, clusterColor(i)));
    return map;
  }, [data]);

  // Shared height so the cluster map and story chains line up when side by side.
  // Driven by the chains' natural lane-stack height, floored so the scatter isn't tiny.
  const chartHeight = useMemo(
    () => Math.max(360, storyChainsNaturalHeight(flow?.series.length ?? 0)),
    [flow],
  );

  // When a topic is chosen, collapse the analytics charts and bring the topics
  // row to the top of the viewport so the items show without extra scrolling.
  useEffect(() => {
    if (!selectedCluster) return;
    setChartsOpen(false);
    const id = requestAnimationFrame(() => {
      topicsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    return () => cancelAnimationFrame(id);
  }, [selectedCluster]);

  // Cluster-map scatter (lazy Plotly import). Only mounted while charts are open.
  useEffect(() => {
    if (!chartsOpen || !data || !plotRef.current) return;
    let disposed = false;
    let plotEl: HTMLDivElement | null = null;

    (async () => {
      const Plotly = (await import("plotly.js-dist-min")).default;
      if (disposed || !plotRef.current) return;
      plotEl = plotRef.current;

      const byCluster = new Map<string | null, typeof data.points>();
      for (const p of data.points) {
        const arr = byCluster.get(p.cluster_id);
        if (arr) arr.push(p);
        else byCluster.set(p.cluster_id, [p]);
      }

      const traces: any[] = [];
      const noise = byCluster.get(null);
      if (noise && noise.length) {
        traces.push({
          x: noise.map((p) => p.x),
          y: noise.map((p) => p.y),
          text: noise.map((p) => p.title),
          customdata: noise.map((p) => [p.item_id, "noise"]),
          mode: "markers",
          type: "scattergl",
          name: `noise (${noise.length})`,
          marker: { color: NOISE_COLOR, size: 6, opacity: 0.45 },
          hovertemplate: "%{text}<extra>noise</extra>",
        });
      }
      for (const c of data.clusters) {
        const pts = byCluster.get(c.id);
        if (!pts || !pts.length) continue;
        traces.push({
          x: pts.map((p) => p.x),
          y: pts.map((p) => p.y),
          text: pts.map((p) => p.title),
          customdata: pts.map((p) => [p.item_id, c.id]),
          mode: "markers",
          type: "scattergl",
          name: `${c.label} (${c.size})`,
          marker: { color: clusterColorById.get(c.id) || NOISE_COLOR, size: 8, opacity: 0.85 },
          hovertemplate: `%{text}<extra>${c.label}</extra>`,
        });
      }

      const isDark = document.documentElement.getAttribute("data-theme") === "dark";
      const paper = isDark ? "#0f0f0f" : "#ffffff";
      const grid = isDark ? "#333" : "#e5e5e5";
      const font = isDark ? "#e5e5e5" : "#1a1a1a";

      const layout: any = {
        height: chartHeight,
        margin: { l: 30, r: 10, t: 10, b: 30 },
        showlegend: true,
        legend: { orientation: "v", x: 1.0, y: 1.0, font: { color: font, size: 11 } },
        xaxis: { gridcolor: grid, zerolinecolor: grid, color: font, showticklabels: false },
        yaxis: { gridcolor: grid, zerolinecolor: grid, color: font, showticklabels: false },
        paper_bgcolor: paper,
        plot_bgcolor: paper,
        hovermode: "closest",
        dragmode: "pan",
      };

      await Plotly.react(plotEl, traces as any, layout, {
        displaylogo: false,
        responsive: true,
        modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"],
      });

      (plotEl as any).on("plotly_click", (ev: any) => {
        const pt = ev?.points?.[0];
        if (!pt?.customdata) return;
        const [itemId, clusterId] = pt.customdata as [string, string];
        if (clusterId === "noise") {
          setSelectedItemId(itemId);
        } else {
          setSelectedCluster(clusterId);
        }
      });
    })().catch((e) => console.error("plotly scatter load failed", e));

    return () => {
      disposed = true;
      if (plotEl) {
        import("plotly.js-dist-min").then((m) => {
          try { (m.default as any).purge(plotEl!); } catch { /* noop */ }
        });
      }
    };
  }, [data, clusterColorById, chartsOpen, chartHeight]);

  // Fetch items when a cluster is selected.
  useEffect(() => {
    if (!selectedCluster || !data) {
      setClusterItems(null);
      return;
    }
    let cancel = false;
    setItemsLoading(true);
    getTopicFlowClusterItems(selectedCluster, data.run_id, 100)
      .then((items) => {
        if (cancel) return;
        setClusterItems(items);
        setConsumedIds(new Set(items.filter((i) => i.consumed).map((i) => i.id)));
      })
      .catch((e) => !cancel && setError(String(e?.message || e)))
      .finally(() => !cancel && setItemsLoading(false));
    return () => { cancel = true; };
  }, [selectedCluster, data]);

  const handleInterest = (itemId: string, value: "up" | "down" | "none") => {
    apiSetInterest(itemId, value).catch(() => {});
    setClusterItems((prev) =>
      prev ? prev.map((i) => (i.id === itemId ? { ...i, user_interest: value === "none" ? null : value } : i)) : prev,
    );
  };

  const handleToggleConsumed = (itemId: string, consumed: boolean) => {
    apiSetConsumed(itemId, consumed).catch(() => {});
    setConsumedIds((prev) => {
      const next = new Set(prev);
      if (consumed) next.add(itemId); else next.delete(itemId);
      return next;
    });
  };

  const selectedClusterMeta = useMemo(
    () => data?.clusters.find((c) => c.id === selectedCluster) || null,
    [data, selectedCluster],
  );

  const selectedItemSubName = useMemo(() => {
    const item = clusterItems?.find((i) => i.id === selectedItemId);
    return item ? subs[item.subscription_id]?.name || "" : "";
  }, [clusterItems, selectedItemId, subs]);

  if (loading) return <div className="topic-flow-page"><p>Loading topic flow…</p></div>;
  if (error)   return <div className="topic-flow-page"><ErrorBanner error={error} /></div>;
  if (!data)   return <div className="topic-flow-page"><p>No data.</p></div>;

  return (
    <div className={`topic-flow-layout${selectedItemId ? " flyout-open" : ""}`}>
      <div className="topic-flow-main">
        <div className="topic-flow-header">
          <div className="topic-flow-stats">
            <span><strong>{data.clusters.length}</strong> clusters</span>
            <span><strong>{data.doc_count}</strong> docs</span>
            <span><strong>{data.noise_count}</strong> noise</span>
            <span className="topic-flow-meta" title={absoluteTime(data.created_at)}>
              last {data.lookback_days}d · updated {formatRelative(data.created_at)} · {data.embedding_model}
            </span>
          </div>
        </div>

        <section className="topic-flow-analytics">
          <button
            className="topic-flow-accordion-toggle"
            onClick={() => setChartsOpen((o) => !o)}
            aria-expanded={chartsOpen}
          >
            <span className="topic-flow-chevron">{chartsOpen ? "▾" : "▸"}</span>
            <span>Analytics</span>
            <span className="topic-flow-accordion-hint">
              {chartsOpen ? "cluster map + topics over time" : "show charts"}
            </span>
          </button>

          {chartsOpen && (
            <div className="topic-flow-charts">
              <div className="topic-flow-chart-block">
                <h3 className="topic-flow-chart-title">Cluster Map</h3>
                <div ref={plotRef} className="topic-flow-plot" style={{ minHeight: chartHeight }} />
              </div>
              <div className="topic-flow-chart-block">
                <h3 className="topic-flow-chart-title">Topic Story Chains</h3>
                {flow && flow.series.length > 0 ? (
                  <TopicStoryChains
                    flow={flow}
                    colorById={clusterColorById}
                    selectedId={selectedCluster}
                    onSelect={(cid) => setSelectedCluster(cid)}
                    height={chartHeight}
                  />
                ) : (
                  <p className="topic-flow-empty">No dated items to chart yet.</p>
                )}
              </div>
            </div>
          )}
        </section>

        <section className="topic-flow-topics" ref={topicsRef}>
          <div className="topic-flow-section-head">
            <h2>Topics</h2>
            {selectedClusterMeta && (
              <span className="topic-flow-section-sub">
                showing <strong>{selectedClusterMeta.label}</strong> ({selectedClusterMeta.size})
              </span>
            )}
          </div>
          <div className="topic-flow-clusters">
            {data.clusters.map((c, i) => {
              const color = clusterColor(i);
              const isActive = c.id === selectedCluster;
              return (
                <button
                  key={c.id}
                  className={`topic-flow-cluster-card${isActive ? " active" : ""}`}
                  onClick={() => setSelectedCluster(isActive ? null : c.id)}
                  style={{ borderLeftColor: color }}
                >
                  <div className="topic-flow-cluster-header">
                    <span className="topic-flow-cluster-dot" style={{ background: color }} />
                    <span className="topic-flow-cluster-label">{c.label}</span>
                    <span className="topic-flow-cluster-size">{c.size}</span>
                  </div>
                  {c.top_terms.length > 0 && (
                    <div className="topic-flow-cluster-terms">
                      {c.top_terms.slice(0, 6).map((t) => (
                        <span key={t} className="topic-flow-term">{t}</span>
                      ))}
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        </section>

        {selectedCluster && (
          <div className="topic-flow-timeline">
            <div className="topic-flow-timeline-header">
              <h2>{selectedClusterMeta?.label || selectedCluster}</h2>
              <button className="topic-flow-clear" onClick={() => setSelectedCluster(null)}>
                Close
              </button>
            </div>
            {itemsLoading && <p>Loading items…</p>}
            {!itemsLoading && clusterItems && (
              <div className="content-grid">
                {clusterItems.map((item) => (
                  <ContentCard
                    key={item.id}
                    item={item}
                    subName={subs[item.subscription_id]?.name || ""}
                    isActive={selectedItemId === item.id}
                    isConsumed={consumedIds.has(item.id)}
                    onSelect={() => setSelectedItemId(item.id)}
                    onInterest={handleInterest}
                    onToggleConsumed={(id, c) => handleToggleConsumed(id, c)}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {selectedItemId && (
        <ContentView
          itemId={selectedItemId}
          subName={selectedItemSubName}
          onClose={() => setSelectedItemId(null)}
          onConsumedChange={handleToggleConsumed}
        />
      )}
    </div>
  );
}
