import { useEffect, useMemo, useRef, useState } from "react";
import type { TopicFlowOverTime } from "../api/client";

const NOISE_COLOR = "#9ca3af";

interface Props {
  flow: TopicFlowOverTime;
  colorById: Map<string, string>;
  selectedId: string | null;
  onSelect: (clusterId: string) => void;
  /** Total SVG height. If larger than the natural lane stack, lanes are centered
   * vertically (lets this chart match the cluster map's height when side by side). */
  height?: number;
}

function hexToRgba(hex: string, alpha: number): string {
  const m = hex.replace("#", "");
  const r = parseInt(m.slice(0, 2), 16);
  const g = parseInt(m.slice(2, 4), 16);
  const b = parseInt(m.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function fmtDay(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Catmull-Rom → cubic-bézier tail (the "C …" commands from points[0] to the end).
 * The pen is assumed to already sit on points[0], so this is appended after an M
 * or after a previous tail that ended on the same point. */
function smoothTail(points: [number, number][]): string {
  let d = "";
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i - 1] ?? points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] ?? p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6;
    const c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6;
    const c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C ${c1x.toFixed(2)} ${c1y.toFixed(2)} ${c2x.toFixed(2)} ${c2y.toFixed(2)} ${p2[0].toFixed(2)} ${p2[1].toFixed(2)}`;
  }
  return d;
}

const PAD_L = 14;
const PAD_R = 14;
const AXIS_H = 26;     // top band reserved for the date axis
const EXTRA_V = 18;    // vertical cushion so thick bands can overflow lane edges
const LANE_PITCH = 24; // vertical distance between lane centers (tighter = less gap)
const MAX_HALF = 30;   // peak-day half-thickness; > LANE_PITCH so big days cross into neighbors
const MIN_HALF = 1.2;  // min half-thickness for a non-zero day (kept visible)
const MIN_DAY_W = 16;  // px per day floor → horizontal scroll on very narrow screens

/** Natural height of the lane stack for `n` clusters (no extra vertical fill). */
export function storyChainsNaturalHeight(n: number): number {
  return AXIS_H + EXTRA_V * 2 + n * LANE_PITCH;
}

/**
 * Temporal "story chains": one horizontal lane per topic cluster. Within a lane,
 * a ribbon flows left→right over the day axis, its thickness at each day set by
 * that day's item count, tapering to a point at the cluster's first/last active
 * day. Colors match the UMAP scatter. Modeled on the Elastic Search Labs blog.
 */
export function TopicStoryChains({ flow, colorById, selectedId, onSelect, height }: Props) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [measured, setMeasured] = useState(900);
  const [hover, setHover] = useState<{ x: number; y: number; text: string } | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w) setMeasured(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const N = flow.days.length;
  const width = Math.max(measured, N * MIN_DAY_W + PAD_L + PAD_R);
  const innerW = Math.max(50, width - PAD_L - PAD_R);
  const dayW = N > 1 ? innerW / (N - 1) : innerW;
  const xOf = (i: number) => (N > 1 ? PAD_L + i * dayW : PAD_L + innerW / 2);
  const naturalH = storyChainsNaturalHeight(flow.series.length);
  const svgH = Math.max(height ?? naturalH, naturalH);
  const vTop = AXIS_H + EXTRA_V + Math.max(0, (svgH - naturalH) / 2);
  const laneCenter = (idx: number) => vTop + LANE_PITCH / 2 + idx * LANE_PITCH;

  const maxCount = useMemo(() => {
    let m = 0;
    for (const s of flow.series) for (const c of s.counts) if (c > m) m = c;
    return m || 1;
  }, [flow]);

  const halfOf = (c: number) => (c <= 0 ? 0 : Math.max(MIN_HALF, (c / maxCount) * MAX_HALF));

  const ticks = useMemo(() => {
    const target = Math.max(2, Math.floor(innerW / 70));
    const step = Math.max(1, Math.ceil(N / target));
    const out: { i: number; label: string }[] = [];
    for (let i = 0; i < N; i += step) out.push({ i, label: fmtDay(flow.days[i]) });
    if (out.length && out[out.length - 1].i !== N - 1) {
      out.push({ i: N - 1, label: fmtDay(flow.days[N - 1]) });
    }
    return out;
  }, [flow, innerW, N]);

  // One ribbon per contiguous run of non-zero days; zero days draw nothing (the
  // chain breaks rather than flat-lining), and each run tapers in/out to a point.
  function lanePath(counts: number[], centerY: number): string | null {
    const runs: [number, number][] = [];
    let i = 0;
    while (i < counts.length) {
      if (counts[i] > 0) {
        let j = i;
        while (j + 1 < counts.length && counts[j + 1] > 0) j++;
        runs.push([i, j]);
        i = j + 1;
      } else {
        i++;
      }
    }
    if (runs.length === 0) return null;

    let d = "";
    for (const [a, b] of runs) {
      const startX = Math.max(PAD_L, xOf(a) - dayW * 0.35);
      const endX = Math.min(PAD_L + innerW, xOf(b) + dayW * 0.35);
      const top: [number, number][] = [[startX, centerY]];
      const bot: [number, number][] = [];
      for (let k = a; k <= b; k++) {
        const h = halfOf(counts[k]);
        top.push([xOf(k), centerY - h]);
        bot.push([xOf(k), centerY + h]);
      }
      top.push([endX, centerY]);
      bot.push([endX, centerY]);
      const botRev = bot.reverse();
      d += `M ${top[0][0].toFixed(2)} ${top[0][1].toFixed(2)}` + smoothTail(top) + smoothTail(botRev) + " Z ";
    }
    return d;
  }

  const countsByCid = useMemo(() => {
    const map = new Map<string, number[]>();
    for (const s of flow.series) map.set(s.cluster_id, s.counts);
    return map;
  }, [flow]);

  function handleMove(e: React.MouseEvent, cid: string, label: string) {
    const svg = svgRef.current;
    const counts = countsByCid.get(cid);
    if (!svg || !counts) return;
    const rect = svg.getBoundingClientRect();
    const x = e.clientX - rect.left;
    let i = Math.round((x - PAD_L) / dayW);
    i = Math.max(0, Math.min(N - 1, i));
    const count = counts[i] ?? 0;
    setHover({
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
      text: `${label} — ${fmtDay(flow.days[i])}: ${count} item${count === 1 ? "" : "s"}`,
    });
  }

  return (
    <div className="topic-flow-chains-wrap" ref={wrapRef}>
      <svg
        ref={svgRef}
        width={width}
        height={svgH}
        className="topic-flow-chains-svg"
        onMouseLeave={() => setHover(null)}
      >
        {/* date axis + faint gridlines */}
        {ticks.map((t) => (
          <g key={`tick-${t.i}`}>
            <line
              x1={xOf(t.i)} y1={AXIS_H - 6} x2={xOf(t.i)} y2={svgH}
              stroke="var(--border)" strokeWidth={1} opacity={0.4}
            />
            <text
              x={xOf(t.i)} y={AXIS_H - 12} textAnchor="middle"
              fontSize={10} fill="var(--text-secondary)"
            >
              {t.label}
            </text>
          </g>
        ))}

        {flow.series.map((s, idx) => {
          const color = colorById.get(s.cluster_id) || NOISE_COLOR;
          const centerY = laneCenter(idx);
          const path = lanePath(s.counts, centerY);
          const total = s.counts.reduce((a, b) => a + b, 0);
          const dim = selectedId && selectedId !== s.cluster_id ? 0.3 : 1;
          const firstActive = s.counts.findIndex((c) => c > 0);
          return (
            <g
              key={s.cluster_id}
              style={{ cursor: "pointer" }}
              opacity={dim}
              onClick={() => onSelect(s.cluster_id)}
            >
              <rect
                x={PAD_L} y={centerY - LANE_PITCH / 2} width={innerW} height={LANE_PITCH}
                fill="transparent"
                onMouseMove={(e) => handleMove(e, s.cluster_id, s.label)}
              />
              {path && (
                <path
                  d={path}
                  fill={hexToRgba(color, selectedId === s.cluster_id ? 0.95 : 0.78)}
                  stroke={color}
                  strokeWidth={selectedId === s.cluster_id ? 1.5 : 1}
                  pointerEvents="none"
                />
              )}
              <text
                x={xOf(Math.max(0, firstActive)) + 6}
                y={centerY}
                dominantBaseline="middle"
                fontSize={11}
                fontWeight={600}
                pointerEvents="none"
                style={{ paintOrder: "stroke", stroke: "rgba(0,0,0,0.55)", strokeWidth: 3 }}
                fill="#ffffff"
              >
                {s.label} ({total})
              </text>
            </g>
          );
        })}
      </svg>
      {hover && (
        <div className="topic-flow-chains-tip" style={{ left: hover.x, top: hover.y }}>
          {hover.text}
        </div>
      )}
    </div>
  );
}
