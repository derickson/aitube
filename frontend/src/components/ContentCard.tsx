import type {
  ContentItemSummary,
  ContentType,
  PlaybackProgress,
} from "../api/client";

export const TYPE_LABELS: Record<ContentType, string> = {
  video: "YouTube",
  podcast_episode: "Podcast",
  article: "Article",
};

export function formatDate(dateStr: string | null): string {
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

export function formatDuration(seconds: number | null): string {
  if (!seconds) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function ContentCard({
  item,
  subName,
  isActive,
  isConsumed,
  progress,
  onSelect,
  onInterest,
  onToggleConsumed,
  showActions = true,
}: {
  item: ContentItemSummary;
  subName: string;
  isActive: boolean;
  isConsumed: boolean;
  progress?: PlaybackProgress;
  onSelect: () => void;
  onInterest?: (itemId: string, value: "up" | "down" | "none") => void;
  onToggleConsumed?: (itemId: string, consumed: boolean, callApi: boolean) => void;
  showActions?: boolean;
}) {
  const description = item.summary || "";

  const classes = [
    "content-card",
    `content-card-type-${item.type}`,
    isActive && "content-card-active",
    isConsumed && "content-card-consumed",
    item.user_interest === "down" && "content-card-downvoted",
  ].filter(Boolean).join(" ");

  const handleInterestClick = (e: React.MouseEvent, value: "up" | "down") => {
    e.stopPropagation();
    onInterest?.(item.id, item.user_interest === value ? "none" : value);
  };

  const handleConsumedClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onToggleConsumed?.(item.id, !isConsumed, true);
  };

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
          {showActions && progress && progress.percent > 0 && progress.percent < 100 && (
            <span className="content-progress">{progress.percent}%</span>
          )}
        </div>
      )}
      <div className="content-body">
        <div className="content-top-row">
          <span className={`content-type-badge content-type-${item.type}`}>
            {TYPE_LABELS[item.type]}
          </span>
          {showActions && (
            <span className="content-interest-btns">
              <button
                className={`interest-btn interest-consumed${isConsumed ? " active" : ""}`}
                onClick={handleConsumedClick}
                title={isConsumed ? "Mark unwatched" : "Mark viewed"}
              >
                &#10003;
              </button>
              <button
                className={`interest-btn interest-up${item.user_interest === "up" ? " active" : ""}`}
                onClick={(e) => handleInterestClick(e, "up")}
                title="Interesting"
              >
                +
              </button>
              <button
                className={`interest-btn interest-down${item.user_interest === "down" ? " active" : ""}`}
                onClick={(e) => handleInterestClick(e, "down")}
                title="Not interested"
              >
                -
              </button>
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
