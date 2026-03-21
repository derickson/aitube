import { useEffect, useState, useCallback, useMemo } from "react";
import {
  listContent,
  listSubscriptions,
  type ContentItem,
  type ContentType,
  type Subscription,
} from "../api/client";

const TYPE_LABELS: Record<ContentType, string> = {
  video: "YouTube",
  podcast_episode: "Podcast",
  article: "Article",
};

const TYPE_FILTERS: { value: ContentType | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "video", label: "YouTube" },
  { value: "podcast_episode", label: "Podcasts" },
  { value: "article", label: "Articles" },
];

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

export function Timeline() {
  const [items, setItems] = useState<ContentItem[]>([]);
  const [subs, setSubs] = useState<Record<string, Subscription>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Filters
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<ContentType | "all">("all");
  const [subFilter, setSubFilter] = useState<string>("all");

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const [contentData, subData] = await Promise.all([
        listContent({ size: 200 }),
        listSubscriptions(),
      ]);
      setItems(contentData);
      const subMap: Record<string, Subscription> = {};
      for (const s of subData) subMap[s.id] = s;
      setSubs(subMap);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load content");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const filtered = useMemo(() => {
    let result = items;
    if (typeFilter !== "all") {
      result = result.filter((i) => i.type === typeFilter);
    }
    if (subFilter !== "all") {
      result = result.filter((i) => i.subscription_id === subFilter);
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter(
        (i) =>
          i.title.toLowerCase().includes(q) ||
          i.summary.toLowerCase().includes(q) ||
          (i.metadata as Record<string, unknown>)?.description?.toString().toLowerCase().includes(q),
      );
    }
    return result;
  }, [items, typeFilter, subFilter, search]);

  const counts = useMemo(() => {
    const c = { all: items.length, video: 0, podcast_episode: 0, article: 0 };
    for (const i of items) c[i.type]++;
    return c;
  }, [items]);

  const uniqueSubs = useMemo(() => {
    const ids = new Set(items.map((i) => i.subscription_id));
    return Array.from(ids)
      .map((id) => subs[id])
      .filter(Boolean)
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [items, subs]);

  return (
    <div className="timeline">
      <div className="timeline-header">
        <h2>Timeline</h2>
        <span className="timeline-count">{filtered.length} items</span>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {!loading && items.length > 0 && (
        <div className="timeline-filters">
          <input
            type="text"
            className="timeline-search"
            placeholder="Search content..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="filter-pills">
            {TYPE_FILTERS.map((f) => (
              <button
                key={f.value}
                className={`filter-pill filter-pill-type-${f.value === "video" ? "youtube_channel" : f.value === "podcast_episode" ? "podcast" : f.value === "article" ? "rss" : f.value}${typeFilter === f.value ? " active" : ""}`}
                onClick={() => setTypeFilter(f.value)}
              >
                {f.label}
                <span className="filter-count">
                  {f.value === "all" ? counts.all : counts[f.value]}
                </span>
              </button>
            ))}
          </div>
          {uniqueSubs.length > 1 && (
            <select
              className="timeline-sub-filter"
              value={subFilter}
              onChange={(e) => setSubFilter(e.target.value)}
            >
              <option value="all">All sources</option>
              {uniqueSubs.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          )}
        </div>
      )}

      {loading ? (
        <p className="loading-text">Loading timeline...</p>
      ) : items.length === 0 ? (
        <p className="empty-text">No content yet. Add subscriptions and poll for new content.</p>
      ) : filtered.length === 0 ? (
        <p className="empty-text">No items match your filters.</p>
      ) : (
        <div className="content-grid">
          {filtered.map((item) => (
            <ContentCard key={item.id} item={item} subName={subs[item.subscription_id]?.name ?? ""} />
          ))}
        </div>
      )}
    </div>
  );
}

function ContentCard({ item, subName }: { item: ContentItem; subName: string }) {
  const description =
    item.summary ||
    (item.metadata as Record<string, unknown>)?.description?.toString() ||
    "";

  return (
    <a
      className={`content-card content-card-type-${item.type}`}
      href={item.url}
      target="_blank"
      rel="noopener noreferrer"
    >
      {item.thumbnail_url && (
        <div className="content-thumb-wrap">
          <img
            className="content-thumb"
            src={item.thumbnail_url}
            alt=""
            loading="lazy"
            onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
          />
          {item.duration_seconds && (
            <span className="content-duration">{formatDuration(item.duration_seconds)}</span>
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
        {description && (
          <p className="content-desc">{description}</p>
        )}
        <div className="content-meta">
          <span className="content-source">{subName}</span>
          <span className="content-date">{formatDate(item.published_at)}</span>
        </div>
      </div>
    </a>
  );
}
