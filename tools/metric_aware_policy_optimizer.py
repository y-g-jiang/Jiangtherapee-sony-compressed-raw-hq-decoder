#!/usr/bin/env python3
"""Metric-aware policy selection over decoder-visible #824/#826 point clouds.

This post-processor treats the exhaustive-ish point cloud as an operational
candidate set. It is similar in spirit to JPEG XS gains/priorities
optimization: choose an allocation policy for a metric under a rate constraint.
The present controls are mostly discrete, so exhaustive selection over the
already generated cloud is preferable to a continuous CMA-ES-style optimizer.

The rate axis is always actual syntax bpp. No target bpp is used.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


SONY = "sony_824_decoder_visible_packet_canonical"
NIKON = "nikon_826_decoder_visible_precinct_canonical"
LABELS = {SONY: "#824 Sony", NIKON: "#826 Nikon"}
CODEC_COLORS = {SONY: "#2f6fb0", NIKON: "#bf6a22"}

OBJECTIVES: dict[str, dict[str, float]] = {
    "psnr": {"psnr": 1.0},
    "mae_guarded": {"psnr": 0.20, "mae": 0.65, "max": 0.15},
    "max_guarded": {"psnr": 0.15, "mae": 0.20, "max": 0.65},
    "balanced": {"psnr": 0.45, "mae": 0.30, "max": 0.20, "rate_closeness": 0.05},
    "near_lossless_balanced": {"psnr": 0.25, "mae": 0.35, "max": 0.35, "rate_closeness": 0.05},
}

OBJECTIVE_COLORS = {
    "psnr": "#1b9e77",
    "mae_guarded": "#d95f02",
    "max_guarded": "#7570b3",
    "balanced": "#e7298a",
    "near_lossless_balanced": "#66a61e",
}

OBJECTIVE_LABELS = {
    "psnr": "PSNR-opt",
    "mae_guarded": "MAE-safe",
    "max_guarded": "MAX-safe",
    "balanced": "Balanced",
    "near_lossless_balanced": "Near-lossless",
}


def finite_float(value: Any, default: float = float("nan")) -> float:
    if value is None or value == "":
        return default
    try:
        out = float(value)
    except Exception:
        return default
    return out


def median(values: Iterable[float]) -> float:
    clean = sorted(v for v in values if math.isfinite(v))
    if not clean:
        return float("nan")
    n = len(clean)
    mid = n // 2
    if n % 2:
        return clean[mid]
    return (clean[mid - 1] + clean[mid]) * 0.5


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
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def compact_json(raw: str) -> str:
    if not raw:
        return ""
    try:
        return json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(raw)


def policy_family(row: dict[str, Any]) -> str:
    codec = str(row.get("codec", ""))
    if codec == SONY:
        selector_map = compact_json(str(row.get("selector_map", "")))
        if selector_map and selector_map != "{}":
            return f"sony_map:{selector_map}"
        cycle_len = row.get("selector_cycle_len", "")
        return f"sony_policy:{row.get('policy_name', '')}|cycle_len:{cycle_len}"
    if codec == NIKON:
        offsets = compact_json(str(row.get("component_offsets", "")))
        return f"nikon_offsets:{offsets}"
    return str(row.get("policy_name", ""))


def candidate_id(row: dict[str, Any]) -> str:
    keys = [
        "codec",
        "source_id",
        "policy_name",
        "policy_class",
        "actual_bpp",
        "knob",
        "base_step",
        "selector_map",
        "selector_cycle_len",
        "bp",
        "br",
        "global_bias",
        "component_offsets",
        "effective_gtli_tuple",
    ]
    payload = "|".join(str(row.get(key, "")) for key in keys)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rate = finite_float(row.get("actual_bpp"))
            psnr = finite_float(row.get("PSNR_raw_whole"))
            mae = finite_float(row.get("MAE_whole"))
            max_abs = finite_float(row.get("MAX_whole"))
            if not all(math.isfinite(v) for v in (rate, psnr, mae, max_abs)):
                continue
            row["candidate_id"] = candidate_id(row)
            row["policy_family"] = policy_family(row)
            row["_rate"] = rate
            row["_psnr"] = psnr
            row["_mae"] = mae
            row["_max"] = max_abs
            rows.append(row)
    return rows


def utility_values(rows: list[dict[str, Any]], center: float, tolerance: float) -> dict[str, dict[str, float]]:
    psnrs = [float(row["_psnr"]) for row in rows]
    maes = [float(row["_mae"]) for row in rows]
    maxes = [float(row["_max"]) for row in rows]

    def high(value: float, values: list[float]) -> float:
        lo = min(values)
        hi = max(values)
        if hi <= lo:
            return 0.5
        return (value - lo) / (hi - lo)

    def low(value: float, values: list[float]) -> float:
        lo = min(values)
        hi = max(values)
        if hi <= lo:
            return 0.5
        return (hi - value) / (hi - lo)

    out: dict[str, dict[str, float]] = {}
    for row in rows:
        rate = float(row["_rate"])
        out[str(row["candidate_id"])] = {
            "psnr": high(float(row["_psnr"]), psnrs),
            "mae": low(float(row["_mae"]), maes),
            "max": low(float(row["_max"]), maxes),
            "rate_closeness": max(0.0, 1.0 - abs(rate - center) / max(tolerance, 1e-9)),
        }
    return out


def rank_percentile(
    rows: list[dict[str, Any]],
    selected: dict[str, Any],
    key: str,
    higher_is_better: bool,
) -> float:
    values = [float(row[key]) for row in rows if math.isfinite(float(row[key]))]
    if not values:
        return float("nan")
    value = float(selected[key])
    if higher_is_better:
        wins = sum(1 for v in values if v <= value)
    else:
        wins = sum(1 for v in values if v >= value)
    return wins / len(values)


def score_row(row: dict[str, Any], utilities: dict[str, dict[str, float]], weights: dict[str, float]) -> float:
    u = utilities[str(row["candidate_id"])]
    return sum(float(weight) * float(u.get(name, 0.0)) for name, weight in weights.items())


def select_recommendations(
    rows: list[dict[str, Any]],
    centers: list[float],
    tolerance: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_group[(str(row["codec"]), str(row["source_id"]))].append(row)

    selected_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    percentile_rows: list[dict[str, Any]] = []

    for (codec, scene), group in sorted(by_group.items()):
        for center in centers:
            lo = center - tolerance
            hi = center + tolerance
            inside = [row for row in group if lo <= float(row["_rate"]) < hi]
            if not inside:
                continue
            utilities = utility_values(inside, center, tolerance)
            for objective, weights in OBJECTIVES.items():
                best = max(
                    inside,
                    key=lambda row: (
                        score_row(row, utilities, weights),
                        float(row["_psnr"]),
                        -float(row["_mae"]),
                        -float(row["_max"]),
                    ),
                )
                u = utilities[str(best["candidate_id"])]
                out = {
                    "objective": objective,
                    "objective_label": OBJECTIVE_LABELS[objective],
                    "objective_weights": json.dumps(weights, sort_keys=True, separators=(",", ":")),
                    "codec": codec,
                    "codec_label": LABELS.get(codec, codec),
                    "source_id": scene,
                    "bin_center_actual_bpp": f"{center:.6f}",
                    "bin_tolerance": f"{tolerance:.6f}",
                    "candidate_count_in_window": len(inside),
                    "candidate_id": best["candidate_id"],
                    "policy_family": best["policy_family"],
                    "policy_name": best.get("policy_name", ""),
                    "policy_class": best.get("policy_class", ""),
                    "actual_bpp": f"{float(best['_rate']):.9f}",
                    "PSNR_raw_whole": f"{float(best['_psnr']):.9f}",
                    "MAE_whole": f"{float(best['_mae']):.9f}",
                    "MAX_whole": f"{float(best['_max']):.9f}",
                    "MSE_whole": best.get("MSE_whole", ""),
                    "objective_score": f"{score_row(best, utilities, weights):.9f}",
                    "utility_psnr": f"{u['psnr']:.9f}",
                    "utility_mae": f"{u['mae']:.9f}",
                    "utility_max": f"{u['max']:.9f}",
                    "utility_rate_closeness": f"{u['rate_closeness']:.9f}",
                    "psnr_percentile_in_window": f"{rank_percentile(inside, best, '_psnr', True):.9f}",
                    "mae_safety_percentile_in_window": f"{rank_percentile(inside, best, '_mae', False):.9f}",
                    "max_safety_percentile_in_window": f"{rank_percentile(inside, best, '_max', False):.9f}",
                    "base_step": best.get("base_step", ""),
                    "selector_map": best.get("selector_map", ""),
                    "selector_cycle_len": best.get("selector_cycle_len", ""),
                    "bp": best.get("bp", ""),
                    "br": best.get("br", ""),
                    "global_bias": best.get("global_bias", ""),
                    "component_offsets": best.get("component_offsets", ""),
                    "effective_gtli_tuple": best.get("effective_gtli_tuple", ""),
                }
                selected_rows.append(out)
                for metric_name, metric_key, higher in [
                    ("PSNR_raw_whole", "_psnr", True),
                    ("MAE_whole_safety", "_mae", False),
                    ("MAX_whole_safety", "_max", False),
                ]:
                    percentile_rows.append(
                        {
                            "objective": objective,
                            "codec": codec,
                            "source_id": scene,
                            "bin_center_actual_bpp": f"{center:.6f}",
                            "metric": metric_name,
                            "percentile_in_window": f"{rank_percentile(inside, best, metric_key, higher):.9f}",
                        }
                    )

    by_summary: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in selected_rows:
        by_summary[(str(row["codec"]), str(row["objective"]), str(row["bin_center_actual_bpp"]))].append(row)
    for (codec, objective, center), group in sorted(
        by_summary.items(), key=lambda item: (item[0][0], item[0][1], float(item[0][2]))
    ):
        summary_rows.append(
            {
                "codec": codec,
                "codec_label": LABELS.get(codec, codec),
                "objective": objective,
                "objective_label": OBJECTIVE_LABELS[objective],
                "bin_center_actual_bpp": center,
                "covered_scene_count": len({row["source_id"] for row in group}),
                "median_actual_bpp": f"{median(finite_float(row['actual_bpp']) for row in group):.9f}",
                "median_PSNR_raw_whole": f"{median(finite_float(row['PSNR_raw_whole']) for row in group):.9f}",
                "median_MAE_whole": f"{median(finite_float(row['MAE_whole']) for row in group):.9f}",
                "median_MAX_whole": f"{median(finite_float(row['MAX_whole']) for row in group):.9f}",
                "median_psnr_percentile": f"{median(finite_float(row['psnr_percentile_in_window']) for row in group):.9f}",
                "median_mae_safety_percentile": f"{median(finite_float(row['mae_safety_percentile_in_window']) for row in group):.9f}",
                "median_max_safety_percentile": f"{median(finite_float(row['max_safety_percentile_in_window']) for row in group):.9f}",
            }
        )
    return selected_rows, summary_rows, percentile_rows


def summarize_policy_families(selected_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_family: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in selected_rows:
        by_family[(str(row["codec"]), str(row["objective"]), str(row["policy_family"]))].append(row)

    rows: list[dict[str, Any]] = []
    for (codec, objective, family), group in by_family.items():
        policy_names = [str(row["policy_name"]) for row in group]
        selector_maps = [str(row["selector_map"]) for row in group]
        component_offsets = [str(row["component_offsets"]) for row in group]
        rows.append(
            {
                "codec": codec,
                "codec_label": LABELS.get(codec, codec),
                "objective": objective,
                "objective_label": OBJECTIVE_LABELS[objective],
                "policy_family": family,
                "selection_count": len(group),
                "covered_scene_count": len({row["source_id"] for row in group}),
                "covered_bin_count": len({row["bin_center_actual_bpp"] for row in group}),
                "median_actual_bpp": f"{median(finite_float(row['actual_bpp']) for row in group):.9f}",
                "median_PSNR_raw_whole": f"{median(finite_float(row['PSNR_raw_whole']) for row in group):.9f}",
                "median_MAE_whole": f"{median(finite_float(row['MAE_whole']) for row in group):.9f}",
                "median_MAX_whole": f"{median(finite_float(row['MAX_whole']) for row in group):.9f}",
                "representative_policy_name": max(set(policy_names), key=policy_names.count),
                "representative_selector_map": max(set(selector_maps), key=selector_maps.count),
                "representative_component_offsets": max(set(component_offsets), key=component_offsets.count),
            }
        )
    rows.sort(key=lambda row: (row["codec"], row["objective"], -int(row["selection_count"]), row["policy_family"]))
    return rows


def sample_background(rows: list[dict[str, Any]], max_points_per_codec: int, seed: int) -> dict[str, list[dict[str, Any]]]:
    rng = random.Random(seed)
    out: dict[str, list[dict[str, Any]]] = {}
    for codec in [SONY, NIKON]:
        group = [row for row in rows if row["codec"] == codec]
        if len(group) > max_points_per_codec:
            group = rng.sample(group, max_points_per_codec)
        out[codec] = group
    return out


def make_recommended_scatter(
    out_dir: Path,
    rows: list[dict[str, Any]],
    selected_rows: list[dict[str, Any]],
    summary_rows: list[dict[str, Any]],
    max_points_per_codec: int,
    seed: int,
) -> Path:
    bg = sample_background(rows, max_points_per_codec, seed)
    fig, axes = plt.subplots(1, 2, figsize=(14.2, 5.6), dpi=170, sharey=True)
    for ax, codec in zip(axes, [SONY, NIKON]):
        group = bg.get(codec, [])
        ax.scatter(
            [float(row["_rate"]) for row in group],
            [float(row["_psnr"]) for row in group],
            s=3,
            alpha=0.08,
            linewidths=0,
            color=CODEC_COLORS[codec],
            label="all candidates",
        )
        selected = [row for row in selected_rows if row["codec"] == codec]
        for objective in OBJECTIVES:
            obj = [row for row in selected if row["objective"] == objective]
            if not obj:
                continue
            ax.scatter(
                [finite_float(row["actual_bpp"]) for row in obj],
                [finite_float(row["PSNR_raw_whole"]) for row in obj],
                s=16,
                alpha=0.26,
                linewidths=0,
                color=OBJECTIVE_COLORS[objective],
            )
            med = [row for row in summary_rows if row["codec"] == codec and row["objective"] == objective]
            med.sort(key=lambda row: finite_float(row["bin_center_actual_bpp"]))
            ax.plot(
                [finite_float(row["median_actual_bpp"]) for row in med],
                [finite_float(row["median_PSNR_raw_whole"]) for row in med],
                marker="*",
                markersize=8,
                linewidth=1.6,
                color=OBJECTIVE_COLORS[objective],
                label=OBJECTIVE_LABELS[objective],
            )
            if med:
                last = med[-1]
                ax.annotate(
                    OBJECTIVE_LABELS[objective],
                    (finite_float(last["median_actual_bpp"]), finite_float(last["median_PSNR_raw_whole"])),
                    textcoords="offset points",
                    xytext=(4, 2),
                    fontsize=7,
                    color=OBJECTIVE_COLORS[objective],
                )
        ax.set_title(LABELS[codec])
        ax.set_xlabel("Actual syntax bpp")
        ax.grid(True, alpha=0.22)
    axes[0].set_ylabel("Whole RAW PSNR (dB)")
    axes[1].legend(loc="lower right", fontsize=8, frameon=False)
    fig.suptitle("Metric-aware selected policies marked on the actual-bpp point cloud", y=0.995)
    fig.tight_layout()
    path = out_dir / "fig_metric_aware_recommended_scatter.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def make_percentile_heatmap(out_dir: Path, percentile_rows: list[dict[str, Any]]) -> Path:
    metrics = ["PSNR_raw_whole", "MAE_whole_safety", "MAX_whole_safety"]
    labels = ["PSNR percentile", "MAE safety percentile", "MAX safety percentile"]
    y_rows: list[tuple[str, str]] = []
    data: list[list[float]] = []
    for codec in [SONY, NIKON]:
        for objective in OBJECTIVES:
            group = [row for row in percentile_rows if row["codec"] == codec and row["objective"] == objective]
            y_rows.append((LABELS[codec], OBJECTIVE_LABELS[objective]))
            values = []
            for metric in metrics:
                values.append(median(finite_float(row["percentile_in_window"]) for row in group if row["metric"] == metric))
            data.append(values)
    arr = np.asarray(data, dtype=np.float64)
    fig, ax = plt.subplots(figsize=(8.8, 6.0), dpi=170)
    im = ax.imshow(arr, vmin=0.0, vmax=1.0, cmap="magma")
    ax.set_xticks(np.arange(len(metrics)), labels=labels, rotation=18, ha="right")
    ax.set_yticks(np.arange(len(y_rows)), labels=[f"{codec} / {obj}" for codec, obj in y_rows])
    for i in range(arr.shape[0]):
        for j in range(arr.shape[1]):
            val = arr[i, j]
            ax.text(j, i, "" if not math.isfinite(val) else f"{val:.2f}", ha="center", va="center", color="white", fontsize=8)
    fig.colorbar(im, ax=ax, label="Median percentile within same scene/codec/bpp window; higher is better")
    ax.set_title("Cross-metric standing of each optimized selection")
    fig.tight_layout()
    path = out_dir / "fig_metric_aware_cross_metric_percentiles.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def make_policy_family_chart(out_dir: Path, family_rows: list[dict[str, Any]], top_n: int) -> Path:
    plot_rows = []
    for codec in [SONY, NIKON]:
        for objective in OBJECTIVES:
            group = [row for row in family_rows if row["codec"] == codec and row["objective"] == objective]
            plot_rows.extend(group[:top_n])
    labels = [f"{LABELS[row['codec']]}\n{OBJECTIVE_LABELS[row['objective']]}\n{row['policy_family'][:42]}" for row in plot_rows]
    counts = [int(row["selection_count"]) for row in plot_rows]
    colors = [OBJECTIVE_COLORS[row["objective"]] for row in plot_rows]
    fig, ax = plt.subplots(figsize=(11.5, max(5.0, len(plot_rows) * 0.23)), dpi=170)
    y = np.arange(len(plot_rows))
    ax.barh(y, counts, color=colors, alpha=0.82)
    ax.set_yticks(y, labels=labels, fontsize=6)
    ax.invert_yaxis()
    ax.set_xlabel("Selected scene-bin count")
    ax.set_title("Most frequent allocation families selected by each objective")
    ax.grid(True, axis="x", alpha=0.25)
    fig.tight_layout()
    path = out_dir / "fig_metric_aware_policy_family_frequency.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-csv", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--bins", default="1.5,2.0,2.5,3.0,4.0,5.0")
    parser.add_argument("--tolerance", type=float, default=0.125)
    parser.add_argument("--max-background-points-per-codec", type=int, default=120000)
    parser.add_argument("--top-family-count", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260608)
    args = parser.parse_args()

    centers = [float(item.strip()) for item in args.bins.split(",") if item.strip()]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows = load_rows(args.policy_csv)
    selected, summary, percentiles = select_recommendations(rows, centers, args.tolerance)
    families = summarize_policy_families(selected)

    write_csv(args.out_dir / "metric_aware_recommended_candidates.csv", selected)
    write_csv(args.out_dir / "metric_aware_recommendation_summary_by_bin.csv", summary)
    write_csv(args.out_dir / "metric_aware_cross_metric_percentiles.csv", percentiles)
    write_csv(args.out_dir / "metric_aware_policy_family_summary.csv", families)

    figures = {
        "recommended_scatter": str(
            make_recommended_scatter(
                args.out_dir,
                rows,
                selected,
                summary,
                args.max_background_points_per_codec,
                args.seed,
            )
        ),
        "cross_metric_percentiles": str(make_percentile_heatmap(args.out_dir, percentiles)),
        "policy_family_frequency": str(make_policy_family_chart(args.out_dir, families, args.top_family_count)),
    }
    manifest = {
        "kind": "metric-aware policy optimizer over decoder-visible #824/#826 point cloud",
        "policy_csv": str(args.policy_csv),
        "out_dir": str(args.out_dir),
        "rate_axis_rule": "actual syntax bpp only; target bpp is not used",
        "method": (
            "For each codec, scene, and actual-bpp window, normalize PSNR/MAE/MAX utilities within the local candidate set "
            "and select rows that maximize metric-specific objective weights. This mimics JPEG XS metric-aware "
            "gains/priorities search, but uses exhaustive selection over discrete decoder-visible policies rather than CMA-ES."
        ),
        "bins": centers,
        "tolerance": args.tolerance,
        "objective_weights": OBJECTIVES,
        "candidate_rows_loaded": len(rows),
        "recommended_candidate_rows": len(selected),
        "summary_rows": len(summary),
        "policy_family_rows": len(families),
        "figures": figures,
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
