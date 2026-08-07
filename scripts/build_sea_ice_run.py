#!/usr/bin/env python3
"""Create a focused sea-ice review run from NASA references and audited candidates."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


FIELDS = [
    "image_id",
    "score",
    "categories",
    "mission",
    "roll",
    "frame",
    "date",
    "latitude",
    "longitude",
    "image_url",
    "thumbnail_url",
    "model_source",
    "model_version",
    "sea_ice_score",
    "sea_ice_reason",
    "sea_ice_method",
    "sea_ice_metadata_used",
    "evidence",
]


def reference_rows(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for source in csv.DictReader(handle):
            evidence = source["label_evidence"].strip()
            rows.append({
                "image_id": source["image_id"],
                "score": "1.0",
                "categories": "sea_ice",
                "mission": source["mission"],
                "roll": source["roll"],
                "frame": source["frame"],
                "image_url": source["image_url"],
                "thumbnail_url": source["thumbnail_url"],
                "model_source": "nasa_metadata_reference",
                "model_version": "nasa-caption-publicfeatures-v1",
                "sea_ice_score": "1.0",
                "sea_ice_reason": (
                    "NASA's catalog metadata explicitly identifies visible sea ice in this frame. "
                    f"Catalog evidence: {evidence}"
                ),
                "sea_ice_method": "explicit_nasa_catalog_label",
                "sea_ice_metadata_used": json.dumps({
                    "label_source": source["label_source"],
                    "label_evidence": evidence,
                    "score_interpretation": "reference label, not model probability",
                }),
                "evidence": "NASA catalog reference label for visible sea ice",
            })
    return rows


def audited_candidate(path: Path, image_id: str) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        candidates = {row["image_id"]: row for row in csv.DictReader(handle)}
    source = candidates[image_id]
    score = source.get("sea_ice_score") or source.get("score") or "0.5"
    metadata = {
        "latitude": source.get("latitude"),
        "longitude": source.get("longitude"),
        "date": "2024-04-27",
        "sun_elevation_degrees": 20,
        "sun_azimuth_degrees": 91,
        "nasa_photo_page": (
            "https://eol.jsc.nasa.gov/SearchPhotos/photo.pl?"
            "mission=ISS071&roll=E&frame=46021"
        ),
        "visual_audit": "white swirls and filaments over open water with dark leads",
        "confounder": "clouds remain visible along the upper-right edge",
        "score_interpretation": "relative model ranking, not probability",
    }
    return {
        "image_id": image_id,
        "score": score,
        "categories": "sea_ice",
        "mission": source.get("mission", ""),
        "roll": source.get("roll", ""),
        "frame": source.get("frame", ""),
        "date": "20240427",
        "latitude": source.get("latitude", ""),
        "longitude": source.get("longitude", ""),
        "image_url": source.get("image_url", ""),
        "thumbnail_url": source.get("thumbnail_url", ""),
        "model_source": "clip_calibrated_plus_metadata_audit",
        "model_version": source.get("model_version", "openai/clip-vit-base-patch32"),
        "sea_ice_score": score,
        "sea_ice_reason": (
            "Calibrated sea-ice candidate: white swirls and filaments appear over the open "
            "Labrador Sea with dark leads between them. The frame was captured near 54.19 N, "
            "55.29 W on April 27, which supports seasonal sea ice. Clouds at the upper-right "
            "edge remain a confounder, so this candidate is intentionally queued for review."
        ),
        "sea_ice_method": "reviewed CLIP prototypes + hard negatives + geospatial/seasonal audit",
        "sea_ice_metadata_used": json.dumps(metadata),
        "evidence": "calibrated visual candidate; sea_ice=" + score,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("../nasa_explicit_sea_ice_manifest.csv"))
    parser.add_argument("--candidates", type=Path, default=Path("../sea_ice_nasa_calibrated_all.csv"))
    parser.add_argument("--output", type=Path, default=Path("../sea_ice_reference_and_candidates.csv"))
    parser.add_argument("--candidate-id", default="ISS071-E-46021")
    args = parser.parse_args()

    rows = reference_rows(args.manifest)
    if args.candidate_id:
        rows.append(audited_candidate(args.candidates, args.candidate_id))
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} sea-ice review records to {args.output}")
    print(f"NASA references: {len(rows) - bool(args.candidate_id)}")
    print(f"Audited new candidates: {int(bool(args.candidate_id))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
