#!/usr/bin/env python3
"""Build a strict open-ocean subset from an existing prediction CSV.

The filter is deliberately conservative. It requires the machine-geolocated
image center and all four footprint corners to be ocean, then samples outward
from every footprint point to confirm that no mapped land occurs within the
configured offshore distance. Only categories that can plausibly occur away
from a coast are retained.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Iterable

import numpy as np
from global_land_mask import globe


EARTH_RADIUS_KM = 6371.0
FOOTPRINT_CORNERS = ("ul", "ur", "ll", "lr")
DEFAULT_CATEGORIES = (
    "sunglint_geometry_candidate",
    "ocean_color",
    "algal_bloom_candidate",
    "sea_ice",
    "floating_material",
    "night_dynamic",
    "night_fishing_boats",
    "tidal_mixing_fronts",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--metadata-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-offshore-km", type=float, default=100.0)
    parser.add_argument("--radial-step-km", type=float, default=5.0)
    parser.add_argument("--bearing-count", type=int, default=36)
    parser.add_argument("--batch-size", type=int, default=400)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--categories", nargs="+", default=list(DEFAULT_CATEGORIES))
    args = parser.parse_args()
    if args.minimum_offshore_km <= 0:
        parser.error("--minimum-offshore-km must be positive")
    if args.radial_step_km <= 0 or args.radial_step_km > args.minimum_offshore_km:
        parser.error("--radial-step-km must be positive and no larger than --minimum-offshore-km")
    if args.bearing_count < 8:
        parser.error("--bearing-count must be at least 8")
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    return args


def read_predictions(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not {"image_id", "year", "categories"}.issubset(reader.fieldnames):
            raise ValueError(f"{path} must contain image_id, year, and categories columns")
        return list(reader.fieldnames), list(reader)


def image_id(record: dict[str, object], prefix: str) -> str:
    return "-".join(str(record.get(f"{prefix}.{field}", "")).strip() for field in ("mission", "roll", "frame"))


def coordinate_points(record: dict[str, object]) -> list[tuple[float, float]] | None:
    fields = [("mlcoord.lat", "mlcoord.lon")]
    fields.extend((f"mlcoord.{corner}_lat", f"mlcoord.{corner}_lon") for corner in FOOTPRINT_CORNERS)
    points: list[tuple[float, float]] = []
    for lat_field, lon_field in fields:
        try:
            latitude = max(-90.0, min(90.0, float(record[lat_field])))
            longitude = (float(record[lon_field]) + 180.0) % 360.0 - 180.0
            points.append((latitude, longitude))
        except (KeyError, TypeError, ValueError):
            return None
    return points


def load_footprints(metadata_root: Path, wanted: set[str], years: Iterable[int]) -> dict[str, list[tuple[float, float]]]:
    footprints: dict[str, list[tuple[float, float]]] = {}
    for year in sorted(set(years)):
        path = metadata_root / "mlcoord" / f"mlcoord_{year}.ndjson"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                candidate_id = image_id(record, "mlcoord")
                if candidate_id not in wanted:
                    continue
                points = coordinate_points(record)
                if points is not None:
                    footprints[candidate_id] = points
    return footprints


def destination_grid(
    latitudes: np.ndarray,
    longitudes: np.ndarray,
    distance_km: float,
    bearings: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    lat1 = np.radians(latitudes)[:, None]
    lon1 = np.radians(longitudes)[:, None]
    angular = distance_km / EARTH_RADIUS_KM
    sin_lat1 = np.sin(lat1)
    cos_lat1 = np.cos(lat1)
    sin_angular = math.sin(angular)
    cos_angular = math.cos(angular)
    lat2 = np.arcsin(sin_lat1 * cos_angular + cos_lat1 * sin_angular * np.cos(bearings))
    lon2 = lon1 + np.arctan2(
        np.sin(bearings) * sin_angular * cos_lat1,
        cos_angular - sin_lat1 * np.sin(lat2),
    )
    lon2 = (lon2 + math.pi) % (2 * math.pi) - math.pi
    latitudes_out = np.clip(np.degrees(lat2), -90.0, 90.0)
    longitudes_out = np.clip(np.degrees(lon2), -180.0, 180.0)
    return latitudes_out, longitudes_out


def offshore_batch(
    footprints: list[list[tuple[float, float]]],
    minimum_km: float,
    radial_step_km: float,
    bearing_count: int,
) -> np.ndarray:
    """Return one pass/fail result per five-point image footprint."""
    point_counts = [len(points) for points in footprints]
    latitudes = np.array([lat for points in footprints for lat, _ in points], dtype=np.float64)
    longitudes = np.array([lon for points in footprints for _, lon in points], dtype=np.float64)
    point_passes = np.logical_not(globe.is_land(latitudes, longitudes))
    bearings = np.linspace(0.0, 2 * math.pi, bearing_count, endpoint=False)[None, :]
    distances = list(np.arange(radial_step_km, minimum_km, radial_step_km)) + [minimum_km]
    for distance in distances:
        active = np.flatnonzero(point_passes)
        if not len(active):
            break
        sample_lat, sample_lon = destination_grid(
            latitudes[active], longitudes[active], float(distance), bearings
        )
        has_land = np.any(globe.is_land(sample_lat, sample_lon), axis=1)
        point_passes[active[has_land]] = False
    offsets = np.cumsum([0, *point_counts])
    return np.array([
        bool(np.all(point_passes[offsets[index]:offsets[index + 1]]))
        for index in range(len(footprints))
    ])


def score(row: dict[str, str], categories: list[str]) -> float:
    values = []
    for category in categories:
        try:
            values.append(float(row.get(f"{category}_score", "") or 0.0))
        except ValueError:
            pass
    return max(values, default=0.0)


def main() -> int:
    args = parse_args()
    fields, rows = read_predictions(args.predictions)
    wanted = {row["image_id"] for row in rows}
    years = [int(row["year"]) for row in rows if row.get("year")]
    footprints = load_footprints(args.metadata_root, wanted, years)
    target_categories = set(args.categories)
    candidates: list[tuple[dict[str, str], list[tuple[float, float]], list[str]]] = []
    missing_footprint = 0
    no_offshore_feature = 0
    for row in rows:
        points = footprints.get(row["image_id"])
        if points is None:
            missing_footprint += 1
            continue
        retained = [
            category.strip()
            for category in row.get("categories", "").split(";")
            if category.strip() in target_categories
        ]
        if not retained:
            no_offshore_feature += 1
            continue
        candidates.append((row, points, retained))

    selected: list[dict[str, str]] = []
    for start in range(0, len(candidates), args.batch_size):
        batch = candidates[start:start + args.batch_size]
        passes = offshore_batch(
            [points for _, points, _ in batch],
            args.minimum_offshore_km,
            args.radial_step_km,
            args.bearing_count,
        )
        for (row, points, retained), passed in zip(batch, passes):
            if not passed:
                continue
            result = dict(row)
            result["categories"] = ";".join(retained)
            result["score"] = f"{score(result, retained):.6f}"
            result["open_ocean_offshore_lower_bound_km"] = f"{args.minimum_offshore_km:g}"
            result["open_ocean_footprint_points_checked"] = str(len(points))
            result["open_ocean_method"] = "strict_geospatial_offshore_footprint_gate"
            result["open_ocean_metadata_used"] = json.dumps({
                "Offshore distance": f"No mapped land sampled within {args.minimum_offshore_km:g} km",
                "Image footprint": "Machine-geolocated center and all four corners are over ocean",
                "Land mask": "global-land-mask",
                "Sampling": f"{args.radial_step_km:g} km radial steps at {args.bearing_count} bearings",
            })
            summary = (
                f"Open-ocean gate passed: the geolocated image center and all four footprint corners are over "
                f"ocean, with no mapped land sampled within {args.minimum_offshore_km:g} km of any footprint point."
            )
            result["evidence"] = "; ".join(value for value in (summary, result.get("evidence", "")) if value)
            selected.append(result)

    selected.sort(
        key=lambda row: (
            "sunglint_geometry_candidate" in row["categories"].split(";"),
            float(row.get("sunglint_geometry_candidate_score", "") or 0.0),
            float(row.get("score", "") or 0.0),
        ),
        reverse=True,
    )
    if args.limit is not None:
        selected = selected[:args.limit]
    extra_fields = [
        "open_ocean_offshore_lower_bound_km",
        "open_ocean_footprint_points_checked",
        "open_ocean_method",
        "open_ocean_metadata_used",
    ]
    output_fields = fields + [field for field in extra_fields if field not in fields]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(selected)

    category_counts = Counter(
        category
        for row in selected
        for category in row["categories"].split(";")
        if category
    )
    print(f"Read {len(rows)} classified images")
    print(f"Loaded {len(footprints)} complete five-point footprints; missing {missing_footprint}")
    print(f"Rejected {no_offshore_feature} images with no retained offshore feature tag")
    print(f"Selected {len(selected)} images at least {args.minimum_offshore_km:g} km from mapped land")
    print("Retained tags: " + json.dumps(dict(category_counts), sort_keys=True))
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Error: {error}")
        raise SystemExit(1)
