#!/usr/bin/env python3
"""Publish a CSV result stream to the collaborative review application."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="Pipeline CSV containing image_id and categories columns")
    parser.add_argument("--app-url", default=os.environ.get("REVIEW_APP_URL"), help="Deployed app URL (or REVIEW_APP_URL)")
    parser.add_argument("--api-key", default=os.environ.get("INGEST_API_KEY"), help="Ingest secret (or INGEST_API_KEY)")
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--run-id", help="Continue an existing run UUID")
    parser.add_argument("--description", default="Incremental NASA ocean candidate stream")
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--batch-size", type=int, default=100, choices=range(1, 201), metavar="1..200")
    parser.add_argument("--watch", action="store_true", help="Keep watching the CSV and publish newly appended image IDs")
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    args = parser.parse_args()
    if not args.app_url or not args.api_key:
        parser.error("--app-url and --api-key are required (or set REVIEW_APP_URL and INGEST_API_KEY)")
    return args


def as_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def captured_at(value: str | None) -> str | None:
    if not value:
        return None
    for pattern in ("%Y%m%d", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(value, pattern).replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except ValueError:
            continue
    return None


def normalized_slug(value: str) -> str:
    return re.sub(r"^_+|_+$", "", re.sub(r"[^a-z0-9]+", "_", value.strip().lower()))


def category_score(category: str, row: dict[str, str]) -> float:
    for key in (f"{category}_score", f"{category}_confidence", category):
        value = as_float(row.get(key))
        if value is not None:
            return max(0.0, min(1.0, value / 100.0 if value > 1 else value))
    evidence = row.get("evidence", "")
    match = re.search(rf"(?:^|[; ]){re.escape(category)}=([0-9.]+)", evidence)
    if match:
        return max(0.0, min(1.0, float(match.group(1))))
    score = as_float(row.get("score"))
    if score is None:
        return 0.5
    return max(0.0, min(1.0, score / 100.0 if score > 1 else score))


def category_reason(category: str, row: dict[str, str]) -> str:
    for key in (
        f"{category}_reason",
        f"{category}_reasoning",
        f"{category}_evidence",
        "reason",
        "brief_reasoning",
        "label_evidence",
        "evidence",
    ):
        value = (row.get(key) or "").strip()
        if value:
            return value
    return "No explanatory evidence was supplied by this classifier run."


def json_value(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def row_to_image(row: dict[str, str]) -> dict[str, Any]:
    image_id = row["image_id"].strip()
    parts = image_id.split("-", 2)
    mission = row.get("mission") or parts[0]
    roll = row.get("roll") or (parts[1] if len(parts) > 1 else None)
    frame = row.get("frame") or (parts[2] if len(parts) > 2 else None)
    categories = [normalized_slug(value) for value in row.get("categories", "").split(";") if normalized_slug(value)]
    default_source = row.get("model_source") or row.get("source") or row.get("sources") or "automated_visual_classifier"
    if default_source.isdigit():
        default_source = "automated_visual_classifier"
    known = {
        "image_id", "image_url", "thumbnail_url", "score", "categories", "date",
        "latitude", "longitude", "mission", "roll", "frame",
    }
    metadata = {key: value for key, value in row.items() if key not in known and value not in (None, "")}
    scores = {category: category_score(category, row) for category in categories}
    target_scores = {category: score for category, score in scores.items() if category != "no_confident_match"}
    raw_ranking_score = as_float(row.get("score"))
    fallback_ranking_score = (
        max(0.0, min(1.0, raw_ranking_score / 100.0 if raw_ranking_score > 1 else raw_ranking_score))
        if raw_ranking_score is not None else None
    )
    return {
        "id": image_id,
        "image_url": row.get("image_url") or f"https://eol.jsc.nasa.gov/DatabaseImages/ESC/large/{mission}/{image_id}.JPG",
        "thumbnail_url": row.get("thumbnail_url") or f"https://eol.jsc.nasa.gov/DatabaseImages/ESC/small/{mission}/{image_id}.JPG",
        "captured_at": captured_at(row.get("date")),
        "latitude": as_float(row.get("latitude")),
        "longitude": as_float(row.get("longitude")),
        "mission": mission,
        "roll": roll,
        "frame": frame,
        "ranking_score": max(target_scores.values()) if target_scores else fallback_ranking_score,
        "metadata": metadata,
        "predictions": [
            {
                "tag": category,
                "score": scores[category],
                "source": row.get(f"{category}_source") or default_source,
                "model_version": row.get(f"{category}_model_version") or row.get("model_version") or None,
                "evidence": {
                    "reason": category_reason(category, row),
                    "summary": row.get("evidence", ""),
                    "method": row.get(f"{category}_method") or None,
                    "metadata_used": json_value(row.get(f"{category}_metadata_used")),
                },
            }
            for category in categories
            if category != "production_unlabeled"
        ],
    }


def post_json(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            if error.code < 500 or attempt == 3:
                raise RuntimeError(f"Ingest failed ({error.code}): {detail}") from error
        except urllib.error.URLError as error:
            if attempt == 3:
                raise RuntimeError(f"Could not reach ingest endpoint: {error}") from error
        time.sleep(2**attempt)
    raise RuntimeError("Ingest failed after retries")


def rows_from_csv(path: Path) -> Iterable[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "image_id" not in reader.fieldnames:
            raise ValueError("CSV must contain an image_id column")
        yield from reader


def publish_batch(args: argparse.Namespace, run_id: str | None, batch: list[dict[str, Any]], processed: int, status: str) -> str:
    payload = {
        "run": {
            **({"id": run_id} if run_id else {}),
            "name": args.run_name,
            "description": args.description,
            "status": status,
            "expected_count": args.expected_count,
            "processed_count": processed,
            "config": {"source_csv": args.csv_path.name, "incremental": True},
        },
        "images": batch,
    }
    result = post_json(args.app_url.rstrip("/") + "/api/ingest", args.api_key, payload)
    print(f"Published {len(batch):>3} · {result.get('available', processed):>6} available · run {result.get('run_id', run_id)}", flush=True)
    return str(result.get("run_id") or run_id)


def main() -> int:
    args = parse_args()
    seen: set[str] = set()
    run_id = args.run_id
    processed = 0
    print(f"Publishing {args.csv_path} to {args.app_url.rstrip('/')} in batches of {args.batch_size}")
    while True:
        batch: list[dict[str, Any]] = []
        for row in rows_from_csv(args.csv_path):
            image_id = row.get("image_id", "").strip()
            if not image_id or image_id in seen:
                continue
            seen.add(image_id)
            batch.append(row_to_image(row))
            processed += 1
            if len(batch) == args.batch_size:
                run_id = publish_batch(args, run_id, batch, processed, "running")
                batch = []
        if batch:
            run_id = publish_batch(args, run_id, batch, processed, "running")
        if not args.watch:
            publish_batch(args, run_id, [], processed, "complete")
            print(f"Complete: {processed} unique images published.")
            return 0
        time.sleep(max(0.5, args.poll_seconds))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
