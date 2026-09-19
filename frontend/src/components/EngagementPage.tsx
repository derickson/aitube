import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  getEngagementReport,
  type CohortKey,
  type EngagementChannel,
  type EngagementReport,
} from "../api/client";
import { ErrorBanner } from "./ErrorBanner";

// --- shared metadata (kept in one place so chips / charts / tables agree) ---

type BucketKey = "positive" | "leaning_positive" | "leaning_negative" | "negative" | "excluded";

const BUCKET_ORDER: BucketKey[] = ["positive", "leaning_positive", "leaning_negative", "negative"];
const BUCKET_LABELS: Record<BucketKey, string> = {
  positive: "Positive",
  leaning_positive: "Leaning positive",
  leaning_negative: "Leaning negative (ambiguous)",
  negative: "Negative",
  excluded: "Excluded",
};
const BUCKET_COLORS: Record<BucketKey, { light: string; dark: string }> = {
  positive: { light: "#16a34a", dark: "#22c55e" },
  leaning_positive: { light: "#86d199", dark: "#3fae64" },
  leaning_negative: { light: "#f2a8a8", dark: "#c15b5b" },
  negative: { light: "#dc2626", dark: "#ef4444" },
  excluded: { light: "#9ca3af", dark: "#6b7280" },
};

const STATE_ORDER = [
  "COMPLETED", "FINISHED_MANUAL", "WATCHED_ELSEWHERE", "LIKED",
  "PARTIAL", "BOOKMARKED", "OPENED",
  "PEEKED", "DISMISSED", "SAMPLED_DROPPED", "PASSED_OVER",
  "REJECTED", "REJECTED_AFTER_WATCH",
  "QUARANTINED", "PENDING",
];
const STATE_BUCKET: Record<string, BucketKey> = {
  COMPLETED: "positive", FINISHED_MANUAL: "positive", WATCHED_ELSEWHERE: "positive", LIKED: "positive",
  PARTIAL: "leaning_positive", BOOKMARKED: "leaning_positive", OPENED: "leaning_positive",
  PEEKED: "leaning_negative", DISMISSED: "leaning_negative", SAMPLED_DROPPED: "leaning_negative", PASSED_OVER: "leaning_negative",
  REJECTED: "negative", REJECTED_AFTER_WATCH: "negative",
  QUARANTINED: "excluded", PENDING: "excluded",
};
const STATE_LABELS: Record<string, string> = {
  COMPLETED: "Completed (≥90% watched)",
  FINISHED_MANUAL: "Finished, marked watched (25–89%)",
  WATCHED_ELSEWHERE: "Voted up + marked watched, no playback here",
  PARTIAL: "Partial watch (25–89%)",
  BOOKMARKED: "Voted up, not watched",
  PEEKED: "Opened, didn't play (post-backfill only)",
  DISMISSED: "Marked watched, no real playback (ambiguous)",
  SAMPLED_DROPPED: "Sampled (10–25%), dropped",
  PASSED_OVER: "Never touched, aged out",
  REJECTED: "Voted down",
  REJECTED_AFTER_WATCH: "Voted down after substantial watch",
  QUARANTINED: "Removed by the external transcript judge",
  PENDING: "Too recent to judge",
  LIKED: "Voted up (article)",
  OPENED: "Opened, no vote (article)",
};

const COHORT_META: Record<CohortKey, { label: string; hint: string; light: string; dark: string; hatched: boolean }> = {
  subscription: { label: "Subscriptions", hint: "channels you follow", light: "#2a78d6", dark: "#3987e5", hatched: false },
  adhoc_aitube_sync: { label: "aitube-sync picks", hint: "recommended, submitted automatically", light: "#eb6834", dark: "#d95926", hatched: false },
  adhoc_manual: { label: "My pastes", hint: "self-selected — expect this to skew positive", light: "#1baf7a", dark: "#199e70", hatched: false },
  adhoc_unclassified: { label: "Legacy ad-hoc", hint: "mixed / pre-tagging, unclassified", light: "#9ca3af", dark: "#6b7280", hatched: true },
};
const COHORT_ORDER: CohortKey[] = ["subscription", "adhoc_aitube_sync", "adhoc_manual", "adhoc_unclassified"];

function fmtPct(x: number | null | undefined, digits = 0): string {
  if (x == null || Number.isNaN(x)) return "—";
  return `${(x * 100).toFixed(digits)}%`;
}

function fmtSigned(x: number | null | undefined, digits = 2): string {
  if (x == null || Number.isNaN(x)) return "—";
  const s = x.toFixed(digits);
  return x > 0 ? `+${s}` : s;
}

function isDarkMode(): boolean {
  return document.documentElement.getAttribute("data-theme") === "dark";
}

// --- small presentational pieces ---

function SignificanceBadge({ badge, kind, title }: { badge: "above" | "below" | "typical" | "too_few"; kind: "positive" | "negative"; title?: string }) {
  if (badge === "too_few") return <span className="engagement-badge badge-neutral" title={title}>· Too few</span>;
  if (badge === "typical") return <span className="engagement-badge badge-neutral" title={title}>– Typical</span>;
  // In the positive table, "above" is good; in the negative table, "above" (more rejected) is bad.
  const good = kind === "positive" ? badge === "above" : badge === "below";
  const cls = good ? "badge-good" : "badge-bad";
  const glyph = badge === "above" ? "▲" : "▼";
  const text = badge === "above" ? "Above baseline" : "Below baseline";
  return <span className={`engagement-badge ${cls}`} title={title}>{glyph} {text}</span>;
}

function MiniStateBar({ bucketCounts, nEligible }: { bucketCounts: Record<string, number>; nEligible: number }) {
  const dark = isDarkMode();
  const total = nEligible || 1;
  return (
    <div className="engagement-mini-bar" title={BUCKET_ORDER.map((b) => `${BUCKET_LABELS[b]}: ${bucketCounts[b] ?? 0}`).join(" · ")}>
      {BUCKET_ORDER.map((b) => {
        const n = bucketCounts[b] ?? 0;
        const pct = (n / total) * 100;
        if (pct <= 0) return null;
        const color = dark ? BUCKET_COLORS[b].dark : BUCKET_COLORS[b].light;
        return <span key={b} style={{ width: `${pct}%`, background: color }} />;
      })}
    </div>
  );
}

function RangeBarCell({ channel, metric, baselineRate }: { channel: EngagementChannel; metric: "positive" | "negative"; baselineRate: number }) {
  const dark = isDarkMode();
  const pair = channel[metric];
  const color = dark ? COHORT_META[channel.dominant_cohort].dark : COHORT_META[channel.dominant_cohort].light;
  const clamp = (v: number) => Math.max(0, Math.min(100, v * 100));
  return (
    <div className="engagement-range-cell">
      <div className="engagement-range-bar">
        <div
          className="engagement-range-band"
          style={{ left: `${clamp(pair.wilson_low)}%`, width: `${clamp(pair.wilson_high) - clamp(pair.wilson_low)}%`, background: color, opacity: 0.25 }}
        />
        <div className="engagement-range-baseline" style={{ left: `${clamp(baselineRate)}%` }} />
        <div className="engagement-range-tick" style={{ left: `${clamp(pair.raw)}%`, borderColor: color }} />
        <div className="engagement-range-dot" style={{ left: `${clamp(pair.shrunk)}%`, background: color }} />
      </div>
      <span className="engagement-range-text">
        <strong>{fmtPct(pair.shrunk)}</strong> <span className="muted">(raw {fmtPct(pair.raw)})</span>
      </span>
    </div>
  );
}

// --- charts ---

function OutcomeMixChart({ report, onSegmentClick }: { report: EngagementReport; onSegmentClick: (cohort: CohortKey) => void }) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    let disposed = false;
    const el = ref.current;

    (async () => {
      const Plotly = (await import("plotly.js-dist-min")).default;
      if (disposed) return;
      const dark = isDarkMode();
      const paper = dark ? "#0f0f0f" : "#ffffff";
      const font = dark ? "#e5e5e5" : "#1a1a1a";
      const grid = dark ? "#333" : "#e5e5e5";

      const rows = COHORT_ORDER.map((k) => report.cohorts.find((c) => c.key === k)!);
      const yLabels = rows.map((c) => `${COHORT_META[c.key].label} (n=${c.n_eligible})`);

      const traces = BUCKET_ORDER.map((b) => ({
        x: rows.map((c) => (c.n_eligible ? ((c.bucket_counts[b] ?? 0) / c.n_eligible) * 100 : 0)),
        y: yLabels,
        name: BUCKET_LABELS[b],
        type: "bar" as const,
        orientation: "h" as const,
        marker: {
          color: rows.map(() => (dark ? BUCKET_COLORS[b].dark : BUCKET_COLORS[b].light)),
          pattern: { shape: rows.map((c) => (COHORT_META[c.key].hatched ? "/" : "")) },
          line: { width: 2, color: paper },
        },
        customdata: rows.map((c) => c.key),
        text: rows.map((c) => (c.n_eligible ? ((c.bucket_counts[b] ?? 0) / c.n_eligible) * 100 : 0)),
        texttemplate: "%{text:.0f}%",
        textposition: "inside" as const,
        insidetextanchor: "middle" as const,
        hovertemplate: `${BUCKET_LABELS[b]}: %{x:.0f}%<extra></extra>`,
      }));

      const layout: any = {
        height: 260,
        barmode: "stack",
        margin: { l: 170, r: 10, t: 10, b: 40 },
        showlegend: true,
        legend: { orientation: "h", y: -0.25, font: { color: font, size: 11 } },
        xaxis: { range: [0, 100], ticksuffix: "%", gridcolor: grid, color: font },
        yaxis: { color: font, automargin: true },
        paper_bgcolor: paper,
        plot_bgcolor: paper,
      };
      await Plotly.react(el, traces as any, layout, { displaylogo: false, responsive: true });
      (el as any).on("plotly_click", (ev: any) => {
        const cohort = ev?.points?.[0]?.customdata as CohortKey | undefined;
        if (cohort) onSegmentClick(cohort);
      });
    })().catch((e) => console.error("outcome mix plot failed", e));

    return () => {
      disposed = true;
      import("plotly.js-dist-min").then((m) => { try { (m.default as any).purge(el); } catch { /* noop */ } });
    };
  }, [report, onSegmentClick]);

  return <div ref={ref} />;
}

function WatchDepthChart({ report }: { report: EngagementReport }) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    let disposed = false;
    const el = ref.current;

    (async () => {
      const Plotly = (await import("plotly.js-dist-min")).default;
      if (disposed) return;
      const dark = isDarkMode();
      const paper = dark ? "#0f0f0f" : "#ffffff";
      const font = dark ? "#e5e5e5" : "#1a1a1a";
      const grid = dark ? "#333" : "#e5e5e5";

      const traces = COHORT_ORDER.filter((k) => report.cohorts.find((c) => c.key === k)?.wp_quartiles).map((k) => {
        const c = report.cohorts.find((cc) => cc.key === k)!;
        const q = c.wp_quartiles!;
        const meta = COHORT_META[k];
        return {
          type: "box" as const,
          name: `${meta.label} (n=${c.n_eligible})`,
          x: [meta.label],
          q1: [q.q1], median: [q.median], q3: [q.q3],
          lowerfence: [q.low_fence], upperfence: [q.high_fence],
          boxpoints: false as const,
          marker: { color: dark ? meta.dark : meta.light },
          fillcolor: dark ? meta.dark : meta.light,
          line: { color: dark ? meta.dark : meta.light },
        };
      });

      const shapes: any[] = [];
      const annotations: any[] = [];
      if (report.corpus_median_wp != null) {
        shapes.push({
          type: "line", x0: 0, x1: 1, xref: "paper", y0: report.corpus_median_wp, y1: report.corpus_median_wp,
          line: { color: grid, dash: "dash", width: 1 },
        });
        annotations.push({
          x: 1, xref: "paper", y: report.corpus_median_wp, xanchor: "right", yanchor: "bottom",
          text: `corpus median ${report.corpus_median_wp.toFixed(0)}%`, showarrow: false, font: { color: font, size: 10 },
        });
      }

      const layout: any = {
        height: 260,
        margin: { l: 45, r: 10, t: 10, b: 40 },
        showlegend: false,
        yaxis: { title: { text: "watch % (furthest point)", font: { color: font, size: 11 } }, range: [0, 100], gridcolor: grid, color: font },
        xaxis: { color: font },
        paper_bgcolor: paper,
        plot_bgcolor: paper,
        shapes, annotations,
      };
      await Plotly.react(el, traces as any, layout, { displaylogo: false, responsive: true });
    })().catch((e) => console.error("watch depth plot failed", e));

    return () => {
      disposed = true;
      import("plotly.js-dist-min").then((m) => { try { (m.default as any).purge(el); } catch { /* noop */ } });
    };
  }, [report]);

  return <div ref={ref} />;
}

function StateDetailChart({ report, cohort }: { report: EngagementReport; cohort: CohortKey }) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    let disposed = false;
    const el = ref.current;
    const c = report.cohorts.find((cc) => cc.key === cohort);
    if (!c) return;

    (async () => {
      const Plotly = (await import("plotly.js-dist-min")).default;
      if (disposed) return;
      const dark = isDarkMode();
      const paper = dark ? "#0f0f0f" : "#ffffff";
      const font = dark ? "#e5e5e5" : "#1a1a1a";
      const grid = dark ? "#333" : "#e5e5e5";

      const states = STATE_ORDER.filter((s) => (c.state_counts[s] ?? 0) > 0);
      const trace: any = {
        type: "bar", orientation: "h",
        y: states.map((s) => STATE_LABELS[s] || s),
        x: states.map((s) => c.state_counts[s] ?? 0),
        marker: { color: states.map((s) => (dark ? BUCKET_COLORS[STATE_BUCKET[s]].dark : BUCKET_COLORS[STATE_BUCKET[s]].light)) },
        textposition: "outside",
        text: states.map((s) => c.state_counts[s] ?? 0),
        hovertemplate: "%{y}: %{x}<extra></extra>",
      };
      const layout: any = {
        height: Math.max(220, states.length * 32 + 60),
        margin: { l: 260, r: 30, t: 10, b: 30 },
        xaxis: { gridcolor: grid, color: font },
        yaxis: { automargin: true, color: font },
        paper_bgcolor: paper,
        plot_bgcolor: paper,
      };
      await Plotly.react(el, [trace], layout, { displaylogo: false, responsive: true });
    })().catch((e) => console.error("state detail plot failed", e));

    return () => {
      disposed = true;
      import("plotly.js-dist-min").then((m) => { try { (m.default as any).purge(el); } catch { /* noop */ } });
    };
  }, [report, cohort]);

  return <div ref={ref} />;
}

function DeclineChart({ report, variant }: { report: EngagementReport; variant: "strict" | "broad" }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const series = variant === "strict" ? report.decline_strict : report.decline_broad;

  useEffect(() => {
    if (!ref.current) return;
    let disposed = false;
    const el = ref.current;

    (async () => {
      const Plotly = (await import("plotly.js-dist-min")).default;
      if (disposed) return;
      const dark = isDarkMode();
      const paper = dark ? "#0f0f0f" : "#ffffff";
      const font = dark ? "#e5e5e5" : "#1a1a1a";
      const grid = dark ? "#333" : "#e5e5e5";
      const accent = dark ? "#3987e5" : "#2a78d6";
      const orange = dark ? "#d95926" : "#eb6834";
      const aqua = dark ? "#199e70" : "#1baf7a";
      const months = series.months.map((m) => m.month);
      const lowConfIdx = series.months.map((m, i) => (m.confident ? -1 : i)).filter((i) => i >= 0);

      const barColors = series.months.map((m) => (m.confident ? (dark ? "#4b5563" : "#9ca3af") : (dark ? "#374151" : "#d1d5db")));

      const traces: any[] = [
        // Row 1: denominator bars
        { x: months, y: series.months.map((m) => m.n_upvoted), type: "bar", marker: { color: barColors },
          name: "voted-up items", xaxis: "x", yaxis: "y",
          customdata: series.months.map((m) => m.n_consumed),
          hovertemplate: "%{x}: %{y} voted up · %{customdata} consumed<extra></extra>" },
        // Row 2: hero + secondary
        { x: months, y: series.months.map((m) => m.rate_of_upvotes * 100), type: "scatter", mode: "lines+markers",
          line: { color: accent, width: 2 }, marker: { color: accent, size: 6 },
          name: "% of voted-up items consumed here", xaxis: "x", yaxis: "y2",
          hovertemplate: "%{y:.0f}%<extra>voted-up, consumed here</extra>" },
        { x: months, y: series.months.map((m) => m.rate_of_consumed * 100), type: "scatter", mode: "lines",
          line: { color: orange, width: 2 }, name: "% of eligible items voted up", xaxis: "x", yaxis: "y2",
          hovertemplate: "%{y:.1f}%<extra>eligible items voted up</extra>" },
        // Row 3: companions
        { x: months, y: series.months.map((m) => m.bookmarked_rate * 100), type: "scatter", mode: "lines",
          line: { color: aqua, width: 2 }, name: "BOOKMARKED rate", xaxis: "x", yaxis: "y3",
          hovertemplate: "%{y:.0f}%<extra>bookmarked</extra>" },
        { x: months, y: series.months.map((m) => m.dismissed_rate * 100), type: "scatter", mode: "lines",
          line: { color: dark ? "#6b7280" : "#9ca3af", width: 2, dash: "dot" }, name: "DISMISSED rate (ambiguous)", xaxis: "x", yaxis: "y3",
          hovertemplate: "%{y:.0f}%<extra>dismissed</extra>" },
      ];

      const shapes = lowConfIdx.map((i) => ({
        type: "rect", xref: "x", yref: "paper", x0: i - 0.5, x1: i + 0.5, y0: 0, y1: 1,
        fillcolor: dark ? "#ffffff" : "#000000", opacity: 0.06, line: { width: 0 },
      }));

      const layout: any = {
        height: 460,
        grid: { rows: 3, columns: 1, pattern: "independent" },
        domain: undefined,
        margin: { l: 55, r: 10, t: 10, b: 40 },
        showlegend: true,
        legend: { orientation: "h", y: -0.18, font: { color: font, size: 10 } },
        hovermode: "x unified",
        xaxis: { type: "category", anchor: "y", domain: [0, 1], matches: "x3", showticklabels: false, gridcolor: grid, color: font },
        xaxis2: { type: "category", anchor: "y2", matches: "x3", showticklabels: false, gridcolor: grid, color: font },
        xaxis3: { type: "category", anchor: "y3", gridcolor: grid, color: font },
        yaxis: { domain: [0.78, 1], title: { text: "voted up", font: { color: font, size: 9 } }, gridcolor: grid, color: font },
        yaxis2: { domain: [0.32, 0.72], title: { text: "%", font: { color: font, size: 9 } }, range: [0, 100], ticksuffix: "%", gridcolor: grid, color: font },
        yaxis3: { domain: [0, 0.24], title: { text: "%", font: { color: font, size: 9 } }, range: [0, 100], ticksuffix: "%", gridcolor: grid, color: font },
        paper_bgcolor: paper,
        plot_bgcolor: paper,
        shapes,
      };
      await Plotly.react(el, traces, layout, { displaylogo: false, responsive: true });
    })().catch((e) => console.error("decline plot failed", e));

    return () => {
      disposed = true;
      import("plotly.js-dist-min").then((m) => { try { (m.default as any).purge(el); } catch { /* noop */ } });
    };
  }, [series]);

  return <div ref={ref} />;
}

function CalibrationChart({ report }: { report: EngagementReport }) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current || report.calibration.deciles.length === 0) return;
    let disposed = false;
    const el = ref.current;

    (async () => {
      const Plotly = (await import("plotly.js-dist-min")).default;
      if (disposed) return;
      const dark = isDarkMode();
      const paper = dark ? "#0f0f0f" : "#ffffff";
      const font = dark ? "#e5e5e5" : "#1a1a1a";
      const grid = dark ? "#333" : "#e5e5e5";
      const accent = dark ? "#3987e5" : "#2a78d6";

      const d = report.calibration.deciles;
      const trace: any = {
        x: d.map((x) => x.mean_predicted * 100), y: d.map((x) => x.observed_positive_rate * 100),
        mode: "markers+lines", type: "scatter",
        marker: { size: d.map((x) => Math.max(8, Math.sqrt(x.n) * 2)), color: accent },
        line: { color: accent, width: 1 },
        text: d.map((x) => `n=${x.n}`),
        hovertemplate: "predicted %{x:.0f}% → observed %{y:.0f}%<br>%{text}<extra></extra>",
      };
      const diag: any = {
        x: [0, 100], y: [0, 100], mode: "lines", type: "scatter",
        line: { color: grid, dash: "dot", width: 1 }, showlegend: false, hoverinfo: "skip",
      };
      const layout: any = {
        height: 320,
        margin: { l: 55, r: 10, t: 10, b: 45 },
        showlegend: false,
        xaxis: { title: { text: "predicted engagement.score (decile mean)", font: { color: font, size: 11 } }, range: [0, 100], ticksuffix: "%", gridcolor: grid, color: font },
        yaxis: { title: { text: "observed positive rate", font: { color: font, size: 11 } }, range: [0, 100], ticksuffix: "%", gridcolor: grid, color: font },
        paper_bgcolor: paper,
        plot_bgcolor: paper,
      };
      await Plotly.react(el, [diag, trace], layout, { displaylogo: false, responsive: true });
    })().catch((e) => console.error("calibration plot failed", e));

    return () => {
      disposed = true;
      import("plotly.js-dist-min").then((m) => { try { (m.default as any).purge(el); } catch { /* noop */ } });
    };
  }, [report]);

  if (report.calibration.deciles.length === 0) {
    return <p className="engagement-chart-caption">Not enough scored items yet to build a reliability curve.</p>;
  }
  return <div ref={ref} />;
}

// --- leaderboard ---

function ChannelDetail({ channel }: { channel: EngagementChannel }) {
  return (
    <dl className="engagement-detail-grid">
      <dt>Risk diff vs baseline</dt><dd>{fmtSigned(channel.positive.risk_diff * 100, 1)} pp (positive) · {fmtSigned(channel.negative.risk_diff * 100, 1)} pp (negative)</dd>
      <dt>Lift</dt><dd>{channel.positive.lift.toFixed(2)}× positive · {channel.negative.lift.toFixed(2)}× negative</dd>
      <dt>Fisher p / BH pass</dt><dd>p={channel.positive.p_fisher.toFixed(3)}, {channel.positive.bh_pass ? "passes" : "doesn't pass"} FDR 0.10</dd>
      <dt>Wilson interval</dt><dd>{fmtPct(channel.positive.wilson_low)} – {fmtPct(channel.positive.wilson_high)}</dd>
      <dt>Watch depth</dt>
      <dd>
        median {channel.median_wp != null ? `${channel.median_wp.toFixed(0)}%` : "—"}
        {channel.watch_depth_percentile != null && ` · P${channel.watch_depth_percentile.toFixed(0)} among channels`}
        {channel.cliffs_delta != null && ` · Cliff's δ = ${channel.cliffs_delta.toFixed(2)}`}
      </dd>
      <dt>Volume</dt><dd>{channel.watched_minutes.toFixed(0)} min watched here{channel.runtime_share != null && ` · ${fmtPct(channel.runtime_share)} of available runtime`}</dd>
      <dt>Quarantined</dt><dd>{channel.n_quarantined} / {channel.n_total} ({fmtPct(channel.n_total ? channel.n_quarantined / channel.n_total : 0)}) — the transcript judge's verdict, not yours</dd>
      {channel.surprise != null && (
        <>
          <dt>Prediction surprise</dt>
          <dd>{fmtSigned(channel.surprise * 100, 1)} pp vs the ML engagement classifier's prediction</dd>
        </>
      )}
      <dt>State mix</dt>
      <dd>{STATE_ORDER.filter((s) => (channel.state_counts[s] ?? 0) > 0).map((s) => `${s} ${channel.state_counts[s]}`).join(" · ")}</dd>
      {channel.is_mixed_cohort && (
        <>
          <dt>Cohort split</dt>
          <dd>{Object.entries(channel.cohort_breakdown).map(([k, v]) => `${COHORT_META[k as CohortKey]?.label ?? k} ${v}`).join(" · ")}</dd>
        </>
      )}
    </dl>
  );
}

function LeaderboardTable({
  title, channels, metric, baselineRate, cohortFilter,
}: {
  title: string;
  channels: EngagementChannel[];
  metric: "positive" | "negative";
  baselineRate: number;
  cohortFilter: CohortKey | null;
}) {
  const [showAll, setShowAll] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showTooFew, setShowTooFew] = useState(false);

  const filtered = cohortFilter ? channels.filter((c) => c.dominant_cohort === cohortFilter) : channels;
  const qualifying = filtered.filter((c) => c.qualifies).sort((a, b) => b[metric].shrunk - a[metric].shrunk);
  const tooFew = filtered.filter((c) => !c.qualifies);
  const rows = showAll ? qualifying : qualifying.slice(0, 15);

  return (
    <section className="engagement-chart-block">
      <h3>{title}</h3>
      <p className="engagement-chart-caption">
        Sorted by shrunk {metric === "positive" ? "positive" : "negative"} rate (small-sample channels pulled toward the corpus baseline).
        {" "}● shrunk · │ raw · band = 95% Wilson interval · vertical line = baseline.
      </p>
      <table className="engagement-leaderboard">
        <thead>
          <tr>
            <th>#</th><th>Channel</th><th>vs baseline</th><th>Rate</th><th>State mix</th><th>mean ev</th><th>Count</th><th>Pctl</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c, i) => (
            <Fragment key={c.channel_id}>
              <tr className="engagement-row" onClick={() => setExpanded((p) => (p === c.channel_id ? null : c.channel_id))}>
                <td className="muted">{i + 1}</td>
                <td className="engagement-channel-cell">
                  <span
                    className="engagement-cohort-dot"
                    style={{ background: isDarkMode() ? COHORT_META[c.dominant_cohort].dark : COHORT_META[c.dominant_cohort].light }}
                    title={COHORT_META[c.dominant_cohort].label}
                  />
                  {c.label}
                  {c.dominant_cohort === "adhoc_unclassified" && <span className="engagement-tag">legacy</span>}
                  {c.also_subscribed && <span className="engagement-tag">also subscribed</span>}
                  {c.is_mixed_cohort && <span className="engagement-tag" title="Multiple submission sources">mixed</span>}
                </td>
                <td>
                  <SignificanceBadge
                    badge={c[metric].badge}
                    kind={metric}
                    title={`q=${c[metric].bh_pass ? "<0.10" : "n/a"} · risk diff ${fmtSigned(c[metric].risk_diff * 100, 1)}pp · lift ${c[metric].lift.toFixed(2)}×`}
                  />
                </td>
                <td><RangeBarCell channel={c} metric={metric} baselineRate={baselineRate} /></td>
                <td><MiniStateBar bucketCounts={c.bucket_counts} nEligible={c.n_eligible} /></td>
                <td className="muted">{fmtSigned(c.mean_ev)}</td>
                <td className="muted">{c[metric].k} / {c.n_eligible}</td>
                <td className="muted">P{c[metric].percentile.toFixed(0)}</td>
              </tr>
              {expanded === c.channel_id && (
                <tr className="engagement-detail-row">
                  <td colSpan={8}><ChannelDetail channel={c} /></td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
      {!showAll && qualifying.length > 15 && (
        <button className="engagement-show-all" onClick={() => setShowAll(true)}>Show all {qualifying.length} rows</button>
      )}
      {tooFew.length > 0 && (
        <div className="engagement-too-few">
          <button className="engagement-show-all" onClick={() => setShowTooFew((s) => !s)}>
            {tooFew.length} channels with fewer than 3 eligible items {showTooFew ? "– hide" : "– show"}
          </button>
          {showTooFew && (
            <table className="engagement-leaderboard engagement-leaderboard-dim">
              <tbody>
                {tooFew.map((c) => (
                  <tr key={c.channel_id}>
                    <td className="engagement-channel-cell">
                      <span className="engagement-cohort-dot" style={{ background: isDarkMode() ? COHORT_META[c.dominant_cohort].dark : COHORT_META[c.dominant_cohort].light }} />
                      {c.label}
                    </td>
                    <td><RangeBarCell channel={c} metric={metric} baselineRate={baselineRate} /></td>
                    <td className="muted">{c[metric].k} / {c.n_eligible}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}

// --- page ---

export function EngagementPage() {
  const [report, setReport] = useState<EngagementReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [cohortFilter, setCohortFilter] = useState<CohortKey | null>(null);
  const [declineVariant, setDeclineVariant] = useState<"strict" | "broad">("strict");

  useEffect(() => {
    let cancel = false;
    getEngagementReport()
      .then((r) => !cancel && setReport(r))
      .catch((e) => !cancel && setError(String(e?.message || e)))
      .finally(() => !cancel && setLoading(false));
    return () => { cancel = true; };
  }, []);

  const overallBaselinePositive = useMemo(() => {
    if (!report) return 0;
    const all = report.cohorts.reduce((acc, c) => ({ k: acc.k + c.n_eligible * c.positive_rate, n: acc.n + c.n_eligible }), { k: 0, n: 0 });
    return all.n ? all.k / all.n : 0;
  }, [report]);
  const overallBaselineNegative = useMemo(() => {
    if (!report) return 0;
    const all = report.cohorts.reduce((acc, c) => ({ k: acc.k + c.n_eligible * c.negative_rate, n: acc.n + c.n_eligible }), { k: 0, n: 0 });
    return all.n ? all.k / all.n : 0;
  }, [report]);

  if (loading) return <div className="engagement-page"><p>Loading engagement analytics…</p></div>;
  if (error) return <div className="engagement-page"><ErrorBanner error={error} /></div>;
  if (!report) return null;

  const trend = report.decline_strict.trend;
  const trendGlyph = trend.label === "declining" ? "▼" : trend.label === "rising" ? "▲" : "–";
  const trendCls = trend.label === "declining" ? "badge-bad" : trend.label === "rising" ? "badge-good" : "badge-neutral";

  return (
    <div className="engagement-page">
      <div className="engagement-header">
        <h2>Engagement</h2>
        <p className="engagement-sub">
          {report.video_total} video · {report.podcast_total} podcast · {report.article_total} article items.
          Video gets full statistical treatment below; podcast/article are counted only — too few items yet for reliable per-channel stats.
          Items settle after <strong>{report.settle_days} days</strong> (calibrated from your playback lag).
          Click a cohort to scope the page. Months are discovery months, not action months — see{" "}
          <a href="#engagement-notes">known limits</a>.
        </p>
      </div>

      <div className="engagement-cohort-strip">
        {COHORT_ORDER.map((key) => {
          const c = report.cohorts.find((cc) => cc.key === key)!;
          const meta = COHORT_META[key];
          const active = cohortFilter === key;
          return (
            <button
              key={key}
              className={`engagement-cohort-chip${active ? " active" : ""}${meta.hatched ? " hatched" : ""}`}
              style={{ borderLeftColor: isDarkMode() ? meta.dark : meta.light }}
              onClick={() => setCohortFilter((p) => (p === key ? null : key))}
            >
              <strong>{fmtPct(c.positive_rate)}</strong>
              <span>{meta.label}</span>
              <span className="engagement-chip-ev">mean ev {fmtSigned(c.mean_ev)}</span>
              <span className="engagement-chip-hint">n {c.n_eligible} eligible · {c.n_pending} pending · {c.n_quarantined} quarantined</span>
              <span className="engagement-chip-hint">{meta.hint}</span>
            </button>
          );
        })}
      </div>

      <div className="engagement-chart-row">
        <section className="engagement-chart-block">
          <h3>Outcome mix by cohort</h3>
          <p className="engagement-chart-caption">Share of settled, non-quarantined items. Legacy items sit outside every baseline. Click a segment to see its states below.</p>
          <OutcomeMixChart report={report} onSegmentClick={setCohortFilter} />
        </section>
        <section className="engagement-chart-block">
          <h3>Watch depth by cohort</h3>
          <p className="engagement-chart-caption">Furthest point reached, not time watched. Items with unknown duration or no playback are excluded here (they're in the outcome mix as leaning-negative).</p>
          <WatchDepthChart report={report} />
        </section>
      </div>

      {cohortFilter && (
        <section className="engagement-chart-block">
          <h3>States — {COHORT_META[cohortFilter].label}</h3>
          <StateDetailChart report={report} cohort={cohortFilter} />
        </section>
      )}

      <div className="engagement-chart-row">
        <LeaderboardTable
          title="Most engaging channels"
          channels={report.channels}
          metric="positive"
          baselineRate={overallBaselinePositive}
          cohortFilter={cohortFilter}
        />
        <LeaderboardTable
          title="Most rejected channels"
          channels={report.channels}
          metric="negative"
          baselineRate={overallBaselineNegative}
          cohortFilter={cohortFilter}
        />
      </div>

      <section className="engagement-chart-block">
        <div className="engagement-decline-header">
          <h3>Voted up, but not watched here</h3>
          <div className="engagement-toggle">
            <button className={declineVariant === "strict" ? "active" : ""} onClick={() => setDeclineVariant("strict")}>Strict</button>
            <button className={declineVariant === "broad" ? "active" : ""} onClick={() => setDeclineVariant("broad")}>Broad</button>
          </div>
        </div>
        <p className="engagement-chart-caption">
          {declineVariant === "strict"
            ? "Voted up, marked watched, under 60s or 10% playback here."
            : "Adds items you watched a minute or two here (up to 25%) before finishing elsewhere."}
          {" "}Binned by discovery month, not action month — a catch-up binge on old items lands in the older month.
        </p>
        <div className="engagement-trend-row">
          <span className={`engagement-badge ${trendCls}`}>
            {trendGlyph} {trend.label === "declining" ? "Declining" : trend.label === "rising" ? "Rising" : "No clear trend"}
            {trend.spearman_rho != null && ` (Spearman ρ = ${trend.spearman_rho.toFixed(2)})`}
          </span>
          <span className="muted">
            {trend.ca_z != null && `Cochran-Armitage z = ${trend.ca_z.toFixed(2)}, p = ${trend.ca_p!.toExponential(1)}`}
          </span>
        </div>
        <DeclineChart report={report} variant={declineVariant} />
        <p className="engagement-chart-caption">
          Row 2: if you're just voting up less overall, the bars fall but the blue line stays flat; a real habit change shows as the blue line itself falling.
          Row 3: if the aqua BOOKMARKED rate rises while the pattern falls, the habit moved to bookmarking rather than stopping. Shaded months have fewer than 5/10 up-votes — shown, not trusted.
        </p>
      </section>

      <details className="engagement-details">
        <summary>Prediction calibration</summary>
        <p className="engagement-chart-caption">Does the ML engagement classifier's predicted score match what actually happens? Scored items only.</p>
        <CalibrationChart report={report} />
      </details>

      <details id="engagement-notes" className="engagement-details">
        <summary>How to read this page / known limits</summary>
        <ul className="engagement-notes-list">
          <li><strong>No action timestamps.</strong> Time series are binned by discovery month, not when you actually watched or voted.</li>
          <li><strong>Playback is "furthest point reached,"</strong> not cumulative watched time — rewatching is invisible, scrubbing forward inflates it.</li>
          <li><strong>No impression log.</strong> "Never touched" items are an approximation of rejection — an item low in a busy week looks the same as one you actively skipped.</li>
          <li><strong>aitube-sync silently skips URLs already indexed,</strong> so its acceptance rate only covers novel recommendations.</li>
          <li><strong>Ad-hoc channel identity is a free-text display name.</strong> A channel rename splits a channel; two same-named channels merge.</li>
          <li><strong>DISMISSED vs. an un-voted "watched elsewhere" is unresolvable</strong> from available data — the up-vote is the only discriminator.</li>
          <li><strong>Legacy ad-hoc items predate the submission-source field</strong> and may be a mix of aitube-sync picks and your own pastes — shown separately, never merged into either.</li>
          <li>
            <strong>Quarantine source strings observed:</strong>{" "}
            {report.quarantine_sources_seen.length ? report.quarantine_sources_seen.join(", ") : "none yet"}
            {" "}(matched to aitube-sync by case-insensitive substring, not an exact enum).
          </li>
          <li><strong>Eligible</strong> = settled, not quarantined, not still pending. <strong>Settled</strong> = older than {report.settle_days} days. <strong>Shrunk</strong> = pulled toward the corpus rate in proportion to sample size (Beta-Binomial). <strong>Wilson</strong> = a 95% confidence interval on the raw rate. <strong>Baseline</strong> = the same-stratum rate excluding the channel's own items.</li>
        </ul>
      </details>
    </div>
  );
}
