import { createClient } from "@supabase/supabase-js";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const maxDuration = 60;

type IngestPrediction = { tag: string; label?: string; score: number; source?: string; model_version?: string; evidence?: Record<string, unknown> };
type IngestImage = {
  id: string;
  image_url: string;
  thumbnail_url: string;
  captured_at?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  mission?: string;
  roll?: string;
  frame?: string;
  ranking_score?: number;
  metadata?: Record<string, unknown>;
  predictions?: IngestPrediction[];
};

function adminClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) throw new Error("Supabase server environment variables are missing");
  return createClient(url, key, { auth: { persistSession: false, autoRefreshToken: false } });
}

export async function POST(request: NextRequest) {
  const ingestKey = process.env.INGEST_API_KEY;
  if (!ingestKey || request.headers.get("authorization") !== `Bearer ${ingestKey}`) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const body = await request.json().catch(() => null);
  if (!body) return NextResponse.json({ error: "The request body must be valid JSON" }, { status: 400 });
  const images = (body.images ?? []) as IngestImage[];
  if (!body.run?.name || images.length > 200 || images.some((image) => !image.id || !image.image_url || !image.thumbnail_url)) {
    return NextResponse.json({ error: "A run name is required and batches may contain at most 200 images" }, { status: 400 });
  }
  const supabase = adminClient();
  const runPayload = {
    ...(body.run.id ? { id: body.run.id } : {}),
    name: body.run.name,
    description: body.run.description ?? null,
    status: body.run.status ?? "running",
    expected_count: body.run.expected_count ?? null,
    ...(body.run.processed_count != null ? { processed_count: body.run.processed_count } : {}),
    config: body.run.config ?? {},
    ...(body.run.status === "complete" ? { completed_at: new Date().toISOString() } : {}),
  };
  const { data: run, error: runError } = await supabase.from("runs").upsert(runPayload).select().single();
  if (runError) return NextResponse.json({ error: runError.message }, { status: 500 });
  if (!images.length) return NextResponse.json({ run, inserted: 0 });

  const { error: imageError } = await supabase.from("images").upsert(images.map((image) => ({
    id: image.id,
    image_url: image.image_url,
    thumbnail_url: image.thumbnail_url,
    captured_at: image.captured_at ?? null,
    latitude: image.latitude ?? null,
    longitude: image.longitude ?? null,
    mission: image.mission ?? null,
    roll: image.roll ?? null,
    frame: image.frame ?? null,
    metadata: image.metadata ?? {},
  })));
  if (imageError) return NextResponse.json({ error: imageError.message }, { status: 500 });

  const { data: runImages, error: candidateError } = await supabase.from("run_images").upsert(
    images.map((image) => ({ run_id: run.id, image_id: image.id, ranking_score: image.ranking_score ?? null })),
    { onConflict: "run_id,image_id" },
  ).select("id,image_id");
  if (candidateError) return NextResponse.json({ error: candidateError.message }, { status: 500 });

  const predictions = images.flatMap((image) => image.predictions ?? []);
  const uniqueTags = [...new Map(predictions.map((prediction) => [prediction.tag, prediction])).values()];
  if (uniqueTags.length) {
    const { error: tagError } = await supabase.from("tags").upsert(uniqueTags.map((prediction) => ({
      slug: prediction.tag,
      label: prediction.label ?? prediction.tag.replaceAll("_", " ").replace(/\b\w/g, (value) => value.toUpperCase()),
    })), { onConflict: "slug" });
    if (tagError) return NextResponse.json({ error: tagError.message }, { status: 500 });
    const { data: tagRows, error: tagReadError } = await supabase.from("tags").select("id,slug").in("slug", uniqueTags.map((tag) => tag.tag));
    if (tagReadError) return NextResponse.json({ error: tagReadError.message }, { status: 500 });
    const tagIds = new Map((tagRows ?? []).map((tag) => [tag.slug, tag.id]));
    const candidateIds = new Map((runImages ?? []).map((candidate) => [candidate.image_id, candidate.id]));
    const predictionRows = images.flatMap((image) => (image.predictions ?? []).map((prediction) => ({
      run_image_id: candidateIds.get(image.id),
      tag_id: tagIds.get(prediction.tag),
      score: Math.max(0, Math.min(1, prediction.score)),
      source: prediction.source ?? "pipeline",
      model_version: prediction.model_version ?? null,
      evidence: prediction.evidence ?? {},
    }))).filter((row) => row.run_image_id && row.tag_id);
    if (predictionRows.length) {
      const { error: predictionError } = await supabase.from("predictions").upsert(predictionRows, { onConflict: "run_image_id,tag_id,source" });
      if (predictionError) return NextResponse.json({ error: predictionError.message }, { status: 500 });
    }
  }

  const { count } = await supabase.from("run_images").select("id", { count: "exact", head: true }).eq("run_id", run.id);
  const { data: updatedRun } = await supabase.from("runs").update({ inserted_count: count ?? 0, processed_count: body.run.processed_count ?? run.processed_count }).eq("id", run.id).select().single();
  return NextResponse.json({ run: updatedRun ?? run, run_id: run.id, inserted: images.length, available: count ?? 0 });
}
