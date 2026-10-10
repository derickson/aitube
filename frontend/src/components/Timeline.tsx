import { useEffect, useState, useCallback, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import {
  searchContent,
  listSubscriptions,
  batchPlaybackProgress,
  setInterest as apiSetInterest,
  setConsumed as apiSetConsumed,
  getCategorySettings,
  CATEGORY_LABELS,
  type DisplayType,
  type ContentSearchResponse,
  type FacetBucket,
  type Subscription,
  type PlaybackProgress,
} from "../api/client";
import { ContentView } from "./ContentView";
import { ErrorBanner } from "./ErrorBanner";
import { ContentCard, TYPE_LABELS } from "./ContentCard";
import { useDemoMode } from "../demo/DemoMode";

function facetCount(buckets: FacetBucket[] | undefined, key: string): number {
  return buckets?.find((b) => b.key === key)?.count ?? 0;
}

type TriState = Record<string, "include" | "exclude">;

/** Tri-state per label: "include" (+), "exclude" (-, muted), or absent (neutral),
 *  seeded once from the given include/exclude URL params. */
function useTriStateFilter(inCsv: string | null, outCsv: string | null) {
  const [state, setState] = useState<TriState>(() => {
    const initial: TriState = {};
    for (const label of (inCsv ?? "").split(",")) {
      if (label) initial[label] = "include";
    }
    for (const label of (outCsv ?? "").split(",")) {
      if (label) initial[label] = "exclude";
    }
    return initial;
  });

  const cycle = useCallback((label: string) => {
    setState((prev) => {
      const current = prev[label];
      const next = { ...prev };
      if (!current) next[label] = "include";
      else if (current === "include") next[label] = "exclude";
      else delete next[label];
      return next;
    });
  }, []);
  const clear = useCallback(() => setState({}), []);

  const includeCsv = Object.keys(state).filter((k) => state[k] === "include").join(",");
  const excludeCsv = Object.keys(state).filter((k) => state[k] === "exclude").join(",");
  return { state, cycle, clear, includeCsv, excludeCsv };
}

function TriStateFacet({ heading, labels, filter, buckets }: {
  heading: string;
  labels: string[];
  filter: ReturnType<typeof useTriStateFilter>;
  buckets: FacetBucket[];
}) {
  return (
    <div className="facet-group">
      <h4 className="facet-heading">{heading}</h4>
      {Object.keys(filter.state).length > 0 && (
        <button className="facet-item" onClick={filter.clear}>
          <span>Clear</span>
        </button>
      )}
      {labels.map((label) => {
        const state = filter.state[label];
        return (
          <button
            key={label}
            className={`facet-item facet-category${state === "include" ? " category-included" : ""}${state === "exclude" ? " category-excluded" : ""}`}
            onClick={() => filter.cycle(label)}
            title={state === "include" ? "Required — click to mute" : state === "exclude" ? "Muted — click to clear" : "Click to require"}
          >
            <span>{state === "include" ? "+ " : state === "exclude" ? "− " : ""}{label}</span>
            <span className="facet-count">{facetCount(buckets, label)}</span>
          </button>
        );
      })}
    </div>
  );
}

// consumedFilter's default ("false" — unwatched) differs from the URL's
// "absent means unset" convention, so it gets its own explicit "all" token.
function readConsumedParam(v: string | null): "true" | "false" | "" {
  if (v === "all") return "";
  if (v === "true" || v === "false") return v;
  return "false";
}

export function Timeline() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<ContentSearchResponse | null>(null);
  const [subs, setSubs] = useState<Record<string, Subscription>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Selected content for inline player
  const [selectedId, setSelectedId] = useState<string | null>(() => searchParams.get("item"));
  const [consumedIds, setConsumedIds] = useState<Set<string>>(new Set());
  // Items the user has just acted on whose change isn't yet visible in ES
  // search results. Hidden client-side until the next intentional refetch
  // (filter change / mount) clears this set and the server becomes authoritative.
  const [pendingHidden, setPendingHidden] = useState<Set<string>>(new Set());
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);

  // The taxonomy is user-editable (Category Settings page), so the facet options
  // must come from there rather than the static bootstrap CATEGORY_LABELS —
  // otherwise a renamed/added/removed category never shows up here even after
  // items have been relabeled.
  const [categoryLabels, setCategoryLabels] = useState<string[]>([...CATEGORY_LABELS]);
  // Topic = subcategory, of whichever categories have them (today: AI and Software).
  const [topicLabels, setTopicLabels] = useState<string[]>([]);
  useEffect(() => {
    getCategorySettings()
      .then((r) => {
        setCategoryLabels(r.categories.map((c) => c.label));
        setTopicLabels(r.categories.flatMap((c) => c.subcategories.map((s) => s.label)));
      })
      .catch(() => {});
  }, []);
  // In Demo Mode the backend restricts everything to one category, so the
  // Category facet would be a single bucket — hide it and its filter.
  const { demoMode } = useDemoMode();

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

  // Server-side filters — initial values are read once from the URL so a
  // refresh or a pasted link restores the exact same view.
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [typeFilter, setTypeFilter] = useState<DisplayType | "">(
    () => (searchParams.get("type") as DisplayType | null) ?? "",
  );
  const [interestFilter, setInterestFilter] = useState<"up" | "down" | "none" | "">(
    () => (searchParams.get("interest") as "up" | "down" | "none" | null) ?? "",
  );
  const [subFilter, setSubFilter] = useState(() => searchParams.get("sub") ?? "");
  const [consumedFilter, setConsumedFilter] = useState<"true" | "false" | "">(
    () => readConsumedParam(searchParams.get("consumed")),
  );
  const categoryFilter = useTriStateFilter(searchParams.get("cat_in"), searchParams.get("cat_out"));
  const topicFilter = useTriStateFilter(searchParams.get("topic_in"), searchParams.get("topic_out"));
  // Client-side filter for the Source facet list
  const [subSearch, setSubSearch] = useState("");
  consumedFilterRef.current = consumedFilter;
  interestFilterRef.current = interestFilter;

  const categoryIncludeCsv = demoMode ? "" : categoryFilter.includeCsv;
  const categoryExcludeCsv = demoMode ? "" : categoryFilter.excludeCsv;
  const topicIncludeCsv = topicFilter.includeCsv;
  const topicExcludeCsv = topicFilter.excludeCsv;

  // Debounce search
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const [debouncedSearch, setDebouncedSearch] = useState(search);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const [contentData, subData] = await Promise.all([
        searchContent({
          q: debouncedSearch || undefined,
          content_type: (typeFilter || undefined) as DisplayType | undefined,
          subscription_id: subFilter || undefined,
          consumed: (consumedFilter || undefined) as "true" | "false" | undefined,
          interest: (interestFilter || undefined) as "up" | "down" | "none" | undefined,
          category_include: categoryIncludeCsv || undefined,
          category_exclude: categoryExcludeCsv || undefined,
          subcategory_include: topicIncludeCsv || undefined,
          subcategory_exclude: topicExcludeCsv || undefined,
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
  }, [debouncedSearch, typeFilter, subFilter, consumedFilter, interestFilter, categoryIncludeCsv, categoryExcludeCsv, topicIncludeCsv, topicExcludeCsv]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Keep the address bar in sync with every filter/toggle and the open flyout,
  // so a refresh or a pasted link restores this exact view.
  useEffect(() => {
    const params = new URLSearchParams();
    if (debouncedSearch) params.set("q", debouncedSearch);
    if (typeFilter) params.set("type", typeFilter);
    if (interestFilter) params.set("interest", interestFilter);
    if (subFilter) params.set("sub", subFilter);
    if (consumedFilter === "") params.set("consumed", "all");
    else if (consumedFilter !== "false") params.set("consumed", consumedFilter);
    // Category state is kept in the URL even while Demo Mode ignores it, so
    // turning Demo Mode off restores the previous view.
    if (categoryFilter.includeCsv) params.set("cat_in", categoryFilter.includeCsv);
    if (categoryFilter.excludeCsv) params.set("cat_out", categoryFilter.excludeCsv);
    if (topicIncludeCsv) params.set("topic_in", topicIncludeCsv);
    if (topicExcludeCsv) params.set("topic_out", topicExcludeCsv);
    if (selectedId) params.set("item", selectedId);
    setSearchParams(params, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch, typeFilter, interestFilter, subFilter, consumedFilter, categoryFilter.includeCsv, categoryFilter.excludeCsv, topicIncludeCsv, topicExcludeCsv, selectedId]);

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
    // Optimistically reflect the new interest on the card (green/red active state)
    // until the next intentional refetch makes the server authoritative.
    setData((prev) =>
      prev
        ? { ...prev, items: prev.items.map((i) => (i.id === itemId ? { ...i, user_interest: value === "none" ? null : value } : i)) }
        : prev,
    );
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
  const categoryBuckets = facets.category ?? [];
  const topicBuckets = facets.subcategory ?? [];
  const subBuckets = (facets.subscription_id ?? [])
    .sort((a, b) => {
      const nameA = subs[a.key]?.name ?? a.key;
      const nameB = subs[b.key]?.name ?? b.key;
      return nameA.localeCompare(nameB);
    })
    .filter((b) => {
      if (!subSearch) return true;
      const name = subs[b.key]?.name ?? b.key;
      return name.toLowerCase().includes(subSearch.toLowerCase());
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
            {(["video", "corridor", "podcast_episode", "article"] as DisplayType[]).map((t) => (
              <button
                key={t}
                className={`facet-item facet-type-${t === "video" ? "youtube_channel" : t === "corridor" ? "corridor_show" : t === "podcast_episode" ? "podcast" : "rss"}${typeFilter === t ? " active" : ""}`}
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

          {!demoMode && (
            <TriStateFacet heading="Category" labels={categoryLabels} filter={categoryFilter} buckets={categoryBuckets} />
          )}

          {topicLabels.length > 0 && (
            <TriStateFacet heading="Topic" labels={topicLabels} filter={topicFilter} buckets={topicBuckets} />
          )}

          <div className="facet-group">
            <h4 className="facet-heading">Source</h4>
            <input
              type="search"
              className="facet-sub-search"
              placeholder="Filter sources…"
              value={subSearch}
              onChange={(e) => setSubSearch(e.target.value)}
            />
            {!subSearch && (
              <button
                className={`facet-item${!subFilter ? " active" : ""}`}
                onClick={() => { setSubFilter(""); closeSidebar(); }}
              >
                <span>All</span>
              </button>
            )}
            {subBuckets.map((b) => {
              const name = subs[b.key]?.name ?? b.key;
              const label = name.length > 20 ? name.slice(0, 20) + "…" : name;
              return (
                <button
                  key={b.key}
                  className={`facet-item${subFilter === b.key ? " active" : ""}`}
                  onClick={() => { setSubFilter(subFilter === b.key ? "" : b.key); setSubSearch(""); closeSidebar(); }}
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

