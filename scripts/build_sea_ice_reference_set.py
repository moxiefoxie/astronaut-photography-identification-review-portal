#!/usr/bin/env python3
"""Build a traceable NASA metadata reference set for actual floating sea ice."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import ssl
import time
import urllib.request
from urllib.error import HTTPError
from pathlib import Path

import certifi


SEA_ICE_PATTERN = re.compile(
    r"\b(sea[ -]?ice|pack[ -]?ice|ice floes?|polynya|drift ice)\b",
    re.IGNORECASE,
)

MARINE_CONTEXT_PATTERN = re.compile(
    r"\b(sea|ocean|bay|gulf|strait|lake|offshore|coast(?:al|line)?|water)\b",
    re.IGNORECASE,
)


def is_visible_sea_ice_reference(source_text: str) -> bool:
    """Keep descriptions that say sea ice is visible, not merely discussed."""
    normalized = " ".join(source_text.lower().split())
    if not SEA_ICE_PATTERN.search(normalized):
        return False
    weak_mentions = (
        "sea ice is at a minimum",
        "sea ice are also of interest",
        "sea ice is also of interest",
        "boundary of the sea ice",
        "likely mingles with some sea ice",
    )
    if any(phrase in normalized for phrase in weak_mentions):
        return False
    # Some historical captions use "ice floes" for land glaciers.  Require
    # an explicit marine setting before treating that wording as sea ice.
    if "ice floe" in normalized and not re.search(r"\b(sea|pack)[ -]?ice\b", normalized):
        if not MARINE_CONTEXT_PATTERN.search(normalized):
            return False
        if "glacier" in normalized and not re.search(
            r"\b(offshore|sea|ocean|bay|gulf|strait|lake|water)\b", normalized
        ):
            return False
    return True


def records(metadata_root: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    sources = (
        (metadata_root / "publicfeatures", "publicfeatures", "features"),
        (metadata_root / "captions", "captions", "caption"),
    )
    for directory, prefix, text_field in sources:
        for path in sorted(directory.glob("*.ndjson")):
            with path.open(errors="ignore") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    source_text = html.unescape(str(row.get(f"{prefix}.{text_field}", "")))
                    if not is_visible_sea_ice_reference(source_text):
                        continue
                    mission = str(row.get(f"{prefix}.mission", "")).strip()
                    roll = str(row.get(f"{prefix}.roll", "")).strip()
                    frame = str(row.get(f"{prefix}.frame", "")).strip()
                    if not mission or not roll or not frame:
                        continue
                    image_id = f"{mission}-{roll}-{frame}"
                    if mission.startswith("ISS"):
                        image_url = f"https://eol.jsc.nasa.gov/DatabaseImages/ESC/large/{mission}/{image_id}.JPG"
                        thumbnail_url = f"https://eol.jsc.nasa.gov/DatabaseImages/ESC/small/{mission}/{image_id}.JPG"
                    else:
                        image_url = f"https://eol.jsc.nasa.gov/DatabaseImages/ISD/lowres/{mission}/{image_id}.JPG"
                        thumbnail_url = image_url
                    result[image_id] = {
                        "image_id": image_id,
                        "mission": mission,
                        "roll": roll,
                        "frame": frame,
                        "categories": "sea_ice",
                        "decision": "accept",
                        "label_source": prefix,
                        "label_evidence": re.sub(r"<[^>]+>", " ", source_text)[:2000],
                        "image_url": image_url,
                        "thumbnail_url": thumbnail_url,
                    }
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
        except Exception as error:  # Network and individual archive failures should not stop the set.
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
    parser.add_argument("--images", type=Path, default=Path("../nasa_explicit_sea_ice_images"))
    parser.add_argument("--manifest", type=Path, default=Path("../nasa_explicit_sea_ice_manifest.csv"))
    parser.add_argument("--decisions", type=Path, default=Path("../nasa_explicit_sea_ice_decisions.json"))
    parser.add_argument("--historical-decisions", type=Path, nargs="*")
    parser.add_argument("--false-positive-csv", type=Path)
    parser.add_argument("--negatives", type=Path, default=Path("../sea_ice_hard_negatives.json"))
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()

    candidates = records(args.metadata_root)
    args.images.mkdir(parents=True, exist_ok=True)
    kept = []
    for index, row in enumerate(candidates.values(), start=1):
        if not args.download or download(row, args.images):
            kept.append(row)
        print(f"References: {index}/{len(candidates)}", end="\r")
    print()

    fields = [
        "image_id", "mission", "roll", "frame", "categories", "decision",
        "label_source", "label_evidence", "image_url", "thumbnail_url",
    ]
    with args.manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(kept)
    args.decisions.write_text(json.dumps([
        {"image_id": row["image_id"], "decision": "accept", "categories": "sea_ice"}
        for row in kept
    ], indent=2), encoding="utf-8")

    negatives: dict[str, dict[str, str]] = {}
    for path in args.historical_decisions or []:
        for row in json.loads(path.read_text()):
            categories = str(row.get("categories", "")).split(";")
            if row.get("decision") == "reject" and "sea_ice" in categories:
                negatives[row["image_id"]] = {
                    "image_id": row["image_id"], "decision": "reject", "categories": "sea_ice"
                }
    if args.false_positive_csv:
        with args.false_positive_csv.open(encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                if "sea_ice" in row.get("categories", "").split(";"):
                    negatives[row["image_id"]] = {
                        "image_id": row["image_id"], "decision": "reject", "categories": "sea_ice"
                    }
    args.negatives.write_text(json.dumps(list(negatives.values()), indent=2), encoding="utf-8")
    print(f"Wrote {len(kept)} explicit NASA sea-ice references")
    print(f"Manifest: {args.manifest}")
    print(f"Decisions: {args.decisions}")
    print(f"Hard negatives: {args.negatives} ({len(negatives)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
