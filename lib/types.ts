export type Decision = "accept" | "reject" | "uncertain" | "skip";

export interface Tag {
  id: string;
  slug: string;
  label: string;
  color: string;
}

export interface Prediction {
  score: number;
  source: string;
  tag: Tag;
}

export interface ReviewImage {
  id: string;
  image_id: string;
  run_id: string;
  image_url: string;
  thumbnail_url: string;
  captured_at: string | null;
  latitude: number | null;
  longitude: number | null;
  metadata: Record<string, unknown>;
  predictions: Prediction[];
  team_reviews: Record<Decision, number>;
}

export interface Run {
  id: string;
  name: string;
  description: string | null;
  status: "queued" | "running" | "complete" | "failed";
  expected_count: number | null;
  inserted_count: number;
  processed_count: number;
  created_at: string;
}

export interface ReviewRecord {
  id: string;
  image_id: string;
  reviewer_id: string;
  decision: Decision;
  notes: string | null;
  tag_ids: string[];
}
