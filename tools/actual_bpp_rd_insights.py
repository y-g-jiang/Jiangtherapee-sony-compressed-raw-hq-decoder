#!/usr/bin/env python3
"""Actual-bpp RD plots and monotonicity checks for strict #824/#826 work.

The requested target bpp is a control input; actual syntax bpp is the rate that
belongs on the x axis for RD interpretation. This script makes that distinction
explicit for both the strict canonical run and the policy sweep.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


SONY_CODEC = "sony_824_decoder_visible_packet_canonical"
NIKON_CODEC = "nikon_826_decoder_visible_precinct_canonical"
CODEC_LABELS = {
    SONY_CODEC: "#824 Sony",
    NIKON_CODEC: "#826 Nikon",
}
CODEC_COLORS = {
    SONY_CODEC: "#2f6fb0",
    NIKON_CODEC: "#bf6a22",
}


def _float(row: dict[str, str], key: str) -> float:
    value = row.get(key, "")
    return float(value) if value != "" else float("nan")


def _median(values: list[float]) -> float:
    clean = [value for value in values if not math.isnan(value)]
    return statistics.median(clean) if clean else float("nan")


def _percentile(values: list[float], q: float) -> float:
    clean = np.asarray([value for value in values if not math.isnan(value)], dtype=float)
    if clean.size == 0:
        return float("nan")
    return float(np.percentile(clean, q))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def load_strict_rows(strict_dir: Path) -> list[dict[str, Any]]:
    psnr: dict[tuple[str, str, float], float] = {}
    mae: dict[tuple[str, str, float], float] = {}
    with (strict_dir / "metrics.csv").open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["split"] != "whole":
                continue
            key = (row["codec"], row["source_id"], _float(row, "target_bpp"))
            if row["metric"] == "PSNR_raw":
                psnr[key] = _float(row, "value")
            elif row["metric"] == "MAE":
                mae[key] = _float(row, "value")

    rows: list[dict[str, Any]] = []
    with (strict_dir / "encodes.csv").open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            target = _float(row, "target_bpp")
            key = (row["codec"], row["source_id"], target)
            rows.append(
                {
                    "codec": row["codec"],
                    "source_id": row["source_id"],
                    "target_bpp": target,
                    "actual_bpp": _float(row, "actual_bpp"),
                    "knob_name": row.get("knob_name", ""),
                    "knob": _float(row, "knob"),
                    "encode_ms": _float(row, "encode_ms"),
                    "PSNR_raw_whole": psnr.get(key, float("nan")),
                    "MAE_whole": mae.get(key, float("nan")),
                }
            )
    return rows


def summarize_strict_targets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets = sorted({float(row["target_bpp"]) for row in rows})
    out: list[dict[str, Any]] = []
    for codec in (SONY_CODEC, NIKON_CODEC):
        for target in targets:
            subset = [
                row
                for row in rows
                if row["codec"] == codec and abs(float(row["target_bpp"]) - target) < 1e-12
            ]
            actual = [float(row["actual_bpp"]) for row in subset]
            psnr = [float(row["PSNR_raw_whole"]) for row in subset]
            out.append(
                {
                    "codec": codec,
                    "codec_label": CODEC_LABELS[codec],
                    "target_bpp": target,
                    "scene_count": len(subset),
                    "actual_bpp_min": min(actual),
                    "actual_bpp_p25": _percentile(actual, 25),
                    "actual_bpp_median": _median(actual),
                    "actual_bpp_p75": _percentile(actual, 75),
                    "actual_bpp_max": max(actual),
                    "psnr_min": min(psnr),
                    "psnr_p25": _percentile(psnr, 25),
                    "psnr_median": _median(psnr),
                    "psnr_p75": _percentile(psnr, 75),
                    "psnr_max": max(psnr),
                }
            )
    return out


def _monotonic_for_group(args: tuple[str, str, list[dict[str, Any]]]) -> dict[str, Any]:
    codec, scene, rows = args
    ordered = sorted(rows, key=lambda row: (float(row["actual_bpp"]), float(row["target_bpp"])))
    actual_increase_psnr_drop = 0
    target_increase_actual_drop = 0
    min_psnr_delta_for_actual_increase = float("inf")
    min_actual_delta_for_target_increase = float("inf")
    for prev, cur in zip(ordered, ordered[1:]):
        actual_delta = float(cur["actual_bpp"]) - float(prev["actual_bpp"])
        psnr_delta = float(cur["PSNR_raw_whole"]) - float(prev["PSNR_raw_whole"])
        target_delta = float(cur["target_bpp"]) - float(prev["target_bpp"])
        if actual_delta > 1e-9:
            min_psnr_delta_for_actual_increase = min(min_psnr_delta_for_actual_increase, psnr_delta)
            if psnr_delta < -1e-9:
                actual_increase_psnr_drop += 1
        if target_delta > 1e-9:
            min_actual_delta_for_target_increase = min(min_actual_delta_for_target_increase, actual_delta)
            if actual_delta < -1e-9:
                target_increase_actual_drop += 1
    row3 = next(row for row in rows if abs(float(row["target_bpp"]) - 3.0) < 1e-12)
    row4 = next(row for row in rows if abs(float(row["target_bpp"]) - 4.0) < 1e-12)
    return {
        "codec": codec,
        "source_id": scene,
        "point_count": len(rows),
        "actual_increase_psnr_drop_count": actual_increase_psnr_drop,
        "target_increase_actual_drop_count": target_increase_actual_drop,
        "min_psnr_delta_when_actual_increases": (
            min_psnr_delta_for_actual_increase
            if min_psnr_delta_for_actual_increase != float("inf")
            else float("nan")
        ),
        "min_actual_delta_when_target_increases": (
            min_actual_delta_for_target_increase
            if min_actual_delta_for_target_increase != float("inf")
            else float("nan")
        ),
        "target3_actual_bpp": float(row3["actual_bpp"]),
        "target3_psnr": float(row3["PSNR_raw_whole"]),
        "target4_actual_bpp": float(row4["actual_bpp"]),
        "target4_psnr": float(row4["PSNR_raw_whole"]),
        "delta_actual_4_minus_3": float(row4["actual_bpp"]) - float(row3["actual_bpp"]),
        "delta_psnr_4_minus_3": float(row4["PSNR_raw_whole"]) - float(row3["PSNR_raw_whole"]),
    }


def strict_monotonic_audit(rows: list[dict[str, Any]], jobs: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["codec"], row["source_id"])].append(row)
    tasks = [(codec, scene, group_rows) for (codec, scene), group_rows in grouped.items()]
    if jobs <= 1:
        return [_monotonic_for_group(task) for task in tasks]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(_monotonic_for_group, tasks))


def load_policy_rows(policy_csv: Path) -> list[dict[str, Any]]:
    rows_by_key: dict[tuple[str, ...], dict[str, Any]] = {}
    with policy_csv.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = (
                row["codec"],
                row["source_id"],
                row["policy_name"],
                row["policy_class"],
                row.get("knob", ""),
                row.get("bp", ""),
                row.get("br", ""),
                row.get("global_bias", ""),
                row.get("component_offsets", ""),
                row.get("selector_map", ""),
                row.get("selector_cycle_len", ""),
                row.get("actual_bpp", ""),
            )
            rows_by_key[key] = {
                "codec": row["codec"],
                "source_id": row["source_id"],
                "target_bpp": _float(row, "target_bpp"),
                "actual_bpp": _float(row, "actual_bpp"),
                "PSNR_raw_whole": _float(row, "PSNR_raw_whole"),
                "policy_name": row.get("policy_name", ""),
                "policy_class": row.get("policy_class", ""),
            }
    return list(rows_by_key.values())


def _policy_bins_for_group(args: tuple[str, str, list[dict[str, Any]], list[float], float]) -> list[dict[str, Any]]:
    codec, scene, rows, bins, tolerance = args
    out: list[dict[str, Any]] = []
    for center in bins:
        window = [row for row in rows if abs(float(row["actual_bpp"]) - center) <= tolerance]
        if not window:
            continue
        psnr = [float(row["PSNR_raw_whole"]) for row in window]
        best = max(window, key=lambda row: float(row["PSNR_raw_whole"]))
        out.append(
            {
                "codec": codec,
                "source_id": scene,
                "bin_center_bpp": center,
                "bin_tolerance": tolerance,
                "candidate_count": len(window),
                "actual_bpp_min": min(float(row["actual_bpp"]) for row in window),
                "actual_bpp_max": max(float(row["actual_bpp"]) for row in window),
                "psnr_min": min(psnr),
                "psnr_median": _median(psnr),
                "psnr_best": float(best["PSNR_raw_whole"]),
                "best_actual_bpp": float(best["actual_bpp"]),
                "best_policy": best["policy_name"],
            }
        )
    return out


def policy_bin_summary(
    policy_rows: list[dict[str, Any]], bins: list[float], tolerance: float, jobs: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in policy_rows:
        grouped[(row["codec"], row["source_id"])].append(row)
    tasks = [(codec, scene, rows, bins, tolerance) for (codec, scene), rows in grouped.items()]
    if jobs <= 1:
        per_scene_nested = [_policy_bins_for_group(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            per_scene_nested = list(pool.map(_policy_bins_for_group, tasks))
    per_scene = [row for nested in per_scene_nested for row in nested]

    summary: list[dict[str, Any]] = []
    scene_count_by_codec = {
        codec: len({row["source_id"] for row in policy_rows if row["codec"] == codec})
        for codec in (SONY_CODEC, NIKON_CODEC)
    }
    for codec in (SONY_CODEC, NIKON_CODEC):
        for center in bins:
            subset = [
                row
                for row in per_scene
                if row["codec"] == codec and abs(float(row["bin_center_bpp"]) - center) < 1e-12
            ]
            if not subset:
                continue
            summary.append(
                {
                    "codec": codec,
                    "codec_label": CODEC_LABELS[codec],
                    "bin_center_bpp": center,
                    "bin_tolerance": tolerance,
                    "scene_count": scene_count_by_codec[codec],
                    "covered_scene_count": len(subset),
                    "coverage_fraction": len(subset) / scene_count_by_codec[codec],
                    "median_candidate_count_per_scene": _median(
                        [float(row["candidate_count"]) for row in subset]
                    ),
                    "median_psnr_min": _median([float(row["psnr_min"]) for row in subset]),
                    "median_psnr_median": _median([float(row["psnr_median"]) for row in subset]),
                    "median_psnr_best": _median([float(row["psnr_best"]) for row in subset]),
                    "median_range_width": _median(
                        [float(row["psnr_best"]) - float(row["psnr_min"]) for row in subset]
                    ),
                    "p90_range_width": _percentile(
                        [float(row["psnr_best"]) - float(row["psnr_min"]) for row in subset], 90
                    ),
                }
            )
    return per_scene, summary


def paired_policy_bin_comparison(per_scene_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {
        (row["source_id"], float(row["bin_center_bpp"]), row["codec"]): row
        for row in per_scene_rows
    }
    scenes = sorted({row["source_id"] for row in per_scene_rows})
    bins = sorted({float(row["bin_center_bpp"]) for row in per_scene_rows})
    out: list[dict[str, Any]] = []
    for center in bins:
        paired: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for scene in scenes:
            sony = by_key.get((scene, center, SONY_CODEC))
            nikon = by_key.get((scene, center, NIKON_CODEC))
            if sony is not None and nikon is not None:
                paired.append((sony, nikon))
        if not paired:
            continue
        deltas = [
            float(sony["psnr_best"]) - float(nikon["psnr_best"])
            for sony, nikon in paired
        ]
        overlaps = []
        overlap_widths = []
        for sony, nikon in paired:
            low = max(float(sony["psnr_min"]), float(nikon["psnr_min"]))
            high = min(float(sony["psnr_best"]), float(nikon["psnr_best"]))
            overlaps.append(high >= low)
            overlap_widths.append(max(0.0, high - low))
        out.append(
            {
                "bin_center_bpp": center,
                "paired_scene_count": len(paired),
                "sony_best_win_count": sum(delta > 1e-9 for delta in deltas),
                "nikon_best_win_count": sum(delta < -1e-9 for delta in deltas),
                "tie_count": sum(abs(delta) <= 1e-9 for delta in deltas),
                "median_best_delta_sony_minus_nikon": _median(deltas),
                "p25_best_delta_sony_minus_nikon": _percentile(deltas, 25),
                "p75_best_delta_sony_minus_nikon": _percentile(deltas, 75),
                "min_best_delta_sony_minus_nikon": min(deltas),
                "max_best_delta_sony_minus_nikon": max(deltas),
                "range_overlap_fraction": sum(overlaps) / len(overlaps),
                "median_overlap_width": _median(overlap_widths),
                "median_sony_best": _median([float(sony["psnr_best"]) for sony, _ in paired]),
                "median_nikon_best": _median([float(nikon["psnr_best"]) for _, nikon in paired]),
                "median_sony_range_width": _median(
                    [float(sony["psnr_best"]) - float(sony["psnr_min"]) for sony, _ in paired]
                ),
                "median_nikon_range_width": _median(
                    [float(nikon["psnr_best"]) - float(nikon["psnr_min"]) for _, nikon in paired]
                ),
            }
        )
    return out


def plot_canonical_median(summary_rows: list[dict[str, Any]], out_dir: Path) -> str:
    fig, ax = plt.subplots(figsize=(9.5, 6.2), dpi=150)
    for codec in (SONY_CODEC, NIKON_CODEC):
        rows = sorted(
            [row for row in summary_rows if row["codec"] == codec],
            key=lambda row: float(row["target_bpp"]),
        )
        x = np.asarray([float(row["actual_bpp_median"]) for row in rows])
        y = np.asarray([float(row["psnr_median"]) for row in rows])
        xerr = np.vstack(
            [
                x - np.asarray([float(row["actual_bpp_p25"]) for row in rows]),
                np.asarray([float(row["actual_bpp_p75"]) for row in rows]) - x,
            ]
        )
        yerr = np.vstack(
            [
                y - np.asarray([float(row["psnr_p25"]) for row in rows]),
                np.asarray([float(row["psnr_p75"]) for row in rows]) - y,
            ]
        )
        color = CODEC_COLORS[codec]
        ax.errorbar(
            x,
            y,
            xerr=xerr,
            yerr=yerr,
            marker="o",
            linewidth=2.0,
            capsize=3,
            color=color,
            label=CODEC_LABELS[codec],
        )
        for row, x_value, y_value in zip(rows, x, y):
            target = float(row["target_bpp"])
            label = f"t={target:g}"
            offset = (4, -12) if abs(target - 4.0) < 1e-9 else (4, 4)
            ax.annotate(label, (x_value, y_value), textcoords="offset points", xytext=offset, fontsize=8)

    ax.set_xlabel("Actual syntax bpp")
    ax.set_ylabel("Median whole-image raw PSNR (dB)")
    ax.set_title("Strict canonical RD trajectory uses actual syntax bpp on x axis")
    ax.grid(color="#dddddd", linewidth=0.8)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.legend(frameon=False)
    fig.tight_layout()
    path = out_dir / "fig_actual_bpp_canonical_median_rd.png"
    fig.savefig(path)
    plt.close(fig)
    return str(path)


def plot_canonical_cloud(rows: list[dict[str, Any]], out_dir: Path) -> str:
    fig, ax = plt.subplots(figsize=(10.0, 6.3), dpi=150)
    target_markers = {3.0: "D", 4.0: "s"}
    for codec in (SONY_CODEC, NIKON_CODEC):
        codec_rows = [row for row in rows if row["codec"] == codec]
        color = CODEC_COLORS[codec]
        other = [row for row in codec_rows if float(row["target_bpp"]) not in target_markers]
        ax.scatter(
            [float(row["actual_bpp"]) for row in other],
            [float(row["PSNR_raw_whole"]) for row in other],
            s=14,
            color=color,
            alpha=0.18,
            edgecolors="none",
        )
        for target, marker in target_markers.items():
            subset = [row for row in codec_rows if abs(float(row["target_bpp"]) - target) < 1e-12]
            ax.scatter(
                [float(row["actual_bpp"]) for row in subset],
                [float(row["PSNR_raw_whole"]) for row in subset],
                s=30,
                color=color,
                alpha=0.72,
                marker=marker,
                label=f"{CODEC_LABELS[codec]} target {target:g}",
            )
    ax.set_xlabel("Actual syntax bpp")
    ax.set_ylabel("Whole-image raw PSNR (dB)")
    ax.set_title("Canonical scene points: target 3/4 bpp shown on actual-bpp axis")
    ax.grid(color="#dddddd", linewidth=0.8)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.legend(frameon=False, fontsize=8, ncol=2)
    fig.tight_layout()
    path = out_dir / "fig_actual_bpp_canonical_scene_cloud_3_4.png"
    fig.savefig(path)
    plt.close(fig)
    return str(path)


def plot_policy_bin_summary(summary_rows: list[dict[str, Any]], out_dir: Path) -> str:
    fig, (ax_rd, ax_width, ax_cov) = plt.subplots(3, 1, figsize=(10.0, 10.2), dpi=150, sharex=True)
    for codec in (SONY_CODEC, NIKON_CODEC):
        rows = sorted(
            [row for row in summary_rows if row["codec"] == codec],
            key=lambda row: float(row["bin_center_bpp"]),
        )
        x = np.asarray([float(row["bin_center_bpp"]) for row in rows])
        y_min = np.asarray([float(row["median_psnr_min"]) for row in rows])
        y_best = np.asarray([float(row["median_psnr_best"]) for row in rows])
        width = np.asarray([float(row["median_range_width"]) for row in rows])
        coverage = np.asarray([float(row["coverage_fraction"]) for row in rows])
        color = CODEC_COLORS[codec]
        ax_rd.fill_between(x, y_min, y_best, color=color, alpha=0.16)
        ax_rd.plot(x, y_best, marker="o", color=color, label=f"{CODEC_LABELS[codec]} median best")
        ax_rd.plot(x, y_min, color=color, linewidth=1.0, alpha=0.60)
        ax_width.plot(x, width, marker="o", color=color, label=CODEC_LABELS[codec])
        ax_cov.plot(x, coverage, marker="o", color=color, label=CODEC_LABELS[codec])

    ax_rd.set_ylabel("PSNR range / best (dB)")
    ax_width.set_ylabel("Median range width (dB)")
    ax_cov.set_ylabel("Scene coverage")
    ax_cov.set_xlabel("Actual syntax bpp bin center")
    ax_cov.set_ylim(-0.02, 1.05)
    ax_rd.set_title("Policy sweep on actual-bpp axis: median same-rate range and upper envelope")
    for ax in (ax_rd, ax_width, ax_cov):
        ax.grid(color="#dddddd", linewidth=0.8)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
    ax_rd.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    path = out_dir / "fig_actual_bpp_policy_range_envelope.png"
    fig.savefig(path)
    plt.close(fig)
    return str(path)


def plot_paired_policy_delta(paired_rows: list[dict[str, Any]], out_dir: Path) -> str:
    rows = sorted(paired_rows, key=lambda row: float(row["bin_center_bpp"]))
    x = np.asarray([float(row["bin_center_bpp"]) for row in rows])
    y = np.asarray([float(row["median_best_delta_sony_minus_nikon"]) for row in rows])
    y_low = y - np.asarray([float(row["p25_best_delta_sony_minus_nikon"]) for row in rows])
    y_high = np.asarray([float(row["p75_best_delta_sony_minus_nikon"]) for row in rows]) - y
    coverage = np.asarray([float(row["paired_scene_count"]) / 24.0 for row in rows])

    fig, (ax_delta, ax_cov) = plt.subplots(2, 1, figsize=(9.5, 7.5), dpi=150, sharex=True)
    ax_delta.axhline(0.0, color="#444444", linewidth=1.0)
    ax_delta.errorbar(
        x,
        y,
        yerr=np.vstack([y_low, y_high]),
        marker="o",
        linewidth=2.0,
        capsize=3,
        color="#444444",
    )
    ax_delta.fill_between(x, 0.0, y, where=y >= 0, color=CODEC_COLORS[SONY_CODEC], alpha=0.16)
    ax_delta.fill_between(x, 0.0, y, where=y < 0, color=CODEC_COLORS[NIKON_CODEC], alpha=0.16)
    ax_delta.set_ylabel("Median best PSNR delta (dB)\n#824 Sony minus #826 Nikon")
    ax_delta.set_title("Paired policy upper-envelope comparison on actual-bpp bins")

    ax_cov.bar(x, coverage, width=0.16, color="#666666", alpha=0.75)
    ax_cov.set_ylim(0, 1.05)
    ax_cov.set_ylabel("Paired scene coverage")
    ax_cov.set_xlabel("Actual syntax bpp bin center")
    for ax in (ax_delta, ax_cov):
        ax.grid(color="#dddddd", linewidth=0.8)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
    fig.tight_layout()
    path = out_dir / "fig_actual_bpp_policy_paired_delta.png"
    fig.savefig(path)
    plt.close(fig)
    return str(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict-dir", type=Path, default=Path("out/strict_824_826_math_eval_full_20260603"))
    parser.add_argument(
        "--policy-csv",
        type=Path,
        default=Path("out/bpp_policy_multiplicity_20260604/policy_candidates.csv"),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("out/bpp_policy_multiplicity_20260604/actual_bpp_rd_insights"),
    )
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--bin-start", type=float, default=1.5)
    parser.add_argument("--bin-stop", type=float, default=5.0)
    parser.add_argument("--bin-step", type=float, default=0.25)
    parser.add_argument("--bin-tolerance", type=float, default=0.20)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    jobs = max(1, args.jobs)

    strict_rows = load_strict_rows(args.strict_dir)
    strict_summary = summarize_strict_targets(strict_rows)
    monotonic_rows = strict_monotonic_audit(strict_rows, jobs)
    bins = [round(value, 6) for value in np.arange(args.bin_start, args.bin_stop + 1e-9, args.bin_step)]
    policy_rows = load_policy_rows(args.policy_csv)
    policy_per_scene, policy_summary = policy_bin_summary(
        policy_rows, bins, args.bin_tolerance, jobs
    )
    paired_policy_rows = paired_policy_bin_comparison(policy_per_scene)

    _write_csv(args.out_dir / "canonical_target_actual_summary.csv", strict_summary)
    _write_csv(args.out_dir / "canonical_monotonic_audit.csv", monotonic_rows)
    _write_csv(args.out_dir / "policy_actual_bpp_bin_per_scene.csv", policy_per_scene)
    _write_csv(args.out_dir / "policy_actual_bpp_bin_summary.csv", policy_summary)
    _write_csv(args.out_dir / "policy_actual_bpp_paired_bin_comparison.csv", paired_policy_rows)

    figures = {
        "canonical_median_rd": plot_canonical_median(strict_summary, args.out_dir),
        "canonical_scene_cloud_3_4": plot_canonical_cloud(strict_rows, args.out_dir),
        "policy_range_envelope": plot_policy_bin_summary(policy_summary, args.out_dir),
        "policy_paired_delta": plot_paired_policy_delta(paired_policy_rows, args.out_dir),
    }
    manifest = {
        "kind": "actual-bpp RD insights for #824/#826",
        "strict_dir": str(args.strict_dir),
        "policy_csv": str(args.policy_csv),
        "out_dir": str(args.out_dir),
        "jobs_requested": jobs,
        "cpu_count": os.cpu_count(),
        "strict_rows": len(strict_rows),
        "policy_unique_rows": len(policy_rows),
        "paired_policy_bin_rows": len(paired_policy_rows),
        "bin_tolerance": args.bin_tolerance,
        "bins": bins,
        "monotonic_actual_increase_psnr_drop_total": sum(
            int(row["actual_increase_psnr_drop_count"]) for row in monotonic_rows
        ),
        "figures": figures,
    }
    with (args.out_dir / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
