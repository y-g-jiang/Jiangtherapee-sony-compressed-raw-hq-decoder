#!/usr/bin/env python3
"""Compute paired BD-rate summaries from controlled-rate RAW codec metrics.

Expected input is a metrics CSV with at least:

  codec,source_id,actual_bpp,metric,value

Optional columns such as split/subset are preserved as grouping keys. The tool
integrates log(rate) over the common quality interval per source, then reports
per-group paired summaries. Negative BD-rate means codec A uses fewer bits than
codec B for the same quality.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median


@dataclass(frozen=True)
class Point:
    rate: float
    quality: float


def parse_float(value: str, field: str) -> float:
    try:
        out = float(value)
    except ValueError as exc:
        raise ValueError(f"invalid {field} value {value!r}") from exc
    if not math.isfinite(out):
        raise ValueError(f"non-finite {field} value {value!r}")
    return out


def load_points(
    path: Path, metric: str, group_fields: list[str], lower_is_better: bool = False
) -> dict[tuple[str, str, tuple[str, ...]], list[Point]]:
    rows: dict[tuple[str, str, tuple[str, ...]], list[Point]] = defaultdict(list)
    with path.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        required = {"codec", "source_id", "actual_bpp", "metric", "value"}
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"{path} missing required columns: {', '.join(missing)}")
        for row in reader:
            if row["metric"] != metric:
                continue
            codec = row["codec"]
            source_id = row["source_id"]
            group = tuple(row.get(field, "") for field in group_fields)
            rate = parse_float(row["actual_bpp"], "actual_bpp")
            try:
                quality = parse_float(row["value"], "value")
            except ValueError as exc:
                if "non-finite" in str(exc):
                    continue
                raise
            if lower_is_better:
                quality = -quality
            if rate <= 0:
                raise ValueError(f"actual_bpp must be positive, got {rate} for {codec}/{source_id}")
            rows[(codec, source_id, group)].append(Point(rate=rate, quality=quality))
    return rows


def dedupe_and_sort(points: list[Point]) -> list[Point]:
    by_quality: dict[float, float] = {}
    for point in points:
        if point.quality not in by_quality or point.rate < by_quality[point.quality]:
            by_quality[point.quality] = point.rate
    return [Point(rate=by_quality[q], quality=q) for q in sorted(by_quality)]


def interp_log_rate(points: list[Point], quality: float) -> float:
    if quality <= points[0].quality:
        return math.log(points[0].rate)
    if quality >= points[-1].quality:
        return math.log(points[-1].rate)
    for left, right in zip(points, points[1:]):
        if left.quality <= quality <= right.quality:
            span = right.quality - left.quality
            if span == 0:
                return math.log(min(left.rate, right.rate))
            t = (quality - left.quality) / span
            return math.log(left.rate) * (1 - t) + math.log(right.rate) * t
    return math.log(points[-1].rate)


def integrate_delta_log_rate(a_points: list[Point], b_points: list[Point], samples: int) -> tuple[float, float, float]:
    a_points = dedupe_and_sort(a_points)
    b_points = dedupe_and_sort(b_points)
    if len(a_points) < 4 or len(b_points) < 4:
        raise ValueError("BD-rate requires at least four quality points per codec")
    q_min = max(a_points[0].quality, b_points[0].quality)
    q_max = min(a_points[-1].quality, b_points[-1].quality)
    if q_max <= q_min:
        raise ValueError("no overlapping quality interval")
    if samples < 2:
        raise ValueError("samples must be at least 2")
    step = (q_max - q_min) / (samples - 1)
    values: list[float] = []
    for i in range(samples):
        q = q_min + step * i
        values.append(interp_log_rate(a_points, q) - interp_log_rate(b_points, q))
    total = 0.0
    for left, right in zip(values, values[1:]):
        total += 0.5 * (left + right) * step
    mean_delta = total / (q_max - q_min)
    return math.exp(mean_delta) - 1.0, q_min, q_max


def percentile(values: list[float], p: float) -> float:
    if not values:
        raise ValueError("cannot take percentile of empty list")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * p
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--metrics", required=True, type=Path, help="metrics.csv path")
    ap.add_argument("--codec-a", required=True, help="first codec; negative BD-rate means this codec is better")
    ap.add_argument("--codec-b", required=True, help="second codec")
    ap.add_argument("--metric", default="PSNR_raw", help="quality metric column value to compare")
    ap.add_argument(
        "--lower-is-better",
        action="store_true",
        help="negate the metric value before integration, for metrics such as MAE or MAX",
    )
    ap.add_argument(
        "--group-field",
        action="append",
        default=[],
        help="optional grouping column such as split/subset; repeatable",
    )
    ap.add_argument("--samples", type=int, default=1001, help="integration samples over common quality interval")
    ap.add_argument("--out", type=Path, help="optional CSV output path")
    ns = ap.parse_args()

    points = load_points(ns.metrics, ns.metric, ns.group_field, ns.lower_is_better)
    grouped: dict[tuple[str, ...], list[dict[str, object]]] = defaultdict(list)
    by_source_group: dict[tuple[str, tuple[str, ...]], dict[str, list[Point]]] = defaultdict(dict)
    for (codec, source_id, group), codec_points in points.items():
        if codec in {ns.codec_a, ns.codec_b}:
            by_source_group[(source_id, group)][codec] = codec_points

    for (source_id, group), codec_map in sorted(by_source_group.items()):
        if ns.codec_a not in codec_map or ns.codec_b not in codec_map:
            continue
        try:
            bd_rate, q_min, q_max = integrate_delta_log_rate(
                codec_map[ns.codec_a], codec_map[ns.codec_b], ns.samples
            )
        except ValueError as exc:
            grouped[group].append(
                {
                    "source_id": source_id,
                    "status": "skipped",
                    "reason": str(exc),
                }
            )
            continue
        grouped[group].append(
            {
                "source_id": source_id,
                "status": "ok",
                "bd_rate": bd_rate,
                "q_min": q_min,
                "q_max": q_max,
            }
        )

    out_rows: list[dict[str, object]] = []
    for group, rows in sorted(grouped.items()):
        ok_values = [float(row["bd_rate"]) for row in rows if row["status"] == "ok"]
        summary = {
            "codec_a": ns.codec_a,
            "codec_b": ns.codec_b,
            "metric": ns.metric,
            "group": "|".join(group),
            "ok_sources": len(ok_values),
            "skipped_sources": sum(1 for row in rows if row["status"] != "ok"),
            "median_bd_rate": median(ok_values) if ok_values else "",
            "p025_bd_rate": percentile(ok_values, 0.025) if ok_values else "",
            "p975_bd_rate": percentile(ok_values, 0.975) if ok_values else "",
        }
        out_rows.append(summary)

    if ns.out:
        fieldnames = [
            "codec_a",
            "codec_b",
            "metric",
            "group",
            "ok_sources",
            "skipped_sources",
            "median_bd_rate",
            "p025_bd_rate",
            "p975_bd_rate",
        ]
        with ns.out.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(out_rows)
    else:
        writer = csv.DictWriter(__import__("sys").stdout, fieldnames=list(out_rows[0]) if out_rows else [])
        if out_rows:
            writer.writeheader()
            writer.writerows(out_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
