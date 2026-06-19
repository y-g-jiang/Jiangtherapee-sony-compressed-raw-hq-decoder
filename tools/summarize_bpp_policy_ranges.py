#!/usr/bin/env python3
"""Summarize same-bpp policy quality ranges from a completed sweep.

This script is intentionally post-processing only. It reads
policy_candidates.csv from tools/policy_sweep_bpp_multiplicity.py and makes
scene-level figures where each codec has a quality interval and a best point
near the requested actual bpp.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


SONY_CODEC = "sony_824_decoder_visible_packet_canonical"
NIKON_CODEC = "nikon_826_decoder_visible_precinct_canonical"
CODEC_LABELS = {
    SONY_CODEC: "#824 Sony packet",
    NIKON_CODEC: "#826 Nikon precinct",
}
CODEC_COLORS = {
    SONY_CODEC: "#2f6fb0",
    NIKON_CODEC: "#bf6a22",
}


def _float(row: dict[str, str], key: str) -> float:
    value = row.get(key, "")
    if value == "":
        return float("nan")
    return float(value)


def _fmt_target(target: float) -> str:
    text = f"{target:.3f}".rstrip("0").rstrip(".")
    return text.replace(".", "p")


def _median(values: list[float]) -> float:
    clean = [v for v in values if not math.isnan(v)]
    return statistics.median(clean) if clean else float("nan")


def _percentile(values: list[float], q: float) -> float:
    clean = np.asarray([v for v in values if not math.isnan(v)], dtype=float)
    if clean.size == 0:
        return float("nan")
    return float(np.percentile(clean, q))


def _dedupe_key(row: dict[str, str]) -> tuple[str, ...]:
    return (
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


def _read_candidates(path: Path) -> tuple[list[dict[str, str]], list[str], list[float]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    scene_order: list[str] = []
    scene_seen: set[str] = set()
    targets: set[float] = set()
    for row in rows:
        scene = row["source_id"]
        if scene not in scene_seen:
            scene_order.append(scene)
            scene_seen.add(scene)
        targets.add(_float(row, "target_bpp"))
    return rows, scene_order, sorted(targets)


def build_ranges(
    rows: list[dict[str, str]],
    scene_order: list[str],
    targets: list[float],
    tolerance: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_scene_codec: dict[tuple[str, str], dict[tuple[str, ...], dict[str, str]]] = defaultdict(dict)
    for row in rows:
        by_scene_codec[(row["source_id"], row["codec"])][_dedupe_key(row)] = row

    range_rows: list[dict[str, Any]] = []
    range_by_key: dict[tuple[str, float, str], dict[str, Any]] = {}
    for target in targets:
        for scene in scene_order:
            for codec in (SONY_CODEC, NIKON_CODEC):
                unique_rows = list(by_scene_codec.get((scene, codec), {}).values())
                if not unique_rows:
                    continue
                for row in unique_rows:
                    row["_actual_bpp_f"] = _float(row, "actual_bpp")
                    row["_psnr_f"] = _float(row, "PSNR_raw_whole")

                window_rows = [
                    row
                    for row in unique_rows
                    if abs(row["_actual_bpp_f"] - target) <= tolerance
                ]
                if window_rows:
                    selected = window_rows
                    mode = "window"
                    nearest_distance = min(abs(row["_actual_bpp_f"] - target) for row in selected)
                    window_count = len(window_rows)
                else:
                    nearest_distance = min(abs(row["_actual_bpp_f"] - target) for row in unique_rows)
                    selected = [
                        row
                        for row in unique_rows
                        if abs(abs(row["_actual_bpp_f"] - target) - nearest_distance) < 1e-12
                    ]
                    mode = "nearest_fallback"
                    window_count = 0

                selected = sorted(
                    selected,
                    key=lambda row: (row["_psnr_f"], -abs(row["_actual_bpp_f"] - target)),
                )
                worst = selected[0]
                best = selected[-1]
                actual_values = [row["_actual_bpp_f"] for row in selected]
                psnr_values = [row["_psnr_f"] for row in selected]
                out = {
                    "target_bpp": target,
                    "source_id": scene,
                    "codec": codec,
                    "codec_label": CODEC_LABELS.get(codec, codec),
                    "selection_mode": mode,
                    "rate_tolerance": tolerance,
                    "unique_policy_points": len(unique_rows),
                    "window_candidate_count": window_count,
                    "used_candidate_count": len(selected),
                    "nearest_rate_abs_error": nearest_distance,
                    "actual_bpp_min": min(actual_values),
                    "actual_bpp_median": _median(actual_values),
                    "actual_bpp_max": max(actual_values),
                    "psnr_min": min(psnr_values),
                    "psnr_median": _median(psnr_values),
                    "psnr_max": max(psnr_values),
                    "psnr_range_width": max(psnr_values) - min(psnr_values),
                    "best_policy": best.get("policy_name", ""),
                    "best_policy_class": best.get("policy_class", ""),
                    "best_actual_bpp": best["_actual_bpp_f"],
                    "best_psnr": best["_psnr_f"],
                    "best_rate_error_to_target": abs(best["_actual_bpp_f"] - target),
                    "worst_policy": worst.get("policy_name", ""),
                    "worst_actual_bpp": worst["_actual_bpp_f"],
                    "worst_psnr": worst["_psnr_f"],
                }
                range_rows.append(out)
                range_by_key[(scene, target, codec)] = out

    comparison_rows: list[dict[str, Any]] = []
    for target in targets:
        for scene in scene_order:
            sony = range_by_key.get((scene, target, SONY_CODEC))
            nikon = range_by_key.get((scene, target, NIKON_CODEC))
            if sony is None or nikon is None:
                continue
            overlap_low = max(float(sony["psnr_min"]), float(nikon["psnr_min"]))
            overlap_high = min(float(sony["psnr_max"]), float(nikon["psnr_max"]))
            delta = float(sony["best_psnr"]) - float(nikon["best_psnr"])
            comparison_rows.append(
                {
                    "target_bpp": target,
                    "source_id": scene,
                    "sony_mode": sony["selection_mode"],
                    "nikon_mode": nikon["selection_mode"],
                    "both_in_window": sony["selection_mode"] == "window"
                    and nikon["selection_mode"] == "window",
                    "sony_window_candidate_count": sony["window_candidate_count"],
                    "nikon_window_candidate_count": nikon["window_candidate_count"],
                    "sony_psnr_min": sony["psnr_min"],
                    "sony_psnr_max": sony["psnr_max"],
                    "sony_best_psnr": sony["best_psnr"],
                    "sony_best_policy": sony["best_policy"],
                    "sony_best_actual_bpp": sony["best_actual_bpp"],
                    "nikon_psnr_min": nikon["psnr_min"],
                    "nikon_psnr_max": nikon["psnr_max"],
                    "nikon_best_psnr": nikon["best_psnr"],
                    "nikon_best_policy": nikon["best_policy"],
                    "nikon_best_actual_bpp": nikon["best_actual_bpp"],
                    "best_delta_sony_minus_nikon": delta,
                    "best_winner": "sony_824" if delta > 1e-9 else "nikon_826" if delta < -1e-9 else "tie",
                    "quality_ranges_overlap": overlap_high >= overlap_low,
                    "overlap_width": max(0.0, overlap_high - overlap_low),
                }
            )

    summary_rows: list[dict[str, Any]] = []
    for target in targets:
        for codec in (SONY_CODEC, NIKON_CODEC):
            subset = [
                row
                for row in range_rows
                if row["target_bpp"] == target and row["codec"] == codec
            ]
            window_subset = [row for row in subset if row["selection_mode"] == "window"]
            summary_rows.append(
                {
                    "kind": "codec_target",
                    "target_bpp": target,
                    "codec": codec,
                    "codec_label": CODEC_LABELS.get(codec, codec),
                    "scene_count": len(subset),
                    "window_scene_count": len(window_subset),
                    "coverage_fraction": len(window_subset) / len(subset) if subset else float("nan"),
                    "median_window_candidate_count": _median(
                        [float(row["window_candidate_count"]) for row in window_subset]
                    ),
                    "median_used_candidate_count": _median(
                        [float(row["used_candidate_count"]) for row in subset]
                    ),
                    "median_best_psnr_window_only": _median(
                        [float(row["best_psnr"]) for row in window_subset]
                    ),
                    "median_psnr_range_width_window_only": _median(
                        [float(row["psnr_range_width"]) for row in window_subset]
                    ),
                    "p90_psnr_range_width_window_only": _percentile(
                        [float(row["psnr_range_width"]) for row in window_subset], 90
                    ),
                    "max_psnr_range_width_window_only": max(
                        [float(row["psnr_range_width"]) for row in window_subset],
                        default=float("nan"),
                    ),
                    "median_best_psnr_used": _median([float(row["best_psnr"]) for row in subset]),
                    "median_psnr_range_width_used": _median(
                        [float(row["psnr_range_width"]) for row in subset]
                    ),
                }
            )

        pair_subset = [row for row in comparison_rows if row["target_bpp"] == target]
        strong_pairs = [row for row in pair_subset if row["both_in_window"]]
        summary_rows.append(
            {
                "kind": "codec_pair_target",
                "target_bpp": target,
                "codec": "sony_824_vs_nikon_826",
                "codec_label": "#824 Sony vs #826 Nikon",
                "scene_count": len(pair_subset),
                "window_scene_count": len(strong_pairs),
                "coverage_fraction": len(strong_pairs) / len(pair_subset) if pair_subset else float("nan"),
                "median_best_delta_sony_minus_nikon": _median(
                    [float(row["best_delta_sony_minus_nikon"]) for row in strong_pairs]
                ),
                "sony_best_win_count": sum(1 for row in strong_pairs if row["best_winner"] == "sony_824"),
                "nikon_best_win_count": sum(1 for row in strong_pairs if row["best_winner"] == "nikon_826"),
                "tie_best_count": sum(1 for row in strong_pairs if row["best_winner"] == "tie"),
                "range_overlap_fraction": (
                    sum(1 for row in strong_pairs if row["quality_ranges_overlap"]) / len(strong_pairs)
                    if strong_pairs
                    else float("nan")
                ),
            }
        )

    return range_rows, comparison_rows, summary_rows, scene_order


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
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
        for row in rows:
            writer.writerow(row)


def make_scene_range_figures(
    range_rows: list[dict[str, Any]],
    scene_order: list[str],
    targets: list[float],
    tolerance: float,
    out_dir: Path,
) -> list[str]:
    figure_paths: list[str] = []
    by_key = {
        (row["source_id"], float(row["target_bpp"]), row["codec"]): row
        for row in range_rows
    }
    offsets = {SONY_CODEC: 0.16, NIKON_CODEC: -0.16}
    markers = {SONY_CODEC: "D", NIKON_CODEC: "o"}

    for target in targets:
        rows_for_target = [row for row in range_rows if float(row["target_bpp"]) == target]
        x_values = [
            float(row[key])
            for row in rows_for_target
            for key in ("psnr_min", "psnr_max", "best_psnr")
            if not math.isnan(float(row[key]))
        ]
        if not x_values:
            continue
        x_min = min(x_values)
        x_max = max(x_values)
        x_pad = max(0.5, (x_max - x_min) * 0.04)

        fig_height = max(8.0, len(scene_order) * 0.36 + 1.8)
        fig, ax = plt.subplots(figsize=(11.6, fig_height), dpi=150)
        y_positions = np.arange(len(scene_order), dtype=float)
        for index, scene in enumerate(scene_order):
            for codec in (SONY_CODEC, NIKON_CODEC):
                row = by_key.get((scene, target, codec))
                if row is None:
                    continue
                y = y_positions[index] + offsets[codec]
                color = CODEC_COLORS[codec]
                is_window = row["selection_mode"] == "window"
                alpha = 0.90 if is_window else 0.28
                ax.hlines(
                    y,
                    float(row["psnr_min"]),
                    float(row["psnr_max"]),
                    color=color,
                    alpha=alpha,
                    linewidth=3.0,
                )
                face = color if is_window else "white"
                ax.scatter(
                    [float(row["best_psnr"])],
                    [y],
                    marker=markers[codec],
                    s=36,
                    color=color,
                    facecolors=face,
                    edgecolors=color,
                    linewidths=1.2,
                    alpha=0.95,
                    zorder=3,
                )

        ax.set_yticks(y_positions)
        ax.set_yticklabels(scene_order, fontsize=8)
        ax.set_xlim(x_min - x_pad, x_max + x_pad)
        ax.set_xlabel("Whole-image raw PSNR (dB)")
        ax.set_title(
            f"Target {target:g} bpp: policy quality ranges by scene "
            f"(actual bpp window +/-{tolerance:g})"
        )
        ax.grid(axis="x", color="#dddddd", linewidth=0.8)
        ax.grid(axis="y", color="#eeeeee", linewidth=0.4)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        ax.plot([], [], color=CODEC_COLORS[SONY_CODEC], linewidth=3, label=CODEC_LABELS[SONY_CODEC])
        ax.plot([], [], color=CODEC_COLORS[NIKON_CODEC], linewidth=3, label=CODEC_LABELS[NIKON_CODEC])
        ax.scatter([], [], marker="D", color="#444444", s=36, label="best point")
        ax.scatter([], [], marker="o", facecolors="white", edgecolors="#444444", s=36, label="nearest fallback")
        ax.legend(loc="lower right", frameon=False, fontsize=8)
        fig.tight_layout()
        path = out_dir / f"fig_quality_range_target_{_fmt_target(target)}.png"
        fig.savefig(path)
        plt.close(fig)
        figure_paths.append(str(path))
    return figure_paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidate-csv",
        type=Path,
        required=True,
        help="policy_candidates.csv from policy_sweep_bpp_multiplicity.py",
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--rate-tolerance",
        type=float,
        default=0.20,
        help="Actual-bpp half-window used to call two points same-bpp.",
    )
    args = parser.parse_args()

    rows, scene_order, targets = _read_candidates(args.candidate_csv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    range_rows, comparison_rows, summary_rows, scene_order = build_ranges(
        rows, scene_order, targets, args.rate_tolerance
    )

    _write_csv(args.out_dir / "target_bpp_quality_ranges.csv", range_rows)
    _write_csv(args.out_dir / "target_bpp_best_comparison.csv", comparison_rows)
    _write_csv(args.out_dir / "target_bpp_range_summary.csv", summary_rows)
    scene_figures = make_scene_range_figures(
        range_rows, scene_order, targets, args.rate_tolerance, args.out_dir
    )

    manifest = {
        "kind": "same target-bpp quality range summary",
        "candidate_csv": str(args.candidate_csv),
        "rate_tolerance": args.rate_tolerance,
        "scene_count": len(scene_order),
        "targets_bpp": targets,
        "range_rows": len(range_rows),
        "comparison_rows": len(comparison_rows),
        "summary_rows": len(summary_rows),
        "selection_rule": (
            "For each scene/codec/target, use all deduplicated policy points whose actual_bpp is within "
            "target +/- tolerance. If none exist, emit a nearest_fallback point and mark it explicitly."
        ),
        "outputs": {
            "target_bpp_quality_ranges": str(args.out_dir / "target_bpp_quality_ranges.csv"),
            "target_bpp_best_comparison": str(args.out_dir / "target_bpp_best_comparison.csv"),
            "target_bpp_range_summary": str(args.out_dir / "target_bpp_range_summary.csv"),
            "scene_figures": scene_figures,
        },
    }
    with (args.out_dir / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
