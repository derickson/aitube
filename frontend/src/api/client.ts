const BASE = "/aitube/api";

export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${await res.text()}`);
  }
  return res.json();
}

// --- Types ---

export type SubscriptionType = "youtube_channel" | "podcast" | "rss";
export type SubscriptionStatus = "active" | "muted" | "unfollowed";
export type ContentType = "video" | "podcast_episode" | "article";

export interface Subscription {
  id: string;
  type: SubscriptionType;
  url: string;
  name: string;
  description: string;
  interest_notes: string;
  status: SubscriptionStatus;
  added_at: string;
  last_polled_at: string | null;
  content_count: number;
}

export interface TranscriptChunk {
  text: string;
  start: number;
  end: number;
}

export interface Transcript {
  text: string;
  chunks: TranscriptChunk[];
}

export interface ContentItem {
  id: string;
  subscription_id: string;
  external_id: string;
  type: ContentType;
  title: string;
  url: string;
  published_at: string | null;
  discovered_at: string;
  duration_seconds: number | null;
  thumbnail_url: string;
  summary: string;
  interest_score: number | null;
  interest_reasoning: string;
  transcript: Transcript | null;
  consumed: boolean;
  user_interest: "up" | "down" | null;
  content_markdown: string;
  metadata: Record<string, unknown>;
}

export interface PlaybackState {
  content_item_id: string;
  position_seconds: number;
  consumed: boolean;
  last_updated_at: string;
}

export interface ResolvedPreview {
  url: string;
  feed_url: string;
  type: SubscriptionType;
  name: string;
  description: string;
  thumbnail_url: string;
  sample_items: { title: string; published: string | null }[];
}

// --- Subscriptions ---

export function resolveUrl(url: string): Promise<ResolvedPreview> {
  return apiFetch("/subscriptions/resolve/", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

export function listSubscriptions(): Promise<Subscription[]> {
  return apiFetch("/subscriptions/");
}

export function getSubscription(id: string): Promise<Subscription> {
  return apiFetch(`/subscriptions/${id}/`);
}

export function createSubscription(data: {
  url: string;
  name: string;
  type: SubscriptionType;
  description?: string;
  interest_notes?: string;
}): Promise<Subscription> {
  return apiFetch("/subscriptions/", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateSubscription(
  id: string,
  data: {
    name?: string;
    description?: string;
    interest_notes?: string;
    status?: SubscriptionStatus;
  },
): Promise<Subscription> {
  return apiFetch(`/subscriptions/${id}/`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export function deleteSubscription(id: string): Promise<{ deleted: string }> {
  return apiFetch(`/subscriptions/${id}/`, { method: "DELETE" });
}

// --- Content ---

export interface FacetBucket {
  key: string;
  count: number;
}

export interface ContentSearchResponse {
  items: ContentItem[];
  total: number;
  facets: Record<string, FacetBucket[]>;
}

export function searchContent(params?: {
  subscription_id?: string;
  content_type?: ContentType;
  consumed?: "true" | "false";
  interest?: "up" | "down" | "none";
  q?: string;
  size?: number;
  offset?: number;
}): Promise<ContentSearchResponse> {
  const search = new URLSearchParams();
  if (params?.subscription_id) search.set("subscription_id", params.subscription_id);
  if (params?.content_type) search.set("content_type", params.content_type);
  if (params?.consumed) search.set("consumed", params.consumed);
  if (params?.interest) search.set("interest", params.interest);
  if (params?.q) search.set("q", params.q);
  if (params?.size !== undefined) search.set("size", String(params.size));
  if (params?.offset !== undefined) search.set("offset", String(params.offset));
  const qs = search.toString();
  return apiFetch(`/content/${qs ? `?${qs}` : ""}`);
}

export function getContentItem(id: string): Promise<ContentItem> {
  return apiFetch(`/content/${id}/`);
}

export function transcribeContentItem(id: string): Promise<{ status: string; transcript_length: number }> {
  return apiFetch(`/content/${id}/transcribe/`, { method: "POST" });
}

export function setConsumed(id: string, consumed: boolean): Promise<{ id: string; consumed: boolean }> {
  return apiFetch(`/content/${id}/consumed/?consumed=${consumed}`, { method: "PUT" });
}

export function setInterest(id: string, interest: "up" | "down" | "none"): Promise<{ id: string; interest: string | null }> {
  return apiFetch(`/content/${id}/interest/?interest=${interest}`, { method: "PUT" });
}

export interface PlaybackProgress {
  position_seconds: number;
  duration_seconds: number;
  percent: number;
}

export function batchPlaybackProgress(itemIds: string[]): Promise<Record<string, PlaybackProgress>> {
  return apiFetch("/content/playback-progress/", {
    method: "POST",
    body: JSON.stringify(itemIds),
  });
}

// --- Playback ---

export function getPlayback(contentItemId: string): Promise<PlaybackState | null> {
  return apiFetch(`/playback/${contentItemId}/`);
}

export function updatePlayback(
  contentItemId: string,
  positionSeconds: number,
  durationSeconds?: number,
): Promise<PlaybackState> {
  const body: Record<string, number> = { position_seconds: positionSeconds };
  if (durationSeconds !== undefined) body.duration_seconds = durationSeconds;
  return apiFetch(`/playback/${contentItemId}/`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

// --- Chat ---

export interface Agent {
  id: string;
  name: string;
}

export function listAgents(): Promise<Agent[]> {
  return apiFetch("/chat/agents/");
}

export function chatStreamUrl(itemId: string): string {
  return `${BASE}/chat/${itemId}/stream/`;
}

// --- Polling ---

export function triggerPoll(): Promise<{ status: string; message?: string }> {
  return apiFetch("/polling/trigger/", { method: "POST" });
}

// --- Add Content ---

export interface ContentPreviewResponse {
  preview_id: string;
  url: string;
  detected_type: ContentType;
  title: string | null;
  thumbnail_url: string | null;
  duration_seconds: number | null;
  published_at: string | null;
  description: string | null;
  author: string | null;
  file_size_bytes: number | null;
}

export function previewContent(url: string): Promise<ContentPreviewResponse> {
  return apiFetch("/add-content/preview/", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

export function confirmContent(
  previewId: string,
  titleOverride?: string,
): Promise<{ status: string }> {
  return apiFetch("/add-content/confirm/", {
    method: "POST",
    body: JSON.stringify({ preview_id: previewId, title_override: titleOverride || null }),
  });
}
