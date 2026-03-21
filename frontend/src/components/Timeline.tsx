import { useEffect, useState, useCallback, useRef } from "react";
import {
  searchContent,
  listSubscriptions,
  type ContentItem,
  type ContentType,
  type ContentSearchResponse,
  type FacetBucket,
  type Subscription,
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

  const fetchDataRef = useRef<() => void>(undefined);

  const handleConsumedChange = useCallback((itemId: string, consumed: boolean) => {
    setConsumedIds((prev) => {
      const next = new Set(prev);
      if (consumed) next.add(itemId);
      else next.delete(itemId);
      return next;
    });
    // Re-fetch after a short delay to let ES update
    setTimeout(() => fetchDataRef.current?.(), 500);
  }, []);

  // Server-side filters
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<ContentType | "">("");
  const subFilter = "";
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
          size: 200,
        }),
        listSubscriptions(),
      ]);
      setData(contentData);
      const subMap: Record<string, Subscription> = {};
      for (const s of subData) subMap[s.id] = s;
      setSubs(subMap);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load content");
    } finally {
      setLoading(false);
    }
  }, [debouncedSearch, typeFilter, subFilter, consumedFilter]);

  fetchDataRef.current = fetchData;

  useEffect(() => {
    fetchData();
  }, [fetchData]);

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

  return (
    <div className="timeline">
      <div className="timeline-header">
        <h2>Timeline</h2>
        <span className="timeline-count">
          {items.length} of {total} items
        </span>
      </div>

      {error && <ErrorBanner error={error} />}

      <div className="timeline-filters">
        <input
          type="text"
          className="timeline-search"
          placeholder="Search content..."
          value={search}
          onChange={(e) => handleSearchChange(e.target.value)}
        />

        <div className="filter-pills">
          <button
            className={`filter-pill${!typeFilter ? " active" : ""}`}
            onClick={() => setTypeFilter("")}
          >
            All <span className="filter-count">{total}</span>
          </button>
          {(["video", "podcast_episode", "article"] as ContentType[]).map((t) => (
            <button
              key={t}
              className={`filter-pill filter-pill-type-${t === "video" ? "youtube_channel" : t === "podcast_episode" ? "podcast" : "rss"}${typeFilter === t ? " active" : ""}`}
              onClick={() => setTypeFilter(typeFilter === t ? "" : t)}
            >
              {TYPE_LABELS[t]}
              <span className="filter-count">{facetCount(typeBuckets, t)}</span>
            </button>
          ))}
        </div>

        <div className="filter-pills">
          <button
            className={`filter-pill${!consumedFilter ? " active" : ""}`}
            onClick={() => setConsumedFilter("")}
          >
            All
          </button>
          {consumedBuckets.map((b) => (
            <button
              key={b.key}
              className={`filter-pill${consumedFilter === (b.key === "watched" ? "true" : "false") ? " active" : ""}`}
              onClick={() => {
                const val = b.key === "watched" ? "true" : "false";
                setConsumedFilter(consumedFilter === val ? "" : val);
              }}
            >
              {b.key === "watched" ? "Watched" : "Unwatched"}
              <span className="filter-count">{b.count}</span>
            </button>
          ))}
        </div>

      </div>

      <div className={`timeline-layout${selectedId ? " flyout-open" : ""}`}>
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
                  onSelect={() => setSelectedId(item.id === selectedId ? null : item.id)}
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
  onSelect,
}: {
  item: ContentItem;
  subName: string;
  isActive: boolean;
  isConsumed: boolean;
  onSelect: () => void;
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
  ].filter(Boolean).join(" ");

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
        </div>
      )}
      <div className="content-body">
        <div className="content-top-row">
          <span className={`content-type-badge content-type-${item.type}`}>
            {TYPE_LABELS[item.type]}
          </span>
          {item.interest_score !== null && (
            <span className="content-score" title="Interest score">
              {Math.round(item.interest_score * 100)}%
            </span>
          )}
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
