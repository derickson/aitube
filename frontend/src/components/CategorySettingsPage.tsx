import { useCallback, useEffect, useRef, useState } from "react";
import {
  getCategorySettings,
  updateCategorySettings,
  recategorizeAllVideos,
  getRecategorizeStatus,
  type CategoryConfig,
  type RecategorizeStatus,
} from "../api/client";
import { ErrorBanner } from "./ErrorBanner";

const POLL_INTERVAL_MS = 2000;

function slugify(label: string): string {
  return label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64);
}

function emptyCategory(): CategoryConfig {
  return { slug: "", label: "", description: "" };
}

export function CategorySettingsPage() {
  const [categories, setCategories] = useState<CategoryConfig[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saved, setSaved] = useState(false);

  const [recatStatus, setRecatStatus] = useState<RecategorizeStatus | null>(null);
  const [recatError, setRecatError] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const pollStatus = useCallback(() => {
    getRecategorizeStatus()
      .then((s) => {
        setRecatStatus(s);
        if (s.status !== "running") stopPolling();
      })
      .catch((e) => {
        setRecatError(String(e?.message || e));
        stopPolling();
      });
  }, [stopPolling]);

  const startPolling = useCallback(() => {
    stopPolling();
    pollStatus();
    pollRef.current = setInterval(pollStatus, POLL_INTERVAL_MS);
  }, [pollStatus, stopPolling]);

  useEffect(() => {
    getCategorySettings()
      .then((r) => setCategories(r.categories))
      .catch((e) => setLoadError(String(e?.message || e)))
      .finally(() => setLoading(false));

    // Pick up a sweep already in progress (started by this page in another
    // tab, or by another process) instead of only tracking ones we start.
    getRecategorizeStatus()
      .then((s) => {
        setRecatStatus(s);
        if (s.status === "running") startPolling();
      })
      .catch(() => {});

    return stopPolling;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const updateRow = (index: number, patch: Partial<CategoryConfig>) => {
    setCategories((prev) => {
      if (!prev) return prev;
      const next = [...prev];
      next[index] = { ...next[index], ...patch };
      return next;
    });
    setSaved(false);
  };

  const handleLabelChange = (index: number, label: string) => {
    setCategories((prev) => {
      if (!prev) return prev;
      const row = prev[index];
      // Keep slug in sync with label until the user edits slug directly.
      const autoSlug = !row.slug || row.slug === slugify(row.label);
      const next = [...prev];
      next[index] = { ...row, label, slug: autoSlug ? slugify(label) : row.slug };
      return next;
    });
    setSaved(false);
  };

  const addRow = () => {
    setCategories((prev) => [...(prev ?? []), emptyCategory()]);
    setSaved(false);
  };

  const removeRow = (index: number) => {
    setCategories((prev) => (prev ? prev.filter((_, i) => i !== index) : prev));
    setSaved(false);
  };

  const handleSave = async () => {
    if (!categories) return;
    setSaveError("");

    const trimmed = categories.map((c) => ({
      slug: c.slug.trim(),
      label: c.label.trim(),
      description: c.description.trim(),
    }));
    if (trimmed.some((c) => !c.slug || !c.label || !c.description)) {
      setSaveError("Every category needs a slug, label, and description.");
      return;
    }
    const slugs = trimmed.map((c) => c.slug);
    if (new Set(slugs).size !== slugs.length) {
      setSaveError("Category slugs must be unique.");
      return;
    }

    setSaving(true);
    try {
      const resp = await updateCategorySettings(trimmed);
      setCategories(resp.categories);
      setSaved(true);
    } catch (e: any) {
      setSaveError(String(e?.message || e));
    } finally {
      setSaving(false);
    }
  };

  const handleRecategorize = async () => {
    if (!confirm(
      "Recategorize all videos? This re-runs classification on every content item " +
      "using the current categories above. It can take a while and cannot be undone.",
    )) return;

    setRecatError("");
    try {
      await recategorizeAllVideos();
      startPolling();
    } catch (e: any) {
      setRecatError(String(e?.message || e));
    }
  };

  if (loading) return <div className="category-settings-page"><p>Loading category settings…</p></div>;
  if (loadError) return <div className="category-settings-page"><ErrorBanner error={loadError} /></div>;
  if (!categories) return null;

  const running = recatStatus?.status === "running";
  const progressPct =
    recatStatus?.total ? Math.min(100, Math.round((recatStatus.scanned / recatStatus.total) * 100)) : null;

  return (
    <div className="category-settings-page">
      <div className="category-settings-header">
        <h2>Category Settings</h2>
        <p className="category-settings-sub">
          Categories used by Jev to classify content. Changes here apply to new content going
          forward; use "Recategorize All Videos" below to relabel everything already indexed.
        </p>
      </div>

      <div className="category-table-wrap">
        <table className="category-table">
          <thead>
            <tr>
              <th>Label</th>
              <th>Slug</th>
              <th>Description (shown to the model)</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {categories.map((c, i) => (
              <tr key={i}>
                <td>
                  <input
                    className="category-input"
                    value={c.label}
                    placeholder="Display label"
                    onChange={(e) => handleLabelChange(i, e.target.value)}
                  />
                </td>
                <td>
                  <input
                    className="category-input category-input-slug"
                    value={c.slug}
                    placeholder="slug"
                    onChange={(e) => updateRow(i, { slug: slugify(e.target.value) })}
                  />
                </td>
                <td>
                  <textarea
                    className="category-input category-input-description"
                    value={c.description}
                    placeholder="What this category means, for the model"
                    rows={2}
                    onChange={(e) => updateRow(i, { description: e.target.value })}
                  />
                </td>
                <td>
                  <button
                    className="btn btn-danger category-remove-btn"
                    onClick={() => removeRow(i)}
                    disabled={categories.length <= 1}
                    title={categories.length <= 1 ? "At least one category is required" : "Remove category"}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="category-settings-actions">
        <button className="btn" onClick={addRow}>+ Add category</button>
        <div className="category-settings-save">
          {saveError && <span className="category-save-error">{saveError}</span>}
          {saved && !saveError && <span className="category-save-ok">Saved</span>}
          <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save categories"}
          </button>
        </div>
      </div>

      <section className="recategorize-panel">
        <h3>Recategorize All Videos</h3>
        <p className="category-settings-sub">
          Reprocesses every content item with the current categories and writes back only the
          category field via a bulk partial update — it does not touch or re-vectorize any other
          field.
        </p>
        <button className="btn btn-primary" onClick={handleRecategorize} disabled={running}>
          {running ? "Recategorizing…" : "Recategorize All Videos"}
        </button>

        {recatError && <ErrorBanner error={recatError} />}

        {recatStatus && recatStatus.status !== "idle" && (
          <div className="recategorize-status">
            <div className="recategorize-status-line">
              <strong>
                {recatStatus.status === "running" && "Running…"}
                {recatStatus.status === "done" && "Done"}
                {recatStatus.status === "error" && "Failed"}
              </strong>
              <span>
                {recatStatus.scanned}
                {recatStatus.total != null ? ` / ${recatStatus.total}` : ""} scanned
                {" · "}{recatStatus.categorized} categorized
                {recatStatus.failed > 0 ? ` · ${recatStatus.failed} failed` : ""}
              </span>
            </div>
            {progressPct != null && (
              <div className="recategorize-progress-track">
                <div className="recategorize-progress-fill" style={{ width: `${progressPct}%` }} />
              </div>
            )}
            {recatStatus.error && <ErrorBanner error={recatStatus.error} />}
          </div>
        )}
      </section>
    </div>
  );
}
