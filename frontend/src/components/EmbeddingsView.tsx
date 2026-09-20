import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

import {
  getEmbeddingsLatest,
  type EmbeddingPoint,
  type EmbeddingsResponse,
} from "../api/client";
import { ContentView } from "./ContentView";
import { ErrorBanner } from "./ErrorBanner";

const PALETTE = [
  "#4f46e5", "#e11d48", "#16a34a", "#d97706", "#0ea5e9",
  "#a21caf", "#0891b2", "#ca8a04", "#dc2626", "#7c3aed",
  "#059669", "#db2777", "#2563eb", "#65a30d", "#ea580c",
  "#0d9488", "#9333ea", "#b91c1c", "#1d4ed8", "#15803d",
];
const UNKNOWN_COLOR = "#9ca3af";
const FALLBACK_LABELS = new Set(["Uncategorized", "Noise", "Unknown", "Unscored", "Unseen"]);

const IDLE_RESUME_MS = 5000;
const BOX_HALF_EXTENT = 8;

// Dot radius scales inversely with how many are on screen, so a filtered-down
// view (e.g. "Unseen" with a handful of items) doesn't look sparse.
const REFERENCE_COUNT = 500;
const BASE_RADIUS = 0.12;
const MIN_RADIUS = 0.08;
const MAX_RADIUS = 0.9;

function radiusForCount(n: number): number {
  const r = BASE_RADIUS * Math.sqrt(REFERENCE_COUNT / Math.max(n, 1));
  return Math.min(MAX_RADIUS, Math.max(MIN_RADIUS, r));
}

type ColorDim = "category" | "cluster" | "channel" | "prediction" | "watch";

const COLOR_DIM_OPTIONS: { value: ColorDim; label: string }[] = [
  { value: "category", label: "Category" },
  { value: "cluster", label: "Topic Cluster" },
  { value: "channel", label: "Channel" },
  { value: "prediction", label: "Prediction" },
  { value: "watch", label: "Watch Status" },
];

type FilterMode = "all" | "unseen";

function watchStatus(p: EmbeddingPoint): string {
  if (p.user_interest === "up") return "Interesting";
  if (p.user_interest === "down") return "Not Interesting";
  if (p.consumed) return "Watched";
  if (p.viewed) return "Seen, not watched";
  return "Unseen";
}

function predictionLabel(p: EmbeddingPoint): string {
  if (p.prediction === "engaged") return "Engaged";
  if (p.prediction === "not_engaged") return "Not Engaged";
  return "Unscored";
}

function bucketKey(p: EmbeddingPoint, dim: ColorDim): string {
  switch (dim) {
    case "category": return p.category || "Uncategorized";
    case "cluster": return p.cluster_label || "Noise";
    case "channel": return p.channel_name || "Unknown";
    case "prediction": return predictionLabel(p);
    case "watch": return watchStatus(p);
  }
}

interface LegendEntry {
  key: string;
  color: string;
  count: number;
}

function buildColorMap(points: EmbeddingPoint[], dim: ColorDim): { colorOf: Map<string, string>; legend: LegendEntry[] } {
  const counts = new Map<string, number>();
  for (const p of points) {
    const k = bucketKey(p, dim);
    counts.set(k, (counts.get(k) || 0) + 1);
  }
  const sorted = [...counts.entries()].sort((a, b) => {
    const aFallback = FALLBACK_LABELS.has(a[0]);
    const bFallback = FALLBACK_LABELS.has(b[0]);
    if (aFallback !== bFallback) return aFallback ? 1 : -1;
    return b[1] - a[1];
  });
  const colorOf = new Map<string, string>();
  const legend: LegendEntry[] = [];
  let i = 0;
  for (const [key, count] of sorted) {
    const color = FALLBACK_LABELS.has(key) ? UNKNOWN_COLOR : PALETTE[i++ % PALETTE.length];
    colorOf.set(key, color);
    legend.push({ key, color, count });
  }
  return { colorOf, legend };
}

/** Rescale raw embedding coords to fit within a fixed box centered at the origin. */
function normalizeCoords(points: EmbeddingPoint[], embeddingKey: string): Float32Array {
  const n = points.length;
  const positions = new Float32Array(n * 3);
  if (n === 0) return positions;

  const mins = [Infinity, Infinity, Infinity];
  const maxs = [-Infinity, -Infinity, -Infinity];
  for (const p of points) {
    const c = p.coords[embeddingKey];
    if (!c) continue;
    for (let axis = 0; axis < 3; axis++) {
      if (c[axis] < mins[axis]) mins[axis] = c[axis];
      if (c[axis] > maxs[axis]) maxs[axis] = c[axis];
    }
  }
  const span = [0, 1, 2].map((axis) => Math.max(maxs[axis] - mins[axis], 1e-6));
  const maxSpan = Math.max(...span);
  const scale = (BOX_HALF_EXTENT * 2) / maxSpan;
  const center = [0, 1, 2].map((axis) => (mins[axis] + maxs[axis]) / 2);

  points.forEach((p, i) => {
    const c = p.coords[embeddingKey] || [0, 0, 0];
    positions[i * 3] = (c[0] - center[0]) * scale;
    positions[i * 3 + 1] = (c[1] - center[1]) * scale;
    positions[i * 3 + 2] = (c[2] - center[2]) * scale;
  });
  return positions;
}

function absoluteTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function EmbeddingsView() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<EmbeddingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // Initial values are read once from the URL so a refresh or a pasted link
  // restores the exact same view (position/color choice, filter, flyout).
  const [embeddingKey, setEmbeddingKey] = useState<string>(() => searchParams.get("embed") ?? "clustering");
  const [colorDim, setColorDim] = useState<ColorDim>(
    () => (searchParams.get("color") as ColorDim | null) ?? "category",
  );
  const [filterMode, setFilterMode] = useState<FilterMode>(
    () => (searchParams.get("watch") === "unseen" ? "unseen" : "all"),
  );
  const [selectedItemId, setSelectedItemId] = useState<string | null>(() => searchParams.get("item"));
  const [hover, setHover] = useState<{ x: number; y: number; point: EmbeddingPoint } | null>(null);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const meshRef = useRef<THREE.InstancedMesh | null>(null);
  const raycasterRef = useRef(new THREE.Raycaster());
  const pointerRef = useRef(new THREE.Vector2());
  const pointsMetaRef = useRef<EmbeddingPoint[]>([]);
  const resumeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dragStartRef = useRef<{ x: number; y: number } | null>(null);
  const onPointClickRef = useRef<(item: EmbeddingPoint) => void>(() => {});
  const onPointHoverRef = useRef<(hit: { x: number; y: number; point: EmbeddingPoint } | null) => void>(() => {});

  useEffect(() => {
    let cancel = false;
    setLoading(true);
    getEmbeddingsLatest()
      .then((resp) => {
        if (cancel) return;
        setData(resp);
        if (resp.embeddings.length && !resp.embeddings.some((e) => e.key === embeddingKey)) {
          setEmbeddingKey(resp.embeddings[0].key);
        }
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep the address bar in sync with the position/color/filter controls and
  // the open flyout, so a refresh or a pasted link restores this exact view.
  useEffect(() => {
    const params = new URLSearchParams();
    if (embeddingKey && embeddingKey !== "clustering") params.set("embed", embeddingKey);
    if (colorDim !== "category") params.set("color", colorDim);
    if (filterMode === "unseen") params.set("watch", "unseen");
    if (selectedItemId) params.set("item", selectedItemId);
    setSearchParams(params, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [embeddingKey, colorDim, filterMode, selectedItemId]);

  const visiblePoints = useMemo(
    () => (data ? (filterMode === "unseen" ? data.points.filter((p) => watchStatus(p) === "Unseen") : data.points) : []),
    [data, filterMode],
  );

  const { colorOf, legend } = useMemo(
    () => buildColorMap(visiblePoints, colorDim),
    [visiblePoints, colorDim],
  );

  onPointClickRef.current = (item) => setSelectedItemId(item.item_id);
  onPointHoverRef.current = setHover;

  // Scene setup — runs once per data load.
  useEffect(() => {
    if (!data || !containerRef.current) return;
    const container = containerRef.current;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 200);
    camera.position.set(14, 11, 14);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);
    rendererRef.current = renderer;
    cameraRef.current = camera;

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.6;
    controlsRef.current = controls;

    const pauseAutoRotate = () => {
      controls.autoRotate = false;
      if (resumeTimerRef.current) clearTimeout(resumeTimerRef.current);
    };
    const scheduleResume = () => {
      if (resumeTimerRef.current) clearTimeout(resumeTimerRef.current);
      resumeTimerRef.current = setTimeout(() => { controls.autoRotate = true; }, IDLE_RESUME_MS);
    };
    controls.addEventListener("start", pauseAutoRotate);
    controls.addEventListener("end", scheduleResume);

    // Ground plane
    const groundGeo = new THREE.PlaneGeometry(BOX_HALF_EXTENT * 4, BOX_HALF_EXTENT * 4);
    const groundMat = new THREE.MeshBasicMaterial({
      color: 0x808080, transparent: true, opacity: 0.06, side: THREE.DoubleSide,
    });
    const ground = new THREE.Mesh(groundGeo, groundMat);
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -BOX_HALF_EXTENT - 1;
    scene.add(ground);

    const grid = new THREE.GridHelper(BOX_HALF_EXTENT * 4, 20, 0x808080, 0x808080);
    (grid.material as THREE.Material).transparent = true;
    (grid.material as THREE.Material).opacity = 0.12;
    grid.position.y = -BOX_HALF_EXTENT - 1;
    scene.add(grid);

    // Axis arrows
    const axisLen = BOX_HALF_EXTENT + 2;
    const origin = new THREE.Vector3(0, 0, 0);
    scene.add(new THREE.ArrowHelper(new THREE.Vector3(1, 0, 0), origin, axisLen, 0xef4444, 0.6, 0.35));
    scene.add(new THREE.ArrowHelper(new THREE.Vector3(0, 1, 0), origin, axisLen, 0x22c55e, 0.6, 0.35));
    scene.add(new THREE.ArrowHelper(new THREE.Vector3(0, 0, 1), origin, axisLen, 0x3b82f6, 0.6, 0.35));

    // Lights — needed for the low-poly spheres' flat-shaded facets to read as facets.
    scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 1.1));
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.9);
    dirLight.position.set(6, 10, 8);
    scene.add(dirLight);

    // Dots: an InstancedMesh of a low-poly (20-face) icosahedron, one instance
    // per content item. `mesh.count` (not the geometry) controls how many are
    // drawn, so filtering just shrinks it instead of reallocating buffers.
    const n = Math.max(data.points.length, 1);
    const sphereGeo = new THREE.IcosahedronGeometry(1, 0);
    const material = new THREE.MeshStandardMaterial({ flatShading: true, roughness: 0.55, metalness: 0.08 });
    const mesh = new THREE.InstancedMesh(sphereGeo, material, n);
    mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(n * 3), 3);
    mesh.count = 0;
    scene.add(mesh);
    meshRef.current = mesh;

    const raycaster = raycasterRef.current;

    const setSize = () => {
      const w = container.clientWidth || 1;
      const h = container.clientHeight || 1;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    setSize();
    const resizeObserver = new ResizeObserver(setSize);
    resizeObserver.observe(container);

    const raycastAt = (clientX: number, clientY: number): { point: EmbeddingPoint; screenX: number; screenY: number } | null => {
      const rect = container.getBoundingClientRect();
      pointerRef.current.x = ((clientX - rect.left) / rect.width) * 2 - 1;
      pointerRef.current.y = -((clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointerRef.current, camera);
      const hits = raycaster.intersectObject(mesh);
      if (!hits.length) return null;
      const idx = hits[0].instanceId;
      if (idx === undefined) return null;
      const point = pointsMetaRef.current[idx];
      if (!point) return null;
      return { point, screenX: clientX - rect.left, screenY: clientY - rect.top };
    };

    const handlePointerMove = (ev: PointerEvent) => {
      const hit = raycastAt(ev.clientX, ev.clientY);
      container.style.cursor = hit ? "pointer" : "grab";
      onPointHoverRef.current(hit ? { x: hit.screenX, y: hit.screenY, point: hit.point } : null);
    };
    const handlePointerDown = (ev: PointerEvent) => {
      dragStartRef.current = { x: ev.clientX, y: ev.clientY };
    };
    const handlePointerUp = (ev: PointerEvent) => {
      const start = dragStartRef.current;
      dragStartRef.current = null;
      if (!start) return;
      const dist = Math.hypot(ev.clientX - start.x, ev.clientY - start.y);
      if (dist > 5) return; // was a drag/orbit, not a click
      const hit = raycastAt(ev.clientX, ev.clientY);
      if (hit) onPointClickRef.current(hit.point);
    };
    const handlePointerLeave = () => onPointHoverRef.current(null);

    renderer.domElement.addEventListener("pointermove", handlePointerMove);
    renderer.domElement.addEventListener("pointerdown", handlePointerDown);
    renderer.domElement.addEventListener("pointerup", handlePointerUp);
    renderer.domElement.addEventListener("pointerleave", handlePointerLeave);

    let raf = 0;
    const animate = () => {
      controls.update();
      renderer.render(scene, camera);
      raf = requestAnimationFrame(animate);
    };
    animate();

    return () => {
      cancelAnimationFrame(raf);
      resizeObserver.disconnect();
      if (resumeTimerRef.current) clearTimeout(resumeTimerRef.current);
      controls.removeEventListener("start", pauseAutoRotate);
      controls.removeEventListener("end", scheduleResume);
      controls.dispose();
      renderer.domElement.removeEventListener("pointermove", handlePointerMove);
      renderer.domElement.removeEventListener("pointerdown", handlePointerDown);
      renderer.domElement.removeEventListener("pointerup", handlePointerUp);
      renderer.domElement.removeEventListener("pointerleave", handlePointerLeave);
      sphereGeo.dispose();
      material.dispose();
      groundGeo.dispose();
      groundMat.dispose();
      renderer.dispose();
      if (renderer.domElement.parentElement === container) {
        container.removeChild(renderer.domElement);
      }
      rendererRef.current = null;
      cameraRef.current = null;
      controlsRef.current = null;
      meshRef.current = null;
    };
  }, [data]);

  // Update dot positions/size when the visible set or active embedding changes.
  // The InstancedMesh is allocated at full (unfiltered) capacity once in scene
  // setup; filtering just shrinks `mesh.count` instead of reallocating.
  useEffect(() => {
    const mesh = meshRef.current;
    if (!mesh) return;
    pointsMetaRef.current = visiblePoints;
    const positions = normalizeCoords(visiblePoints, embeddingKey);
    const radius = radiusForCount(visiblePoints.length);
    const matrix = new THREE.Matrix4();
    const scaleV = new THREE.Vector3(radius, radius, radius);
    const quat = new THREE.Quaternion();
    const pos = new THREE.Vector3();
    visiblePoints.forEach((_, i) => {
      pos.set(positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]);
      matrix.compose(pos, quat, scaleV);
      mesh.setMatrixAt(i, matrix);
    });
    mesh.count = visiblePoints.length;
    mesh.instanceMatrix.needsUpdate = true;
    // Cached, not auto-recomputed on setMatrixAt — stale bounds can wrongly
    // frustum-cull the whole mesh after positions/count change.
    mesh.computeBoundingSphere();
  }, [visiblePoints, embeddingKey]);

  // Update dot colors when the visible set or color dimension changes.
  useEffect(() => {
    const mesh = meshRef.current;
    if (!mesh || !mesh.instanceColor) return;
    const c = new THREE.Color();
    visiblePoints.forEach((p, i) => {
      c.set(colorOf.get(bucketKey(p, colorDim)) || UNKNOWN_COLOR);
      mesh.setColorAt(i, c);
    });
    mesh.instanceColor.needsUpdate = true;
  }, [visiblePoints, colorDim, colorOf]);

  const selectedPoint = useMemo(
    () => data?.points.find((p) => p.item_id === selectedItemId) || null,
    [data, selectedItemId],
  );

  if (loading) return <div className="embeddings-page"><p>Loading embeddings…</p></div>;
  if (error) return <div className="embeddings-page"><ErrorBanner error={error} /></div>;
  if (!data) return <div className="embeddings-page"><p>No data.</p></div>;

  return (
    <div className={`embeddings-layout${selectedItemId ? " flyout-open" : ""}`}>
      <div className="embeddings-main">
        <div className="embeddings-header">
          <div className="topic-flow-stats">
            <span>
              <strong>{visiblePoints.length}</strong> {filterMode === "unseen" ? "unseen" : "items"}
              {filterMode === "unseen" ? ` of ${data.doc_count}` : ""}
            </span>
            <span><strong>{data.clusters.length}</strong> clusters</span>
            <span className="topic-flow-meta" title={absoluteTime(data.created_at)}>
              embeddings run · updated {absoluteTime(data.created_at)}
            </span>
          </div>
        </div>

        <div className="embeddings-canvas-wrap">
          <div className="embeddings-canvas" ref={containerRef} />

          <div className="embeddings-controls">
            <div className="embeddings-control-row">
              <span>Watch status</span>
              <div className="embeddings-toggle" role="group" aria-label="Filter by watch status">
                <button
                  type="button"
                  className={filterMode === "all" ? "active" : ""}
                  onClick={() => setFilterMode("all")}
                >
                  All
                </button>
                <button
                  type="button"
                  className={filterMode === "unseen" ? "active" : ""}
                  onClick={() => setFilterMode("unseen")}
                >
                  Unseen
                </button>
              </div>
            </div>
            <label className="embeddings-control-row">
              <span>Position by</span>
              <select value={embeddingKey} onChange={(e) => setEmbeddingKey(e.target.value)}>
                {data.embeddings.map((e) => (
                  <option key={e.key} value={e.key}>{e.label}</option>
                ))}
              </select>
            </label>
            <label className="embeddings-control-row">
              <span>Color by</span>
              <select value={colorDim} onChange={(e) => setColorDim(e.target.value as ColorDim)}>
                {COLOR_DIM_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </label>
            <div className="embeddings-legend">
              {legend.slice(0, 14).map((entry) => (
                <div key={entry.key} className="embeddings-legend-row">
                  <span className="embeddings-legend-swatch" style={{ background: entry.color }} />
                  <span className="embeddings-legend-label">{entry.key}</span>
                  <span className="embeddings-legend-count">{entry.count}</span>
                </div>
              ))}
              {legend.length > 14 && (
                <div className="embeddings-legend-more">+{legend.length - 14} more</div>
              )}
            </div>
            <div className="embeddings-hint">
              drag to orbit · scroll to zoom · auto-rotates when idle
            </div>
          </div>

          {hover && (
            <div
              className="embeddings-tooltip"
              style={{ left: hover.x + 14, top: hover.y + 14 }}
            >
              <div className="embeddings-tooltip-title">{hover.point.title}</div>
              <div className="embeddings-tooltip-meta">
                {hover.point.channel_name || "Unknown channel"}
                {hover.point.category ? ` · ${hover.point.category}` : ""}
              </div>
            </div>
          )}
        </div>
      </div>

      {selectedItemId && (
        <ContentView
          itemId={selectedItemId}
          subName={selectedPoint?.channel_name || ""}
          onClose={() => setSelectedItemId(null)}
        />
      )}
    </div>
  );
}
