#!/usr/bin/env python3
"""Consolidate reviewed labels while replacing legacy sea-ice examples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_rows(path: Path) -> list[dict[str, object]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"Expected a JSON array in {path}")
    return [row for row in value if isinstance(row, dict) and row.get("image_id")]


def categories(row: dict[str, object]) -> list[str]:
    value = row.get("categories", "")
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item for item in str(value).split(";") if item]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical", type=Path, nargs="+", required=True)
    parser.add_argument("--sea-ice-negatives", type=Path, required=True)
    parser.add_argument("--sea-ice-references", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("../production_training_decisions.json"))
    args = parser.parse_args()

    consolidated: dict[str, dict[str, object]] = {}
    for path in args.historical:
        for source in read_rows(path):
            kept = [
                category for category in categories(source)
                if category not in {"sea_ice", "sunglint_waves"}
            ]
            if not kept:
                consolidated.pop(str(source["image_id"]), None)
                continue
            consolidated[str(source["image_id"])] = {
                "image_id": source["image_id"],
                "decision": source.get("decision", "uncertain"),
                "categories": ";".join(kept),
            }

    # Sea ice is deliberately category-isolated because the older multi-label
    # reviews confused clouds, glaciers, snow-covered land, and sunglint with
    # floating ocean ice.  References are applied last so an explicit NASA
    # catalog label wins over any prior model-generated hard negative.
    for source in read_rows(args.sea_ice_negatives):
        consolidated[str(source["image_id"])] = {
            "image_id": source["image_id"],
            "decision": "reject",
            "categories": "sea_ice",
        }
    for source in read_rows(args.sea_ice_references):
        consolidated[str(source["image_id"])] = {
            "image_id": source["image_id"],
            "decision": "accept",
            "categories": "sea_ice",
        }

    rows = sorted(consolidated.values(), key=lambda row: str(row["image_id"]))
    args.output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} consolidated review decisions to {args.output}")
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        for category in str(row["categories"]).split(";"):
            key = (category, str(row["decision"]))
            counts[key] = counts.get(key, 0) + 1
    for (category, decision), count in sorted(counts.items()):
        print(f"{category:24} {decision:9} {count:4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
