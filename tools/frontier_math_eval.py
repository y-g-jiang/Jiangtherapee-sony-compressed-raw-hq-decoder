#!/usr/bin/env python3
"""Frontier-level mathematical evaluation for strict #824/#826 experiments.

This is a post-processing tool. It upgrades simple point comparisons to an
operational RD-set view:

* actual syntax bpp is the only rate axis;
* per-scene operational upper envelopes are built from policy candidates;
* BD-rate is computed per scene, then summarized with paired bootstrap CIs;
* actual-bpp bins report paired upper-envelope deltas with bootstrap CIs;
* Pareto/frontier cardinality and actual-rate support are reported.

It does not claim production encoder optimality.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import statistics
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


SONY = "sony_824_decoder_visible_packet_canonical"
NIKON = "nikon_826_decoder_visible_precinct_canonical"
LABELS = {SONY: "#824 Sony", NIKON: "#826 Nikon"}
COLORS = {SONY: "#2f6fb0", NIKON: "#bf6a22"}


@dataclass(frozen=True)
class Point:
    rate: float
    quality: float
    policy: str = ""


def f(row: dict[str, str], key: str, default: float = float("nan")) -> float:
    value = row.get(key, "")
    if value == "":
        return default
    return float(value)


def percentile(values: list[float], q: float) -> float:
    clean = sorted(v for v in values if math.isfinite(v))
    if not clean:
        return float("nan")
    if len(clean) == 1:
        return clean[0]
    pos = (len(clean) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return clean[lo]
    frac = pos - lo
    return clean[lo] * (1 - frac) + clean[hi] * frac


def median(values: list[float]) -> float:
    clean = [v for v in values if math.isfinite(v)]
    return statistics.median(clean) if clean else float("nan")


def mean(values: list[float]) -> float:
    clean = [v for v in values if math.isfinite(v)]
    return statistics.fmean(clean) if clean else float("nan")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_policy_rows(path: Path) -> list[dict[str, Any]]:
    unique: dict[tuple[str, ...], dict[str, Any]] = {}
    with path.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rate = f(row, "actual_bpp")
            psnr = f(row, "PSNR_raw_whole")
            if not (math.isfinite(rate) and math.isfinite(psnr)) or rate <= 0:
                continue
            key = (
                row["codec"],
                row["source_id"],
                row.get("policy_name", ""),
                row.get("policy_class", ""),
                row.get("knob", ""),
                row.get("bp", ""),
                row.get("br", ""),
                row.get("global_bias", ""),
                row.get("component_offsets", ""),
                row.get("selector_map", ""),
                row.get("selector_cycle_len", ""),
                row.get("actual_bpp", ""),
            )
            unique[key] = {
                "codec": row["codec"],
                "source_id": row["source_id"],
                "actual_bpp": rate,
                "PSNR_raw_whole": psnr,
                "MAE_whole": f(row, "MAE_whole"),
                "MAX_whole": f(row, "MAX_whole"),
                "MSE_whole": f(row, "MSE_whole"),
                "policy_name": row.get("policy_name", ""),
                "policy_class": row.get("policy_class", ""),
                "encode_ms": f(row, "encode_ms"),
            }
    return list(unique.values())


def load_strict_points(metrics_csv: Path, metric: str = "PSNR_raw") -> dict[tuple[str, str], list[Point]]:
    out: dict[tuple[str, str], list[Point]] = defaultdict(list)
    with metrics_csv.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("split") != "whole" or row.get("metric") != metric:
                continue
            rate = f(row, "actual_bpp")
            quality = f(row, "value")
            if math.isfinite(rate) and math.isfinite(quality) and rate > 0:
                out[(row["source_id"], row["codec"])].append(Point(rate, quality, "strict_canonical"))
    return out


def psnr_frontier(points: list[Point]) -> list[Point]:
    """Return nondominated PSNR frontier sorted by actual rate.

    A point is kept if it improves quality over every lower-or-equal-rate point.
    """
    best_at_rate: dict[float, Point] = {}
    for point in points:
        if point.rate <= 0 or not (math.isfinite(point.rate) and math.isfinite(point.quality)):
            continue
        old = best_at_rate.get(point.rate)
        if old is None or point.quality > old.quality:
            best_at_rate[point.rate] = point
    ordered = sorted(best_at_rate.values(), key=lambda p: (p.rate, -p.quality))
    frontier: list[Point] = []
    best_quality = -float("inf")
    for point in ordered:
        if point.quality > best_quality + 1e-10:
            frontier.append(point)
            best_quality = point.quality
    return frontier


def prepare_quality_to_rate(points: list[Point]) -> list[Point]:
    by_quality: dict[float, float] = {}
    for point in psnr_frontier(points):
        old = by_quality.get(point.quality)
        if old is None or point.rate < old:
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
            if span <= 0:
                return math.log(min(left.rate, right.rate))
            t = (quality - left.quality) / span
            return math.log(left.rate) * (1 - t) + math.log(right.rate) * t
    return math.log(points[-1].rate)


def bd_rate(codec_a: list[Point], codec_b: list[Point], samples: int) -> tuple[float, float, float]:
    """Return mean exp(log-rate delta)-1, q_min, q_max.

    codec_a and codec_b are compared at equal quality. Positive means codec_a
    needs more actual bpp than codec_b.
    """
    a = prepare_quality_to_rate(codec_a)
    b = prepare_quality_to_rate(codec_b)
    if len(a) < 4 or len(b) < 4:
        raise ValueError("requires at least four frontier quality points per codec")
    q_min = max(a[0].quality, b[0].quality)
    q_max = min(a[-1].quality, b[-1].quality)
    if q_max <= q_min:
        raise ValueError("no overlapping quality interval")
    samples = max(2, samples)
    step = (q_max - q_min) / (samples - 1)
    values = [
        interp_log_rate(a, q_min + i * step) - interp_log_rate(b, q_min + i * step)
        for i in range(samples)
    ]
    area = sum(0.5 * (left + right) * step for left, right in zip(values, values[1:]))
    return math.exp(area / (q_max - q_min)) - 1.0, q_min, q_max


def compute_scene_bd_task(args: tuple[str, list[Point], list[Point], int, str]) -> dict[str, Any]:
    scene, nikon_points, sony_points, samples, kind = args
    try:
        value, q_min, q_max = bd_rate(nikon_points, sony_points, samples)
        return {
            "kind": kind,
            "source_id": scene,
            "status": "ok",
            "bd_rate_nikon_vs_sony": value,
            "bd_rate_percent": value * 100.0,
            "q_min": q_min,
            "q_max": q_max,
            "nikon_frontier_points": len(prepare_quality_to_rate(nikon_points)),
            "sony_frontier_points": len(prepare_quality_to_rate(sony_points)),
        }
    except ValueError as exc:
        return {
            "kind": kind,
            "source_id": scene,
            "status": "skipped",
            "reason": str(exc),
            "nikon_frontier_points": len(prepare_quality_to_rate(nikon_points)),
            "sony_frontier_points": len(prepare_quality_to_rate(sony_points)),
        }


def bootstrap_chunk(args: tuple[list[float], int, int, str]) -> list[float]:
    values, draws, seed, stat = args
    rng = random.Random(seed)
    n = len(values)
    out: list[float] = []
    for _ in range(draws):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        out.append(median(sample) if stat == "median" else mean(sample))
    return out


def bootstrap_ci(
    values: list[float],
    draws: int,
    seed: int,
    jobs: int,
    stat: str = "median",
) -> tuple[float, float, float]:
    clean = [v for v in values if math.isfinite(v)]
    if not clean:
        return float("nan"), float("nan"), float("nan")
    center = median(clean) if stat == "median" else mean(clean)
    if len(clean) == 1 or draws <= 0:
        return center, center, center
    jobs = max(1, min(jobs, draws))
    counts = [draws // jobs] * jobs
    for i in range(draws % jobs):
        counts[i] += 1
    tasks = [(clean, count, seed + i * 100_003, stat) for i, count in enumerate(counts) if count]
    if jobs == 1:
        samples = [v for task in tasks for v in bootstrap_chunk(task)]
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            samples = [v for chunk in pool.map(bootstrap_chunk, tasks) for v in chunk]
    return center, percentile(samples, 0.025), percentile(samples, 0.975)


def exact_sign_p(wins_a: int, wins_b: int) -> float:
    n = wins_a + wins_b
    if n == 0:
        return float("nan")
    k = min(wins_a, wins_b)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def summarize_bd(rows: list[dict[str, Any]], draws: int, seed: int, jobs: int) -> dict[str, Any]:
    ok = [float(row["bd_rate_percent"]) for row in rows if row.get("status") == "ok"]
    med, lo, hi = bootstrap_ci(ok, draws, seed, jobs, "median")
    avg, avg_lo, avg_hi = bootstrap_ci(ok, draws, seed + 17, jobs, "mean")
    return {
        "ok_sources": len(ok),
        "skipped_sources": sum(1 for row in rows if row.get("status") != "ok"),
        "median_bd_rate_percent": med,
        "median_ci95_low": lo,
        "median_ci95_high": hi,
        "mean_bd_rate_percent": avg,
        "mean_ci95_low": avg_lo,
        "mean_ci95_high": avg_hi,
        "p25_bd_rate_percent": percentile(ok, 0.25),
        "p75_bd_rate_percent": percentile(ok, 0.75),
        "min_bd_rate_percent": min(ok) if ok else float("nan"),
        "max_bd_rate_percent": max(ok) if ok else float("nan"),
    }


def grouped_policy_points(policy_rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[Point]]:
    out: dict[tuple[str, str], list[Point]] = defaultdict(list)
    for row in policy_rows:
        out[(row["source_id"], row["codec"])].append(
            Point(float(row["actual_bpp"]), float(row["PSNR_raw_whole"]), str(row["policy_name"]))
        )
    return out


def policy_frontier_cardinality(policy_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = grouped_policy_points(policy_rows)
    out: list[dict[str, Any]] = []
    for (scene, codec), points in sorted(grouped.items()):
        frontier = psnr_frontier(points)
        rates = [p.rate for p in points]
        qualities = [p.quality for p in points]
        out.append(
            {
                "source_id": scene,
                "codec": codec,
                "codec_label": LABELS.get(codec, codec),
                "candidate_count": len(points),
                "psnr_frontier_count": len(frontier),
                "dominated_count": len(points) - len(frontier),
                "frontier_fraction": len(frontier) / len(points) if points else float("nan"),
                "actual_bpp_min": min(rates),
                "actual_bpp_max": max(rates),
                "actual_bpp_span": max(rates) - min(rates),
                "psnr_min": min(qualities),
                "psnr_max": max(qualities),
                "psnr_span": max(qualities) - min(qualities),
            }
        )
    return out


def actual_bin_best_rows(
    policy_rows: list[dict[str, Any]], bins: list[float], tolerance: float
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in policy_rows:
        grouped[(row["source_id"], row["codec"])].append(row)
    out: list[dict[str, Any]] = []
    for (scene, codec), rows in sorted(grouped.items()):
        for center in bins:
            window = [row for row in rows if abs(float(row["actual_bpp"]) - center) <= tolerance]
            if not window:
                continue
            best = max(window, key=lambda row: float(row["PSNR_raw_whole"]))
            worst = min(window, key=lambda row: float(row["PSNR_raw_whole"]))
            out.append(
                {
                    "source_id": scene,
                    "codec": codec,
                    "bin_center_actual_bpp": center,
                    "bin_tolerance": tolerance,
                    "candidate_count": len(window),
                    "best_psnr": float(best["PSNR_raw_whole"]),
                    "best_actual_bpp": float(best["actual_bpp"]),
                    "best_policy": best["policy_name"],
                    "worst_psnr": float(worst["PSNR_raw_whole"]),
                    "range_width": float(best["PSNR_raw_whole"]) - float(worst["PSNR_raw_whole"]),
                }
            )
    return out


def paired_bin_summary(
    bin_rows: list[dict[str, Any]], draws: int, seed: int, jobs: int
) -> list[dict[str, Any]]:
    by_key = {
        (row["source_id"], float(row["bin_center_actual_bpp"]), row["codec"]): row
        for row in bin_rows
    }
    scenes = sorted({row["source_id"] for row in bin_rows})
    bins = sorted({float(row["bin_center_actual_bpp"]) for row in bin_rows})
    out: list[dict[str, Any]] = []
    for idx, center in enumerate(bins):
        deltas: list[float] = []
        sony_ranges: list[float] = []
        nikon_ranges: list[float] = []
        for scene in scenes:
            sony = by_key.get((scene, center, SONY))
            nikon = by_key.get((scene, center, NIKON))
            if not sony or not nikon:
                continue
            deltas.append(float(sony["best_psnr"]) - float(nikon["best_psnr"]))
            sony_ranges.append(float(sony["range_width"]))
            nikon_ranges.append(float(nikon["range_width"]))
        if not deltas:
            continue
        med, lo, hi = bootstrap_ci(deltas, draws, seed + idx * 31, jobs, "median")
        wins_sony = sum(delta > 1e-9 for delta in deltas)
        wins_nikon = sum(delta < -1e-9 for delta in deltas)
        out.append(
            {
                "bin_center_actual_bpp": center,
                "bin_tolerance": bin_rows[0]["bin_tolerance"],
                "paired_scene_count": len(deltas),
                "coverage_fraction": len(deltas) / len(scenes) if scenes else float("nan"),
                "median_delta_sony_minus_nikon": med,
                "median_ci95_low": lo,
                "median_ci95_high": hi,
                "p25_delta": percentile(deltas, 0.25),
                "p75_delta": percentile(deltas, 0.75),
                "sony_win_count": wins_sony,
                "nikon_win_count": wins_nikon,
                "tie_count": len(deltas) - wins_sony - wins_nikon,
                "two_sided_sign_p": exact_sign_p(wins_sony, wins_nikon),
                "median_sony_range_width": median(sony_ranges),
                "median_nikon_range_width": median(nikon_ranges),
            }
        )
    return out


def rate_support_summary(policy_rows: list[dict[str, Any]], bins: list[float], tolerance: float) -> list[dict[str, Any]]:
    scenes_by_codec = {
        codec: sorted({row["source_id"] for row in policy_rows if row["codec"] == codec})
        for codec in (SONY, NIKON)
    }
    out: list[dict[str, Any]] = []
    for codec in (SONY, NIKON):
        rows = [row for row in policy_rows if row["codec"] == codec]
        for center in bins:
            scene_counts: list[int] = []
            covered = 0
            for scene in scenes_by_codec[codec]:
                count = sum(
                    1
                    for row in rows
                    if row["source_id"] == scene and abs(float(row["actual_bpp"]) - center) <= tolerance
                )
                scene_counts.append(count)
                if count:
                    covered += 1
            out.append(
                {
                    "codec": codec,
                    "codec_label": LABELS[codec],
                    "bin_center_actual_bpp": center,
                    "bin_tolerance": tolerance,
                    "scene_count": len(scene_counts),
                    "covered_scene_count": covered,
                    "coverage_fraction": covered / len(scene_counts) if scene_counts else float("nan"),
                    "median_candidate_count_per_scene": median([float(v) for v in scene_counts]),
                    "max_candidate_count_per_scene": max(scene_counts) if scene_counts else 0,
                }
            )
    return out


def multimetric_pareto_summary(policy_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in policy_rows:
        grouped[(row["source_id"], row["codec"])].append(row)
    out: list[dict[str, Any]] = []
    for (scene, codec), rows in sorted(grouped.items()):
        nd = 0
        for i, row in enumerate(rows):
            dominated = False
            for j, other in enumerate(rows):
                if i == j:
                    continue
                no_worse = (
                    float(other["actual_bpp"]) <= float(row["actual_bpp"]) + 1e-12
                    and float(other["PSNR_raw_whole"]) >= float(row["PSNR_raw_whole"]) - 1e-12
                    and float(other["MAE_whole"]) <= float(row["MAE_whole"]) + 1e-12
                    and float(other["MAX_whole"]) <= float(row["MAX_whole"]) + 1e-12
                )
                strictly_better = (
                    float(other["actual_bpp"]) < float(row["actual_bpp"]) - 1e-12
                    or float(other["PSNR_raw_whole"]) > float(row["PSNR_raw_whole"]) + 1e-12
                    or float(other["MAE_whole"]) < float(row["MAE_whole"]) - 1e-12
                    or float(other["MAX_whole"]) < float(row["MAX_whole"]) - 1e-12
                )
                if no_worse and strictly_better:
                    dominated = True
                    break
            if not dominated:
                nd += 1
        out.append(
            {
                "source_id": scene,
                "codec": codec,
                "codec_label": LABELS[codec],
                "candidate_count": len(rows),
                "multimetric_pareto_count": nd,
                "multimetric_pareto_fraction": nd / len(rows) if rows else float("nan"),
                "metrics": "min(actual_bpp), max(PSNR), min(MAE), min(MAX)",
            }
        )
    return out


def plot_bd_summary(rows: list[dict[str, Any]], out_dir: Path) -> str:
    labels = [row["kind"] for row in rows]
    med = np.asarray([float(row["median_bd_rate_percent"]) for row in rows])
    lo = med - np.asarray([float(row["median_ci95_low"]) for row in rows])
    hi = np.asarray([float(row["median_ci95_high"]) for row in rows]) - med
    fig, ax = plt.subplots(figsize=(8.8, 4.8), dpi=150)
    ax.axhline(0, color="#444444", linewidth=1.0)
    ax.errorbar(np.arange(len(rows)), med, yerr=np.vstack([lo, hi]), fmt="o", capsize=4, color="#333333")
    ax.set_xticks(np.arange(len(rows)), labels)
    ax.set_ylabel("BD-rate %, Nikon relative to Sony")
    ax.set_title("Per-scene BD-rate summaries with paired bootstrap CI")
    ax.grid(axis="y", color="#dddddd", linewidth=0.8)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.tight_layout()
    path = out_dir / "fig_bd_rate_bootstrap_summary.png"
    fig.savefig(path)
    plt.close(fig)
    return str(path)


def plot_paired_bins(rows: list[dict[str, Any]], out_dir: Path) -> str:
    x = np.asarray([float(row["bin_center_actual_bpp"]) for row in rows])
    med = np.asarray([float(row["median_delta_sony_minus_nikon"]) for row in rows])
    lo = med - np.asarray([float(row["median_ci95_low"]) for row in rows])
    hi = np.asarray([float(row["median_ci95_high"]) for row in rows]) - med
    coverage = np.asarray([float(row["coverage_fraction"]) for row in rows])
    fig, (ax, ax_cov) = plt.subplots(2, 1, figsize=(9.5, 7.6), dpi=150, sharex=True)
    ax.axhline(0, color="#444444", linewidth=1.0)
    ax.errorbar(x, med, yerr=np.vstack([lo, hi]), fmt="o-", capsize=3, color="#333333")
    ax.fill_between(x, 0, med, where=med >= 0, color=COLORS[SONY], alpha=0.15)
    ax.fill_between(x, 0, med, where=med < 0, color=COLORS[NIKON], alpha=0.15)
    ax.set_ylabel("Median best PSNR delta (dB)\nSony minus Nikon")
    ax.set_title("Actual-bpp paired bin upper-envelope delta with bootstrap CI")
    ax_cov.bar(x, coverage, width=0.16, color="#777777", alpha=0.75)
    ax_cov.set_ylim(0, 1.05)
    ax_cov.set_ylabel("Paired scene coverage")
    ax_cov.set_xlabel("Actual syntax bpp bin center")
    for axis in (ax, ax_cov):
        axis.grid(color="#dddddd", linewidth=0.8)
        for spine in ("top", "right"):
            axis.spines[spine].set_visible(False)
    fig.tight_layout()
    path = out_dir / "fig_paired_actual_bpp_delta_ci.png"
    fig.savefig(path)
    plt.close(fig)
    return str(path)


def plot_rate_support(rows: list[dict[str, Any]], out_dir: Path) -> str:
    fig, ax = plt.subplots(figsize=(9.5, 4.8), dpi=150)
    for codec in (SONY, NIKON):
        subset = [row for row in rows if row["codec"] == codec]
        x = [float(row["bin_center_actual_bpp"]) for row in subset]
        y = [float(row["coverage_fraction"]) for row in subset]
        ax.plot(x, y, marker="o", color=COLORS[codec], label=LABELS[codec])
    ax.set_ylim(-0.02, 1.05)
    ax.set_xlabel("Actual syntax bpp bin center")
    ax.set_ylabel("Scene coverage fraction")
    ax.set_title("Operational actual-rate support across policy candidates")
    ax.grid(color="#dddddd", linewidth=0.8)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.legend(frameon=False)
    fig.tight_layout()
    path = out_dir / "fig_actual_rate_support.png"
    fig.savefig(path)
    plt.close(fig)
    return str(path)


def plot_frontier_examples(policy_rows: list[dict[str, Any]], out_dir: Path) -> str:
    scenes = ["smooth_gradient", "fine_texture", "color_edges", "shadow_noise"]
    available = sorted({row["source_id"] for row in policy_rows})
    scenes = [scene for scene in scenes if scene in available] or available[:4]
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.0), dpi=150, sharex=False, sharey=False)
    axes_flat = axes.ravel()
    for ax, scene in zip(axes_flat, scenes):
        for codec in (SONY, NIKON):
            subset = [row for row in policy_rows if row["source_id"] == scene and row["codec"] == codec]
            points = [
                Point(float(row["actual_bpp"]), float(row["PSNR_raw_whole"]), str(row["policy_name"]))
                for row in subset
            ]
            frontier = psnr_frontier(points)
            ax.scatter(
                [p.rate for p in points],
                [p.quality for p in points],
                s=14,
                color=COLORS[codec],
                alpha=0.22,
                edgecolors="none",
            )
            ax.plot(
                [p.rate for p in frontier],
                [p.quality for p in frontier],
                marker="o",
                linewidth=1.4,
                color=COLORS[codec],
                label=LABELS[codec],
            )
        ax.set_title(scene)
        ax.set_xlabel("Actual syntax bpp")
        ax.set_ylabel("Whole raw PSNR (dB)")
        ax.grid(color="#dddddd", linewidth=0.8)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
    for ax in axes_flat[len(scenes):]:
        ax.axis("off")
    axes_flat[0].legend(frameon=False, fontsize=8)
    fig.suptitle("Operational policy candidates and PSNR frontier examples", y=0.995)
    fig.tight_layout()
    path = out_dir / "fig_operational_frontier_examples.png"
    fig.savefig(path)
    plt.close(fig)
    return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-csv", type=Path, default=Path("out/bpp_policy_multiplicity_20260604/policy_candidates.csv"))
    parser.add_argument("--strict-metrics", type=Path, default=Path("out/strict_824_826_math_eval_full_20260603/metrics.csv"))
    parser.add_argument("--out-dir", type=Path, default=Path("out/bpp_policy_multiplicity_20260604/frontier_math_eval_20260605"))
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--bootstrap-samples", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20260605)
    parser.add_argument("--bd-samples", type=int, default=1001)
    parser.add_argument("--bin-start", type=float, default=1.5)
    parser.add_argument("--bin-stop", type=float, default=5.0)
    parser.add_argument("--bin-step", type=float, default=0.25)
    parser.add_argument("--bin-tolerance", type=float, default=0.20)
    args = parser.parse_args()

    jobs = max(1, int(args.jobs))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    policy_rows = load_policy_rows(args.policy_csv)
    policy_groups = grouped_policy_points(policy_rows)
    scenes = sorted({row["source_id"] for row in policy_rows})

    policy_tasks = [
        (
            scene,
            policy_groups.get((scene, NIKON), []),
            policy_groups.get((scene, SONY), []),
            args.bd_samples,
            "policy_psnr_envelope",
        )
        for scene in scenes
    ]

    strict_groups = load_strict_points(args.strict_metrics)
    strict_scenes = sorted({scene for scene, _codec in strict_groups})
    strict_tasks = [
        (
            scene,
            strict_groups.get((scene, NIKON), []),
            strict_groups.get((scene, SONY), []),
            args.bd_samples,
            "strict_canonical",
        )
        for scene in strict_scenes
    ]

    with ProcessPoolExecutor(max_workers=jobs) as pool:
        policy_bd_rows = list(pool.map(compute_scene_bd_task, policy_tasks))
        strict_bd_rows = list(pool.map(compute_scene_bd_task, strict_tasks))

    bd_summary_rows = [
        {"kind": "strict_canonical", **summarize_bd(strict_bd_rows, args.bootstrap_samples, args.seed, jobs)},
        {"kind": "policy_psnr_envelope", **summarize_bd(policy_bd_rows, args.bootstrap_samples, args.seed + 911, jobs)},
    ]

    bins = [round(float(x), 6) for x in np.arange(args.bin_start, args.bin_stop + 1e-12, args.bin_step)]
    bin_rows = actual_bin_best_rows(policy_rows, bins, args.bin_tolerance)
    paired_bins = paired_bin_summary(bin_rows, args.bootstrap_samples, args.seed + 2000, jobs)
    rate_support = rate_support_summary(policy_rows, bins, args.bin_tolerance)
    frontier_cardinality = policy_frontier_cardinality(policy_rows)
    multimetric_pareto = multimetric_pareto_summary(policy_rows)

    write_csv(args.out_dir / "strict_canonical_bd_rate_psnr_per_scene.csv", strict_bd_rows)
    write_csv(args.out_dir / "policy_envelope_bd_rate_psnr_per_scene.csv", policy_bd_rows)
    write_csv(args.out_dir / "bd_rate_bootstrap_summary.csv", bd_summary_rows)
    write_csv(args.out_dir / "paired_actual_bpp_bin_best_rows.csv", bin_rows)
    write_csv(args.out_dir / "paired_actual_bpp_bin_delta_ci.csv", paired_bins)
    write_csv(args.out_dir / "actual_rate_support.csv", rate_support)
    write_csv(args.out_dir / "policy_psnr_frontier_cardinality.csv", frontier_cardinality)
    write_csv(args.out_dir / "multimetric_pareto_summary.csv", multimetric_pareto)

    figures = {
        "bd_rate_bootstrap_summary": plot_bd_summary(bd_summary_rows, args.out_dir),
        "paired_actual_bpp_delta_ci": plot_paired_bins(paired_bins, args.out_dir),
        "actual_rate_support": plot_rate_support(rate_support, args.out_dir),
        "operational_frontier_examples": plot_frontier_examples(policy_rows, args.out_dir),
    }

    manifest = {
        "kind": "frontier-level mathematical evaluation for #824/#826 strict/policy data",
        "policy_csv": str(args.policy_csv),
        "strict_metrics": str(args.strict_metrics),
        "out_dir": str(args.out_dir),
        "jobs_requested": jobs,
        "cpu_count": os.cpu_count(),
        "bootstrap_samples": args.bootstrap_samples,
        "bd_samples": args.bd_samples,
        "bin_tolerance": args.bin_tolerance,
        "bins_actual_bpp": bins,
        "policy_unique_rows": len(policy_rows),
        "scene_count_policy": len(scenes),
        "scene_count_strict": len(strict_scenes),
        "strict_bd_ok": sum(1 for row in strict_bd_rows if row.get("status") == "ok"),
        "policy_envelope_bd_ok": sum(1 for row in policy_bd_rows if row.get("status") == "ok"),
        "rate_axis_rule": "No performance figure uses target_bpp as the rate axis; actual syntax bpp is the only RD x-axis.",
        "strict_boundary": (
            "Policy envelopes are operational envelopes over enumerated decoder-visible candidates only. "
            "They are not production encoder optima and do not include subjective testing."
        ),
        "outputs": {
            "bd_rate_bootstrap_summary": str(args.out_dir / "bd_rate_bootstrap_summary.csv"),
            "paired_actual_bpp_bin_delta_ci": str(args.out_dir / "paired_actual_bpp_bin_delta_ci.csv"),
            "actual_rate_support": str(args.out_dir / "actual_rate_support.csv"),
            "policy_psnr_frontier_cardinality": str(args.out_dir / "policy_psnr_frontier_cardinality.csv"),
            "multimetric_pareto_summary": str(args.out_dir / "multimetric_pareto_summary.csv"),
            "figures": figures,
        },
    }
    with (args.out_dir / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
