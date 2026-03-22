import { useState } from "react";
import { previewContent, ingestContent, type ContentPreview } from "../api/client";
import { ErrorBanner } from "./ErrorBanner";

const TYPE_LABELS: Record<string, string> = {
  video: "YouTube Video",
  podcast_episode: "Podcast Episode",
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
  const [preview, setPreview] = useState<ContentPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [ingesting, setIngesting] = useState(false);
  const [ingestStatus, setIngestStatus] = useState<"idle" | "queued" | "exists">("idle");
  const [error, setError] = useState("");

  const handlePreview = async () => {
    const trimmed = url.trim();
    if (!trimmed) return;
    setPreviewing(true);
    setError("");
    setPreview(null);
    setIngestStatus("idle");
    try {
      const result = await previewContent(trimmed);
      setPreview(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to preview URL");
    } finally {
      setPreviewing(false);
    }
  };

  const handleIngest = async () => {
    if (!preview) return;
    setIngesting(true);
    setError("");
    try {
      const result = await ingestContent(preview.url);
      setIngestStatus(result.status === "already_exists" ? "exists" : "queued");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to ingest content");
    } finally {
      setIngesting(false);
    }
  };

  const handleReset = () => {
    setUrl("");
    setPreview(null);
    setIngestStatus("idle");
    setError("");
  };

  return (
    <div className="add-content-page">
      <h2>Add Content</h2>
      <p className="add-content-hint">
        Paste a URL to a YouTube video, podcast episode, or web article to ingest it individually
        without subscribing to the full feed.
      </p>

      {error && <ErrorBanner error={error} />}

      {ingestStatus !== "idle" ? (
        <div className="add-content-result">
          {ingestStatus === "queued" ? (
            <p className="add-content-queued">
              Content is being processed and will appear on your timeline when ready.
            </p>
          ) : (
            <p className="add-content-exists">
              This content has already been ingested and is available on your timeline.
            </p>
          )}
          <button className="btn" onClick={handleReset}>Add Another</button>
        </div>
      ) : (
        <>
          <div className="add-content-input-row">
            <input
              type="url"
              className="add-content-url-input"
              placeholder="https://..."
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handlePreview(); }}
              disabled={previewing}
            />
            <button
              className="btn btn-primary"
              onClick={handlePreview}
              disabled={previewing || !url.trim()}
            >
              {previewing ? "Loading…" : "Preview"}
            </button>
          </div>

          {preview && (
            <div className="add-content-preview">
              <div className="add-content-preview-card">
                {preview.thumbnail_url && (
                  <img
                    className="add-content-thumb"
                    src={preview.thumbnail_url}
                    alt=""
                    onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                  />
                )}
                <div className="add-content-preview-body">
                  <div className="add-content-preview-top">
                    <span className={`content-type-badge content-type-${preview.detected_type}`}>
                      {TYPE_LABELS[preview.detected_type] ?? preview.detected_type}
                    </span>
                    {preview.duration_seconds && (
                      <span className="add-content-duration">{formatDuration(preview.duration_seconds)}</span>
                    )}
                  </div>
                  <h3 className="add-content-title">{preview.title}</h3>
                  <div className="add-content-meta">
                    {preview.source_name && <span className="content-source">{preview.source_name}</span>}
                    {preview.author && preview.author !== preview.source_name && (
                      <span className="add-content-author">by {preview.author}</span>
                    )}
                    {preview.published_date && (
                      <span className="content-date">{new Date(preview.published_date).toLocaleDateString()}</span>
                    )}
                  </div>
                  {preview.description && (
                    <p className="add-content-desc">
                      {preview.description.length > 300
                        ? preview.description.slice(0, 300) + "…"
                        : preview.description}
                    </p>
                  )}
                  {preview.already_ingested_id ? (
                    <p className="add-content-exists-note">
                      This content has already been ingested.
                    </p>
                  ) : (
                    <button
                      className="btn btn-primary"
                      onClick={handleIngest}
                      disabled={ingesting}
                    >
                      {ingesting ? "Ingesting…" : "Ingest"}
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
