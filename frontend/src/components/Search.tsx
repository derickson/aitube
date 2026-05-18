import { useEffect, useState, useRef } from "react";
import {
  searchContent,
  listSubscriptions,
  type ContentSearchResponse,
  type Subscription,
} from "../api/client";
import { ContentView } from "./ContentView";
import { ErrorBanner } from "./ErrorBanner";
import { ContentCard } from "./ContentCard";

export function Search() {
  const [data, setData] = useState<ContentSearchResponse | null>(null);
  const [subs, setSubs] = useState<Record<string, Subscription>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listSubscriptions()
      .then((subList) => {
        const subMap: Record<string, Subscription> = {};
        for (const s of subList) subMap[s.id] = s;
        setSubs(subMap);
      })
      .catch(() => {});
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const q = debouncedSearch.trim();
    if (!q) {
      setData(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    searchContent({ q, sort: "relevance", size: 200 })
      .then((res) => {
        if (cancelled) return;
        setData(res);
        setError("");
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Search failed");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [debouncedSearch]);

  useEffect(() => {
    if (selectedId && window.innerWidth <= 768) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [selectedId]);

  const handleSearchChange = (value: string) => {
    setSearch(value);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDebouncedSearch(value);
    }, 300);
  };

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const hasQuery = debouncedSearch.trim().length > 0;

  return (
    <div className="search-page">
      <div className="search-input-wrap">
        <input
          ref={inputRef}
          type="text"
          className="search-input"
          placeholder="Search across titles, descriptions, and summaries..."
          value={search}
          onChange={(e) => handleSearchChange(e.target.value)}
          autoFocus
        />
      </div>

      {error && <ErrorBanner error={error} />}

      <div className={`timeline-layout${selectedId ? " flyout-open" : ""}`}>
        <div className="timeline-main">
          {!hasQuery ? (
            <div className="empty-state">
              <p className="empty-text">Type to search across your library.</p>
            </div>
          ) : loading && items.length === 0 ? (
            <p className="loading-text">Searching...</p>
          ) : items.length === 0 ? (
            <div className="empty-state">
              <p className="empty-text">No results for "{debouncedSearch}".</p>
            </div>
          ) : (
            <>
              <p className="search-result-count">
                {total} result{total === 1 ? "" : "s"}
              </p>
              <div className="content-grid">
                {items.map((item) => (
                  <ContentCard
                    key={item.id}
                    item={item}
                    subName={subs[item.subscription_id]?.name ?? ""}
                    isActive={item.id === selectedId}
                    isConsumed={item.consumed}
                    onSelect={() =>
                      setSelectedId(item.id === selectedId ? null : item.id)
                    }
                    showActions={false}
                  />
                ))}
              </div>
            </>
          )}
        </div>

        {selectedId && (
          <ContentView
            itemId={selectedId}
            subName={
              subs[
                items.find((i) => i.id === selectedId)?.subscription_id ?? ""
              ]?.name ?? ""
            }
            onClose={() => setSelectedId(null)}
          />
        )}
      </div>
    </div>
  );
}
