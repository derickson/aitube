import { useEffect, useState, useCallback, useRef } from "react";
import {
  searchContent,
  listSubscriptions,
  batchPlaybackProgress,
  setInterest as apiSetInterest,
  setConsumed as apiSetConsumed,
  type ContentItem,
  type ContentType,
  type ContentSearchResponse,
  type FacetBucket,
  type Subscription,
  type PlaybackProgress,
} from "../api/client";
import { ContentView } from "./ContentView";
import { ErrorBanner } from "./ErrorBanner";

const TYPE_LABELS: Record<ContentType, string> = {
  video: "YouTube",
  podcast_episode: "Podcast",
  article: "Article",
};

function formatDate(dateStr: string | null): string {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffH = Math.floor(diffMs / 3600000);
  if (diffH < 1) return "just now";
  if (diffH < 24) return `${diffH}h ago`;
  const diffD = Math.floor(diffH / 24);
  if (diffD < 7) return `${diffD}d ago`;
  return d.toLocaleDateString();
}

function formatDuration(seconds: number | null): string {
  if (!seconds) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function facetCount(buckets: FacetBucket[] | undefined, key: string): number {
  return buckets?.find((b) => b.key === key)?.count ?? 0;
}

export function Timeline() {
  const [data, setData] = useState<ContentSearchResponse | null>(null);
  const [subs, setSubs] = useState<Record<string, Subscription>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Selected content for inline player
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [consumedIds, setConsumedIds] = useState<Set<string>>(new Set());
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);

  const fetchDataRef = useRef<() => void>(undefined);

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
    // Re-fetch after a short delay to let ES update
    setTimeout(() => fetchDataRef.current?.(), 500);
  }, []);

  // Playback progress (lazy loaded)
  const [progress, setProgress] = useState<Record<string, PlaybackProgress>>({});

  // Server-side filters
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<ContentType | "">("");
  const [interestFilter, setInterestFilter] = useState<"up" | "down" | "none" | "">("");
  const [subFilter, setSubFilter] = useState("");
  const [consumedFilter, setConsumedFilter] = useState<"true" | "false" | "">("false");

  // Debounce search
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const [debouncedSearch, setDebouncedSearch] = useState("");

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const [contentData, subData] = await Promise.all([
        searchContent({
          q: debouncedSearch || undefined,
          content_type: (typeFilter || undefined) as ContentType | undefined,
          subscription_id: subFilter || undefined,
          consumed: (consumedFilter || undefined) as "true" | "false" | undefined,
          interest: (interestFilter || undefined) as "up" | "down" | "none" | undefined,
          size: 200,
        }),
        listSubscriptions(),
      ]);
      setData(contentData);
      const subMap: Record<string, Subscription> = {};
      for (const s of subData) subMap[s.id] = s;
      setSubs(subMap);
      setError("");

      // Lazy load playback progress
      const ids = contentData.items
        .filter((i) => i.type === "video" || i.type === "podcast_episode")
        .map((i) => i.id);
      if (ids.length > 0) {
        batchPlaybackProgress(ids).then(setProgress).catch(() => {});
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load content");
    } finally {
      setLoading(false);
    }
  }, [debouncedSearch, typeFilter, subFilter, consumedFilter, interestFilter]);

  fetchDataRef.current = fetchData;

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

  const handleInterestChange = useCallback(async (itemId: string, value: "up" | "down" | "none") => {
    await apiSetInterest(itemId, value).catch(() => {});
    setTimeout(() => fetchDataRef.current?.(), 500);
  }, []);

  const handleSearchChange = (value: string) => {
    setSearch(value);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDebouncedSearch(value);
    }, 300);
  };

  const items = data?.items ?? [];
  const facets = data?.facets ?? {};
  const total = data?.total ?? 0;

  const typeBuckets = facets.type ?? [];
  const consumedBuckets = facets.consumed ?? [];
  const interestBuckets = facets.interest ?? [];
  const subBuckets = (facets.subscription_id ?? []).sort((a, b) => {
    const nameA = subs[a.key]?.name ?? a.key;
    const nameB = subs[b.key]?.name ?? b.key;
    return nameA.localeCompare(nameB);
  });

  return (
    <div className="timeline">
      <div className="timeline-header">
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <button className="btn sidebar-toggle" onClick={() => setSidebarOpen((s) => !s)}>Filters</button>
          <h2>Timeline</h2>
        </div>
        <span className="timeline-count">
          {items.length} of {total} items
        </span>
      </div>

      {error && <ErrorBanner error={error} />}

      <div className={`timeline-layout${selectedId ? " flyout-open" : ""}`}>
        <aside className={`facet-sidebar${sidebarOpen ? " sidebar-open" : ""}`}>
          <button className="btn sidebar-close" onClick={closeSidebar}>Close</button>
          <input
            type="text"
            className="facet-search"
            placeholder="Search..."
            value={search}
            onChange={(e) => handleSearchChange(e.target.value)}
          />

          <div className="facet-group">
            <h4 className="facet-heading">Type</h4>
            <button
              className={`facet-item${!typeFilter ? " active" : ""}`}
              onClick={() => { setTypeFilter(""); closeSidebar(); }}
            >
              <span>All</span><span className="facet-count">{total}</span>
            </button>
            {(["video", "podcast_episode", "article"] as ContentType[]).map((t) => (
              <button
                key={t}
                className={`facet-item facet-type-${t === "video" ? "youtube_channel" : t === "podcast_episode" ? "podcast" : "rss"}${typeFilter === t ? " active" : ""}`}
                onClick={() => { setTypeFilter(typeFilter === t ? "" : t); closeSidebar(); }}
              >
                <span>{TYPE_LABELS[t]}</span><span className="facet-count">{facetCount(typeBuckets, t)}</span>
              </button>
            ))}
          </div>

          <div className="facet-group">
            <h4 className="facet-heading">Status</h4>
            <button
              className={`facet-item${!consumedFilter ? " active" : ""}`}
              onClick={() => { setConsumedFilter(""); closeSidebar(); }}
            >
              <span>All</span>
            </button>
            {consumedBuckets.map((b) => (
              <button
                key={b.key}
                className={`facet-item${consumedFilter === (b.key === "watched" ? "true" : "false") ? " active" : ""}`}
                onClick={() => {
                  const val = b.key === "watched" ? "true" : "false";
                  setConsumedFilter(consumedFilter === val ? "" : val);
                  closeSidebar();
                }}
              >
                <span>{b.key === "watched" ? "Watched" : "Unwatched"}</span>
                <span className="facet-count">{b.count}</span>
              </button>
            ))}
          </div>

          <div className="facet-group">
            <h4 className="facet-heading">Interest</h4>
            <button
              className={`facet-item${!interestFilter ? " active" : ""}`}
              onClick={() => { setInterestFilter(""); closeSidebar(); }}
            >
              <span>All</span>
            </button>
            <button
              className={`facet-item facet-interest-up${interestFilter === "up" ? " active" : ""}`}
              onClick={() => { setInterestFilter(interestFilter === "up" ? "" : "up"); closeSidebar(); }}
            >
              <span>Interesting</span><span className="facet-count">{facetCount(interestBuckets, "up")}</span>
            </button>
            <button
              className={`facet-item facet-interest-down${interestFilter === "down" ? " active" : ""}`}
              onClick={() => { setInterestFilter(interestFilter === "down" ? "" : "down"); closeSidebar(); }}
            >
              <span>Not interested</span><span className="facet-count">{facetCount(interestBuckets, "down")}</span>
            </button>
          </div>

          <div className="facet-group">
            <h4 className="facet-heading">Source</h4>
            <button
              className={`facet-item${!subFilter ? " active" : ""}`}
              onClick={() => { setSubFilter(""); closeSidebar(); }}
            >
              <span>All</span>
            </button>
            {subBuckets.map((b) => {
              const name = subs[b.key]?.name ?? b.key;
              const label = name.length > 15 ? name.slice(0, 15) + "…" : name;
              return (
                <button
                  key={b.key}
                  className={`facet-item${subFilter === b.key ? " active" : ""}`}
                  onClick={() => { setSubFilter(subFilter === b.key ? "" : b.key); closeSidebar(); }}
                  title={name}
                >
                  <span>{label}</span><span className="facet-count">{b.count}</span>
                </button>
              );
            })}
          </div>
        </aside>

        <div className="timeline-main">
          {loading && items.length === 0 ? (
            <p className="loading-text">Loading timeline...</p>
          ) : items.length === 0 ? (
            <p className="empty-text">
              {total === 0
                ? "No content yet. Add subscriptions and poll for new content."
                : "No items match your filters."}
            </p>
          ) : (
            <div className="content-grid">
              {items.map((item) => (
                <ContentCard
                  key={item.id}
                  item={item}
                  subName={subs[item.subscription_id]?.name ?? ""}
                  isActive={item.id === selectedId}
                  isConsumed={consumedIds.has(item.id)}
                  progress={progress[item.id]}
                  onSelect={() => setSelectedId(item.id === selectedId ? null : item.id)}
                  onInterest={handleInterestChange}
                  onToggleConsumed={handleConsumedChange}
                />
              ))}
            </div>
          )}
        </div>

        {selectedId && (
          <ContentView
            itemId={selectedId}
            onClose={() => setSelectedId(null)}
            onConsumedChange={handleConsumedChange}
          />
        )}
      </div>
    </div>
  );
}

function ContentCard({
  item,
  subName,
  isActive,
  isConsumed,
  progress,
  onSelect,
  onInterest,
  onToggleConsumed,
}: {
  item: ContentItem;
  subName: string;
  isActive: boolean;
  isConsumed: boolean;
  progress?: PlaybackProgress;
  onSelect: () => void;
  onInterest: (itemId: string, value: "up" | "down" | "none") => void;
  onToggleConsumed: (itemId: string, consumed: boolean, callApi: boolean) => void;
}) {
  const description =
    item.summary ||
    (item.metadata as Record<string, unknown>)?.description?.toString() ||
    "";

  const classes = [
    "content-card",
    `content-card-type-${item.type}`,
    isActive && "content-card-active",
    isConsumed && "content-card-consumed",
    item.user_interest === "down" && "content-card-downvoted",
  ].filter(Boolean).join(" ");

  const handleInterestClick = (e: React.MouseEvent, value: "up" | "down") => {
    e.stopPropagation();
    onInterest(item.id, item.user_interest === value ? "none" : value);
  };

  const handleConsumedClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onToggleConsumed(item.id, !isConsumed, true);
  };

  return (
    <div
      className={classes}
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === "Enter") onSelect(); }}
    >
      {item.thumbnail_url && (
        <div className="content-thumb-wrap">
          <img
            className="content-thumb"
            src={item.thumbnail_url}
            alt=""
            loading="lazy"
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = "none";
            }}
          />
          {item.duration_seconds && (
            <span className="content-duration">
              {formatDuration(item.duration_seconds)}
            </span>
          )}
          {progress && progress.percent > 0 && progress.percent < 100 && (
            <span className="content-progress">{progress.percent}%</span>
          )}
        </div>
      )}
      <div className="content-body">
        <div className="content-top-row">
          <span className={`content-type-badge content-type-${item.type}`}>
            {TYPE_LABELS[item.type]}
          </span>
          <span className="content-interest-btns">
            <button
              className={`interest-btn interest-consumed${isConsumed ? " active" : ""}`}
              onClick={handleConsumedClick}
              title={isConsumed ? "Mark unwatched" : "Mark viewed"}
            >
              &#10003;
            </button>
            <button
              className={`interest-btn interest-up${item.user_interest === "up" ? " active" : ""}`}
              onClick={(e) => handleInterestClick(e, "up")}
              title="Interesting"
            >
              +
            </button>
            <button
              className={`interest-btn interest-down${item.user_interest === "down" ? " active" : ""}`}
              onClick={(e) => handleInterestClick(e, "down")}
              title="Not interested"
            >
              -
            </button>
          </span>
        </div>
        <h3 className="content-title">{item.title}</h3>
        {description && <p className="content-desc">{description}</p>}
        <div className="content-meta">
          <span className="content-source">{subName}</span>
          <span className="content-date">
            {formatDate(item.published_at)}
          </span>
        </div>
      </div>
    </div>
  );
}
