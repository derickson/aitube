import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  displayType,
  listSubscriptions,
  searchExplain,
  type ExplainedItem,
  type ExplainRetriever,
  type SearchExplainResponse,
  type Subscription,
} from "../api/client";
import { ErrorBanner } from "./ErrorBanner";
import { TYPE_LABELS, formatDate } from "./ContentCard";

const HL_RE = /(\[\[hl\]\].*?\[\[\/hl\]\])/g;

/** Render a snippet where the backend wrapped matches in [[hl]]..[[/hl]]. */
function Highlighted({ text }: { text: string }) {
  return (
    <>
      {text.split(HL_RE).map((part, i) =>
        part.startsWith("[[hl]]") ? (
          <mark key={i}>{part.slice(6, -7)}</mark>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </>
  );
}

const fmt = (n: number | null, digits = 2) => (n == null ? "—" : n.toFixed(digits));

function ComponentBar({ value, max, className }: { value: number; max: number; className?: string }) {
  const pct = max > 0 ? Math.max(2, (value / max) * 100) : 0;
  return (
    <div className="se-bar">
      <div className={`se-bar-fill ${className ?? ""}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

function RetrieverPanel({
  r,
  hybrid,
  window,
  maxContribution,
  finalScore,
}: {
  r: ExplainRetriever;
  hybrid: boolean;
  window: number;
  maxContribution: number;
  finalScore: number;
}) {
  const isLexical = r.name === "lexical";
  const matched = r.rank != null;
  const maxField = Math.max(0, ...r.fields.map((f) => f.score));
  return (
    <section className={`se-retriever${matched ? "" : " se-miss"}`}>
      <header className="se-retriever-head">
        <h4>{r.label}</h4>
        {matched ? (
          <span className="se-rank">rank #{r.rank}</span>
        ) : (
          <span className="se-rank se-rank-miss">not in top {window}</span>
        )}
      </header>

      {matched ? (
        <>
          <div className="se-stats">
            <div>
              <span className="se-stat-label">{isLexical ? "BM25 score" : "Similarity"}</span>
              <span className="se-stat-value">{fmt(r.score, isLexical ? 2 : 4)}</span>
            </div>
            {hybrid && (
              <div>
                <span className="se-stat-label">
                  RRF contribution = 1 / (60 + {r.rank})
                </span>
                <span className="se-stat-value">{fmt(r.rrf_contribution, 5)}</span>
                <ComponentBar value={r.rrf_contribution} max={maxContribution} className="se-fill-rrf" />
                <span className="se-stat-sub">
                  {finalScore > 0 ? `${((r.rrf_contribution / finalScore) * 100).toFixed(0)}% of final score` : ""}
                </span>
              </div>
            )}
          </div>

          {isLexical && r.fields.length > 0 && (
            <table className="se-fields">
              <thead>
                <tr>
                  <th>Field</th>
                  <th>Boost</th>
                  <th>Score</th>
                  <th></th>
                  <th>Matching terms (score)</th>
                </tr>
              </thead>
              <tbody>
                {r.fields.map((f) => (
                  <tr key={f.field} className={f.counted ? "se-counted" : "se-uncounted"}>
                    <td>
                      {f.field}
                      {f.counted && <span className="se-winner">counted</span>}
                    </td>
                    <td>×{f.boost}</td>
                    <td>{fmt(f.score, 1)}</td>
                    <td className="se-bar-cell">
                      <ComponentBar value={f.score} max={maxField} className={f.counted ? "se-fill-win" : "se-fill-lose"} />
                    </td>
                    <td className="se-terms">
                      {f.terms.map((t) => `${t.term} (${t.score.toFixed(1)})`).join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {isLexical && r.fields.length > 0 && (
            <p className="se-note">
              Keyword search uses <code>best_fields</code>: only the single highest-scoring field sets the
              BM25 score. Other fields that also matched are shown dimmed for context.
            </p>
          )}

          {r.snippets.length > 0 && (
            <div className="se-snippets">
              <span className="se-stat-label">
                {isLexical ? "Matching text" : "Best-matching passages"}
              </span>
              {r.snippets.map((s, i) => (
                <blockquote key={i}>
                  <Highlighted text={s} />
                </blockquote>
              ))}
            </div>
          )}
        </>
      ) : (
        <p className="se-note">
          This retriever didn't surface the item, so it adds nothing to the fused score.
        </p>
      )}
    </section>
  );
}

function Explanation({ entry, data }: { entry: ExplainedItem; data: SearchExplainResponse }) {
  const hybrid = data.mode === "hybrid";
  const maxContribution = Math.max(...entry.retrievers.map((r) => r.rrf_contribution), 0);
  const sourceLabel = (d: SearchExplainResponse) => d.retrievers.map((r) => r.label).join(" + ");
  const { item } = entry;

  return (
    <div className="se-detail">
      <div className="se-detail-head">
        <span className="se-detail-rank">#{entry.rank}</span>
        <div>
          <h3>
            <a href={item.url} target="_blank" rel="noreferrer">
              {item.title}
            </a>
          </h3>
          <p className="se-meta">
            {TYPE_LABELS[displayType(item)]}
            {item.published_at && ` · ${formatDate(item.published_at)}`}
          </p>
        </div>
      </div>

      <div className="se-score-box">
        <div>
          <span className="se-stat-label">{hybrid ? "Final score (RRF)" : "Final score (BM25)"}</span>
          <span className="se-score-big">{fmt(entry.score, hybrid ? 5 : 2)}</span>
        </div>
        {hybrid ? (
          <div className="se-stack" title="Share of the final score from each retriever">
            {entry.retrievers.map((r) =>
              r.rrf_contribution > 0 ? (
                <div
                  key={r.name}
                  className={`se-stack-seg se-seg-${r.name}`}
                  style={{ flexGrow: r.rrf_contribution }}
                >
                  {r.label.split(":")[0].replace(" (BM25)", "")} · #{r.rank}
                </div>
              ) : null,
            )}
          </div>
        ) : (
          <p className="se-note">
            Semantic search is off, so ranking is keyword (BM25) only: {sourceLabel(data)}.
          </p>
        )}
      </div>

      {hybrid && (
        <p className="se-note">
          Hybrid search runs {data.retrievers.length} retrievers independently, then fuses their rankings with{" "}
          <strong>Reciprocal Rank Fusion</strong>: each retriever contributes 1 / ({data.rank_constant} + rank).
          Raw scores are not compared (BM25 and cosine similarity aren't on the same scale), only ranks, so an item
          ranked highly by several retrievers wins.
        </p>
      )}

      {entry.retrievers.map((r) => (
        <RetrieverPanel
          key={r.name}
          r={r}
          hybrid={hybrid}
          window={data.rank_window}
          maxContribution={maxContribution}
          finalScore={entry.score}
        />
      ))}
    </div>
  );
}

export function SearchExplain() {
  const [data, setData] = useState<SearchExplainResponse | null>(null);
  const [subs, setSubs] = useState<Record<string, Subscription>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    listSubscriptions()
      .then((list) => setSubs(Object.fromEntries(list.map((s) => [s.id, s]))))
      .catch(() => {});
  }, []);

  useEffect(() => {
    const q = debounced.trim();
    if (!q) {
      setData(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    searchExplain(q)
      .then((res) => {
        if (cancelled) return;
        setData(res);
        setError("");
        setSelectedId(res.items[0]?.item.id ?? null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Search failed");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [debounced]);

  const onChange = (value: string) => {
    setSearch(value);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebounced(value), 300);
  };

  const items = data?.items ?? [];
  const selected = useMemo(
    () => items.find((e) => e.item.id === selectedId) ?? null,
    [items, selectedId],
  );
  const maxScore = items[0]?.score ?? 0;

  return (
    <div className="search-page">
      <div className="search-input-wrap">
        <input
          type="text"
          className="search-input"
          placeholder="Search, then click a result to see why it ranked there..."
          value={search}
          onChange={(e) => onChange(e.target.value)}
          autoFocus
        />
      </div>

      {error && <ErrorBanner error={error} />}

      {!debounced.trim() ? (
        <div className="empty-state">
          <p className="empty-text">Type to search and see how each result was scored.</p>
        </div>
      ) : loading && items.length === 0 ? (
        <p className="loading-text">Searching...</p>
      ) : items.length === 0 ? (
        <div className="empty-state">
          <p className="empty-text">No results for "{debounced}".</p>
        </div>
      ) : (
        <div className="se-layout">
          <div className="se-list">
            <p className="search-result-count">
              Top {items.length} · {data?.mode === "hybrid" ? "hybrid (keyword + semantic)" : "keyword only"}
            </p>
            {items.map((e) => (
              <button
                key={e.item.id}
                className={`se-row${e.item.id === selectedId ? " active" : ""}`}
                onClick={() => setSelectedId(e.item.id)}
              >
                <span className="se-row-rank">{e.rank}</span>
                {e.item.thumbnail_url && (
                  <img
                    className="se-row-thumb"
                    src={e.item.thumbnail_url}
                    alt=""
                    loading="lazy"
                    onError={(ev) => {
                      (ev.target as HTMLImageElement).style.display = "none";
                    }}
                  />
                )}
                <span className="se-row-body">
                  <span className="se-row-title">{e.item.title}</span>
                  <span className="se-row-sub">
                    {subs[e.item.subscription_id]?.name ?? TYPE_LABELS[displayType(e.item)]}
                  </span>
                  <span className="se-row-chips">
                    {e.retrievers.map((r) => (
                      <span
                        key={r.name}
                        className={`se-chip se-seg-${r.name}${r.rank == null ? " off" : ""}`}
                        title={r.label}
                      >
                        {r.name === "lexical" ? "KW" : r.name === "semantic_headline" ? "SH" : "SB"}
                        {r.rank != null ? ` #${r.rank}` : " –"}
                      </span>
                    ))}
                  </span>
                  <ComponentBar value={e.score} max={maxScore} className="se-fill-rrf" />
                </span>
                <span className="se-row-score">{fmt(e.score, data?.mode === "hybrid" ? 4 : 1)}</span>
              </button>
            ))}
            <p className="se-note">KW = keyword · SH = semantic title/summary · SB = semantic full text</p>
          </div>

          <div className="se-pane">
            {selected && data ? (
              <Explanation entry={selected} data={data} />
            ) : (
              <p className="empty-text">Select a result.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
