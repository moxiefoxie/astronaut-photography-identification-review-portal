"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { User } from "@supabase/supabase-js";
import type { Decision, ReviewImage, ReviewRecord, Run, Tag } from "@/lib/types";
import { demoImages, demoRun, demoTags } from "@/lib/demo-data";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { AuthGate } from "./AuthGate";
import { ImageCard } from "./ImageCard";
import { PortalNav } from "./PortalNav";
import { SwipeReview } from "./SwipeReview";

type ViewMode = "swipe" | "grid";
const PAGE_SIZE = 150;
const IMAGE_SELECT = "*, image:images(*), predictions(score, source, tag:tags(id, slug, label, color)), reviews(decision)";

export function ReviewWorkspace() {
  const params = useParams<{ runId: string }>();
  return <AuthGate>{(user) => <Workspace runId={params.runId} user={user} />}</AuthGate>;
}

function normalizeImage(row: Record<string, unknown>): ReviewImage {
  const rawPredictions = (row.predictions as Array<Record<string, unknown>> | undefined) ?? [];
  const rawReviews = (row.reviews as Array<{ decision: Decision }> | undefined) ?? [];
  const sourceImage = row.image as Record<string, unknown> | undefined;
  const teamReviews: Record<Decision, number> = { accept: 0, reject: 0, uncertain: 0, skip: 0 };
  for (const review of rawReviews) teamReviews[review.decision] += 1;
  return {
    ...(row as unknown as ReviewImage),
    image_id: String(row.image_id),
    image_url: String(sourceImage?.image_url ?? row.image_url),
    thumbnail_url: String(sourceImage?.thumbnail_url ?? row.thumbnail_url),
    captured_at: (sourceImage?.captured_at ?? row.captured_at) as string | null,
    latitude: (sourceImage?.latitude ?? row.latitude) as number | null,
    longitude: (sourceImage?.longitude ?? row.longitude) as number | null,
    metadata: (sourceImage?.metadata ?? row.metadata ?? {}) as Record<string, unknown>,
    predictions: rawPredictions.map((prediction) => ({
      score: Number(prediction.score),
      source: String(prediction.source),
      tag: prediction.tag as Tag,
    })),
    team_reviews: teamReviews,
  };
}

function slugify(value: string) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
}

function Workspace({ runId, user }: { runId: string; user: User | null }) {
  const [run, setRun] = useState<Run | null>(null);
  const [images, setImages] = useState<ReviewImage[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [reviews, setReviews] = useState<Record<string, ReviewRecord>>({});
  const [selectedTags, setSelectedTags] = useState<Record<string, Tag[]>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const [mode, setMode] = useState<ViewMode>("swipe");
  const [cursor, setCursor] = useState(0);
  const [category, setCategory] = useState("");
  const [decisionFilter, setDecisionFilter] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [nextOffset, setNextOffset] = useState(PAGE_SIZE);
  const [hasMore, setHasMore] = useState(false);
  const [loadError, setLoadError] = useState("");
  const reviewerId = user?.id ?? "demo-user";

  const loadImage = useCallback(async (imageId: string) => {
    const client = getSupabaseBrowserClient();
    if (!client) return;
    const { data } = await client.from("run_images")
      .select(IMAGE_SELECT)
      .eq("id", imageId)
      .eq("run_id", runId)
      .single();
    if (data) {
      const normalized = normalizeImage(data);
      setImages((current) => current.some((image) => image.id === imageId)
        ? current.map((image) => image.id === imageId ? normalized : image)
        : [...current, normalized]);
    }
  }, [runId]);

  const loadMore = useCallback(async () => {
    const client = getSupabaseBrowserClient();
    if (!client || loadingMore || !hasMore) return;
    setLoadingMore(true);
    setLoadError("");
    const { data, error } = await client.from("run_images")
      .select(IMAGE_SELECT)
      .eq("run_id", runId)
      .order("created_at")
      .range(nextOffset, nextOffset + PAGE_SIZE - 1);
    if (error) {
      setLoadError(error.message);
    } else {
      const page = ((data ?? []) as Array<Record<string, unknown>>).map(normalizeImage);
      setImages((current) => {
        const merged = new Map(current.map((image) => [image.id, image]));
        for (const image of page) merged.set(image.id, image);
        return [...merged.values()];
      });
      setNextOffset((offset) => offset + page.length);
      setHasMore(page.length === PAGE_SIZE);
    }
    setLoadingMore(false);
  }, [hasMore, loadingMore, nextOffset, runId]);

  useEffect(() => {
    const client = getSupabaseBrowserClient();
    if (!client) {
      setRun(demoRun);
      setImages(demoImages);
      setTags(demoTags);
      const stored = JSON.parse(localStorage.getItem(`demo-reviews-${runId}`) ?? "{}") as Record<string, ReviewRecord>;
      setReviews(stored);
      setLoading(false);
      return;
    }

    async function load() {
      const [runResult, imageResult, tagResult, reviewResult] = await Promise.all([
        client!.from("runs").select("*").eq("id", runId).single(),
        client!.from("run_images").select(IMAGE_SELECT).eq("run_id", runId).order("created_at").range(0, PAGE_SIZE - 1),
        client!.from("tags").select("*").eq("active", true).order("label"),
        client!.from("reviews").select("*, review_tags(tag_id)").eq("reviewer_id", reviewerId),
      ]);
      setRun(runResult.data as Run);
      const firstPage = ((imageResult.data ?? []) as Array<Record<string, unknown>>).map(normalizeImage);
      setImages(firstPage);
      setNextOffset(firstPage.length);
      setHasMore(firstPage.length === PAGE_SIZE);
      setTags((tagResult.data as Tag[]) ?? []);
      const reviewMap: Record<string, ReviewRecord> = {};
      for (const raw of (reviewResult.data ?? []) as Array<Record<string, unknown>>) {
        const reviewTags = (raw.review_tags as Array<{ tag_id: string }>) ?? [];
        reviewMap[String(raw.image_id)] = { ...(raw as unknown as ReviewRecord), tag_ids: reviewTags.map((value) => value.tag_id) };
      }
      setReviews(reviewMap);
      setLoading(false);
    }
    load();

    const imageChannel = client.channel(`run-images-${runId}`).on(
      "postgres_changes",
      { event: "INSERT", schema: "public", table: "run_images", filter: `run_id=eq.${runId}` },
      (payload) => loadImage(String(payload.new.id)),
    ).subscribe();
    const predictionChannel = client.channel(`prediction-tags-${runId}`).on(
      "postgres_changes",
      { event: "*", schema: "public", table: "predictions" },
      (payload) => {
        const next = payload.new as { run_image_id?: string };
        const previous = payload.old as { run_image_id?: string };
        const candidateId = next.run_image_id ?? previous.run_image_id;
        if (candidateId) loadImage(candidateId);
      },
    ).subscribe();
    const reviewChannel = client.channel(`team-reviews-${runId}`).on(
      "postgres_changes",
      { event: "*", schema: "public", table: "reviews" },
      (payload) => {
        const next = payload.new as { image_id?: string };
        const previous = payload.old as { image_id?: string };
        const candidateId = next.image_id ?? previous.image_id;
        if (candidateId) loadImage(candidateId);
      },
    ).subscribe();
    const runChannel = client.channel(`run-progress-${runId}`).on(
      "postgres_changes",
      { event: "UPDATE", schema: "public", table: "runs", filter: `id=eq.${runId}` },
      (payload) => setRun(payload.new as Run),
    ).subscribe();
    return () => { client.removeChannel(imageChannel); client.removeChannel(predictionChannel); client.removeChannel(reviewChannel); client.removeChannel(runChannel); };
  }, [loadImage, reviewerId, runId]);

  useEffect(() => {
    setSelectedTags((current) => {
      const next = { ...current };
      for (const image of images) {
        if (next[image.id]) continue;
        const ownReview = reviews[image.id];
        next[image.id] = ownReview
          ? tags.filter((tag) => ownReview.tag_ids.includes(tag.id))
          : image.predictions.map((prediction) => prediction.tag).filter(Boolean);
      }
      return next;
    });
  }, [images, reviews, tags]);

  const filtered = useMemo(() => images.filter((image) => {
    const imageTags = selectedTags[image.id] ?? image.predictions.map((prediction) => prediction.tag);
    const review = reviews[image.id];
    return (!category || imageTags.some((tag) => tag.slug === category))
      && (!decisionFilter || (review?.decision ?? "unreviewed") === decisionFilter)
      && (!search || `${image.image_id} ${imageTags.map((tag) => tag.label).join(" ")}`.toLowerCase().includes(search.toLowerCase()));
  }), [category, decisionFilter, images, reviews, search, selectedTags]);

  const swipeQueue = useMemo(() => filtered.filter((image) => !reviews[image.id]), [filtered, reviews]);
  useEffect(() => { if (cursor >= swipeQueue.length) setCursor(Math.max(0, swipeQueue.length - 1)); }, [cursor, swipeQueue.length]);
  useEffect(() => {
    if (mode === "swipe" && swipeQueue.length - cursor <= 10 && hasMore && !loadingMore) loadMore();
  }, [cursor, hasMore, loadMore, loadingMore, mode, swipeQueue.length]);

  async function createTag(label: string): Promise<Tag | null> {
    if (!label.trim()) return null;
    const existing = tags.find((tag) => tag.label.toLowerCase() === label.trim().toLowerCase());
    if (existing) return existing;
    const tag: Tag = { id: crypto.randomUUID(), slug: slugify(label), label: label.trim(), color: "#38bdf8" };
    const client = getSupabaseBrowserClient();
    if (client) {
      const { data, error } = await client.from("tags").insert({ slug: tag.slug, label: tag.label, color: tag.color, created_by: reviewerId }).select().single();
      if (error) return null;
      Object.assign(tag, data);
    }
    setTags((current) => [...current, tag].sort((a, b) => a.label.localeCompare(b.label)));
    return tag;
  }

  async function saveDecision(image: ReviewImage, decision: Decision) {
    setSavingId(image.id);
    const imageTagIds = (selectedTags[image.id] ?? []).map((tag) => tag.id);
    const client = getSupabaseBrowserClient();
    let record: ReviewRecord;
    if (!client) {
      record = { id: crypto.randomUUID(), image_id: image.id, reviewer_id: reviewerId, decision, notes: null, tag_ids: imageTagIds };
      const next = { ...reviews, [image.id]: record };
      setReviews(next);
      localStorage.setItem(`demo-reviews-${runId}`, JSON.stringify(next));
    } else {
      const { data, error } = await client.from("reviews").upsert(
        { image_id: image.id, reviewer_id: reviewerId, decision },
        { onConflict: "image_id,reviewer_id" },
      ).select().single();
      if (error || !data) { setSavingId(null); return; }
      await client.from("review_tags").delete().eq("review_id", data.id);
      if (imageTagIds.length) await client.from("review_tags").insert(imageTagIds.map((tagId) => ({ review_id: data.id, tag_id: tagId })));
      record = { ...(data as ReviewRecord), tag_ids: imageTagIds };
      setReviews((current) => ({ ...current, [image.id]: record }));
      loadImage(image.id);
    }
    setCursor((value) => Math.min(value, Math.max(0, swipeQueue.length - 2)));
    setSavingId(null);
  }

  if (loading) return <main className="centered"><div className="spinner" />Loading candidate stream…</main>;
  if (!run) return <main className="centered"><h1>Run not found</h1><Link href="/">Return to runs</Link></main>;
  const progress = Math.min(100, Math.round((run.processed_count / (run.expected_count || run.processed_count || 1)) * 100));

  return (
    <main className="review-page">
      <PortalNav currentRunId={run.id} userEmail={user?.email ?? null} />
      <header className="review-header">
        <div className="review-heading"><div><div className="eyebrow">{run.status} · {progress}% processed</div><h1>{run.name}</h1></div></div>
        <div className="review-header-actions"><span>{images.length} loaded · {run.inserted_count} available</span><div className="segmented"><button className={mode === "swipe" ? "active" : ""} onClick={() => setMode("swipe")}>One at a time</button><button className={mode === "grid" ? "active" : ""} onClick={() => setMode("grid")}>Grid</button></div></div>
      </header>
      <div className="live-progress"><i style={{ width: `${progress}%` }} /><span className={`live-dot ${run.status}`} />{run.status === "running" ? "Live—new results will appear automatically" : "Run complete"}</div>
      <section className="filters">
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search image ID or tag" />
        <select value={category} onChange={(event) => setCategory(event.target.value)}><option value="">All tags</option>{tags.map((tag) => <option key={tag.id} value={tag.slug}>{tag.label}</option>)}</select>
        <select value={decisionFilter} onChange={(event) => setDecisionFilter(event.target.value)}><option value="">All decisions</option><option value="unreviewed">Unreviewed</option><option value="accept">Accepted</option><option value="uncertain">Uncertain</option><option value="reject">Rejected</option></select>
        <span className="review-count">{Object.keys(reviews).length} reviewed by you</span>
      </section>
      {mode === "swipe" ? (
        <SwipeReview
          image={swipeQueue[cursor] ?? null}
          position={cursor}
          total={swipeQueue.length}
          tags={tags}
          selectedTags={swipeQueue[cursor] ? (selectedTags[swipeQueue[cursor].id] ?? []) : []}
          saving={savingId != null}
          waitingForMore={loadingMore || hasMore || run.status === "running"}
          onTagsChange={(value) => swipeQueue[cursor] && setSelectedTags((current) => ({ ...current, [swipeQueue[cursor].id]: value }))}
          onCreateTag={createTag}
          onDecision={(decision) => swipeQueue[cursor] && saveDecision(swipeQueue[cursor], decision)}
        />
      ) : (
        <>
          <section className="image-grid">
            {filtered.map((image) => <ImageCard key={image.id} image={image} tags={tags} selectedTags={selectedTags[image.id] ?? []} decision={reviews[image.id]?.decision} saving={savingId === image.id} onTagsChange={(value) => setSelectedTags((current) => ({ ...current, [image.id]: value }))} onCreateTag={createTag} onDecision={(decision) => saveDecision(image, decision)} />)}
          </section>
          {loadError && <p className="error load-message">Could not load the next page: {loadError}</p>}
          {hasMore && <button className="load-more" disabled={loadingMore} onClick={loadMore}>{loadingMore ? "Loading…" : `Load ${PAGE_SIZE} more`}</button>}
        </>
      )}
    </main>
  );
}
