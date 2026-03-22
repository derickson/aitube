import { useState } from "react";
import { ErrorBanner } from "./ErrorBanner";
import { previewContent, ingestContent, type ContentPreview } from "../api/client";

const TYPE_LABELS: Record<string, string> = {
  video: "YouTube",
  podcast_episode: "Podcast",
  article: "Article",
};

function formatDuration(seconds: number | null): string {
  if (!seconds) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function AddContent() {
  const [url, setUrl] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<ContentPreview | null>(null);
  const [ingesting, setIngesting] = useState(false);
  const [ingestQueued, setIngestQueued] = useState(false);
  const [error, setError] = useState("");

  const handlePreview = async (e?: React.FormEvent) => {
    e?.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;
    setPreviewing(true);
    setPreview(null);
    setError("");
    setIngestQueued(false);
    try {
      const result = await previewContent(trimmed);
      setPreview(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not fetch preview");
    } finally {
      setPreviewing(false);
    }
  };

  const handleIngest = async () => {
    if (!preview) return;
    setIngesting(true);
    setError("");
    try {
      await ingestContent(preview.url);
      setIngestQueued(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ingest request failed");
    } finally {
      setIngesting(false);
    }
  };

  const handleReset = () => {
    setUrl("");
    setPreview(null);
    setIngestQueued(false);
    setError("");
  };

  const contentTypeClass =
    preview?.type === "video"
      ? "sub-type-youtube_channel"
      : preview?.type === "podcast_episode"
        ? "sub-type-podcast"
        : "sub-type-rss";

  return (
    <div className="subscriptions">
      <div className="subs-header">
        <h2>Add Individual Content</h2>
      </div>
      <p className="add-content-intro">
        Paste a URL to add a single article, YouTube video, or podcast episode to your timeline.
        The source will be grouped automatically — no subscription is created.
      </p>

      {error && <ErrorBanner error={error} />}

      {!ingestQueued ? (
        <>
          <form className="resolve-row" onSubmit={handlePreview}>
            <input
              type="url"
              placeholder="https://..."
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                if (preview) setPreview(null);
              }}
              autoFocus
            />
            <button
              className="btn btn-primary"
              type="submit"
              disabled={previewing || !url.trim()}
            >
              {previewing ? "Checking..." : "Preview"}
            </button>
          </form>

          {previewing && (
            <div className="resolve-spinner">Fetching metadata…</div>
          )}

          {preview && (
            <div className="preview-card">
              <div className="preview-main">
                {preview.thumbnail_url && (
                  <img
                    className="preview-thumb"
                    src={preview.thumbnail_url}
                    alt=""
                    onError={(e) => {
                      (e.target as HTMLImageElement).style.display = "none";
                    }}
                  />
                )}
                <div className="preview-info">
                  <span className={`sub-type-badge ${contentTypeClass}`}>
                    {TYPE_LABELS[preview.type] ?? preview.type}
                  </span>
                  <div className="preview-name-input" style={{ fontWeight: 600, fontSize: "1.05rem", marginBottom: "0.25rem" }}>
                    {preview.title}
                  </div>
                  <div className="preview-feed-url">
                    Source:{" "}
                    <code>{preview.source_name || preview.source_url}</code>
                  </div>
                  {preview.author && preview.author !== preview.source_name && (
                    <div className="preview-feed-url">Author: <code>{preview.author}</code></div>
                  )}
                  {preview.duration_seconds && (
                    <div className="preview-feed-url">
                      Duration: <code>{formatDuration(preview.duration_seconds)}</code>
                    </div>
                  )}
                  {preview.published_at && (
                    <div className="preview-feed-url">
                      Published: <code>{new Date(preview.published_at).toLocaleDateString()}</code>
                    </div>
                  )}
                  {preview.description && (
                    <p className="preview-desc">
                      {preview.description.length > 240
                        ? preview.description.slice(0, 240) + "…"
                        : preview.description}
                    </p>
                  )}
                </div>
              </div>

              <div className="preview-actions">
                <button
                  className="btn btn-primary"
                  onClick={handleIngest}
                  disabled={ingesting}
                >
                  {ingesting ? "Queueing…" : "Ingest"}
                </button>
                <button className="btn" onClick={handleReset}>
                  Cancel
                </button>
              </div>
            </div>
          )}
        </>
      ) : (
        <div className="preview-card">
          <div style={{ padding: "1rem 0" }}>
            <strong>Content queued for ingestion.</strong>
            <p style={{ marginTop: "0.5rem", color: "var(--text-muted, #888)" }}>
              Processing is running in the background — the item will appear on your Timeline when it's ready.
              This may take a minute for videos and podcasts.
            </p>
            {preview && (
              <div style={{ marginTop: "0.75rem", fontStyle: "italic" }}>
                "{preview.title}"
              </div>
            )}
          </div>
          <div className="preview-actions">
            <button className="btn btn-primary" onClick={handleReset}>
              Add Another
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
