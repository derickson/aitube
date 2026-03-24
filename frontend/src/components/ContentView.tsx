import { useEffect, useState, useCallback, useRef } from "react";
import { ErrorBanner } from "./ErrorBanner";
import { ContentTabs } from "./ContentTabs";
import { ChatPanel } from "./ChatPanel";
import {
  getContentItem,
  getPlayback,
  updatePlayback,
  setConsumed as apiSetConsumed,
  type ContentItem,
  type PlaybackState,
} from "../api/client";

function formatTimestamp(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function renderInlineMarkdown(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, "<em>$1</em>")
    .replace(/_(.+?)_/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g, '<span class="summary-timestamp-link" data-timestamp="$1">[$1]</span>')
    .replace(/\n\n+/g, "</p><p>")
    .replace(/\n/g, "<br>")
    .replace(/^/, "<p>")
    .replace(/$/, "</p>");
}

function parseTimestamp(ts: string): number {
  const parts = ts.split(":").map(Number);
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  return parts[0] * 60 + parts[1];
}

interface Props {
  itemId: string;
  subName?: string;
  onClose: () => void;
  onConsumedChange?: (itemId: string, consumed: boolean) => void;
}

function formatPublishDate(dateStr: string | null): string {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  return d.toLocaleDateString("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function ContentView({ itemId, subName, onClose, onConsumedChange }: Props) {
  const [item, setItem] = useState<ContentItem | null>(null);
  const [playback, setPlayback] = useState<PlaybackState | null>(null);
  const [consumed, setConsumed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [currentTime, setCurrentTime] = useState(0);
  const [activeTab, setActiveTab] = useState("summary");
  const panelRef = useRef<HTMLDivElement>(null);
  const audioSeekRef = useRef<((time: number) => void) | null>(null);
  const videoSeekRef = useRef<((time: number) => void) | null>(null);

  // Keep flyout height matched to visible viewport
  useEffect(() => {
    const el = panelRef.current;
    if (!el) return;
    const update = () => {
      const rect = el.getBoundingClientRect();
      el.style.height = `${window.innerHeight - rect.top}px`;
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update, { passive: true });
    return () => {
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, [itemId, loading]);

  useEffect(() => {
    setLoading(true);
    Promise.all([getContentItem(itemId), getPlayback(itemId)])
      .then(([contentData, playbackData]) => {
        setItem(contentData);
        setPlayback(playbackData);
        setConsumed(contentData.consumed ?? false);
        setError("");
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [itemId]);

  // Scroll panel into view and reset tab when opened
  useEffect(() => {
    panelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    setActiveTab("summary");
  }, [itemId]);

  if (loading) {
    return (
      <div className="content-panel" ref={panelRef}>
        <p className="loading-text">Loading...</p>
      </div>
    );
  }
  if (error) {
    return (
      <div className="content-panel" ref={panelRef}>
        <ErrorBanner error={error} />
      </div>
    );
  }
  if (!item) return null;

  const handleMarkViewed = async () => {
    const newConsumed = !consumed;
    await apiSetConsumed(itemId, newConsumed).catch(() => {});
    setConsumed(newConsumed);
    onConsumedChange?.(itemId, newConsumed);
  };

  const hasTranscript = item.transcript && (item.transcript.text || item.transcript.chunks?.length > 0);

  const description =
    (item.metadata as Record<string, unknown>)?.description?.toString() || "";

  return (
    <aside className="flyout" ref={panelRef}>
      <div className="flyout-sticky">
        <div className="flyout-header">
          <div>
            <h2 className="flyout-title">{item.title}</h2>
            <p className="flyout-meta">
              {subName && <span className="flyout-meta-source">{subName}</span>}
              {subName && item.published_at && <span className="flyout-meta-sep"> · </span>}
              {item.published_at && <span>{formatPublishDate(item.published_at)}</span>}
            </p>
          </div>
          <div className="flyout-actions">
            <a href={item.url} target="_blank" rel="noopener noreferrer" className="btn">
              Original
            </a>
            <button
              className={`btn ${consumed ? "btn-viewed" : "btn-primary"}`}
              onClick={handleMarkViewed}
            >
              {consumed ? "Viewed" : "Mark Viewed"}
            </button>
            <button className="btn" onClick={onClose}>Close</button>
          </div>
        </div>
        {description && <p className="flyout-desc">{description}</p>}

        {item.type === "video" && (
          <YouTubePlayer
            item={item}
            initialPosition={playback?.position_seconds ?? 0}
            seekRef={videoSeekRef}
            onTimeUpdate={setCurrentTime}
            onConsumed={() => { setConsumed(true); onConsumedChange?.(itemId, true); }}
          />
        )}
        {item.type === "podcast_episode" && (
          <AudioPlayer
            item={item}
            initialPosition={playback?.position_seconds ?? 0}
            seekRef={audioSeekRef}
            onTimeUpdate={setCurrentTime}
            onConsumed={() => { setConsumed(true); onConsumedChange?.(itemId, true); }}
          />
        )}
      </div>

      <div className={`flyout-scroll${activeTab === "chat" ? " chat-active" : ""}`}>
        <ContentTabs
          tabs={[
            { id: "summary", label: "Summary" },
            ...(hasTranscript ? [{ id: "transcript", label: "Transcript" }] : []),
            { id: "chat", label: "Chat" },
          ]}
          activeTab={activeTab}
          onTabChange={setActiveTab}
        >
          <div style={{ display: activeTab === "summary" ? "block" : "none" }}>
            {item.summary && (
              <div className="flyout-summary">
                <h3 className="flyout-summary-heading">AI Summary</h3>
                <div
                  className="flyout-summary-text"
                  dangerouslySetInnerHTML={{ __html: renderInlineMarkdown(item.summary) }}
                  onClick={(e) => {
                    const target = e.target as HTMLElement;
                    const ts = target.dataset?.timestamp;
                    if (ts) {
                      const seconds = parseTimestamp(ts);
                      if (item.type === "podcast_episode") {
                        audioSeekRef.current?.(seconds);
                      } else if (item.type === "video") {
                        videoSeekRef.current?.(seconds);
                      }
                    }
                  }}
                />
              </div>
            )}

            {item.type === "article" && (
              <ArticleReader item={item} onConsumed={() => {
                setConsumed(true);
                onConsumedChange?.(itemId, true);
              }} />
            )}

            <div className="flyout-actions flyout-actions-bottom">
              <a href={item.url} target="_blank" rel="noopener noreferrer" className="btn">
                Original
              </a>
              <button
                className={`btn ${consumed ? "btn-viewed" : "btn-primary"}`}
                onClick={handleMarkViewed}
              >
                {consumed ? "Viewed" : "Mark Viewed"}
              </button>
              <button className="btn" onClick={onClose}>Close</button>
            </div>
          </div>

          {hasTranscript && item.transcript && (
            <div style={{ display: activeTab === "transcript" ? "block" : "none" }}>
              <TranscriptViewer
                transcript={item.transcript}
                currentTime={currentTime}
                onSeek={(time) => {
                  if (item.type === "podcast_episode") {
                    audioSeekRef.current?.(time);
                  } else if (item.type === "video") {
                    videoSeekRef.current?.(time);
                  }
                }}
              />
            </div>
          )}

          <div style={{ display: activeTab === "chat" ? "flex" : "none" }} className="chat-tab-panel">
            <ChatPanel
              item={item}
              onSeek={(time) => {
                if (item.type === "podcast_episode") {
                  audioSeekRef.current?.(time);
                } else if (item.type === "video") {
                  videoSeekRef.current?.(time);
                }
              }}
            />
          </div>
        </ContentTabs>
      </div>
    </aside>
  );
}

// --- YouTube Player ---

function extractVideoId(url: string): string | null {
  const m = url.match(/(?:v=|youtu\.be\/)([\w-]+)/);
  return m ? m[1] : null;
}

function YouTubePlayer({
  item,
  initialPosition,
  seekRef,
  onTimeUpdate,
  onConsumed,
}: {
  item: ContentItem;
  initialPosition: number;
  seekRef?: React.MutableRefObject<((time: number) => void) | null>;
  onTimeUpdate?: (time: number) => void;
  onConsumed?: () => void;
}) {
  const videoId = extractVideoId(item.url);
  const playerRef = useRef<YT.Player | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const timeIntervalRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const [ready, setReady] = useState(false);
  const consumedFiredRef = useRef(false);
  const onConsumedRef = useRef(onConsumed);
  onConsumedRef.current = onConsumed;

  // Expose seek function to parent via ref
  useEffect(() => {
    if (seekRef) {
      seekRef.current = (time: number) => {
        const player = playerRef.current;
        if (player && typeof player.seekTo === "function") {
          player.seekTo(time, true);
        }
      };
      return () => { seekRef.current = null; };
    }
  }, [seekRef]);

  useEffect(() => {
    if ((window as unknown as Record<string, unknown>).YT) {
      setReady(true);
      return;
    }
    const tag = document.createElement("script");
    tag.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(tag);
    (window as unknown as Record<string, () => void>).onYouTubeIframeAPIReady = () => {
      setReady(true);
    };
  }, []);

  useEffect(() => {
    if (!ready || !videoId) return;

    playerRef.current = new YT.Player("yt-player", {
      videoId,
      playerVars: {
        start: Math.floor(initialPosition),
        autoplay: 1,
        rel: 0,
      },
      events: {
        onStateChange: (event: YT.OnStateChangeEvent) => {
          if (event.data === YT.PlayerState.PLAYING) {
            startTracking();
            startTimeUpdates();
          } else {
            stopTracking();
            stopTimeUpdates();
            reportPosition();
            if (event.data === YT.PlayerState.ENDED && !consumedFiredRef.current) {
              consumedFiredRef.current = true;
              apiSetConsumed(item.id, true).catch(() => {});
              onConsumedRef.current?.();
            }
          }
        },
      },
    });

    return () => {
      stopTracking();
      stopTimeUpdates();
      playerRef.current?.destroy();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, videoId]);

  const reportPosition = useCallback(() => {
    const player = playerRef.current;
    if (!player || typeof player.getCurrentTime !== "function") return;
    const pos = player.getCurrentTime();
    const dur = typeof player.getDuration === "function" ? player.getDuration() : undefined;
    if (pos > 0) updatePlayback(item.id, pos, dur && dur > 0 ? dur : undefined).catch(() => {});
  }, [item.id]);

  const startTracking = useCallback(() => {
    stopTracking();
    intervalRef.current = setInterval(reportPosition, 5000);
  }, [reportPosition]);

  const stopTracking = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = undefined;
    }
  }, []);

  const startTimeUpdates = useCallback(() => {
    stopTimeUpdates();
    timeIntervalRef.current = setInterval(() => {
      const player = playerRef.current;
      if (player && typeof player.getCurrentTime === "function") {
        const current = player.getCurrentTime();
        onTimeUpdate?.(current);
        if (!consumedFiredRef.current && typeof player.getDuration === "function") {
          const duration = player.getDuration();
          if (duration > 0 && current / duration >= 0.9) {
            consumedFiredRef.current = true;
            apiSetConsumed(item.id, true).catch(() => {});
            onConsumedRef.current?.();
          }
        }
      }
    }, 500);
  }, [onTimeUpdate, item.id]);

  const stopTimeUpdates = useCallback(() => {
    if (timeIntervalRef.current) {
      clearInterval(timeIntervalRef.current);
      timeIntervalRef.current = undefined;
    }
  }, []);

  if (!videoId) return <p>Could not extract video ID from URL.</p>;

  return (
    <div className="yt-wrapper">
      <div id="yt-player" />
    </div>
  );
}

// --- Audio Player ---

function AudioPlayer({
  item,
  initialPosition,
  seekRef,
  onTimeUpdate,
  onConsumed,
}: {
  item: ContentItem;
  initialPosition: number;
  seekRef?: React.MutableRefObject<((time: number) => void) | null>;
  onTimeUpdate?: (time: number) => void;
  onConsumed?: () => void;
}) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const hasSeekRef = useRef(false);
  const consumedFiredRef = useRef(false);
  const onConsumedRef = useRef(onConsumed);
  onConsumedRef.current = onConsumed;

  const extras = (item.metadata as Record<string, Record<string, unknown>>)?.extras;
  const audioUrl = extras?.enclosure_url ? String(extras.enclosure_url) : item.url;

  // Expose seek function to parent via ref
  useEffect(() => {
    if (seekRef) {
      seekRef.current = (time: number) => {
        const audio = audioRef.current;
        if (audio) {
          audio.currentTime = time;
          audio.play().catch(() => {});
        }
      };
      return () => { seekRef.current = null; };
    }
  }, [seekRef]);

  const reportPosition = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || audio.paused) return;
    const dur = audio.duration && isFinite(audio.duration) ? audio.duration : undefined;
    if (audio.currentTime > 0) updatePlayback(item.id, audio.currentTime, dur).catch(() => {});
  }, [item.id]);

  const startTracking = useCallback(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    intervalRef.current = setInterval(reportPosition, 5000);
  }, [reportPosition]);

  const stopTracking = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = undefined;
    }
  }, []);

  const handleLoadedMetadata = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (initialPosition > 0 && !hasSeekRef.current) {
      audio.currentTime = initialPosition;
      hasSeekRef.current = true;
    }
    // Autoplay
    audio.play().catch(() => {});
  }, [initialPosition]);

  useEffect(() => {
    return () => stopTracking();
  }, [stopTracking]);

  const formatTime = (s: number) => {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = Math.floor(s % 60);
    if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
    return `${m}:${String(sec).padStart(2, "0")}`;
  };

  return (
    <div className="audio-container">
      {item.thumbnail_url && (
        <img
          className="audio-thumb"
          src={item.thumbnail_url}
          alt=""
          onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
        />
      )}
      <audio
        ref={audioRef}
        src={audioUrl}
        controls
        className="audio-player"
        onLoadedMetadata={handleLoadedMetadata}
        onPlay={startTracking}
        onPause={() => { stopTracking(); reportPosition(); }}
        onEnded={() => {
          stopTracking(); reportPosition();
          if (!consumedFiredRef.current) {
            consumedFiredRef.current = true;
            apiSetConsumed(item.id, true).catch(() => {});
            onConsumedRef.current?.();
          }
        }}
        onTimeUpdate={() => {
          if (audioRef.current) {
            const current = audioRef.current.currentTime;
            const duration = audioRef.current.duration;
            onTimeUpdate?.(current);
            if (!consumedFiredRef.current && duration > 0 && current / duration >= 0.9) {
              consumedFiredRef.current = true;
              apiSetConsumed(item.id, true).catch(() => {});
              onConsumedRef.current?.();
            }
          }
        }}
      />
      {initialPosition > 0 && (
        <div className="audio-resume-hint">Resuming from {formatTime(initialPosition)}</div>
      )}
    </div>
  );
}

// --- Article Reader ---

function TranscriptViewer({
  transcript,
  currentTime = 0,
  onSeek,
}: {
  transcript: { text: string; chunks: { text: string; start: number; end: number }[] };
  currentTime?: number;
  onSeek?: (time: number) => void;
}) {
  const activeRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const lastScrolledIndex = useRef(-1);

  // Find the active chunk index
  const activeIndex = transcript.chunks?.findIndex((chunk, i) => {
    const next = transcript.chunks[i + 1];
    return currentTime >= chunk.start && (!next || currentTime < next.start);
  }) ?? -1;

  // Auto-scroll to active chunk, or to top if playback hasn't reached first chunk.
  // Use manual scroll on the nearest scrollable ancestor rather than scrollIntoView,
  // which would propagate to all ancestor scroll containers (including the page) and
  // cause the left timeline panel to scroll unexpectedly.
  useEffect(() => {
    if (activeIndex >= 0 && activeIndex !== lastScrolledIndex.current && activeRef.current) {
      lastScrolledIndex.current = activeIndex;
      const activeEl = activeRef.current;
      // Find the nearest scrollable ancestor
      let scrollParent: Element | null = activeEl.parentElement;
      while (scrollParent && scrollParent !== document.documentElement) {
        const overflow = getComputedStyle(scrollParent).overflowY;
        if (overflow === "auto" || overflow === "scroll") break;
        scrollParent = scrollParent.parentElement;
      }
      if (scrollParent && scrollParent !== document.documentElement) {
        const parentRect = scrollParent.getBoundingClientRect();
        const activeRect = activeEl.getBoundingClientRect();
        const offset =
          activeRect.top - parentRect.top - scrollParent.clientHeight / 2 + activeEl.clientHeight / 2;
        scrollParent.scrollBy({ top: offset, behavior: "smooth" });
      }
    } else if (activeIndex === -1 && lastScrolledIndex.current === -1 && containerRef.current) {
      containerRef.current.scrollTop = 0;
    }
  }, [activeIndex]);

  return (
    <div className="transcript-viewer">
      <h3 className="transcript-heading">Transcript</h3>
      {transcript.chunks && transcript.chunks.length > 0 ? (
        <div className="transcript-chunks" ref={containerRef}>
          {transcript.chunks.map((chunk, i) => (
            <div
              key={i}
              ref={i === activeIndex ? activeRef : undefined}
              className={[
                "transcript-chunk",
                onSeek && "transcript-chunk-clickable",
                i === activeIndex && "transcript-chunk-active",
              ].filter(Boolean).join(" ")}
              onClick={() => onSeek?.(chunk.start)}
            >
              <span className="transcript-time">
                {formatTimestamp(chunk.start)}
              </span>
              <span className="transcript-text">{chunk.text}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="transcript-plain">{transcript.text}</p>
      )}
    </div>
  );
}

function upgradeImageUrl(url: string): string {
  if (url.includes("storage.googleapis.com/")) {
    return url.replace(/\.width-\d+\./, ".width-800.");
  }
  return url;
}

function ReaderImage({ src, alt }: { src: string; alt: string }) {
  const upgraded = upgradeImageUrl(src);
  const needsFallback = upgraded !== src;
  return (
    <img
      src={upgraded}
      alt={alt}
      className="reader-img"
      {...(needsFallback && {
        onError: (e) => { (e.target as HTMLImageElement).src = src; },
      })}
    />
  );
}

function ArticleReader({ item, onConsumed }: { item: ContentItem; onConsumed?: () => void }) {
  const onConsumedRef = useRef(onConsumed);
  onConsumedRef.current = onConsumed;

  useEffect(() => {
    if (item.id) {
      apiSetConsumed(item.id, true).catch(() => {});
      onConsumedRef.current?.();
    }
  }, [item.id]);

  const markdown = item.content_markdown;
  const description =
    (item.metadata as Record<string, unknown>)?.description?.toString() || "";

  const renderContent = (text: string) => {
    if (!text) return null;

    // Split on fenced code blocks first, preserving them as separate segments
    const segments: { type: "text" | "code"; content: string }[] = [];
    const codeBlockRegex = /```(?:\w*)\n?([\s\S]*?)```/g;
    let lastIndex = 0;
    let match;

    while ((match = codeBlockRegex.exec(text)) !== null) {
      if (match.index > lastIndex) {
        segments.push({ type: "text", content: text.slice(lastIndex, match.index) });
      }
      segments.push({ type: "code", content: match[1] });
      lastIndex = match.index + match[0].length;
    }
    if (lastIndex < text.length) {
      segments.push({ type: "text", content: text.slice(lastIndex) });
    }

    const result: React.ReactNode[] = [];
    let key = 0;

    for (const segment of segments) {
      if (segment.type === "code") {
        result.push(
          <pre key={key++} className="reader-code-block">
            <code>{segment.content.trimEnd()}</code>
          </pre>
        );
      } else {
        for (const block of segment.content.split(/\n\n+/)) {
          const trimmed = block.trim();
          if (!trimmed) continue;
          const k = key++;
          if (trimmed.startsWith("# ")) {
            result.push(<h2 key={k}>{trimmed.slice(2)}</h2>);
          } else if (trimmed.startsWith("## ")) {
            result.push(<h3 key={k}>{trimmed.slice(3)}</h3>);
          } else if (trimmed.startsWith("### ")) {
            result.push(<h4 key={k}>{trimmed.slice(4)}</h4>);
          } else if (trimmed.startsWith("![")) {
            const imgMatch = trimmed.match(/!\[([^\]]*)\]\(([^)]+)\)/);
            if (imgMatch) result.push(<ReaderImage key={k} src={imgMatch[2]} alt={imgMatch[1]} />);
          } else if (trimmed.startsWith("`") && trimmed.endsWith("`") && !trimmed.slice(1, -1).includes("`")) {
            // Entire block wrapped in single backticks — render as a code block
            result.push(
              <pre key={k} className="reader-code-block">
                <code>{trimmed.slice(1, -1)}</code>
              </pre>
            );
          } else {
            result.push(<p key={k} dangerouslySetInnerHTML={{ __html: inlineMarkdown(trimmed) }} />);
          }
        }
      }
    }

    return result;
  };

  const inlineMarkdown = (text: string): string => {
    return text
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/_(.+?)_/g, "<em>$1</em>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  };

  return (
    <div className="reader-container">
      {markdown ? (
        <article className="reader-article">{renderContent(markdown)}</article>
      ) : description ? (
        <article className="reader-article">
          {description.split(/\n\n+/).map((p, i) => <p key={i}>{p}</p>)}
        </article>
      ) : (
        <p className="empty-text">
          No article content available.{" "}
          <a href={item.url} target="_blank" rel="noopener noreferrer">View the original page.</a>
        </p>
      )}
    </div>
  );
}
