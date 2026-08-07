#!/usr/bin/env python3
"""Apply transparent metadata gates and add geometry-based sunglint candidates."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
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


def capture_month(row: dict[str, str]) -> int | None:
    value = "".join(character for character in row.get("date", "") if character.isdigit())
    if len(value) < 6:
        return None
    try:
        month = int(value[4:6])
        return month if 1 <= month <= 12 else None
    except ValueError:
        return None


def sea_ice_region(latitude: float, longitude: float) -> str | None:
    """Return a conservative ocean sector where seasonal sea ice is plausible."""
    if latitude <= -55:
        return "Southern Ocean margin"
    if latitude >= 60:
        return "high northern latitude"
    if 45 <= latitude < 60 and -100 <= longitude <= -45:
        return "Hudson/James Bay or Labrador–Gulf of St. Lawrence sector"
    if 45 <= latitude < 60 and (135 <= longitude <= 180 or -180 <= longitude <= -130):
        return "Sea of Okhotsk, Bering, or Gulf of Alaska sector"
    if 54 <= latitude < 60 and 8 <= longitude <= 32:
        return "Baltic sector"
    return None


def sea_ice_season(latitude: float, month: int | None) -> bool:
    if month is None or abs(latitude) >= 65:
        return True
    if latitude >= 0:
        return month in {11, 12, 1, 2, 3, 4, 5, 6}
    return month in {5, 6, 7, 8, 9, 10, 11}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("output_csv", type=Path)
    parser.add_argument("--sea-ice-min-latitude", type=float, default=45.0)
    parser.add_argument(
        "--sea-ice-require-ocean-center",
        action="store_true",
        help="Require the machine-geolocated photo center to fall over water for automated sea-ice labels.",
    )
    parser.add_argument(
        "--sea-ice-seasonal-region-gate",
        action="store_true",
        help="Restrict automated sea ice to conservative seasonal ocean sectors.",
    )
    parser.add_argument(
        "--reject-sea-ice",
        action="store_true",
        help=(
            "Remove sea-ice candidates after batch-level review finds the batch unreliable. "
            "This is preferable to presenting relative rank as calibrated confidence."
        ),
    )
    parser.add_argument("--night-max-sun-elevation", type=float, default=-6.0)
    parser.add_argument("--add-sunglint", action="store_true")
    parser.add_argument("--sunglint-max-mismatch", type=float, default=10.0)
    parser.add_argument("--report", type=Path, help="Optional JSON summary of gates and final tag counts.")
    args = parser.parse_args()

    land_globe = None
    if args.sea_ice_require_ocean_center:
        try:
            from global_land_mask import globe as land_globe
        except ImportError as error:
            parser.error(
                "--sea-ice-require-ocean-center requires global-land-mask "
                "(install with: pip install global-land-mask)"
            )

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

            if category == "sea_ice":
                if latitude is not None:
                    metadata_used["Target latitude"] = f"{latitude:g}°"
                longitude = number(row, "longitude")
                month = capture_month(row)
                if longitude is not None:
                    metadata_used["Target longitude"] = f"{longitude:g}°"
                method = "Visual similarity constrained by target latitude and NASA solar-elevation metadata."
                if args.reject_sea_ice:
                    metadata_used["Batch QA result"] = "Current sea-ice candidate set rejected"
                    method = (
                        "Candidate removed by batch-level human QA after the relative-rank model confused "
                        "clouds and sunglint with ice."
                    )
                    reject_reason = (
                        "batch-level review found the current sea-ice retrieval unreliable; the visual model "
                        "must pass an absolute, independently calibrated ice gate before this label is restored"
                    )
                elif latitude is not None and abs(latitude) < args.sea_ice_min_latitude:
                    reject_reason = (
                        f"target latitude {latitude:g}° is below the ±{args.sea_ice_min_latitude:g}° sea-ice eligibility gate"
                    )
                elif (
                    land_globe is not None
                    and latitude is not None
                    and longitude is not None
                    and bool(land_globe.is_land(latitude, longitude))
                ):
                    metadata_used["Ocean-center gate"] = "Rejected: target center falls on land"
                    method = (
                        "Visual similarity constrained by target latitude, NASA solar-elevation metadata, "
                        "and a conservative global land/ocean mask."
                    )
                    reject_reason = (
                        "the machine-geolocated photo center falls on land, so snow, glaciers, frozen rivers, "
                        "and cities cannot be automatically labeled as ocean sea ice"
                    )
                elif land_globe is not None and latitude is not None and longitude is not None:
                    metadata_used["Ocean-center gate"] = "Passed: target center falls over water"
                    method = (
                        "Visual similarity constrained by target latitude, NASA solar-elevation metadata, "
                        "and a conservative global land/ocean mask."
                    )
                if (
                    not reject_reason
                    and args.sea_ice_seasonal_region_gate
                    and latitude is not None
                    and longitude is not None
                ):
                    region = sea_ice_region(latitude, longitude)
                    metadata_used["Seasonal sea-ice region"] = region or "Outside conservative sectors"
                    if month is not None:
                        metadata_used["Capture month"] = str(month)
                    if region is None:
                        reject_reason = (
                            "the target is outside the conservative ocean sectors used for automated "
                            "seasonal sea-ice retrieval"
                        )
                    elif not sea_ice_season(latitude, month):
                        reject_reason = (
                            f"capture month {month} falls outside the conservative sea-ice season for this hemisphere"
                        )
                    else:
                        metadata_used["Seasonal-region gate"] = "Passed"
                        method = (
                            "Visual similarity constrained by latitude, solar elevation, an ocean-center mask, "
                            "and conservative regional/seasonal sea-ice plausibility."
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

    category_counts = Counter(
        category
        for row in output
        for category in row.get("categories", "").split(";")
        if category
    )
    if args.report:
        args.report.write_text(json.dumps({
            "input": str(args.input_csv),
            "output": str(args.output_csv),
            "images": len(output),
            "removed_labels": removed_counts,
            "added_sunglint_geometry_candidates": sunglint_count,
            "no_confident_match": no_match_count,
            "category_counts": dict(sorted(category_counts.items())),
            "gates": {
                "sea_ice_min_latitude": args.sea_ice_min_latitude,
                "sea_ice_require_ocean_center": args.sea_ice_require_ocean_center,
                "sea_ice_seasonal_region_gate": args.sea_ice_seasonal_region_gate,
                "night_max_sun_elevation": args.night_max_sun_elevation,
                "sunglint_max_mismatch": args.sunglint_max_mismatch if args.add_sunglint else None,
            },
        }, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {len(output)} metadata-audited images to {args.output_csv}")
    print(f"Removed labels: {json.dumps(removed_counts, sort_keys=True)}")
    print(f"Added sunglint geometry candidates: {sunglint_count}")
    print(f"No confident match after gating: {no_match_count}")
    if args.report:
        print(f"Wrote metadata audit report to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
