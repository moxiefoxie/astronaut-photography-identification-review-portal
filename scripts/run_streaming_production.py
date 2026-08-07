#!/usr/bin/env python3
"""Run the visual classifier in resumable download/classify/publish batches.

Only the current batch of NASA thumbnails is stored locally. Each successful
batch is metadata-audited, appended to a durable CSV, and immediately upserted
into one portal run before its thumbnail directory is removed.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CATEGORIES = (
    "ocean_color",
    "sediment_plume",
    "river_discharge",
    "algal_bloom_candidate",
    "sea_ice",
    "coastal_process",
    "floating_material",
    "night_dynamic",
    "night_fishing_boats",
    "shoreline_sediment_transport",
    "tidal_mixing_fronts",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--review-manifests", type=Path, nargs="+", required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--review-images", type=Path, required=True)
    parser.add_argument("--supplemental-decisions", type=Path, nargs="*", default=[])
    parser.add_argument("--supplemental-images", type=Path, nargs="*", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--download-script", type=Path, required=True)
    parser.add_argument("--classifier-script", type=Path, required=True)
    parser.add_argument("--metadata-script", type=Path, required=True)
    parser.add_argument("--ingest-script", type=Path, required=True)
    parser.add_argument("--metadata-root", type=Path, required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--description", default="Streaming NASA ocean-feature production run")
    parser.add_argument("--app-url", default=os.environ.get("REVIEW_APP_URL"))
    parser.add_argument("--api-key", default=os.environ.get("INGEST_API_KEY"))
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--download-workers", type=int, default=8)
    parser.add_argument("--model-batch-size", type=int, default=24)
    parser.add_argument("--minimum-score", type=float, default=0.58)
    parser.add_argument("--max-tags-per-image", type=int, default=3)
    parser.add_argument("--categories", nargs="+", default=list(DEFAULT_CATEGORIES))
    parser.add_argument("--keep-batches", action="store_true")
    args = parser.parse_args()
    if not args.app_url or not args.api_key:
        parser.error("--app-url and --api-key are required (or set REVIEW_APP_URL and INGEST_API_KEY)")
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    return args


def load_ingest(path: Path):
    spec = importlib.util.spec_from_file_location("portal_ingest", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load ingest helpers from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "image_id" not in reader.fieldnames:
            raise ValueError(f"{path} must contain an image_id column")
        return list(reader.fieldnames), list(reader)


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def output_ids(path: Path) -> set[str]:
    if not path.exists() or not path.stat().st_size:
        return set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return {row["image_id"] for row in csv.DictReader(handle) if row.get("image_id")}


def append_csv(source: Path, destination: Path) -> list[dict[str, str]]:
    fields, rows = read_csv(source)
    if not rows:
        return []
    destination.parent.mkdir(parents=True, exist_ok=True)
    exists = destination.exists() and destination.stat().st_size > 0
    if exists:
        current_fields, current_rows = read_csv(destination)
        if current_fields != fields:
            merged_fields = current_fields + [field for field in fields if field not in current_fields]
            write_csv(destination, merged_fields, current_rows)
            fields = merged_fields
    with destination.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(rows)
    return rows


def rows_for_ids(path: Path, image_ids: set[str]) -> list[dict[str, str]]:
    if not path.exists() or not image_ids:
        return []
    _, rows = read_csv(path)
    return [row for row in rows if row.get("image_id") in image_ids]


def command(parts: list[object]) -> None:
    printable = " ".join(str(part) for part in parts)
    print(f"$ {printable}", flush=True)
    subprocess.run([str(part) for part in parts], check=True)


def publish(ingest: Any, args: argparse.Namespace, run_id: str, rows: list[dict[str, str]], processed: int, status: str) -> None:
    images = [ingest.row_to_image(row) for row in rows]
    batches = [images[index:index + 100] for index in range(0, len(images), 100)] or [[]]
    for batch in batches:
        payload = {
            "run": {
                "id": run_id,
                "name": args.run_name,
                "description": args.description,
                "status": status,
                "expected_count": len(POOL_ROWS),
                "processed_count": processed,
                "config": {
                    "streaming": True,
                    "download_batch_size": args.batch_size,
                    "thumbnail_retention": "deleted after classification",
                    "score_interpretation": "batch-relative visual ranking, not calibrated probability",
                    "categories": args.categories,
                },
            },
            "images": batch,
        }
        result = ingest.post_json(args.app_url.rstrip("/") + "/api/ingest", args.api_key, payload)
        print(
            f"Published {len(batch):>3} images · {result.get('available', 0):>6} available · "
            f"{processed}/{len(POOL_ROWS)} processed · run {run_id}",
            flush=True,
        )


def load_state(path: Path, pool: Path, expected: int) -> dict[str, Any]:
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("pool") != str(pool.resolve()) or state.get("expected_count") != expected:
            raise ValueError("Existing streaming state belongs to a different pool")
        return state
    state = {
        "run_id": str(uuid.uuid4()),
        "pool": str(pool.resolve()),
        "expected_count": expected,
        "next_offset": 0,
        "published_images": 0,
        "status": "running",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def process_batch(args: argparse.Namespace, fields: list[str], rows: list[dict[str, str]], batch_number: int) -> Path:
    current = args.work_dir / "current"
    if current.exists():
        shutil.rmtree(current)
    images = current / "images"
    current.mkdir(parents=True)
    pool_path = current / "pool.csv"
    visual_path = current / "visual.csv"
    audited_path = current / "audited.csv"
    report_path = args.report_dir / f"batch_{batch_number:05d}_visual.json"
    audit_report_path = args.report_dir / f"batch_{batch_number:05d}_metadata.json"
    write_csv(pool_path, fields, rows)

    command([
        args.python, args.download_script,
        "--use-manifest", pool_path,
        "--resolve-images", "--download", "--source", "nasa",
        "--image-size", "small", "--workers", args.download_workers,
        "--destination", images,
    ])
    if not any(images.glob("*.JPG")):
        write_csv(audited_path, fields, [])
        return audited_path

    classifier = [
        args.python, args.classifier_script,
        "--review-manifests", *args.review_manifests,
        "--decisions", args.decisions,
        "--review-images", args.review_images,
        "--pool", pool_path,
        "--pool-images", images,
        "--output", visual_path,
        "--report", report_path,
        "--batch-size", args.model_batch_size,
        "--catalog-all",
        "--minimum-score", args.minimum_score,
        "--max-tags-per-image", args.max_tags_per_image,
        "--categories", *args.categories,
    ]
    if args.supplemental_decisions:
        classifier.extend(["--supplemental-decisions", *args.supplemental_decisions])
    if args.supplemental_images:
        classifier.extend(["--supplemental-images", *args.supplemental_images])
    command(classifier)

    command([
        args.python, args.metadata_script,
        visual_path, audited_path,
        "--sea-ice-require-ocean-center",
        "--sea-ice-seasonal-region-gate",
        "--add-sunglint",
        "--report", audit_report_path,
    ])
    return audited_path


POOL_ROWS: list[dict[str, str]] = []


def main() -> int:
    global POOL_ROWS
    args = parse_args()
    pool_fields, POOL_ROWS = read_csv(args.pool)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(args.state, args.pool, len(POOL_ROWS))
    ingest = load_ingest(args.ingest_script)
    run_id = str(state["run_id"])
    completed_ids = output_ids(args.output)

    publish(ingest, args, run_id, [], int(state["next_offset"]), "running")
    for start in range(int(state["next_offset"]), len(POOL_ROWS), args.batch_size):
        end = min(start + args.batch_size, len(POOL_ROWS))
        batch_ids = {row["image_id"] for row in POOL_ROWS[start:end]}
        pending = [row for row in POOL_ROWS[start:end] if row["image_id"] not in completed_ids]
        print(f"Streaming batch {start // args.batch_size + 1}: pool rows {start + 1}-{end} ({len(pending)} pending)", flush=True)
        rows: list[dict[str, str]] = []
        if pending:
            audited = process_batch(args, pool_fields, pending, start // args.batch_size + 1)
            rows = append_csv(audited, args.output)
            completed_ids.update(row["image_id"] for row in rows)
        # Re-publishing the whole completed slice is deliberate: portal
        # upserts are idempotent and this recovers cleanly if a prior process
        # stopped after appending the CSV but before its network request.
        publish_rows = rows_for_ids(args.output, batch_ids)
        if publish_rows:
            publish(ingest, args, run_id, publish_rows, end, "running")
        state.update({
            "next_offset": end,
            "published_images": len(completed_ids),
            "status": "running",
        })
        save_state(args.state, state)
        if not args.keep_batches and (args.work_dir / "current").exists():
            shutil.rmtree(args.work_dir / "current")

    publish(ingest, args, run_id, [], len(POOL_ROWS), "complete")
    state["status"] = "complete"
    state["completed_at"] = datetime.now(timezone.utc).isoformat()
    save_state(args.state, state)
    print(f"Complete: {len(completed_ids)} images published to run {run_id}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Streaming run stopped: {error}", file=sys.stderr)
        raise SystemExit(1)
