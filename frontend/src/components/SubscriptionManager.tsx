import { useEffect, useState, useCallback, useMemo } from "react";
import { ErrorBanner } from "./ErrorBanner";
import {
  listSubscriptions,
  createSubscription,
  updateSubscription,
  deleteSubscription,
  resolveUrl,
  type Subscription,
  type SubscriptionType,
  type SubscriptionStatus,
  type ResolvedPreview,
} from "../api/client";

const TYPE_LABELS: Record<SubscriptionType, string> = {
  youtube_channel: "YouTube",
  podcast: "Podcast",
  rss: "RSS",
};

const TYPE_FILTERS: { value: SubscriptionType | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "youtube_channel", label: "YouTube" },
  { value: "podcast", label: "Podcasts" },
  { value: "rss", label: "RSS" },
];

const STATUS_FILTERS: { value: SubscriptionStatus | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "muted", label: "Muted" },
  { value: "unfollowed", label: "Unfollowed" },
];

const STATUS_OPTIONS: SubscriptionStatus[] = ["active", "muted", "unfollowed"];

export function SubscriptionManager() {
  const [subs, setSubs] = useState<Subscription[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Filters
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<SubscriptionType | "all">("all");
  const [statusFilter, setStatusFilter] = useState<SubscriptionStatus | "all">("all");

  // Add flow state
  const [showAdd, setShowAdd] = useState(false);
  const [addUrl, setAddUrl] = useState("");
  const [resolving, setResolving] = useState(false);
  const [preview, setPreview] = useState<ResolvedPreview | null>(null);
  const [addNotes, setAddNotes] = useState("");
  const [nameOverride, setNameOverride] = useState("");
  const [adding, setAdding] = useState(false);

  // Edit state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editNotes, setEditNotes] = useState("");
  const [editName, setEditName] = useState("");

  const fetchSubs = useCallback(async () => {
    try {
      setLoading(true);
      const data = await listSubscriptions();
      setSubs(data);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load subscriptions");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSubs();
  }, [fetchSubs]);

  const filtered = useMemo(() => {
    let result = subs;
    if (typeFilter !== "all") {
      result = result.filter((s) => s.type === typeFilter);
    }
    if (statusFilter !== "all") {
      result = result.filter((s) => s.status === statusFilter);
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.url.toLowerCase().includes(q) ||
          s.interest_notes.toLowerCase().includes(q) ||
          s.description.toLowerCase().includes(q),
      );
    }
    return result;
  }, [subs, typeFilter, statusFilter, search]);

  const counts = useMemo(() => {
    const c = { all: subs.length, youtube_channel: 0, podcast: 0, rss: 0 };
    for (const s of subs) c[s.type]++;
    return c;
  }, [subs]);

  const handleLookup = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!addUrl.trim()) return;
    setResolving(true);
    setPreview(null);
    setError("");
    try {
      const result = await resolveUrl(addUrl.trim());
      setPreview(result);
      setNameOverride(result.name);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not resolve URL");
    } finally {
      setResolving(false);
    }
  };

  const handleConfirmAdd = async () => {
    if (!preview) return;
    setAdding(true);
    try {
      await createSubscription({
        url: preview.feed_url,
        name: nameOverride || preview.name,
        type: preview.type,
        description: preview.description,
        interest_notes: addNotes.trim(),
      });
      setAddUrl("");
      setPreview(null);
      setAddNotes("");
      setNameOverride("");
      setShowAdd(false);
      fetchSubs();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add subscription");
    } finally {
      setAdding(false);
    }
  };

  const handleCancelAdd = () => {
    setShowAdd(false);
    setAddUrl("");
    setPreview(null);
    setAddNotes("");
    setNameOverride("");
    setError("");
  };

  const handleStatusChange = async (sub: Subscription, status: SubscriptionStatus) => {
    try {
      await updateSubscription(sub.id, { status });
      fetchSubs();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update");
    }
  };

  const handleSaveEdit = async (id: string) => {
    try {
      await updateSubscription(id, {
        name: editName,
        interest_notes: editNotes,
      });
      setEditingId(null);
      fetchSubs();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save");
    }
  };

  const handleDelete = async (sub: Subscription) => {
    if (!confirm(`Delete "${sub.name}"? This cannot be undone.`)) return;
    try {
      await deleteSubscription(sub.id);
      setSubs((prev) => prev.filter((s) => s.id !== sub.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete");
    }
  };

  const startEdit = (sub: Subscription) => {
    setEditingId(sub.id);
    setEditName(sub.name);
    setEditNotes(sub.interest_notes);
  };

  return (
    <div className="subscriptions">
      <div className="subs-header">
        <h2>Subscriptions</h2>
        <button className="btn btn-primary" onClick={() => (showAdd ? handleCancelAdd() : setShowAdd(true))}>
          {showAdd ? "Cancel" : "+ Add Subscription"}
        </button>
      </div>

      {error && <ErrorBanner error={error} />}

      {showAdd && (
        <div className="add-form">
          <form className="resolve-row" onSubmit={handleLookup}>
            <input
              type="text"
              placeholder="Paste any URL — YouTube channel, podcast page, website, or feed XML..."
              value={addUrl}
              onChange={(e) => setAddUrl(e.target.value)}
              autoFocus
            />
            <button className="btn btn-primary" type="submit" disabled={resolving || !addUrl.trim()}>
              {resolving ? "Looking up..." : "Look up"}
            </button>
          </form>

          {resolving && <div className="resolve-spinner">Detecting feed type and fetching metadata...</div>}

          {preview && (
            <div className="preview-card">
              <div className="preview-main">
                {preview.thumbnail_url && (
                  <img
                    className="preview-thumb"
                    src={preview.thumbnail_url}
                    alt=""
                    onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                  />
                )}
                <div className="preview-info">
                  <span className={`sub-type-badge sub-type-${preview.type}`}>{TYPE_LABELS[preview.type]}</span>
                  <input
                    className="preview-name-input"
                    type="text"
                    value={nameOverride}
                    onChange={(e) => setNameOverride(e.target.value)}
                  />
                  {preview.description && (
                    <p className="preview-desc">{preview.description}</p>
                  )}
                  <div className="preview-feed-url">
                    Feed: <code>{preview.feed_url}</code>
                  </div>
                </div>
              </div>

              {preview.sample_items.length > 0 ? (
                <div className="preview-samples">
                  <div className="preview-samples-label">Recent items:</div>
                  <ul>
                    {preview.sample_items.map((item, i) => (
                      <li key={i}>
                        <span className="sample-title">{item.title}</span>
                        {item.published && (
                          <span className="sample-date">{item.published}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : preview.type === "rss" && (
                <div className="preview-warning">
                  No RSS or Atom feed was found at this URL. You can still subscribe, but polling may not find any articles.
                </div>
              )}

              <textarea
                placeholder="Interest notes (optional) — guide AI curation, e.g. 'only reviews, skip interviews'"
                value={addNotes}
                onChange={(e) => setAddNotes(e.target.value)}
                rows={2}
              />

              <div className="preview-actions">
                <button className="btn btn-primary" onClick={handleConfirmAdd} disabled={adding}>
                  {adding ? "Adding..." : "Subscribe"}
                </button>
                <button className="btn" onClick={handleCancelAdd}>Cancel</button>
              </div>
            </div>
          )}
        </div>
      )}

      {!loading && subs.length > 0 && (
        <div className="subs-filters">
          <input
            type="text"
            className="subs-search"
            placeholder="Search subscriptions..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="filter-pills">
            {TYPE_FILTERS.map((f) => (
              <button
                key={f.value}
                className={`filter-pill filter-pill-type-${f.value}${typeFilter === f.value ? " active" : ""}`}
                onClick={() => setTypeFilter(f.value)}
              >
                {f.label}
                <span className="filter-count">
                  {f.value === "all" ? counts.all : counts[f.value]}
                </span>
              </button>
            ))}
          </div>
          <div className="filter-pills">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.value}
                className={`filter-pill${statusFilter === f.value ? " active" : ""}`}
                onClick={() => setStatusFilter(f.value)}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {loading ? (
        <p className="loading-text">Loading subscriptions...</p>
      ) : subs.length === 0 ? (
        <p className="empty-text">No subscriptions yet. Add one to get started.</p>
      ) : filtered.length === 0 ? (
        <p className="empty-text">No subscriptions match your filters.</p>
      ) : (
        <ul className="sub-list">
          {filtered.map((sub) => (
            <li key={sub.id} className={`sub-card sub-status-${sub.status} sub-card-type-${sub.type}`}>
              {editingId === sub.id ? (
                <div className="sub-edit">
                  <input
                    type="text"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    className="edit-name"
                  />
                  <textarea
                    value={editNotes}
                    onChange={(e) => setEditNotes(e.target.value)}
                    placeholder="Interest notes..."
                    rows={2}
                  />
                  <div className="sub-edit-actions">
                    <button className="btn btn-primary" onClick={() => handleSaveEdit(sub.id)}>
                      Save
                    </button>
                    <button className="btn" onClick={() => setEditingId(null)}>
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="sub-info">
                    <div className="sub-top-row">
                      <span className={`sub-type-badge sub-type-${sub.type}`}>{TYPE_LABELS[sub.type]}</span>
                      <h3 className="sub-name">{sub.name}</h3>
                    </div>
                    <div className="sub-url">{sub.url}</div>
                    {sub.interest_notes && (
                      <div className="sub-notes">{sub.interest_notes}</div>
                    )}
                    <div className="sub-meta">
                      <span className={`status-badge status-${sub.status}`}>{sub.status}</span>
                      <span className="sub-content-count">{sub.content_count} items</span>
                      {sub.last_polled_at && (
                        <span className="sub-polled">
                          Polled: {new Date(sub.last_polled_at).toLocaleString()}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="sub-actions">
                    <select
                      value={sub.status}
                      onChange={(e) =>
                        handleStatusChange(sub, e.target.value as SubscriptionStatus)
                      }
                    >
                      {STATUS_OPTIONS.map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                    <button className="btn" onClick={() => startEdit(sub)}>
                      Edit
                    </button>
                    <button className="btn btn-danger" onClick={() => handleDelete(sub)}>
                      Delete
                    </button>
                  </div>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
