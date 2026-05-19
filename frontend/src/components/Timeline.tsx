import { useEffect, useState, useCallback, useRef } from "react";
import {
  searchContent,
  listSubscriptions,
  batchPlaybackProgress,
  setInterest as apiSetInterest,
  setConsumed as apiSetConsumed,
  type ContentType,
  type ContentSearchResponse,
  type FacetBucket,
  type Subscription,
  type PlaybackProgress,
} from "../api/client";
import { ContentView } from "./ContentView";
import { ErrorBanner } from "./ErrorBanner";
import { ContentCard, TYPE_LABELS } from "./ContentCard";

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
  // Items the user has just acted on whose change isn't yet visible in ES
  // search results. Hidden client-side until the next intentional refetch
  // (filter change / mount) clears this set and the server becomes authoritative.
  const [pendingHidden, setPendingHidden] = useState<Set<string>>(new Set());
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);

  const consumedFilterRef = useRef<"true" | "false" | "">("false");
  const interestFilterRef = useRef<"up" | "down" | "none" | "">("");

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
    setPendingHidden((prev) => {
      const next = new Set(prev);
      const filter = consumedFilterRef.current;
      // Hide if the item no longer matches the active consumed filter.
      if ((filter === "false" && consumed) || (filter === "true" && !consumed)) {
        next.add(itemId);
      } else {
        // Toggle-back: if the user reverses their action, unhide.
        next.delete(itemId);
      }
      return next;
    });
  }, []);

  // Playback progress (lazy loaded)
  const [progress, setProgress] = useState<Record<string, PlaybackProgress>>({});

  // Server-side filters
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<ContentType | "">("");
  const [interestFilter, setInterestFilter] = useState<"up" | "down" | "none" | "">("");
  const [subFilter, setSubFilter] = useState("");
  const [consumedFilter, setConsumedFilter] = useState<"true" | "false" | "">("false");
  consumedFilterRef.current = consumedFilter;
  interestFilterRef.current = interestFilter;

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
      setConsumedIds(new Set(contentData.items.filter((i) => i.consumed).map((i) => i.id)));
      // Fresh server state — drop any client-side hides; server is authoritative now.
      setPendingHidden(new Set());
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

  const handleInterestChange = useCallback((itemId: string, value: "up" | "down" | "none") => {
    apiSetInterest(itemId, value).catch(() => {});
    setPendingHidden((prev) => {
      const next = new Set(prev);
      const filter = interestFilterRef.current;
      if (filter && filter !== value) next.add(itemId);
      else next.delete(itemId);
      return next;
    });
  }, []);

  const handleSearchChange = (value: string) => {
    setSearch(value);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDebouncedSearch(value);
    }, 300);
  };

  const items = (data?.items ?? []).filter((i) => !pendingHidden.has(i.id));
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
            <div className="empty-state">
              {total === 0 && (
                <img
                  src="/aitube/images/sleeping.png"
                  alt="Sleeping"
                  className="empty-state-img"
                />
              )}
              <p className="empty-text">
                {total === 0
                  ? "Nothing to watch right now. New content from your subscriptions is checked every 30 minutes, or you can add more subscriptions to expand your feed."
                  : "No items match your filters."}
              </p>
            </div>
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
            subName={subs[items.find(i => i.id === selectedId)?.subscription_id ?? ""]?.name ?? ""}
            onClose={() => setSelectedId(null)}
            onConsumedChange={handleConsumedChange}
          />
        )}
      </div>
    </div>
  );
}

