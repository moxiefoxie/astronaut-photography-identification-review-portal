"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { SupabaseClient, User } from "@supabase/supabase-js";
import type { Prediction, Run, Tag } from "@/lib/types";
import { demoImages, demoRun, demoTags } from "@/lib/demo-data";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { AuthGate } from "./AuthGate";
import { PortalNav } from "./PortalNav";

const PAGE_SIZE = 60;
const EXPORT_PAGE_SIZE = 500;
const CATALOG_SELECT = `
  id, image_id, ranking_score, created_at,
  image:images!run_images_image_id_fkey(id, image_url, thumbnail_url, captured_at, latitude, longitude, mission, roll, frame, metadata),
  predictions!inner(score, source, model_version, evidence, tag:tags!predictions_tag_id_fkey(id, slug, label, color))
`;

type CatalogFilters = {
  runId: string;
  tagId: string;
  minimumScore: number;
  search: string;
};

type CatalogImage = {
  runImageId: string;
  imageId: string;
  rankingScore: number | null;
  imageUrl: string;
  thumbnailUrl: string;
  capturedAt: string | null;
  latitude: number | null;
  longitude: number | null;
  mission: string | null;
  roll: string | null;
  frame: string | null;
  metadata: Record<string, unknown>;
  predictions: Prediction[];
};

export function AiCatalog() {
  return <AuthGate>{(user) => <CatalogContent user={user} />}</AuthGate>;
}

function CatalogContent({ user }: { user: User | null }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [runId, setRunId] = useState("");
  const [tagId, setTagId] = useState("");
  const [minimumScore, setMinimumScore] = useState(0.5);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [page, setPage] = useState(0);
  const [items, setItems] = useState<CatalogImage[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportProgress, setExportProgress] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const client = getSupabaseBrowserClient();
    if (!client) {
      setRuns([demoRun]);
      setTags(demoTags);
      setRunId(demoRun.id);
      return;
    }
    let active = true;
    void Promise.all([
      client.from("runs").select("*").order("created_at", { ascending: false }),
      client.from("tags").select("id, slug, label, color").eq("active", true).order("label"),
    ]).then(([runResult, tagResult]) => {
      if (!active) return;
      const nextRuns = (runResult.data as Run[]) ?? [];
      setRuns(nextRuns);
      setTags((tagResult.data as Tag[]) ?? []);
      setRunId((current) => current || nextRuns[0]?.id || "");
    });
    return () => { active = false; };
  }, []);

  useEffect(() => { setPage(0); }, [debouncedSearch, minimumScore, runId, tagId]);

  const filters = useMemo<CatalogFilters>(() => ({
    runId,
    tagId,
    minimumScore,
    search: debouncedSearch,
  }), [debouncedSearch, minimumScore, runId, tagId]);

  const loadPage = useCallback(async () => {
    if (!runId) return;
    setLoading(true);
    setLoadError("");
    const client = getSupabaseBrowserClient();
    if (!client) {
      const filtered = demoCatalog(filters);
      setTotal(filtered.length);
      setItems(filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE));
      setLoading(false);
      return;
    }
    const result = await fetchCatalogPage(client, filters, page * PAGE_SIZE, PAGE_SIZE, true);
    if (result.error) setLoadError(result.error);
    setItems(result.items);
    setTotal(result.count ?? result.items.length);
    setLoading(false);
  }, [filters, page, runId]);

  useEffect(() => { void loadPage(); }, [loadPage, refreshKey]);

  useEffect(() => {
    const client = getSupabaseBrowserClient();
    if (!client || !runId) return;
    let timer: number | null = null;
    const refreshSoon = () => {
      if (timer != null) window.clearTimeout(timer);
      timer = window.setTimeout(() => setRefreshKey((value) => value + 1), 800);
    };
    const channel = client.channel(`ai-catalog-${runId}`)
      .on("postgres_changes", { event: "INSERT", schema: "public", table: "predictions" }, refreshSoon)
      .on("postgres_changes", { event: "INSERT", schema: "public", table: "run_images", filter: `run_id=eq.${runId}` }, refreshSoon)
      .subscribe();
    return () => {
      if (timer != null) window.clearTimeout(timer);
      void client.removeChannel(channel);
    };
  }, [runId]);

  const selectedRun = runs.find((run) => run.id === runId) ?? null;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  async function downloadFilteredCatalog() {
    if (!runId || exporting || total === 0) return;
    setExporting(true);
    setExportProgress("Preparing export…");
    setLoadError("");
    const client = getSupabaseBrowserClient();
    const exported: CatalogImage[] = [];
    if (!client) {
      exported.push(...demoCatalog(filters));
    } else {
      for (let offset = 0; ; offset += EXPORT_PAGE_SIZE) {
        const result = await fetchCatalogPage(client, filters, offset, EXPORT_PAGE_SIZE, false);
        if (result.error) {
          setLoadError(`Export failed: ${result.error}`);
          setExporting(false);
          setExportProgress("");
          return;
        }
        exported.push(...result.items);
        setExportProgress(`Collected ${exported.length.toLocaleString()} of ${total.toLocaleString()}…`);
        if (result.items.length < EXPORT_PAGE_SIZE) break;
      }
    }
    const records = exported.map((item) => exportRecord(item, selectedRun));
    const filename = `ai-catalog-${slugify(selectedRun?.name ?? "run")}.json`;
    triggerDownload(filename, "application/json", JSON.stringify(records, null, 2));
    setExporting(false);
    setExportProgress("");
  }

  return (
    <main className="catalog-page">
      <PortalNav currentRunId={runId || undefined} userEmail={user?.email ?? null} />
      <header className="catalog-header">
        <div>
          <div className="eyebrow">AUTOMATED IMAGE CLASSIFICATION</div>
          <h1>AI catalog</h1>
          <p>Model-generated ranking tags for every processed photograph. Scores and evidence remain separate from human corrections in My Tags.</p>
        </div>
        <button className="primary" disabled={exporting || total === 0} onClick={downloadFilteredCatalog}>
          {exporting ? exportProgress : "Download filtered JSON"}
        </button>
      </header>
      <section className="catalog-controls" aria-label="AI catalog filters">
        <label><span>Run</span><select value={runId} onChange={(event) => setRunId(event.target.value)}>{runs.map((run) => <option key={run.id} value={run.id}>{run.name}</option>)}</select></label>
        <label><span>AI tag</span><select value={tagId} onChange={(event) => setTagId(event.target.value)}><option value="">All automated tags</option>{tags.map((tag) => <option key={tag.id} value={tag.id}>{tag.label}</option>)}</select></label>
        <label><span>Minimum ranking score</span><select value={minimumScore} onChange={(event) => setMinimumScore(Number(event.target.value))}><option value="0">Any score</option><option value="0.4">40%+</option><option value="0.5">50%+</option><option value="0.6">60%+</option><option value="0.7">70%+</option><option value="0.8">80%+</option><option value="0.9">90%+</option></select></label>
        <label className="catalog-search"><span>Image</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search image ID" /></label>
        <button onClick={() => setRefreshKey((value) => value + 1)}>Refresh</button>
      </section>
      <div className="catalog-status">
        <strong>{total.toLocaleString()}</strong> matching automatically tagged {total === 1 ? "photo" : "photos"}
        {selectedRun && <span>{selectedRun.status === "running" ? "Live—new predictions appear automatically" : selectedRun.name}</span>}
      </div>
      {loading ? <div className="catalog-message"><div className="spinner" />Loading automated predictions…</div> : loadError ? <div className="catalog-message error">{loadError}</div> : items.length ? (
        <section className="catalog-grid">
          {items.map((item) => <CatalogCard key={item.runImageId} item={item} />)}
        </section>
      ) : <div className="catalog-message"><h2>No predictions match these filters</h2><p>Lower the ranking-score threshold or choose a different run or category.</p></div>}
      {total > PAGE_SIZE && (
        <nav className="catalog-pagination" aria-label="AI catalog pages">
          <button disabled={page === 0 || loading} onClick={() => setPage((value) => Math.max(0, value - 1))}>← Previous</button>
          <span>Page {page + 1} of {pageCount}</span>
          <button disabled={page + 1 >= pageCount || loading} onClick={() => setPage((value) => value + 1)}>Next →</button>
        </nav>
      )}
    </main>
  );
}

function CatalogCard({ item }: { item: CatalogImage }) {
  return (
    <article className="catalog-card">
      <a className="catalog-image-link" href={item.imageUrl} target="_blank" rel="noreferrer"><img src={item.thumbnailUrl} alt={item.imageId} loading="lazy" /></a>
      <div className="catalog-card-body">
        <div className="catalog-title"><h2>{item.imageId}</h2><a href={item.imageUrl} target="_blank" rel="noreferrer">Full resolution ↗</a></div>
        <p className="image-meta">{item.capturedAt ? new Date(item.capturedAt).toLocaleDateString() : "Date unavailable"}{item.latitude != null ? ` · ${item.latitude.toFixed(2)}, ${item.longitude?.toFixed(2)}` : ""}</p>
        <div className="prediction-list">
          {item.predictions.map((prediction) => (
            <section className="prediction" key={`${prediction.tag.id}-${prediction.source}`}>
              <div className="prediction-heading"><span className="tag-chip static" style={{ "--tag-color": prediction.tag.color } as React.CSSProperties}>{prediction.tag.label}</span><strong>{prediction.tag.slug === "no_confident_match" ? "Unclassified" : `${Math.round(prediction.score * 100)}% ranking score`}</strong></div>
              {prediction.tag.slug !== "no_confident_match" && <div className="confidence-track"><i style={{ width: `${Math.round(prediction.score * 100)}%` }} /></div>}
              <p>{predictionReason(prediction)}</p>
              <div className="prediction-method">
                <strong>How this was identified</strong>
                <span>{predictionMethod(prediction)}</span>
                {predictionMetadata(prediction).length ? (
                  <dl>{predictionMetadata(prediction).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
                ) : <span><b>Metadata used:</b> none. Coordinates and solar/camera angles did not affect this score.</span>}
              </div>
              <small>{prediction.source}{prediction.model_version ? ` · ${prediction.model_version}` : ""}</small>
            </section>
          ))}
        </div>
      </div>
    </article>
  );
}

async function fetchCatalogPage(client: SupabaseClient, filters: CatalogFilters, offset: number, limit: number, includeCount: boolean) {
  let query = client.from("run_images")
    .select(CATALOG_SELECT, { count: includeCount ? "exact" : undefined })
    .eq("run_id", filters.runId)
    .gte("predictions.score", filters.minimumScore)
    .order("ranking_score", { ascending: false, nullsFirst: false })
    .order("created_at", { ascending: false })
    .range(offset, offset + limit - 1);
  if (filters.tagId) query = query.eq("predictions.tag_id", filters.tagId);
  if (filters.search) query = query.ilike("image_id", `%${filters.search}%`);
  const { data, error, count } = await query;
  return {
    items: ((data ?? []) as unknown as Array<Record<string, unknown>>).flatMap(normalizeCatalogRow),
    count,
    error: error?.message ?? "",
  };
}

function normalizeCatalogRow(raw: Record<string, unknown>): CatalogImage[] {
  const image = raw.image as Record<string, unknown> | null;
  if (!image) return [];
  const predictions = ((raw.predictions as Array<Record<string, unknown>> | null) ?? []).flatMap((prediction): Prediction[] => {
    const tag = prediction.tag as Tag | null;
    if (!tag) return [];
    const rawSource = String(prediction.source ?? "automated visual classifier");
    return [{
      score: Number(prediction.score),
      source: /^\d+$/.test(rawSource) ? "automated visual classifier" : rawSource,
      model_version: prediction.model_version as string | null,
      evidence: (prediction.evidence ?? {}) as Record<string, unknown>,
      tag,
    }];
  }).sort((a, b) => b.score - a.score);
  return [{
    runImageId: String(raw.id),
    imageId: String(raw.image_id ?? image.id),
    rankingScore: raw.ranking_score == null ? null : Number(raw.ranking_score),
    imageUrl: String(image.image_url),
    thumbnailUrl: String(image.thumbnail_url),
    capturedAt: image.captured_at as string | null,
    latitude: image.latitude as number | null,
    longitude: image.longitude as number | null,
    mission: image.mission as string | null,
    roll: image.roll as string | null,
    frame: image.frame as string | null,
    metadata: (image.metadata ?? {}) as Record<string, unknown>,
    predictions,
  }];
}

function demoCatalog(filters: CatalogFilters) {
  return demoImages.map((image): CatalogImage => ({
    runImageId: image.id,
    imageId: image.image_id,
    rankingScore: Math.max(...image.predictions.map((prediction) => prediction.score)),
    imageUrl: image.image_url,
    thumbnailUrl: image.thumbnail_url,
    capturedAt: image.captured_at,
    latitude: image.latitude,
    longitude: image.longitude,
    mission: String(image.metadata.mission ?? "") || null,
    roll: null,
    frame: String(image.metadata.frame ?? "") || null,
    metadata: image.metadata,
    predictions: image.predictions,
  })).map((item) => ({
    ...item,
    predictions: item.predictions.filter((prediction) => prediction.score >= filters.minimumScore && (!filters.tagId || prediction.tag.id === filters.tagId)),
  })).filter((item) => item.predictions.length && (!filters.search || item.imageId.toLowerCase().includes(filters.search.toLowerCase())))
    .sort((a, b) => (b.rankingScore ?? 0) - (a.rankingScore ?? 0));
}

function predictionReason(prediction: Prediction) {
  const evidence = prediction.evidence;
  if (!evidence) return "No explanatory evidence was supplied by this model run.";
  for (const key of ["reason", "brief_reasoning", "brief_reason", "explanation"]) {
    const value = evidence[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  const summary = evidence.summary;
  if (typeof summary === "string" && /visual production ranking/i.test(summary)) {
    const strength = prediction.score >= 0.85 ? "strong" : prediction.score >= 0.65 ? "moderate" : "weak";
    const otherMatches = [...summary.matchAll(/([a-z0-9_]+)=([0-9.]+)/gi)]
      .filter(([, slug]) => slug !== prediction.tag.slug)
      .slice(0, 2)
      .map(([, slug, score]) => `${slug.replaceAll("_", " ")} (${Math.round(Number(score) * 100)}%)`);
    return `The visual model found a ${strength} match to reviewed ${prediction.tag.label.toLowerCase()} examples (${Math.round(prediction.score * 100)}% relative ranking score, not a calibrated probability).${otherMatches.length ? ` It also matched ${otherMatches.join(" and ")}.` : ""}`;
  }
  if (typeof summary === "string" && summary.trim()) return summary.trim();
  const firstText = Object.values(evidence).find((value) => typeof value === "string" && value.trim()) as string | undefined;
  return firstText?.trim() || "No explanatory evidence was supplied by this model run.";
}

function predictionMethod(prediction: Prediction) {
  const method = prediction.evidence?.method;
  if (typeof method === "string" && method.trim()) return method.trim();
  if (prediction.source === "clip_reviewed_prototype") {
    return "Visual similarity: image pixels were compared with reviewed examples and the category description. This is not object detection or a scene description.";
  }
  if (/geometry|specular|sunglint/i.test(prediction.source)) {
    return "Metadata geometry: the Sun direction and camera-to-ground viewing direction were compared for specular-reflection alignment.";
  }
  return `Automated prediction supplied by ${prediction.source}.`;
}

function predictionMetadata(prediction: Prediction): Array<[string, string]> {
  const raw = prediction.evidence?.metadata_used;
  if (!raw) return [];
  if (Array.isArray(raw)) {
    return raw.flatMap((entry): Array<[string, string]> => {
      if (typeof entry === "string") return [["Input", entry]];
      if (!entry || typeof entry !== "object") return [];
      const record = entry as Record<string, unknown>;
      return [[String(record.label ?? record.field ?? "Input"), String(record.value ?? "available")]];
    });
  }
  if (typeof raw === "object") {
    return Object.entries(raw as Record<string, unknown>).flatMap(([key, value]) => value == null ? [] : [[humanizeKey(key), String(value)] as [string, string]]);
  }
  return [["Input", String(raw)]];
}

function humanizeKey(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function exportRecord(item: CatalogImage, run: Run | null) {
  return {
    image_id: item.imageId,
    image_url: item.imageUrl,
    thumbnail_url: item.thumbnailUrl,
    run_id: run?.id ?? null,
    run_name: run?.name ?? null,
    captured_at: item.capturedAt,
    latitude: item.latitude,
    longitude: item.longitude,
    mission: item.mission,
    roll: item.roll,
    frame: item.frame,
    ai_predictions: item.predictions.map((prediction) => ({
      tag: prediction.tag.slug,
      label: prediction.tag.label,
      model_score: prediction.score,
      score_interpretation: prediction.tag.slug === "no_confident_match"
        ? "catalog status: no target category reached the configured threshold"
        : "ranking signal; not a calibrated probability",
      reason: predictionReason(prediction),
      source: prediction.source,
      model_version: prediction.model_version ?? null,
      evidence: prediction.evidence ?? {},
    })),
    nasa_metadata: item.metadata,
  };
}

function triggerDownload(filename: string, mimeType: string, content: string) {
  const url = URL.createObjectURL(new Blob([content], { type: mimeType }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function slugify(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "run";
}
