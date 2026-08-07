"use client";

import { useEffect, useMemo, useState } from "react";
import type { User } from "@supabase/supabase-js";
import type { Decision, ReviewRecord, Run, Tag } from "@/lib/types";
import { demoImages, demoRun, demoTags } from "@/lib/demo-data";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { AuthGate } from "./AuthGate";
import { PortalNav } from "./PortalNav";

type GalleryImage = {
  id: string;
  imageUrl: string;
  thumbnailUrl: string;
  capturedAt: string | null;
  latitude: number | null;
  longitude: number | null;
  mission: string | null;
  roll: string | null;
  frame: string | null;
  metadata: Record<string, unknown>;
};

type GalleryItem = {
  reviewId: string;
  decision: Decision;
  updatedAt: string;
  tags: Tag[];
  run: Pick<Run, "id" | "name" | "created_at">;
  image: GalleryImage;
};

const GALLERY_SELECT = `
  id, decision, updated_at,
  review_tags(tag:tags!review_tags_tag_id_fkey(id, slug, label, color)),
  run_image:run_images!reviews_image_id_fkey(
    image_id,
    run:runs!run_images_run_id_fkey(id, name, created_at),
    image:images!run_images_image_id_fkey(id, image_url, thumbnail_url, captured_at, latitude, longitude, mission, roll, frame, metadata)
  )
`;

export function Gallery() {
  return <AuthGate>{(user) => <GalleryContent user={user} />}</AuthGate>;
}

function GalleryContent({ user }: { user: User | null }) {
  const [items, setItems] = useState<GalleryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [decision, setDecision] = useState<Decision | "all">("accept");
  const [runId, setRunId] = useState("");
  const [search, setSearch] = useState("");

  useEffect(() => {
    const client = getSupabaseBrowserClient();
    if (!client) {
      const stored = JSON.parse(localStorage.getItem(`demo-reviews-${demoRun.id}`) ?? "{}") as Record<string, ReviewRecord>;
      const demoItems = demoImages.flatMap((image): GalleryItem[] => {
        const review = stored[image.id];
        if (!review) return [];
        const tags = demoTags.filter((tag) => review.tag_ids.includes(tag.id));
        if (!tags.length) return [];
        return [{
          reviewId: review.id,
          decision: review.decision,
          updatedAt: new Date().toISOString(),
          tags,
          run: { id: demoRun.id, name: demoRun.name, created_at: demoRun.created_at },
          image: {
            id: image.image_id,
            imageUrl: image.image_url,
            thumbnailUrl: image.thumbnail_url,
            capturedAt: image.captured_at,
            latitude: image.latitude,
            longitude: image.longitude,
            mission: String(image.metadata.mission ?? "") || null,
            roll: null,
            frame: String(image.metadata.frame ?? "") || null,
            metadata: image.metadata,
          },
        }];
      });
      setItems(demoItems);
      setLoading(false);
      return;
    }

    let active = true;
    async function loadGallery() {
      const { data, error } = await client!
        .from("reviews")
        .select(GALLERY_SELECT)
        .eq("reviewer_id", user!.id)
        .order("updated_at", { ascending: false });

      if (!active) return;
      if (error) {
        setLoadError(error.message);
        setLoading(false);
        return;
      }

      const normalized = ((data ?? []) as unknown as Array<Record<string, unknown>>).flatMap(normalizeGalleryRow);
      setItems(normalized.filter((item) => item.tags.length > 0));
      setLoading(false);
    }
    void loadGallery();
    return () => { active = false; };
  }, [user]);

  const runs = useMemo(() => {
    const unique = new Map<string, GalleryItem["run"]>();
    for (const item of items) unique.set(item.run.id, item.run);
    return [...unique.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
  }, [items]);

  const filtered = useMemo(() => items.filter((item) => {
    const haystack = `${item.image.id} ${item.run.name} ${item.tags.map((tag) => tag.label).join(" ")}`.toLowerCase();
    return (decision === "all" || item.decision === decision)
      && (!runId || item.run.id === runId)
      && (!search || haystack.includes(search.toLowerCase()));
  }), [decision, items, runId, search]);
  const metadataJson = useMemo(() => JSON.stringify(filtered.map(exportRecord), null, 2), [filtered]);
  const metadataCsv = useMemo(() => buildMetadataCsv(filtered), [filtered]);

  return (
    <main className="gallery-page">
      <PortalNav userEmail={user?.email ?? null} />
      <header className="gallery-header">
        <div><div className="eyebrow">SAVED TO YOUR REVIEWER ACCOUNT</div><h1>Your tagged photo gallery</h1><p>These are the tagged reviews saved by your signed-in account across every run and device. Preview selections or download reviewer-enriched metadata with links to every full-resolution NASA photograph.</p></div>
        <div className="gallery-export-actions">
          <TextDownloadLink disabled={!filtered.length} filename="ocean-review-metadata.csv" mimeType="text/csv" content={metadataCsv}>Download metadata CSV</TextDownloadLink>
          <TextDownloadLink disabled={!filtered.length} filename="ocean-review-selections.json" mimeType="application/json" content={metadataJson}>Download selections JSON</TextDownloadLink>
        </div>
      </header>
      <section className="gallery-summary" aria-label="Gallery totals">
        <strong>{items.filter((item) => item.decision === "accept").length}<span>accepted</span></strong>
        <strong>{items.filter((item) => item.decision === "uncertain").length}<span>uncertain</span></strong>
        <strong>{items.length}<span>tagged</span></strong>
      </section>
      <section className="gallery-filters">
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search image ID or tag" />
        <select aria-label="Filter by decision" value={decision} onChange={(event) => setDecision(event.target.value as Decision | "all")}>
          <option value="accept">Accepted</option><option value="uncertain">Uncertain</option><option value="reject">Rejected</option><option value="skip">Skipped</option><option value="all">All decisions</option>
        </select>
        <select aria-label="Filter by run" value={runId} onChange={(event) => setRunId(event.target.value)}>
          <option value="">All runs</option>{runs.map((run) => <option key={run.id} value={run.id}>{run.name}</option>)}
        </select>
      </section>
      {loading ? <div className="gallery-loading"><div className="spinner" />Loading your selections…</div> : loadError ? <p className="error">Could not load gallery: {loadError}</p> : filtered.length ? (
        <section className="gallery-grid">
          {filtered.map((item) => <GalleryCard key={item.reviewId} item={item} />)}
        </section>
      ) : (
        <section className="gallery-empty"><h2>No matching selections yet</h2><p>Tag a photo and record a decision in a review run, then it will appear here.</p></section>
      )}
    </main>
  );
}

function GalleryCard({ item }: { item: GalleryItem }) {
  const metadata = JSON.stringify(exportRecord(item), null, 2);
  return (
    <article className="gallery-card">
      <a className="gallery-image-link" href={item.image.imageUrl} target="_blank" rel="noreferrer"><img src={item.image.thumbnailUrl} alt={item.image.id} loading="lazy" /></a>
      <div className="gallery-card-body">
        <div className="gallery-title-row"><h2>{item.image.id}</h2><span className={`decision-pill ${item.decision}`}>{item.decision}</span></div>
        <p>{item.run.name}{item.image.capturedAt ? ` · ${new Date(item.image.capturedAt).toLocaleDateString()}` : ""}</p>
        <div className="tag-list">{item.tags.map((tag) => <span className="tag-chip static" style={{ "--tag-color": tag.color } as React.CSSProperties} key={tag.id}>{tag.label}</span>)}</div>
        <div className="gallery-card-actions">
          <a className="primary button-link" href={item.image.imageUrl} target="_blank" rel="noreferrer">Open full-resolution</a>
          <TextDownloadLink filename={`${item.image.id}-metadata.json`} mimeType="application/json" content={metadata}>Metadata</TextDownloadLink>
        </div>
      </div>
    </article>
  );
}

function normalizeGalleryRow(raw: Record<string, unknown>): GalleryItem[] {
  const runImage = raw.run_image as Record<string, unknown> | null;
  const sourceImage = runImage?.image as Record<string, unknown> | null;
  const sourceRun = runImage?.run as Record<string, unknown> | null;
  if (!runImage || !sourceImage || !sourceRun) return [];
  const reviewTags = (raw.review_tags as Array<{ tag?: Tag | null }> | null) ?? [];
  return [{
    reviewId: String(raw.id),
    decision: raw.decision as Decision,
    updatedAt: String(raw.updated_at),
    tags: reviewTags.map((value) => value.tag).filter((tag): tag is Tag => Boolean(tag)),
    run: { id: String(sourceRun.id), name: String(sourceRun.name), created_at: String(sourceRun.created_at) },
    image: {
      id: String(sourceImage.id ?? runImage.image_id),
      imageUrl: String(sourceImage.image_url),
      thumbnailUrl: String(sourceImage.thumbnail_url),
      capturedAt: sourceImage.captured_at as string | null,
      latitude: sourceImage.latitude as number | null,
      longitude: sourceImage.longitude as number | null,
      mission: sourceImage.mission as string | null,
      roll: sourceImage.roll as string | null,
      frame: sourceImage.frame as string | null,
      metadata: (sourceImage.metadata ?? {}) as Record<string, unknown>,
    },
  }];
}

function exportRecord(item: GalleryItem) {
  return {
    image_id: item.image.id,
    source_url: item.image.imageUrl,
    thumbnail_url: item.image.thumbnailUrl,
    run_id: item.run.id,
    run_name: item.run.name,
    decision: item.decision,
    tags: item.tags.map((tag) => tag.label),
    tagged_at: item.updatedAt,
    captured_at: item.image.capturedAt,
    latitude: item.image.latitude,
    longitude: item.image.longitude,
    mission: item.image.mission,
    roll: item.image.roll,
    frame: item.image.frame,
    nasa_metadata: item.image.metadata,
  };
}

function csvCell(value: unknown) {
  return `"${String(value).replaceAll('"', '""')}"`;
}

function buildMetadataCsv(items: GalleryItem[]) {
  const rows = items.map((item) => {
    const record = exportRecord(item);
    return [record.image_id, record.run_name, record.decision, record.tags.join("; "), record.captured_at ?? "", record.latitude ?? "", record.longitude ?? "", record.source_url, JSON.stringify(record.nasa_metadata)];
  });
  const header = ["image_id", "run", "decision", "tags", "captured_at", "latitude", "longitude", "source_url", "nasa_metadata"];
  return [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\n");
}

function TextDownloadLink({ filename, mimeType, content, disabled = false, children }: { filename: string; mimeType: string; content: string; disabled?: boolean; children: React.ReactNode }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    const nextUrl = URL.createObjectURL(new Blob([content], { type: mimeType }));
    setUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [content, mimeType]);
  return <a aria-disabled={disabled} download={filename} href={disabled ? undefined : url}>{children}</a>;
}
