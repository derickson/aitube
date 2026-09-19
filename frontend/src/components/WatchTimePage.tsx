import { useEffect, useMemo, useRef, useState } from "react";
import { getWatchTimeHourly, type WatchTimeHourlyResponse } from "../api/client";
import { ErrorBanner } from "./ErrorBanner";

// Validated 3-slot categorical palette (blue/orange/aqua), light+dark steps —
// see the dataviz skill's references/palette.md. Fixed order, never cycled.
const TYPE_META: Record<string, { label: string; light: string; dark: string }> = {
  youtube_channel: { label: "YouTube", light: "#2a78d6", dark: "#3987e5" },
  podcast: { label: "Podcast", light: "#eb6834", dark: "#d95926" },
  rss: { label: "Article", light: "#1baf7a", dark: "#199e70" },
};

const TODAY_COLOR = { light: "#2a78d6", dark: "#3987e5" };
const YESTERDAY_COLOR = { light: "#9ca3af", dark: "#6b7280" };

const REFRESH_INTERVAL_MS = 60_000;

function hourTickText(hour: number): string {
  if (hour === 0) return "12a";
  if (hour === 12) return "12p";
  return hour < 12 ? `${hour}a` : `${hour - 12}p`;
}

function formatMinutes(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  if (h === 0) return `${m}m`;
  return `${h}h ${m}m`;
}

export function WatchTimePage() {
  const [data, setData] = useState<WatchTimeHourlyResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const compareRef = useRef<HTMLDivElement | null>(null);
  const breakdownRef = useRef<HTMLDivElement | null>(null);

  const tz = useMemo(() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone;
    } catch {
      return "UTC";
    }
  }, []);

  useEffect(() => {
    let cancel = false;
    const load = (isFirst: boolean) => {
      if (isFirst) setLoading(true);
      getWatchTimeHourly(tz)
        .then((resp) => !cancel && setData(resp))
        .catch((e) => !cancel && setError(String(e?.message || e)))
        .finally(() => !cancel && isFirst && setLoading(false));
    };
    load(true);
    const interval = setInterval(() => load(false), REFRESH_INTERVAL_MS);
    return () => {
      cancel = true;
      clearInterval(interval);
    };
  }, [tz]);

  // Today vs Yesterday: two-line comparison by hour-of-day (a highlighted
  // subject against a neutral baseline — not a full categorical palette).
  useEffect(() => {
    if (!data || !compareRef.current) return;
    let disposed = false;
    const plotEl = compareRef.current;

    (async () => {
      const Plotly = (await import("plotly.js-dist-min")).default;
      if (disposed) return;
      const isDark = document.documentElement.getAttribute("data-theme") === "dark";
      const paper = isDark ? "#0f0f0f" : "#ffffff";
      const font = isDark ? "#e5e5e5" : "#1a1a1a";
      const grid = isDark ? "#333" : "#e5e5e5";
      const today = isDark ? TODAY_COLOR.dark : TODAY_COLOR.light;
      const yesterday = isDark ? YESTERDAY_COLOR.dark : YESTERDAY_COLOR.light;

      const hours = Array.from({ length: 24 }, (_, h) => h);
      const byHour = (buckets: WatchTimeHourlyResponse["today"]) => {
        const m = new Map(buckets.map((b) => [b.hour_of_day, b.minutes]));
        return hours.map((h) => m.get(h) ?? 0);
      };

      const traces: any[] = [
        {
          x: hours,
          y: byHour(data.yesterday),
          name: "Yesterday",
          type: "scatter",
          mode: "lines",
          line: { color: yesterday, width: 2, dash: "dot" },
          hovertemplate: "%{y:.0f} min<extra>Yesterday</extra>",
        },
        {
          x: hours,
          y: byHour(data.today),
          name: "Today",
          type: "scatter",
          mode: "lines+markers",
          line: { color: today, width: 2 },
          marker: { color: today, size: 6 },
          hovertemplate: "%{y:.0f} min<extra>Today</extra>",
        },
      ];

      const layout: any = {
        height: 340,
        margin: { l: 45, r: 10, t: 10, b: 40 },
        showlegend: true,
        legend: { orientation: "h", y: -0.2, font: { color: font, size: 11 } },
        xaxis: {
          tickvals: [0, 3, 6, 9, 12, 15, 18, 21],
          ticktext: [0, 3, 6, 9, 12, 15, 18, 21].map(hourTickText),
          gridcolor: grid,
          color: font,
          range: [-0.5, 23.5],
        },
        yaxis: {
          title: { text: "minutes watched", font: { color: font, size: 11 } },
          gridcolor: grid,
          zerolinecolor: grid,
          color: font,
          rangemode: "tozero",
        },
        paper_bgcolor: paper,
        plot_bgcolor: paper,
        hovermode: "x unified",
      };
      await Plotly.react(plotEl, traces, layout, { displaylogo: false, responsive: true });
    })().catch((e) => console.error("watch-time compare plot failed", e));

    return () => {
      disposed = true;
      import("plotly.js-dist-min").then((m) => { try { (m.default as any).purge(plotEl); } catch { /* noop */ } });
    };
  }, [data]);

  // Today by content type: composition, stacked bar.
  useEffect(() => {
    if (!data || !breakdownRef.current) return;
    let disposed = false;
    const plotEl = breakdownRef.current;

    (async () => {
      const Plotly = (await import("plotly.js-dist-min")).default;
      if (disposed) return;
      const isDark = document.documentElement.getAttribute("data-theme") === "dark";
      const paper = isDark ? "#0f0f0f" : "#ffffff";
      const font = isDark ? "#e5e5e5" : "#1a1a1a";
      const grid = isDark ? "#333" : "#e5e5e5";

      const hours = Array.from({ length: 24 }, (_, h) => h);
      const byHour = new Map(data.today.map((b) => [b.hour_of_day, b]));
      const typesPresent = Object.keys(TYPE_META).filter((t) =>
        data.today.some((b) => (b.minutes_by_type[t] ?? 0) > 0),
      );

      const traces: any[] = typesPresent.map((t) => ({
        x: hours,
        y: hours.map((h) => byHour.get(h)?.minutes_by_type[t] ?? 0),
        name: TYPE_META[t].label,
        type: "bar",
        marker: { color: isDark ? TYPE_META[t].dark : TYPE_META[t].light },
        hovertemplate: `%{y:.0f} min<extra>${TYPE_META[t].label}</extra>`,
      }));

      const layout: any = {
        height: 300,
        barmode: "stack",
        margin: { l: 45, r: 10, t: 10, b: 40 },
        showlegend: traces.length > 1,
        legend: { orientation: "h", y: -0.25, font: { color: font, size: 11 } },
        bargap: 0.15,
        xaxis: {
          tickvals: [0, 3, 6, 9, 12, 15, 18, 21],
          ticktext: [0, 3, 6, 9, 12, 15, 18, 21].map(hourTickText),
          gridcolor: grid,
          color: font,
          range: [-0.5, 23.5],
        },
        yaxis: {
          title: { text: "minutes watched", font: { color: font, size: 11 } },
          gridcolor: grid,
          color: font,
          rangemode: "tozero",
        },
        paper_bgcolor: paper,
        plot_bgcolor: paper,
      };
      await Plotly.react(plotEl, traces, layout, { displaylogo: false, responsive: true });
    })().catch((e) => console.error("watch-time breakdown plot failed", e));

    return () => {
      disposed = true;
      import("plotly.js-dist-min").then((m) => { try { (m.default as any).purge(plotEl); } catch { /* noop */ } });
    };
  }, [data]);

  if (loading) return <div className="watch-time-page"><p>Loading watch time…</p></div>;
  if (error) return <div className="watch-time-page"><ErrorBanner error={error} /></div>;
  if (!data) return null;

  return (
    <div className="watch-time-page">
      <div className="watch-time-header">
        <h2>Watch Time</h2>
        <p className="watch-time-sub">
          Minutes of content consumed per hour, tracked from playback position updates. Timezone: {data.timezone}.
        </p>
      </div>

      <div className="watch-time-stat-strip">
        <div className="watch-time-stat-tile">
          <strong>{formatMinutes(data.today_total_minutes)}</strong>
          <span>Today so far</span>
        </div>
        <div className="watch-time-stat-tile">
          <strong>{formatMinutes(data.yesterday_total_minutes)}</strong>
          <span>Yesterday</span>
        </div>
      </div>

      <section className="watch-time-chart-block">
        <h3>Today vs Yesterday</h3>
        <p className="watch-time-chart-caption">Minutes watched by hour of day, local time.</p>
        <div ref={compareRef} />
      </section>

      <section className="watch-time-chart-block">
        <h3>Today by Source</h3>
        <p className="watch-time-chart-caption">Composition of today's watch time by content type.</p>
        <div ref={breakdownRef} />
      </section>
    </div>
  );
}
