import { useState } from "react";
import { ErrorBanner } from "./ErrorBanner";
import {
  previewContent,
  confirmContent,
  type ContentPreviewResponse,
  type ContentType,
} from "../api/client";

const TYPE_LABELS: Record<ContentType, string> = {
  video: "YouTube",
  podcast_episode: "Podcast",
  article: "Article",
};

const TYPE_BADGE_CLASS: Record<ContentType, string> = {
  video: "sub-type-youtube_channel",
  podcast_episode: "sub-type-podcast",
  article: "sub-type-rss",
};

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

export function AddContent() {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState<ContentPreviewResponse | null>(null);
  const [titleOverride, setTitleOverride] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");

  const handlePreview = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;
    setLoading(true);
    setPreview(null);
    setError("");
    setConfirmed(false);
    try {
      const result = await previewContent(url.trim());
      setPreview(result);
      setTitleOverride(result.title || "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to preview URL");
    } finally {
      setLoading(false);
    }
  };

  const handleConfirm = async () => {
    if (!preview) return;
    setConfirming(true);
    setError("");
    try {
      await confirmContent(preview.preview_id, titleOverride || undefined);
      setConfirmed(true);
      setPreview(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to submit content");
    } finally {
      setConfirming(false);
    }
  };

  const handleReset = () => {
    setUrl("");
    setPreview(null);
    setTitleOverride("");
    setConfirmed(false);
    setError("");
  };

  return (
    <div className="add-content">
      <h2>Add Content</h2>

      {error && <ErrorBanner error={error} />}

      {confirmed ? (
        <div className="add-content-success">
          <div className="success-icon">&#10003;</div>
          <p>Content submitted! It will appear in your timeline shortly after processing completes.</p>
          <button className="btn btn-primary" onClick={handleReset}>
            Add Another
          </button>
        </div>
      ) : (
        <>
          <div className="add-form">
            <form className="resolve-row" onSubmit={handlePreview}>
              <input
                type="text"
                placeholder="Paste a YouTube URL, podcast MP3 link, or article URL..."
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                autoFocus
              />
              <button
                className="btn btn-primary"
                type="submit"
                disabled={loading || !url.trim()}
              >
                {loading ? "Loading..." : "Preview"}
              </button>
            </form>

            {loading && (
              <div className="resolve-spinner">
                Detecting content type and fetching metadata...
              </div>
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
                    <span className={`sub-type-badge ${TYPE_BADGE_CLASS[preview.detected_type]}`}>
                      {TYPE_LABELS[preview.detected_type]}
                    </span>
                    <input
                      className="preview-name-input"
                      type="text"
                      value={titleOverride}
                      onChange={(e) => setTitleOverride(e.target.value)}
                      placeholder={
                        preview.detected_type === "podcast_episode"
                          ? "Title will be extracted from transcript..."
                          : "Title"
                      }
                    />
                    <div className="preview-meta-row">
                      {preview.author && (
                        <span className="preview-meta-item">{preview.author}</span>
                      )}
                      {preview.duration_seconds != null && (
                        <span className="preview-meta-item">
                          {formatDuration(preview.duration_seconds)}
                        </span>
                      )}
                      {preview.published_at && (
                        <span className="preview-meta-item">
                          {formatDate(preview.published_at)}
                        </span>
                      )}
                      {preview.file_size_bytes != null && (
                        <span className="preview-meta-item">
                          {formatFileSize(preview.file_size_bytes)}
                        </span>
                      )}
                    </div>
                    {preview.description && (
                      <p className="preview-desc">{preview.description}</p>
                    )}
                    <div className="preview-feed-url">
                      URL: <code>{preview.url}</code>
                    </div>
                  </div>
                </div>

                {preview.detected_type === "podcast_episode" && !titleOverride && (
                  <div className="preview-note">
                    Title will be automatically determined from the transcript after processing.
                  </div>
                )}

                <div className="preview-actions">
                  <button
                    className="btn btn-primary"
                    onClick={handleConfirm}
                    disabled={confirming}
                  >
                    {confirming ? "Submitting..." : "Add to Library"}
                  </button>
                  <button className="btn" onClick={handleReset}>
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
