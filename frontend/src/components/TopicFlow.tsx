import { useEffect, useMemo, useRef, useState } from "react";
import {
  getTopicFlowLatest,
  getTopicFlowClusterItems,
  listSubscriptions,
  setInterest as apiSetInterest,
  setConsumed as apiSetConsumed,
  type ContentItemSummary,
  type Subscription,
  type TopicFlowResponse,
} from "../api/client";
import { ContentCard } from "./ContentCard";
import { ContentView } from "./ContentView";
import { ErrorBanner } from "./ErrorBanner";

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

function formatRunDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function TopicFlow() {
  const [data, setData] = useState<TopicFlowResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedCluster, setSelectedCluster] = useState<string | null>(null);
  const [clusterItems, setClusterItems] = useState<ContentItemSummary[] | null>(null);
  const [itemsLoading, setItemsLoading] = useState(false);
  const [subs, setSubs] = useState<Record<string, Subscription>>({});
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [consumedIds, setConsumedIds] = useState<Set<string>>(new Set());

  const plotRef = useRef<HTMLDivElement | null>(null);

  // Initial load
  useEffect(() => {
    let cancel = false;
    setLoading(true);
    Promise.all([getTopicFlowLatest(), listSubscriptions()])
      .then(([flow, subList]) => {
        if (cancel) return;
        setData(flow);
        const subMap: Record<string, Subscription> = {};
        for (const s of subList) subMap[s.id] = s;
        setSubs(subMap);
      })
      .catch((e) => {
        if (cancel) return;
        const msg = String(e?.message || e);
        if (msg.includes("404")) {
          setError("No clustering run found yet. Run `uv run aitube-cluster` to generate one.");
        } else {
          setError(msg);
        }
      })
      .finally(() => !cancel && setLoading(false));
    return () => { cancel = true; };
  }, []);

  // Build a stable index → color map for clusters (sorted by size desc to match
  // the cards order).
  const clusterColorById = useMemo(() => {
    const map = new Map<string, string>();
    if (!data) return map;
    data.clusters.forEach((c, i) => map.set(c.id, clusterColor(i)));
    return map;
  }, [data]);

  // Render Plotly scatter (lazy import).
  useEffect(() => {
    if (!data || !plotRef.current) return;
    let disposed = false;
    let plotEl: HTMLDivElement | null = null;

    (async () => {
      const Plotly = (await import("plotly.js-dist-min")).default;
      if (disposed || !plotRef.current) return;
      plotEl = plotRef.current;

      // One trace per cluster + a noise trace.
      const byCluster = new Map<string | null, typeof data.points>();
      for (const p of data.points) {
        const key = p.cluster_id;
        const arr = byCluster.get(key);
        if (arr) arr.push(p);
        else byCluster.set(key, [p]);
      }

      const traces: any[] = [];
      // Noise first (drawn under)
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
        height: 520,
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
    })().catch((e) => {
      console.error("plotly load failed", e);
    });

    return () => {
      disposed = true;
      if (plotEl) {
        // Best-effort cleanup
        import("plotly.js-dist-min").then((m) => {
          try { (m.default as any).purge(plotEl!); } catch { /* noop */ }
        });
      }
    };
  }, [data, clusterColorById]);

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

  if (loading) return <div className="topic-flow-page"><p>Loading topic flow…</p></div>;
  if (error)   return <div className="topic-flow-page"><ErrorBanner error={error} /></div>;
  if (!data)   return <div className="topic-flow-page"><p>No data.</p></div>;

  return (
    <div className="topic-flow-page">
      <div className="topic-flow-header">
        <div className="topic-flow-stats">
          <span><strong>{data.clusters.length}</strong> clusters</span>
          <span><strong>{data.doc_count}</strong> docs</span>
          <span><strong>{data.noise_count}</strong> noise</span>
          <span className="topic-flow-meta">
            last {data.lookback_days}d · {formatRunDate(data.created_at)} · {data.embedding_model}
          </span>
        </div>
      </div>

      <div ref={plotRef} className="topic-flow-plot" />

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

      {selectedItemId && (
        <ContentView
          itemId={selectedItemId}
          onClose={() => setSelectedItemId(null)}
        />
      )}
    </div>
  );
}
