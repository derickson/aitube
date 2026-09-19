import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  getQuarantineStats,
  listSubscriptions,
  type QuarantineEventDetail,
  type QuarantineStatsResponse,
  type Subscription,
} from "../api/client";
import { ErrorBanner } from "./ErrorBanner";

const REASON_PALETTE = [
  "#4f46e5", "#e11d48", "#0ea5e9", "#d97706", "#16a34a",
  "#a21caf", "#0891b2", "#ca8a04", "#7c3aed", "#059669",
];

const VERDICT_META: Record<
  QuarantineEventDetail["verdict"],
  { label: string; hint: string }
> = {
  fooled: { label: "Fooled", hint: "both models liked it" },
  split: { label: "Split", hint: "models disagreed" },
  suspected: { label: "Already suspected", hint: "both models were skeptical" },
  unscored: { label: "Unscored", hint: "not enough signal to judge" },
};

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  } catch {
    return iso;
  }
}

function pct(n: number | null): string {
  return n == null ? "—" : `${Math.round(n)}`;
}

export function QuarantinePage() {
  const [stats, setStats] = useState<QuarantineStatsResponse | null>(null);
  const [subs, setSubs] = useState<Record<string, Subscription>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [verdictFilter, setVerdictFilter] = useState<QuarantineEventDetail["verdict"] | null>(null);
  const [reasonFilter, setReasonFilter] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [showAllRows, setShowAllRows] = useState(false);

  const blindSpotRef = useRef<HTMLDivElement | null>(null);
  const reasonBarRef = useRef<HTMLDivElement | null>(null);
  const weeklyRef = useRef<HTMLDivElement | null>(null);
  const leaderboardRef = useRef<HTMLDivElement | null>(null);
  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({});

  useEffect(() => {
    let cancel = false;
    setLoading(true);
    Promise.all([getQuarantineStats(), listSubscriptions()])
      .then(([statsData, subList]) => {
        if (cancel) return;
        setStats(statsData);
        const subMap: Record<string, Subscription> = {};
        for (const s of subList) subMap[s.id] = s;
        setSubs(subMap);
      })
      .catch((e) => !cancel && setError(String(e?.message || e)))
      .finally(() => !cancel && setLoading(false));
    return () => { cancel = true; };
  }, []);

  const reasonColor = useMemo(() => {
    const map = new Map<string, string>();
    stats?.reason_counts.forEach((r, i) => map.set(r.reason_code, REASON_PALETTE[i % REASON_PALETTE.length]));
    return map;
  }, [stats]);

  const subName = (id: string | null) => (id && subs[id]?.name) || id || "unknown";

  const filteredEvents = useMemo(() => {
    if (!stats) return [];
    return stats.events.filter(
      (e) =>
        (!verdictFilter || e.verdict === verdictFilter) &&
        (!reasonFilter || e.reason_code === reasonFilter),
    );
  }, [stats, verdictFilter, reasonFilter]);

  // Blind-Spot Map: scatter of engagement percentile vs. interest percentile,
  // one trace per reason_code, quadrant lines at the population median (50).
  useEffect(() => {
    if (!stats || !blindSpotRef.current) return;
    let disposed = false;
    const plotEl = blindSpotRef.current;
    // Plot anything with at least one score — dropping events missing either
    // score would empty the chart entirely whenever one model (commonly
    // interest_score) hasn't scored quarantined items yet. Missing dimensions
    // land in a "no signal" margin lane below 0 instead of vanishing.
    const NO_SIGNAL = -10;
    const plottable = filteredEvents.filter(
      (e) => e.interest_percentile != null || e.engagement_percentile != null,
    );

    (async () => {
      const Plotly = (await import("plotly.js-dist-min")).default;
      if (disposed) return;

      const isDark = document.documentElement.getAttribute("data-theme") === "dark";
      const paper = isDark ? "#0f0f0f" : "#ffffff";
      const grid = isDark ? "#333" : "#e5e5e5";
      const font = isDark ? "#e5e5e5" : "#1a1a1a";

      const byReason = new Map<string, typeof plottable>();
      for (const e of plottable) {
        const arr = byReason.get(e.reason_code);
        if (arr) arr.push(e);
        else byReason.set(e.reason_code, [e]);
      }

      const traces = Array.from(byReason.entries()).map(([reason, evs]) => ({
        x: evs.map((e) => e.engagement_percentile ?? NO_SIGNAL),
        y: evs.map((e) => e.interest_percentile ?? NO_SIGNAL),
        text: evs.map((e) => `${e.title}<br>${e.reason}`),
        customdata: evs.map((e) => e.content_item_id),
        mode: "markers" as const,
        type: "scattergl" as const,
        name: `${reason} (${evs.length})`,
        marker: { color: reasonColor.get(reason) || "#9ca3af", size: 9, opacity: 0.85 },
        hovertemplate: "%{text}<br>engagement pctl %{x} · interest pctl %{y}<extra></extra>",
      }));

      const q = { tr: 0, tl: 0, br: 0, bl: 0, bothKnown: 0 };
      for (const e of plottable) {
        if (e.interest_percentile == null || e.engagement_percentile == null) continue;
        q.bothKnown++;
        const x = e.engagement_percentile, y = e.interest_percentile;
        if (x >= 50 && y >= 50) q.tr++;
        else if (x < 50 && y >= 50) q.tl++;
        else if (x >= 50 && y < 50) q.br++;
        else q.bl++;
      }

      const layout: any = {
        height: 460,
        margin: { l: 50, r: 10, t: 10, b: 50 },
        showlegend: true,
        legend: { orientation: "h", y: -0.22, font: { color: font, size: 11 } },
        xaxis: {
          title: { text: "engagement classifier percentile →", font: { color: font, size: 11 } },
          range: [-16, 102], gridcolor: grid, zerolinecolor: grid, color: font,
          tickvals: [-10, 0, 20, 40, 50, 60, 80, 100],
          ticktext: ["no signal", "0", "20", "40", "50", "60", "80", "100"],
        },
        yaxis: {
          title: { text: "interest model percentile →", font: { color: font, size: 11 } },
          range: [-16, 102], gridcolor: grid, zerolinecolor: grid, color: font,
          tickvals: [-10, 0, 20, 40, 50, 60, 80, 100],
          ticktext: ["no signal", "0", "20", "40", "50", "60", "80", "100"],
        },
        paper_bgcolor: paper,
        plot_bgcolor: paper,
        hovermode: "closest",
        shapes: [
          { type: "line", x0: 50, x1: 50, y0: -16, y1: 102, line: { color: grid, dash: "dash", width: 1 } },
          { type: "line", x0: -16, x1: 102, y0: 50, y1: 50, line: { color: grid, dash: "dash", width: 1 } },
          { type: "line", x0: -4, x1: -4, y0: -16, y1: 102, line: { color: grid, dash: "dot", width: 1 } },
          { type: "line", x0: -16, x1: 102, y0: -4, y1: -4, line: { color: grid, dash: "dot", width: 1 } },
        ],
        annotations: [
          { x: 98, y: 98, text: `Fooled (${q.tr})`, showarrow: false, xanchor: "right", font: { color: "#dc2626", size: 11 } },
          { x: 2, y: 98, text: `Interest model fooled (${q.tl})`, showarrow: false, xanchor: "left", font: { color: font, size: 10 } },
          { x: 98, y: 2, text: `Classifier fooled (${q.br})`, showarrow: false, xanchor: "right", font: { color: font, size: 10 } },
          { x: 2, y: 2, text: `Already suspected (${q.bl})`, showarrow: false, xanchor: "left", font: { color: "#16a34a", size: 11 } },
        ],
      };

      await Plotly.react(plotEl, traces as any, layout, { displaylogo: false, responsive: true });
      (plotEl as any).on("plotly_click", (ev: any) => {
        const id = ev?.points?.[0]?.customdata;
        if (!id) return;
        setExpandedId(id);
        rowRefs.current[id]?.scrollIntoView({ behavior: "smooth", block: "center" });
      });
    })().catch((e) => console.error("blind-spot plot failed", e));

    return () => {
      disposed = true;
      import("plotly.js-dist-min").then((m) => { try { (m.default as any).purge(plotEl); } catch { /* noop */ } });
    };
  }, [stats, filteredEvents, reasonColor]);

  // Reason breakdown: horizontal bar, sorted desc, click to filter.
  useEffect(() => {
    if (!stats || !reasonBarRef.current) return;
    let disposed = false;
    const plotEl = reasonBarRef.current;

    (async () => {
      const Plotly = (await import("plotly.js-dist-min")).default;
      if (disposed) return;
      const isDark = document.documentElement.getAttribute("data-theme") === "dark";
      const paper = isDark ? "#0f0f0f" : "#ffffff";
      const font = isDark ? "#e5e5e5" : "#1a1a1a";
      const grid = isDark ? "#333" : "#e5e5e5";

      const sorted = [...stats.reason_counts].sort((a, b) => a.count - b.count);
      const trace: any = {
        x: sorted.map((r) => r.count),
        y: sorted.map((r) => r.reason_code),
        customdata: sorted.map((r) => r.reason_code),
        type: "bar",
        orientation: "h",
        marker: {
          color: sorted.map((r) =>
            reasonFilter && reasonFilter !== r.reason_code ? (isDark ? "#333" : "#e5e5e5") : reasonColor.get(r.reason_code),
          ),
        },
        hovertemplate: "%{y}: %{x}<extra></extra>",
      };
      const layout: any = {
        height: Math.max(180, sorted.length * 34 + 60),
        margin: { l: 10, r: 10, t: 10, b: 30 },
        xaxis: { gridcolor: grid, color: font },
        yaxis: { automargin: true, color: font },
        paper_bgcolor: paper,
        plot_bgcolor: paper,
      };
      await Plotly.react(plotEl, [trace], layout, { displaylogo: false, responsive: true });
      (plotEl as any).on("plotly_click", (ev: any) => {
        const code = ev?.points?.[0]?.customdata;
        if (!code) return;
        setReasonFilter((prev) => (prev === code ? null : code));
      });
    })().catch((e) => console.error("reason bar plot failed", e));

    return () => {
      disposed = true;
      import("plotly.js-dist-min").then((m) => { try { (m.default as any).purge(plotEl); } catch { /* noop */ } });
    };
  }, [stats, reasonColor, reasonFilter]);

  // Weekly burst timeline: stacked bars by reason_code.
  useEffect(() => {
    if (!stats || !weeklyRef.current || stats.weekly.weeks.length === 0) return;
    let disposed = false;
    const plotEl = weeklyRef.current;

    (async () => {
      const Plotly = (await import("plotly.js-dist-min")).default;
      if (disposed) return;
      const isDark = document.documentElement.getAttribute("data-theme") === "dark";
      const paper = isDark ? "#0f0f0f" : "#ffffff";
      const font = isDark ? "#e5e5e5" : "#1a1a1a";
      const grid = isDark ? "#333" : "#e5e5e5";

      const traces = stats.weekly.series.map((s) => ({
        x: stats.weekly.weeks,
        y: s.counts,
        name: s.reason_code,
        type: "bar" as const,
        marker: { color: reasonColor.get(s.reason_code) || "#9ca3af" },
      }));
      const layout: any = {
        height: 320,
        barmode: "stack",
        margin: { l: 40, r: 10, t: 10, b: 40 },
        showlegend: true,
        legend: { orientation: "h", y: -0.25, font: { color: font, size: 11 } },
        xaxis: { gridcolor: grid, color: font },
        yaxis: { gridcolor: grid, color: font, title: { text: "quarantines / week", font: { color: font, size: 11 } } },
        paper_bgcolor: paper,
        plot_bgcolor: paper,
      };
      await Plotly.react(plotEl, traces as any, layout, { displaylogo: false, responsive: true });
    })().catch((e) => console.error("weekly plot failed", e));

    return () => {
      disposed = true;
      import("plotly.js-dist-min").then((m) => { try { (m.default as any).purge(plotEl); } catch { /* noop */ } });
    };
  }, [stats, reasonColor]);

  // Channel leaderboard: quarantine rate per subscription (top offenders).
  useEffect(() => {
    if (!stats || !leaderboardRef.current || stats.subscriptions.length === 0) return;
    let disposed = false;
    const plotEl = leaderboardRef.current;
    const top = stats.subscriptions.slice(0, 15);

    (async () => {
      const Plotly = (await import("plotly.js-dist-min")).default;
      if (disposed) return;
      const isDark = document.documentElement.getAttribute("data-theme") === "dark";
      const paper = isDark ? "#0f0f0f" : "#ffffff";
      const font = isDark ? "#e5e5e5" : "#1a1a1a";
      const grid = isDark ? "#333" : "#e5e5e5";
      const danger = isDark ? "#ef4444" : "#dc2626";

      const sorted = [...top].sort((a, b) => a.rate - b.rate);
      const trace: any = {
        x: sorted.map((s) => s.rate * 100),
        y: sorted.map((s) => subName(s.subscription_id)),
        text: sorted.map((s) => `${s.quarantined} / ${s.total_items}`),
        type: "bar",
        orientation: "h",
        marker: { color: danger },
        hovertemplate: "%{y}: %{x:.0f}%% (%{text})<extra></extra>",
        textposition: "outside",
        texttemplate: "%{text}",
      };
      const layout: any = {
        height: Math.max(200, sorted.length * 32 + 60),
        margin: { l: 10, r: 60, t: 10, b: 30 },
        xaxis: { gridcolor: grid, color: font, title: { text: "% of channel's items quarantined", font: { color: font, size: 11 } } },
        yaxis: { automargin: true, color: font },
        paper_bgcolor: paper,
        plot_bgcolor: paper,
      };
      await Plotly.react(plotEl, [trace], layout, { displaylogo: false, responsive: true });
    })().catch((e) => console.error("leaderboard plot failed", e));

    return () => {
      disposed = true;
      import("plotly.js-dist-min").then((m) => { try { (m.default as any).purge(plotEl); } catch { /* noop */ } });
    };
  }, [stats, subs]);

  if (loading) return <div className="quarantine-page"><p>Loading quarantine history…</p></div>;
  if (error) return <div className="quarantine-page"><ErrorBanner error={error} /></div>;
  if (!stats || stats.total_events === 0) {
    return (
      <div className="quarantine-page">
        <h2>Quarantine</h2>
        <p>Nothing has been quarantined yet.</p>
      </div>
    );
  }

  const vc = stats.verdict_counts;
  const rows = showAllRows ? filteredEvents : filteredEvents.slice(0, 20);

  return (
    <div className="quarantine-page">
      <div className="quarantine-header">
        <h2>Quarantine</h2>
        <p className="quarantine-sub">
          {stats.total_events} items quarantined by the external transcript judge. Click a verdict or reason to filter.
        </p>
      </div>

      <div className="quarantine-verdict-strip">
        {(Object.keys(VERDICT_META) as QuarantineEventDetail["verdict"][]).map((v) => (
          <button
            key={v}
            className={`quarantine-verdict-chip verdict-${v}${verdictFilter === v ? " active" : ""}`}
            onClick={() => setVerdictFilter((prev) => (prev === v ? null : v))}
          >
            <strong>{vc[v]}</strong>
            <span>{VERDICT_META[v].label}</span>
            <span className="quarantine-verdict-hint">{VERDICT_META[v].hint}</span>
          </button>
        ))}
      </div>

      <section className="quarantine-chart-block">
        <h3>Blind-Spot Map</h3>
        <p className="quarantine-chart-caption">
          Did our own models like it before the judge rejected it? Top-right = both models were fooled.
          Items missing a score sit in the "no signal" margin on that axis rather than being dropped.
        </p>
        <div ref={blindSpotRef} />
      </section>

      <div className="quarantine-chart-row">
        <section className="quarantine-chart-block">
          <h3>Reasons</h3>
          <div ref={reasonBarRef} />
        </section>
        <section className="quarantine-chart-block">
          <h3>Channel Leaderboard</h3>
          <p className="quarantine-chart-caption">Share of each channel's items that got quarantined.</p>
          <div ref={leaderboardRef} />
        </section>
      </div>

      <section className="quarantine-chart-block">
        <h3>Over Time</h3>
        <div ref={weeklyRef} />
      </section>

      <section className="quarantine-ledger">
        <h3>Ledger {reasonFilter || verdictFilter ? <span className="quarantine-chart-caption">(filtered)</span> : null}</h3>
        <table className="quarantine-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Title</th>
              <th>Channel</th>
              <th>Reason</th>
              <th>Verdict</th>
              <th>Interest%</th>
              <th>Engagement%</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <Fragment key={e.content_item_id}>
                <tr
                  ref={(el) => { rowRefs.current[e.content_item_id] = el; }}
                  className={`quarantine-row${expandedId === e.content_item_id ? " expanded" : ""}`}
                  onClick={() => setExpandedId((prev) => (prev === e.content_item_id ? null : e.content_item_id))}
                >
                  <td>{formatDate(e.created_at)}</td>
                  <td className="quarantine-title-cell">
                    {e.url ? <a href={e.url} target="_blank" rel="noreferrer" onClick={(ev) => ev.stopPropagation()}>{e.title || e.content_item_id}</a> : (e.title || e.content_item_id)}
                    {e.recurrence_count > 1 && <span className="quarantine-recurrence" title="Same transcript excerpt seen before">↻ {e.recurrence_count}</span>}
                  </td>
                  <td>{subName(e.subscription_id)}</td>
                  <td>
                    <span className="quarantine-reason-chip" style={{ background: reasonColor.get(e.reason_code) }}>
                      {e.reason_code}
                    </span>
                  </td>
                  <td><span className={`quarantine-verdict-badge verdict-${e.verdict}`}>{VERDICT_META[e.verdict].label}</span></td>
                  <td>{pct(e.interest_percentile)}</td>
                  <td>{pct(e.engagement_percentile)}</td>
                </tr>
                {expandedId === e.content_item_id && (
                  <tr className="quarantine-detail-row">
                    <td colSpan={7}>
                      <div className="quarantine-detail">
                        <div>
                          <h4>Why the judge rejected it</h4>
                          <p>{e.reason || "—"}</p>
                        </div>
                        <div>
                          <h4>Why our interest model liked it</h4>
                          <p>{e.interest_reasoning || "no reasoning recorded"}</p>
                        </div>
                      </div>
                      {e.summary && <p className="quarantine-detail-summary">{e.summary}</p>}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
        {!showAllRows && filteredEvents.length > 20 && (
          <button className="quarantine-show-all" onClick={() => setShowAllRows(true)}>
            Show all {filteredEvents.length} rows
          </button>
        )}
      </section>
    </div>
  );
}
