import type { ReviewImage, Run, Tag } from "./types";

const demoTags: Tag[] = [
  { id: "tag-sediment", slug: "sediment_plume", label: "Sediment plume", color: "#f59e0b" },
  { id: "tag-night", slug: "night_dynamic", label: "Night imagery", color: "#8b5cf6" },
  { id: "tag-algae", slug: "algal_bloom_candidate", label: "Algal bloom candidate", color: "#22c55e" },
];

export const demoRun: Run = {
  id: "demo-round-3",
  name: "Round 3 — Demo",
  description: "Local demo mode. Connect Supabase to enable team collaboration.",
  status: "running",
  expected_count: 3000,
  inserted_count: 286,
  processed_count: 3000,
  created_at: new Date().toISOString(),
};

export const demoImages: ReviewImage[] = [
  ["ISS070-E-53597", "ISS070", "53597", demoTags[0]],
  ["ISS071-E-144727", "ISS071", "144727", demoTags[2]],
  ["ISS073-E-604742", "ISS073", "604742", demoTags[0]],
  ["ISS073-E-516289", "ISS073", "516289", demoTags[1]],
].map(([id, mission, frame, tag], index) => ({
  id: id as string,
  image_id: id as string,
  run_id: demoRun.id,
  image_url: `https://eol.jsc.nasa.gov/DatabaseImages/ESC/large/${mission}/${id}.JPG`,
  thumbnail_url: `https://eol.jsc.nasa.gov/DatabaseImages/ESC/small/${mission}/${id}.JPG`,
  captured_at: null,
  latitude: null,
  longitude: null,
  metadata: { mission, frame, demo: true },
  predictions: [{
    score: 0.92 - index * 0.04,
    source: "automated visual classifier",
    model_version: "demo-1",
    evidence: {
      reason: index === 0
        ? "Tan water forms a broad plume that spreads into darker coastal water."
        : index === 1
          ? "Green-blue surface color forms a coherent offshore pattern rather than a cloud shadow."
          : index === 2
            ? "A pale suspended-material feature follows the coast and mixes into clearer water."
            : "The frame is dark and contains concentrated light patterns near the ocean surface.",
    },
    tag: tag as Tag,
  }],
  team_reviews: { accept: 0, reject: 0, uncertain: 0, skip: 0 },
}));

export { demoTags };
