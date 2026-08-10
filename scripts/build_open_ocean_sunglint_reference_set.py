#!/usr/bin/env python3
"""Build NASA-captioned open-ocean sunglint and surface-physics references."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from pathlib import Path


GLINT = re.compile(r"\b(?:sun\s*[- ]?glint|sunglint|sun\s*[- ]?glitter|sunglitter)\b", re.I)
COASTAL = re.compile(
    r"\b(?:island|coast|shore|bay|gulf|lake|river|strait|harbou?r|delta|peninsula|reef|atoll|"
    r"beach|estuary|cape|city|port|land|channel|bahia|littoral|peru|south america|clipperton)\w*\b",
    re.I,
)
OCEAN_CONTEXT = re.compile(
    r"\b(?:ocean|sea surface|surface water|water mass|internal wave|surface current|current structure|"
    r"current shear|sea roughness|sea traffic|swell pattern|capillary wave|ship wake|oil slick|plankton bloom)\w*\b",
    re.I,
)
PHYSICS = re.compile(
    r"\b(?:internal wave|front|water mass|current|eddy|wind|roughness|slick|wake|swell|capillary wave|"
    r"downdraft|down-draft)\w*\b",
    re.I,
)
COLOR = re.compile(r"\b(?:plankton|bloom|ocean colo(?:u)?r)\w*\b", re.I)
TAG_FIELDS = (
    "score", "source", "model_version", "method", "reason", "metadata_used",
)

# These frames were individually inspected after the caption search. Each image
# contains open water without a visible coast, island, reef, or atoll. Keep this
# list deliberately small: it is intended to be a high-precision reference set,
# not another automatically accepted prediction batch.
VERIFIED_OPEN_OCEAN_IDS = {
    "ISS040-E-87412",
    "STS036-86-6",
    "STS039-93-66",
    "STS040-72-87",
    "STS044-79-76",
    "STS046-79-17",
    "STS048-102-67",
    "STS049-83-94",
    "STS049-96-84",
    "STS068-216-102",
    "STS077-718-47",
    "STS086-723-63",
}


def clean_caption(value: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", html.unescape(value), flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument(
        "--verified-only",
        action="store_true",
        help="emit only the manually inspected, noncoastal open-ocean reference frames",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows: dict[str, dict[str, str]] = {}
    for path in sorted((args.metadata_root / "captions").glob("captions_*.ndjson")):
        year = int(path.stem.rsplit("_", 1)[1])
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                caption = clean_caption(str(record.get("captions.caption", "")))
                if not GLINT.search(caption) or COASTAL.search(caption) or not OCEAN_CONTEXT.search(caption):
                    continue
                mission = str(record.get("captions.mission", "")).strip()
                roll = str(record.get("captions.roll", "")).strip()
                frame = str(record.get("captions.frame", "")).strip()
                if not all((mission, roll, frame)):
                    continue
                candidate_id = f"{mission}-{roll}-{frame}"
                if args.verified_only and candidate_id not in VERIFIED_OPEN_OCEAN_IDS:
                    continue
                if mission.startswith("ISS"):
                    image_url = f"https://eol.jsc.nasa.gov/DatabaseImages/ESC/large/{mission}/{candidate_id}.JPG"
                    thumbnail_url = f"https://eol.jsc.nasa.gov/DatabaseImages/ESC/small/{mission}/{candidate_id}.JPG"
                else:
                    image_url = f"https://eol.jsc.nasa.gov/DatabaseImages/ISD/highres/{mission}/{candidate_id}.JPG"
                    thumbnail_url = f"https://eol.jsc.nasa.gov/DatabaseImages/ISD/lowres/{mission}/{candidate_id}.JPG"
                categories = ["open_ocean_sunglint"]
                if not args.verified_only and PHYSICS.search(caption):
                    categories.append("tidal_mixing_fronts")
                if not args.verified_only and COLOR.search(caption):
                    categories.append("ocean_color")
                result = {
                    "image_id": candidate_id,
                    "image_url": image_url,
                    "thumbnail_url": thumbnail_url,
                    "year": str(year),
                    "score": "1.0",
                    "categories": ";".join(categories),
                    "sources": "nasa_caption_reference",
                    "date": str(record.get("__pdate", "")),
                    "latitude": "",
                    "longitude": "",
                    "cloud_percent": "",
                    "geography": "open ocean",
                    "mission": mission,
                    "roll": roll,
                    "frame": frame,
                    "evidence": caption,
                    "model_source": "nasa_caption_reference",
                    "model_version": "NASA Earth Observations caption archive",
                }
                for category in categories:
                    result[f"{category}_score"] = "1.0"
                    result[f"{category}_source"] = "nasa_caption_reference"
                    result[f"{category}_model_version"] = "NASA Earth Observations caption archive"
                    result[f"{category}_method"] = (
                        "explicit_nasa_caption_plus_visual_verification"
                        if args.verified_only
                        else "explicit_nasa_caption_reference"
                    )
                    result[f"{category}_reason"] = caption
                    result[f"{category}_metadata_used"] = json.dumps({
                        "NASA caption": caption,
                        "Reference interpretation": (
                            "Explicitly captioned sunglint over open-ocean water; image individually checked for visible land, islands, reefs, and atolls"
                            if args.verified_only
                            else "Explicitly captioned sunglint over open-ocean water; coastal terms excluded"
                        ),
                    })
                # Prefer captions that describe physical ocean structures, then newer records.
                result["_sort"] = str((2 if PHYSICS.search(caption) else 0) + (1 if COLOR.search(caption) else 0))
                rows[candidate_id] = result

    selected = sorted(
        rows.values(),
        key=lambda row: (int(row["_sort"]), int(row["year"]), row["image_id"]),
        reverse=True,
    )[:args.limit]
    fields = [
        "image_id", "image_url", "thumbnail_url", "year", "score", "categories", "sources", "date", "latitude", "longitude",
        "cloud_percent", "geography", "mission", "roll", "frame", "evidence", "model_source", "model_version",
    ]
    for category in ("open_ocean_sunglint", "tidal_mixing_fronts", "ocean_color"):
        fields.extend(f"{category}_{suffix}" for suffix in TAG_FIELDS)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(selected)
    counts = {
        category: sum(category in row["categories"].split(";") for row in selected)
        for category in ("open_ocean_sunglint", "tidal_mixing_fronts", "ocean_color")
    }
    print(f"Selected {len(selected)} explicit NASA caption references")
    print("Tags: " + json.dumps(counts, sort_keys=True))
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Error: {error}")
        raise SystemExit(1)
