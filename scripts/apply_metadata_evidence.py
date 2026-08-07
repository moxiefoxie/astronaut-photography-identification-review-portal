#!/usr/bin/env python3
"""Apply transparent metadata gates and add geometry-based sunglint candidates."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


DAYLIGHT_TAGS = {
    "ocean_color",
    "sediment_plume",
    "river_discharge",
    "algal_bloom_candidate",
    "sea_ice",
    "coastal_process",
    "floating_material",
}


def number(row: dict[str, str], key: str) -> float | None:
    try:
        value = row.get(key, "")
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def score(row: dict[str, str], category: str) -> float:
    value = number(row, f"{category}_score")
    return value if value is not None else 0.0


def metadata_json(values: dict[str, str]) -> str:
    return json.dumps(values, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("output_csv", type=Path)
    parser.add_argument("--sea-ice-min-latitude", type=float, default=45.0)
    parser.add_argument("--night-max-sun-elevation", type=float, default=-6.0)
    parser.add_argument("--add-sunglint", action="store_true")
    parser.add_argument("--sunglint-max-mismatch", type=float, default=10.0)
    args = parser.parse_args()

    with args.input_csv.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = list(reader.fieldnames or [])

    output: list[dict[str, str]] = []
    removed_counts: dict[str, int] = {}
    sunglint_count = 0
    no_match_count = 0
    generated_fields: set[str] = set()

    for source in rows:
        row = dict(source)
        original = [value for value in row.get("categories", "").split(";") if value]
        categories = [value for value in original if value != "no_confident_match"]
        rejected: list[str] = []
        latitude = number(row, "latitude")
        sun_elevation = number(row, "sun_elevation")
        sun_azimuth = number(row, "sun_azimuth")
        mismatch_center = number(row, "specular_mismatch_center_deg")
        mismatch_min = number(row, "specular_mismatch_min_deg")

        for category in list(categories):
            metadata_used: dict[str, str] = {}
            method = "Visual similarity from image pixels; no coordinate or angle metadata affected this score."
            reject_reason = ""

            if category in DAYLIGHT_TAGS and sun_elevation is not None:
                metadata_used["Sun elevation"] = f"{sun_elevation:g}°"
                method = "Visual similarity constrained by NASA solar-elevation metadata."
                if sun_elevation <= args.night_max_sun_elevation:
                    reject_reason = f"solar elevation {sun_elevation:g}° indicates a dark frame"
            elif category == "night_dynamic" and sun_elevation is not None:
                metadata_used["Sun elevation"] = f"{sun_elevation:g}°"
                method = "Dark-image similarity constrained by NASA solar-elevation metadata."
                if sun_elevation > args.night_max_sun_elevation:
                    reject_reason = f"solar elevation {sun_elevation:g}° does not satisfy the nighttime gate"

            if category == "sea_ice" and latitude is not None:
                metadata_used["Target latitude"] = f"{latitude:g}°"
                method = "Visual similarity constrained by target latitude and NASA solar-elevation metadata."
                if abs(latitude) < args.sea_ice_min_latitude:
                    reject_reason = (
                        f"target latitude {latitude:g}° is below the ±{args.sea_ice_min_latitude:g}° sea-ice eligibility gate"
                    )

            if category == "algal_bloom_candidate":
                competing = max(score(row, "sediment_plume"), score(row, "river_discharge"))
                if score(row, category) <= competing:
                    reject_reason = (
                        f"the sediment/river alternative ({competing * 100:.1f}%) outranked the algae score "
                        f"({score(row, category) * 100:.1f}%)"
                    )
                    method += " A conservative confusion gate requires algae to outrank sediment and river alternatives."

            row[f"{category}_method"] = method
            row[f"{category}_metadata_used"] = metadata_json(metadata_used)
            generated_fields.update({f"{category}_method", f"{category}_metadata_used"})
            if reject_reason:
                categories.remove(category)
                rejected.append(f"{category.replace('_', ' ')} rejected because {reject_reason}")
                removed_counts[category] = removed_counts.get(category, 0) + 1

        if args.add_sunglint and mismatch_min is not None and mismatch_min <= args.sunglint_max_mismatch:
            category = "sunglint_geometry_candidate"
            categories.append(category)
            geometry_score = 0.6 + 0.4 * max(0.0, 1.0 - mismatch_min / args.sunglint_max_mismatch)
            row[f"{category}_score"] = f"{geometry_score:.6f}"
            row[f"{category}_reason"] = (
                f"The minimum Sun/view specular mismatch across the geolocated image footprint is {mismatch_min:.1f}°, "
                f"within the configured {args.sunglint_max_mismatch:g}° candidate threshold. This geometry supports "
                "possible sunglint, but the reflective ocean pattern still requires visual confirmation."
            )
            row[f"{category}_source"] = "solar_specular_geometry"
            row[f"{category}_model_version"] = "specular-half-vector-v1"
            row[f"{category}_method"] = (
                "Metadata geometry: the NASA Sun direction and camera-to-ground view direction were compared "
                "using the specular half-vector mismatch."
            )
            row[f"{category}_metadata_used"] = metadata_json({
                "Target latitude": f"{latitude:g}°" if latitude is not None else "unavailable",
                "Target longitude": f"{number(row, 'longitude'):g}°" if number(row, "longitude") is not None else "unavailable",
                "Sun elevation": f"{sun_elevation:g}°" if sun_elevation is not None else "unavailable",
                "Sun azimuth": f"{sun_azimuth:g}°" if sun_azimuth is not None else "unavailable",
                "Center mismatch": f"{mismatch_center:.1f}°" if mismatch_center is not None else "unavailable",
                "Minimum footprint mismatch": f"{mismatch_min:.1f}°",
            })
            generated_fields.update({
                f"{category}_score", f"{category}_reason", f"{category}_source",
                f"{category}_model_version", f"{category}_method", f"{category}_metadata_used",
            })
            sunglint_count += 1

        if not categories:
            categories = ["no_confident_match"]
            row["no_confident_match_score"] = "1.000000"
            row["no_confident_match_reason"] = (
                "Metadata and confusion gates removed the candidate labels. " + "; ".join(rejected)
                if rejected else row.get("no_confident_match_reason", "No target category met the configured gates.")
            )
            row["no_confident_match_method"] = "Conservative rule-based catalog status."
            row["no_confident_match_metadata_used"] = metadata_json({
                "Gate results": "; ".join(rejected) or "No target category met the configured gates"
            })
            generated_fields.update({
                "no_confident_match_score", "no_confident_match_reason",
                "no_confident_match_method", "no_confident_match_metadata_used",
            })
            no_match_count += 1

        row["categories"] = ";".join(dict.fromkeys(categories))
        category_scores = [score(row, category) for category in categories if category != "no_confident_match"]
        if category_scores:
            row["score"] = f"{max(category_scores) * 100:.1f}"
        output.append(row)

    fields.extend(field for field in sorted(generated_fields) if field not in fields)
    with args.output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(output)

    print(f"Wrote {len(output)} metadata-audited images to {args.output_csv}")
    print(f"Removed labels: {json.dumps(removed_counts, sort_keys=True)}")
    print(f"Added sunglint geometry candidates: {sunglint_count}")
    print(f"No confident match after gating: {no_match_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
