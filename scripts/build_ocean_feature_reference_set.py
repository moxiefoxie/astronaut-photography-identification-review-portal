#!/usr/bin/env python3
"""Build traceable NASA-caption reference sets for newer ocean feature tags."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import ssl
import time
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError

import certifi


TARGETS = (
    "tidal_mixing_fronts",
    "shoreline_sediment_transport",
    "night_fishing_boats",
    "bioluminescence",
)

MARINE = re.compile(r"\b(ocean|sea|bay|gulf|strait|coast|shore|water|river plume)\b", re.I)
COASTAL = re.compile(r"\b(ocean|sea|bay|gulf|strait|coast\w*|shore\w*|nearshore|estuar\w*|delta)\b", re.I)
LAND_SEDIMENT = re.compile(r"\b(dune|dunefield|desert|dust storm|sand sea)\b", re.I)


def clean(value: object) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(str(value or ""))).split())


def categories_for(text: str, solar_elevation: float | None) -> set[str]:
    """Return only tags explicitly supported by the NASA feature text/caption."""
    result: set[str] = set()
    normalized = text.lower()

    negative_longshore = bool(re.search(r"\bno longshore (?:current|visible|activity)\b", normalized))
    shoreline_transport = (not negative_longshore) and bool(
        re.search(r"\b(longshore|alongshore|littoral)\b", normalized)
        and re.search(r"\b(sediment|sand|plume|current|transport|drift|deposit)\w*\b", normalized)
    )
    for clause in re.split(r"[.!?;]+", normalized):
        explicit_motion = re.search(
            r"\b(sediment\w*.{0,80}(?:transport|carri|mov|spread|flow|dispers|advect)\w*|"
            r"(?:transport|carri|mov|spread|flow|dispers|advect)\w*.{0,80}sediment\w*)",
            clause,
        )
        visible_pattern = re.search(
            r"\b(visible|seen|shows?|image|photograph|patterns?|plumes?|color\w*|turbid\w*|"
            r"streamlin\w*|ribbons?|swirls?|streaks?|bands?|lens)\b",
            clause,
        )
        historical_only = re.search(r"\b(ancient|historical|thousands? of years|million years|in the past)\b", clause)
        if COASTAL.search(clause) and explicit_motion and visible_pattern and not historical_only:
            shoreline_transport = True
            break
    if "coast, sediment transport" in normalized:
        shoreline_transport = True
    if shoreline_transport and COASTAL.search(normalized):
        # Avoid terrestrial sediment transport unless the evidence explicitly
        # describes a shoreline/current process.
        if not LAND_SEDIMENT.search(normalized) or re.search(r"\b(coast|shore|longshore|alongshore|littoral)\b", normalized):
            result.add("shoreline_sediment_transport")

    tidal_surface_feature = (
        r"(?:front\w*|boundar\w*|shear\w*|mix\w*|convergence\w*|turbulen\w*|"
        r"internal waves?|solitons?|current (?:channels?|boundar\w*|shears?|patterns?)|surface currents?|"
        r"sediment plumes?)"
    )
    tidal_front = bool(
        re.search(rf"\b(?:tidal|tide)\w*\b.{{0,180}}\b{tidal_surface_feature}\b", normalized)
        or re.search(rf"\b{tidal_surface_feature}\b.{{0,180}}\b(?:tidal|tide)\w*\b", normalized)
        or re.search(r"\b(current shears?|ocean fronts?|river plume fronts?)\b", normalized)
    )
    for clause in re.split(r"[.!?;]+", normalized):
        atmospheric = re.search(r"\b(storm|cyclone|hurricane|atmospher\w*|weather front|cloud front)\b", clause)
        if atmospheric:
            continue
        if MARINE.search(clause) and re.search(r"\b(frontal boundar\w*|convergence zones?)\b", clause):
            tidal_front = True
            break
    irrelevant_convergence = "intertropical convergence zone" in normalized
    if tidal_front and MARINE.search(normalized) and not irrelevant_convergence and "tidal wetlands" not in normalized:
        result.add("tidal_mixing_fronts")

    fishing = bool(re.search(r"\bfishing (?:boats?|vessels?|fleets?)\b", normalized))
    explicit_night = bool(
        re.search(r"\b(at night|nighttime|night image|night photograph)\b", normalized)
        or (solar_elevation is not None and solar_elevation <= -6)
    )
    # "Fishing boats frequent this region" is not image evidence. Require
    # nighttime/light wording or a nighttime solar-elevation record.
    if fishing and explicit_night and MARINE.search(normalized):
        result.add("night_fishing_boats")

    if re.search(r"\bbioluminescen\w*\b", normalized) and MARINE.search(normalized):
        result.add("bioluminescence")

    return result


def number(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def image_urls(mission: str, image_id: str) -> tuple[str, str]:
    if mission.startswith("ISS"):
        return (
            f"https://eol.jsc.nasa.gov/DatabaseImages/ESC/large/{mission}/{image_id}.JPG",
            f"https://eol.jsc.nasa.gov/DatabaseImages/ESC/small/{mission}/{image_id}.JPG",
        )
    url = f"https://eol.jsc.nasa.gov/DatabaseImages/ISD/lowres/{mission}/{image_id}.JPG"
    return url, url


def collect(metadata_root: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    sources = (
        (metadata_root / "captions", "captions", "caption"),
        (metadata_root / "publicfeatures", "publicfeatures", "features"),
        (metadata_root / "frames", "frames", "feat"),
    )
    for directory, prefix, text_field in sources:
        for path in sorted(directory.glob("*.ndjson")):
            with path.open(errors="ignore") as handle:
                for line in handle:
                    try:
                        source = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    mission = str(source.get(f"{prefix}.mission", "")).strip()
                    roll = str(source.get(f"{prefix}.roll", "")).strip()
                    frame = str(source.get(f"{prefix}.frame", "")).strip()
                    if not mission or not roll or not frame:
                        continue
                    evidence = clean(source.get(f"{prefix}.{text_field}"))
                    geographic_context = clean(source.get(f"{prefix}.geon"))
                    solar_elevation = number(source.get(f"{prefix}.elev"))
                    categories = categories_for(f"{evidence} {geographic_context}", solar_elevation)
                    if (
                        prefix == "frames"
                        and COASTAL.search(geographic_context)
                        and re.search(r"\bsediment (?:flow|transport)\b", evidence, re.I)
                    ):
                        categories.add("shoreline_sediment_transport")
                    if not categories:
                        continue
                    image_id = f"{mission}-{roll}-{frame}"
                    image_url, thumbnail_url = image_urls(mission, image_id)
                    row = result.setdefault(image_id, {
                        "image_id": image_id,
                        "mission": mission,
                        "roll": roll,
                        "frame": frame,
                        "date": str(source.get(f"{prefix}.pdate", "") or source.get("__pdate", "")),
                        "latitude": str(source.get(f"{prefix}.lat", "") or ""),
                        "longitude": str(source.get(f"{prefix}.lon", "") or ""),
                        "sun_elevation": str(source.get(f"{prefix}.elev", "") or ""),
                        "categories": "",
                        "decision": "accept",
                        "score": "1.0",
                        "model_source": "nasa_metadata_reference",
                        "label_source": prefix,
                        "label_evidence": evidence[:4000],
                        "image_url": image_url,
                        "thumbnail_url": thumbnail_url,
                    })
                    existing = set(row["categories"].split(";"))
                    row["categories"] = ";".join(sorted((existing | categories) - {""}))
                    if prefix not in row["label_source"].split(";"):
                        row["label_source"] += f";{prefix}"
                    if evidence and evidence not in row["label_evidence"]:
                        row["label_evidence"] = (row["label_evidence"] + " | " + evidence)[:4000]
                    for source_key, output_key in (
                        ("pdate", "date"), ("lat", "latitude"), ("lon", "longitude"), ("elev", "sun_elevation")
                    ):
                        if not row[output_key] and source.get(f"{prefix}.{source_key}") not in (None, ""):
                            row[output_key] = str(source[f"{prefix}.{source_key}"])
    return result


def download(row: dict[str, str], destination: Path, retries: int = 3) -> bool:
    target = destination / f"{row['image_id']}.JPG"
    if target.exists() and target.stat().st_size:
        return True
    request = urllib.request.Request(row["image_url"], headers={"User-Agent": "NASA-ocean-review/1.0"})
    for attempt in range(retries):
        try:
            context = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(request, timeout=60, context=context) as response:
                target.write_bytes(response.read())
            return target.stat().st_size > 0
        except HTTPError as error:
            if target.exists():
                target.unlink()
            print(f"Skipped {row['image_id']}: {error}")
            return False
        except Exception as error:
            if target.exists():
                target.unlink()
            if attempt + 1 == retries:
                print(f"Skipped {row['image_id']}: {error}")
            else:
                time.sleep(1 + attempt)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-root", type=Path, default=Path(".."))
    parser.add_argument("--images", type=Path, default=Path("../nasa_ocean_feature_reference_images"))
    parser.add_argument("--manifest", type=Path, default=Path("../nasa_ocean_feature_reference_manifest.csv"))
    parser.add_argument("--decisions", type=Path, default=Path("../nasa_ocean_feature_reference_decisions.json"))
    parser.add_argument("--categories", nargs="+", choices=TARGETS, default=list(TARGETS))
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()

    selected = set(args.categories)
    rows = [
        row for row in collect(args.metadata_root).values()
        if set(row["categories"].split(";")) & selected
    ]
    for row in rows:
        row["categories"] = ";".join(sorted(set(row["categories"].split(";")) & selected))
    rows.sort(key=lambda row: (row["categories"], row["image_id"]))

    args.images.mkdir(parents=True, exist_ok=True)
    if args.download:
        rows = [row for row in rows if download(row, args.images)]

    fields = [
        "image_id", "mission", "roll", "frame", "date", "latitude", "longitude", "sun_elevation",
        "categories", "decision", "score", "model_source", "label_source", "label_evidence",
        "image_url", "thumbnail_url",
    ]
    with args.manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    args.decisions.write_text(json.dumps([
        {"image_id": row["image_id"], "decision": "accept", "categories": row["categories"]}
        for row in rows
    ], indent=2), encoding="utf-8")

    counts: Counter[str] = Counter()
    for row in rows:
        counts.update(row["categories"].split(";"))
    print(f"Wrote {len(rows)} explicit NASA feature references")
    for category in TARGETS:
        print(f"  {category}: {counts[category]}")
    print(f"Manifest: {args.manifest}")
    print(f"Decisions: {args.decisions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
