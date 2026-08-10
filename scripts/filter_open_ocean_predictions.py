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
    parser.add_argument(
        "--center-only",
        action="store_true",
        help="Gate only the geolocated image center; useful for preselecting a visual-classifier pool.",
    )
    parser.add_argument(
        "--derive-sunglint-geometry",
        action="store_true",
        help="Create sunglint geometry candidates directly from specular_mismatch_min_deg before filtering.",
    )
    parser.add_argument("--sunglint-max-mismatch", type=float, default=10.0)
    parser.add_argument(
        "--shoreline-shapefile",
        type=Path,
        help="Optional full-resolution GSHHG L1 shapefile used to reject any land polygon inside the photo footprint.",
    )
    parser.add_argument(
        "--minimum-detailed-shoreline-km",
        type=float,
        default=0.0,
        help="When GSHHG is supplied, require the complete footprint to remain this far from its nearest shoreline.",
    )
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
    if args.minimum_detailed_shoreline_km < 0:
        parser.error("--minimum-detailed-shoreline-km cannot be negative")
    if args.minimum_detailed_shoreline_km and not args.shoreline_shapefile:
        parser.error("--minimum-detailed-shoreline-km requires --shoreline-shapefile")
    if args.sunglint_max_mismatch <= 0:
        parser.error("--sunglint-max-mismatch must be positive")
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


def footprint_variants(points: list[tuple[float, float]]):
    """Return dateline-safe Shapely geometries for a center or footprint."""
    from shapely.affinity import translate
    from shapely.geometry import Point, Polygon

    if len(points) == 1:
        latitude, longitude = points[0]
        return [Point(longitude, latitude)]

    center_lon = points[0][1]
    corners = [points[index] for index in (1, 2, 4, 3)]  # UL, UR, LR, LL
    unwrapped = [
        (center_lon + ((longitude - center_lon + 180.0) % 360.0 - 180.0), latitude)
        for latitude, longitude in corners
    ]
    polygon = Polygon(unwrapped)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    variants = []
    for offset in (-360.0, 0.0, 360.0):
        candidate = translate(polygon, xoff=offset)
        minimum_x, _, maximum_x, _ = candidate.bounds
        if maximum_x >= -180.0 and minimum_x <= 180.0:
            variants.append(candidate)
    return variants


def reject_shoreline_intersections(
    candidates: list[tuple[dict[str, str], list[tuple[float, float]], list[str]]],
    shapefile_path: Path,
    minimum_distance_km: float = 0.0,
) -> set[int]:
    """Find photos intersecting or too near full-resolution land."""
    try:
        import shapefile
        from shapely.geometry import shape as shapely_shape
        from shapely.ops import nearest_points
    except ImportError as error:
        raise RuntimeError(
            "--shoreline-shapefile requires the shapely and pyshp packages"
        ) from error

    variant_rows: list[tuple[int, object, float]] = []
    cells: dict[tuple[int, int], list[int]] = {}
    for candidate_index, (_, points, _) in enumerate(candidates):
        center_latitude = points[0][0]
        latitude_expansion = minimum_distance_km / 111.0
        longitude_expansion = minimum_distance_km / max(
            1.0, 111.0 * abs(math.cos(math.radians(center_latitude)))
        )
        for polygon in footprint_variants(points):
            variant_index = len(variant_rows)
            variant_rows.append((candidate_index, polygon, center_latitude))
            minimum_x, minimum_y, maximum_x, maximum_y = polygon.bounds
            minimum_x -= longitude_expansion
            maximum_x += longitude_expansion
            minimum_y -= latitude_expansion
            maximum_y += latitude_expansion
            for cell_lon in range(math.floor(minimum_x), math.floor(maximum_x) + 1):
                for cell_lat in range(math.floor(minimum_y), math.floor(maximum_y) + 1):
                    cells.setdefault((cell_lon, cell_lat), []).append(variant_index)

    rejected: set[int] = set()
    reader = shapefile.Reader(str(shapefile_path))
    cell_keys = tuple(cells)
    for shoreline in reader.iterShapes():
        if not shoreline.points or not shoreline.bbox:
            continue
        minimum_x, minimum_y, maximum_x, maximum_y = shoreline.bbox
        width = max(0, math.floor(maximum_x) - math.floor(minimum_x) + 1)
        height = max(0, math.floor(maximum_y) - math.floor(minimum_y) + 1)
        variant_ids: set[int] = set()
        if width * height <= len(cell_keys):
            for cell_lon in range(math.floor(minimum_x), math.floor(maximum_x) + 1):
                for cell_lat in range(math.floor(minimum_y), math.floor(maximum_y) + 1):
                    variant_ids.update(cells.get((cell_lon, cell_lat), ()))
        else:
            for cell in cell_keys:
                if minimum_x <= cell[0] + 1 and maximum_x >= cell[0] and minimum_y <= cell[1] + 1 and maximum_y >= cell[1]:
                    variant_ids.update(cells[cell])
        if not variant_ids:
            continue
        land = shapely_shape(shoreline.__geo_interface__)
        for variant_index in variant_ids:
            candidate_index, footprint, _ = variant_rows[variant_index]
            if candidate_index in rejected:
                continue
            if footprint.intersects(land):
                rejected.add(candidate_index)
                continue
            if minimum_distance_km:
                footprint_point, land_point = nearest_points(footprint, land)
                distance = haversine_km(
                    footprint_point.y,
                    footprint_point.x,
                    land_point.y,
                    land_point.x,
                )
                if distance < minimum_distance_km:
                    rejected.add(candidate_index)
    return rejected


def haversine_km(first_lat: float, first_lon: float, second_lat: float, second_lon: float) -> float:
    first_latitude, second_latitude = math.radians(first_lat), math.radians(second_lat)
    delta_latitude = second_latitude - first_latitude
    delta_longitude = math.radians((second_lon - first_lon + 180.0) % 360.0 - 180.0)
    value = (
        math.sin(delta_latitude / 2.0) ** 2
        + math.cos(first_latitude) * math.cos(second_latitude) * math.sin(delta_longitude / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(value)))


def main() -> int:
    args = parse_args()
    fields, rows = read_predictions(args.predictions)
    input_count = len(rows)
    if args.derive_sunglint_geometry:
        derived_rows = []
        for row in rows:
            try:
                mismatch = float(row.get("specular_mismatch_min_deg", ""))
            except ValueError:
                continue
            if mismatch > args.sunglint_max_mismatch:
                continue
            geometry_score = 0.6 + 0.4 * max(
                0.0, 1.0 - mismatch / args.sunglint_max_mismatch
            )
            result = dict(row)
            current = [value for value in result.get("categories", "").split(";") if value]
            if "sunglint_geometry_candidate" not in current:
                current.append("sunglint_geometry_candidate")
            result["categories"] = ";".join(current)
            result["sunglint_geometry_candidate_score"] = f"{geometry_score:.6f}"
            result["sunglint_geometry_candidate_source"] = "solar_specular_geometry"
            result["sunglint_geometry_candidate_model_version"] = "specular-half-vector-v1"
            result["sunglint_geometry_candidate_method"] = "solar_specular_geometry"
            result["sunglint_geometry_candidate_reason"] = (
                f"The minimum Sun/view specular mismatch across the geolocated image footprint is {mismatch:.1f}°, "
                f"within the configured {args.sunglint_max_mismatch:g}° candidate threshold. Geometry supports "
                "possible sunglint, but visible reflection still requires review."
            )
            derived_rows.append(result)
        rows = derived_rows
        for field in (
            "sunglint_geometry_candidate_score",
            "sunglint_geometry_candidate_source",
            "sunglint_geometry_candidate_model_version",
            "sunglint_geometry_candidate_method",
            "sunglint_geometry_candidate_reason",
        ):
            if field not in fields:
                fields.append(field)
    wanted = {row["image_id"] for row in rows}
    years = [int(row["year"]) for row in rows if row.get("year")]
    if args.center_only:
        footprints = {}
        for row in rows:
            try:
                latitude = max(-90.0, min(90.0, float(row["latitude"])))
                longitude = (float(row["longitude"]) + 180.0) % 360.0 - 180.0
            except (KeyError, TypeError, ValueError):
                continue
            footprints[row["image_id"]] = [(latitude, longitude)]
    else:
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

    offshore_candidates: list[tuple[dict[str, str], list[tuple[float, float]], list[str]]] = []
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
            offshore_candidates.append((row, points, retained))

    shoreline_rejected: set[int] = set()
    if args.shoreline_shapefile:
        if not args.shoreline_shapefile.exists():
            raise ValueError(f"Shoreline shapefile does not exist: {args.shoreline_shapefile}")
        shoreline_rejected = reject_shoreline_intersections(
            offshore_candidates,
            args.shoreline_shapefile,
            args.minimum_detailed_shoreline_km,
        )

    selected: list[dict[str, str]] = []
    for candidate_index, (row, points, retained) in enumerate(offshore_candidates):
        if candidate_index in shoreline_rejected:
            continue
        result = dict(row)
        result["categories"] = ";".join(retained)
        result["score"] = f"{score(result, retained):.6f}"
        result["open_ocean_offshore_lower_bound_km"] = f"{args.minimum_offshore_km:g}"
        result["open_ocean_footprint_points_checked"] = str(len(points))
        result["open_ocean_method"] = (
            "geospatial_offshore_center_candidate_gate"
            if args.center_only
            else "strict_geospatial_offshore_footprint_gate"
        )
        if args.shoreline_shapefile:
            result["open_ocean_shoreline_dataset"] = "GSHHG 2.3.7 full-resolution L1"
        result["open_ocean_metadata_used"] = json.dumps({
            "Offshore distance": f"No mapped land sampled within {args.minimum_offshore_km:g} km",
            "Image footprint": (
                "Machine-geolocated center is over ocean; the image itself still requires visual land rejection"
                if args.center_only
                else "Machine-geolocated center and all four corners are over ocean"
            ),
            "Land mask": "global-land-mask",
            "Sampling": f"{args.radial_step_km:g} km radial steps at {args.bearing_count} bearings",
            **({
                "Detailed shoreline": (
                    f"{'Image center' if args.center_only else 'Complete four-corner image footprint'} is at least {args.minimum_detailed_shoreline_km:g} km from "
                    "GSHHG 2.3.7 full-resolution land"
                    if args.minimum_detailed_shoreline_km
                    else (
                        "The image center does not intersect GSHHG 2.3.7 full-resolution land"
                        if args.center_only
                        else "No GSHHG 2.3.7 full-resolution land polygon intersects the complete four-corner image footprint"
                    )
                ),
            } if args.shoreline_shapefile else {}),
        })
        summary = (
            f"Open-ocean candidate gate passed: the geolocated image center is over ocean, with no coarse-mask "
            f"land sampled within {args.minimum_offshore_km:g} km of the center. Visual land rejection is still required."
            if args.center_only
            else (
                f"Open-ocean gate passed: the geolocated image center and all four footprint corners are over "
                f"ocean, with no coarse-mask land sampled within {args.minimum_offshore_km:g} km of any footprint point."
            )
        )
        if args.shoreline_shapefile:
            if args.minimum_detailed_shoreline_km:
                summary += (
                    f" The {'image center' if args.center_only else 'complete footprint'} is at least {args.minimum_detailed_shoreline_km:g} km from "
                    "the nearest GSHHG full-resolution shoreline."
                )
            else:
                summary += (
                    " The image center does not intersect GSHHG full-resolution land."
                    if args.center_only
                    else " No GSHHG full-resolution land polygon intersects the complete image footprint."
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
        "open_ocean_shoreline_dataset",
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
    print(f"Read {input_count} input images")
    if args.derive_sunglint_geometry:
        print(f"Derived {len(rows)} sunglint geometry candidates at <= {args.sunglint_max_mismatch:g}° mismatch")
    geometry_label = "geolocated centers" if args.center_only else "complete five-point footprints"
    print(f"Loaded {len(footprints)} {geometry_label}; missing {missing_footprint}")
    print(f"Rejected {no_offshore_feature} images with no retained offshore feature tag")
    if args.shoreline_shapefile:
        print(f"Rejected {len(shoreline_rejected)} images intersecting full-resolution GSHHG land")
    print(f"Selected {len(selected)} images at least {args.minimum_offshore_km:g} km from mapped land")
    print("Retained tags: " + json.dumps(dict(category_counts), sort_keys=True))
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"Error: {error}")
        raise SystemExit(1)
