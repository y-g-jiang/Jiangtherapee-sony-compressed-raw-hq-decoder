#!/usr/bin/env python3
"""JPEG XS-style PSNR/MS-SSIM policy optimization for #824/#826 clouds.

Brummer/de Vleeschouwer optimize JPEG XS gains/priorities for a metric and a
rate.  This script mirrors that evaluation logic on our decoder-visible RAW
controls:

* Sony controls: component selector allocation plus base_step.
* Nikon controls: component GTLI offsets, global bias, and Bp/Br row.
* Objectives: whole RAW PSNR and detail-plane mean MS-SSIM.

The search is exhaustive over the already generated operational point cloud
inside actual-bpp windows.  This is deliberately not a production encoder claim
and never uses target_bpp as an RD axis.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import statistics
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import policy_sweep_bpp_multiplicity as sweep
import strict_824_826_math_eval as strict


SONY = strict.SONY_CODEC
NIKON = strict.NIKON_CODEC
LABELS = {SONY: "#824 Sony", NIKON: "#826 Nikon"}
CODEC_COLORS = {SONY: "#2f6fb0", NIKON: "#bf6a22"}
OBJECTIVE_COLORS = {"psnr": "#1b9e77", "msssim": "#d95f02"}
OBJECTIVE_LABELS = {"psnr": "PSNR-opt", "msssim": "MS-SSIM-opt"}


@dataclass(frozen=True)
class SceneTask:
    source_id: str
    planes: dict[str, np.ndarray]
    rows: tuple[dict[str, str], ...]
    levels: int
    real_controls: str


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
    return statistics.median(clean)


def percentile(values: Iterable[float], q: float) -> float:
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
    return clean[lo] * (1.0 - frac) + clean[hi] * frac


def msssim_db(value: float) -> float:
    if not math.isfinite(value):
        return float("nan")
    return -10.0 * math.log10(max(1.0 - min(value, 1.0), 1e-12))


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


def matching_bin(rate: float, centers: list[float], tolerance: float) -> float | None:
    hits = [center for center in centers if center - tolerance <= rate < center + tolerance]
    if not hits:
        return None
    return min(hits, key=lambda center: abs(rate - center))


def load_window_rows(path: Path, centers: list[float], tolerance: float) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rate = finite_float(row.get("actual_bpp"))
            psnr = finite_float(row.get("PSNR_raw_whole"))
            if not (math.isfinite(rate) and math.isfinite(psnr)):
                continue
            center = matching_bin(rate, centers, tolerance)
            if center is None:
                continue
            row["candidate_id"] = candidate_id(row)
            row["policy_family"] = policy_family(row)
            row["bin_center_actual_bpp"] = f"{center:.6f}"
            rows.append(row)
    return rows


def generate_sources(seed: int, width: int, height: int, scene_names: list[str]) -> dict[str, dict[str, np.ndarray]]:
    rng = np.random.default_rng(seed)
    all_sources = {
        scene: strict.generate_scene(scene, height // 2, width // 2, rng)
        for scene in strict.SCENES
    }
    return {scene: all_sources[scene] for scene in scene_names}


def sony_policy_from_row(row: dict[str, str], real_controls: Path) -> dict[str, Any]:
    raw_map = str(row.get("selector_map", ""))
    if raw_map:
        try:
            parsed = json.loads(raw_map)
        except json.JSONDecodeError:
            parsed = {}
        if parsed:
            return {"class": "component_map", "map": {str(k): int(v) for k, v in parsed.items()}}
    name = str(row.get("policy_name", ""))
    if name in sweep.SONY_POLICIES:
        return dict(sweep.SONY_POLICIES[name])
    if name == "real_hq_selector_cycle":
        return {
            "class": "row_cycle",
            "cycle": list(sweep.read_sony_real_selector_cycle(real_controls)),
            "description": "Selector cycle fitted from public Sony HQ raw.pixls samples.",
        }
    raise ValueError(f"cannot reconstruct Sony policy from row: {name}")


def reconstruction_key(row: dict[str, str]) -> str:
    codec = str(row["codec"])
    if codec == NIKON:
        return "|".join([codec, row["source_id"], str(row.get("effective_gtli_tuple", ""))])
    return "|".join(
        [
            codec,
            row["source_id"],
            str(row.get("policy_name", "")),
            str(row.get("base_step", "")),
            str(row.get("selector_map", "")),
            str(row.get("selector_cycle_len", "")),
        ]
    )


def mean_msssim(source: dict[str, np.ndarray], recon: dict[str, np.ndarray]) -> float:
    return float(statistics.mean(strict.ms_ssim_index(source[plane], recon[plane]) for plane in ("R", "G0", "G1", "B")))


def evaluate_scene_task(task: SceneTask) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    t0 = time.perf_counter()
    out: list[dict[str, Any]] = []
    cache: dict[str, float] = {}

    sony_rows = [row for row in task.rows if row["codec"] == SONY]
    nikon_rows = [row for row in task.rows if row["codec"] == NIKON]
    sony_coeffs = sony_sizes = None
    nikon_coeffs = nikon_sizes = None
    real_controls = Path(task.real_controls)

    if sony_rows:
        sony_comps = strict.sony_forward(task.planes)
        sony_coeffs, sony_sizes = strict.transform_components(sony_comps, task.levels)
    if nikon_rows:
        nikon_comps = strict.nikon_forward(task.planes)
        nikon_coeffs, nikon_sizes = strict.transform_components(nikon_comps, task.levels)

    for row in task.rows:
        key = reconstruction_key(row)
        value = cache.get(key)
        if value is None:
            if row["codec"] == SONY:
                assert sony_coeffs is not None and sony_sizes is not None
                policy = sony_policy_from_row(row, real_controls)
                deq, _syntax = sweep.sony_syntax_encode_policy(sony_coeffs, finite_float(row.get("base_step")), policy)
                recon = strict.sony_inverse(strict.inverse_transform_components(deq, sony_sizes))
            else:
                assert nikon_coeffs is not None and nikon_sizes is not None
                offsets = {str(k): int(v) for k, v in json.loads(str(row.get("component_offsets", "{}"))).items()}
                bp = int(finite_float(row.get("bp")))
                br = int(finite_float(row.get("br")))
                global_bias = int(finite_float(row.get("global_bias")))
                deq, _syntax = sweep.nikon_syntax_encode_policy(nikon_coeffs, bp, br, global_bias, offsets)
                recon = strict.nikon_inverse(strict.inverse_transform_components(deq, nikon_sizes))
            value = mean_msssim(task.planes, recon)
            cache[key] = value

        psnr = finite_float(row.get("PSNR_raw_whole"))
        out.append(
            {
                "candidate_id": row["candidate_id"],
                "codec": row["codec"],
                "codec_label": LABELS.get(row["codec"], row["codec"]),
                "source_id": row["source_id"],
                "bin_center_actual_bpp": row["bin_center_actual_bpp"],
                "actual_bpp": f"{finite_float(row.get('actual_bpp')):.9f}",
                "PSNR_raw_whole": f"{psnr:.9f}",
                "MS_SSIM_mean_detail": f"{value:.12f}",
                "MS_SSIM_db_detail": f"{msssim_db(value):.9f}",
                "MAE_whole": row.get("MAE_whole", ""),
                "MAX_whole": row.get("MAX_whole", ""),
                "MSE_whole": row.get("MSE_whole", ""),
                "policy_family": row["policy_family"],
                "policy_name": row.get("policy_name", ""),
                "policy_class": row.get("policy_class", ""),
                "base_step": row.get("base_step", ""),
                "selector_map": row.get("selector_map", ""),
                "selector_cycle_len": row.get("selector_cycle_len", ""),
                "bp": row.get("bp", ""),
                "br": row.get("br", ""),
                "global_bias": row.get("global_bias", ""),
                "component_offsets": row.get("component_offsets", ""),
                "effective_gtli_tuple": row.get("effective_gtli_tuple", ""),
            }
        )
    return (
        task.source_id,
        out,
        {
            "source_id": task.source_id,
            "input_rows": len(task.rows),
            "unique_reconstruction_keys": len(cache),
            "elapsed_seconds": time.perf_counter() - t0,
        },
    )


def local_percentile(rows: list[dict[str, Any]], selected: dict[str, Any], key: str) -> float:
    values = [finite_float(row[key]) for row in rows]
    value = finite_float(selected[key])
    return sum(1 for candidate in values if candidate <= value) / max(1, len(values))


def select_objective_rows(metric_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_group: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in metric_rows:
        by_group[(str(row["codec"]), str(row["source_id"]), str(row["bin_center_actual_bpp"]))].append(row)

    selected: list[dict[str, Any]] = []
    regret: list[dict[str, Any]] = []
    for (codec, scene, center), rows in sorted(by_group.items(), key=lambda item: (item[0][0], item[0][1], float(item[0][2]))):
        psnr_best = max(rows, key=lambda row: (finite_float(row["PSNR_raw_whole"]), finite_float(row["MS_SSIM_mean_detail"]), -abs(finite_float(row["actual_bpp"]) - float(center))))
        msssim_best = max(rows, key=lambda row: (finite_float(row["MS_SSIM_mean_detail"]), finite_float(row["PSNR_raw_whole"]), -abs(finite_float(row["actual_bpp"]) - float(center))))
        bests = {"psnr": psnr_best, "msssim": msssim_best}
        psnr_ref = finite_float(psnr_best["PSNR_raw_whole"])
        msssim_ref = finite_float(msssim_best["MS_SSIM_db_detail"])
        for objective, row in bests.items():
            out = dict(row)
            out["objective"] = objective
            out["objective_label"] = OBJECTIVE_LABELS[objective]
            out["psnr_regret_db_vs_psnr_opt"] = f"{psnr_ref - finite_float(row['PSNR_raw_whole']):.9f}"
            out["msssim_db_regret_vs_msssim_opt"] = f"{msssim_ref - finite_float(row['MS_SSIM_db_detail']):.9f}"
            out["psnr_percentile_in_window"] = f"{local_percentile(rows, row, 'PSNR_raw_whole'):.9f}"
            out["msssim_percentile_in_window"] = f"{local_percentile(rows, row, 'MS_SSIM_mean_detail'):.9f}"
            out["candidate_count_in_window"] = len(rows)
            selected.append(out)
            regret.append(
                {
                    "codec": codec,
                    "source_id": scene,
                    "bin_center_actual_bpp": center,
                    "objective": objective,
                    "objective_label": OBJECTIVE_LABELS[objective],
                    "psnr_regret_db_vs_psnr_opt": out["psnr_regret_db_vs_psnr_opt"],
                    "msssim_db_regret_vs_msssim_opt": out["msssim_db_regret_vs_msssim_opt"],
                    "psnr_percentile_in_window": out["psnr_percentile_in_window"],
                    "msssim_percentile_in_window": out["msssim_percentile_in_window"],
                }
            )

    summary: list[dict[str, Any]] = []
    by_summary: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        by_summary[(str(row["codec"]), str(row["objective"]), str(row["bin_center_actual_bpp"]))].append(row)
    for (codec, objective, center), rows in sorted(by_summary.items(), key=lambda item: (item[0][0], item[0][1], float(item[0][2]))):
        summary.append(
            {
                "codec": codec,
                "codec_label": LABELS.get(codec, codec),
                "objective": objective,
                "objective_label": OBJECTIVE_LABELS[objective],
                "bin_center_actual_bpp": center,
                "covered_scene_count": len({row["source_id"] for row in rows}),
                "median_actual_bpp": f"{median(finite_float(row['actual_bpp']) for row in rows):.9f}",
                "median_PSNR_raw_whole": f"{median(finite_float(row['PSNR_raw_whole']) for row in rows):.9f}",
                "median_MS_SSIM_mean_detail": f"{median(finite_float(row['MS_SSIM_mean_detail']) for row in rows):.12f}",
                "median_MS_SSIM_db_detail": f"{median(finite_float(row['MS_SSIM_db_detail']) for row in rows):.9f}",
                "median_psnr_regret_db_vs_psnr_opt": f"{median(finite_float(row['psnr_regret_db_vs_psnr_opt']) for row in rows):.9f}",
                "median_msssim_db_regret_vs_msssim_opt": f"{median(finite_float(row['msssim_db_regret_vs_msssim_opt']) for row in rows):.9f}",
                "median_psnr_percentile": f"{median(finite_float(row['psnr_percentile_in_window']) for row in rows):.9f}",
                "median_msssim_percentile": f"{median(finite_float(row['msssim_percentile_in_window']) for row in rows):.9f}",
            }
        )
    return selected, summary, regret


def summarize_policy_families(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_family: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        by_family[(str(row["codec"]), str(row["objective"]), str(row["policy_family"]))].append(row)
    rows: list[dict[str, Any]] = []
    for (codec, objective, family), group in by_family.items():
        names = [str(row["policy_name"]) for row in group]
        selector_maps = [str(row["selector_map"]) for row in group]
        offsets = [str(row["component_offsets"]) for row in group]
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
                "median_MS_SSIM_mean_detail": f"{median(finite_float(row['MS_SSIM_mean_detail']) for row in group):.12f}",
                "median_MS_SSIM_db_detail": f"{median(finite_float(row['MS_SSIM_db_detail']) for row in group):.9f}",
                "representative_policy_name": max(set(names), key=names.count),
                "representative_selector_map": max(set(selector_maps), key=selector_maps.count),
                "representative_component_offsets": max(set(offsets), key=offsets.count),
            }
        )
    rows.sort(key=lambda row: (row["codec"], row["objective"], -int(row["selection_count"]), row["policy_family"]))
    return rows


def sample_rows(rows: list[dict[str, Any]], max_points_per_codec: int, seed: int) -> dict[str, list[dict[str, Any]]]:
    rng = random.Random(seed)
    out: dict[str, list[dict[str, Any]]] = {}
    for codec in [SONY, NIKON]:
        group = [row for row in rows if row["codec"] == codec]
        if len(group) > max_points_per_codec:
            group = rng.sample(group, max_points_per_codec)
        out[codec] = group
    return out


def make_metric_scatter(
    out_dir: Path,
    metric_rows: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    summary: list[dict[str, Any]],
    y_key: str,
    y_label: str,
    filename: str,
    max_points_per_codec: int,
    seed: int,
) -> Path:
    bg = sample_rows(metric_rows, max_points_per_codec, seed)
    fig, axes = plt.subplots(1, 2, figsize=(14.2, 5.6), dpi=170, sharey=True)
    for ax, codec in zip(axes, [SONY, NIKON]):
        group = bg.get(codec, [])
        ax.scatter(
            [finite_float(row["actual_bpp"]) for row in group],
            [finite_float(row[y_key]) for row in group],
            s=3,
            alpha=0.08,
            linewidths=0,
            color=CODEC_COLORS[codec],
        )
        for objective in ["psnr", "msssim"]:
            obj_rows = [row for row in selected if row["codec"] == codec and row["objective"] == objective]
            ax.scatter(
                [finite_float(row["actual_bpp"]) for row in obj_rows],
                [finite_float(row[y_key]) for row in obj_rows],
                s=18,
                alpha=0.34,
                linewidths=0,
                color=OBJECTIVE_COLORS[objective],
            )
            med = [row for row in summary if row["codec"] == codec and row["objective"] == objective]
            med.sort(key=lambda row: finite_float(row["bin_center_actual_bpp"]))
            summary_y_key = "median_" + y_key
            ax.plot(
                [finite_float(row["median_actual_bpp"]) for row in med],
                [finite_float(row[summary_y_key]) for row in med],
                marker="*" if objective == "msssim" else "o",
                markersize=8,
                linewidth=1.6,
                color=OBJECTIVE_COLORS[objective],
                label=OBJECTIVE_LABELS[objective],
            )
        ax.set_title(LABELS[codec])
        ax.set_xlabel("Actual syntax bpp")
        ax.grid(True, alpha=0.22)
    axes[0].set_ylabel(y_label)
    axes[1].legend(loc="lower right", frameon=False, fontsize=8)
    fig.tight_layout()
    path = out_dir / filename
    fig.savefig(path)
    plt.close(fig)
    return path


def make_regret_heatmap(out_dir: Path, regret: list[dict[str, Any]]) -> Path:
    columns = [
        ("psnr_regret_db_vs_psnr_opt", "PSNR regret dB"),
        ("msssim_db_regret_vs_msssim_opt", "MS-SSIM dB regret"),
        ("psnr_percentile_in_window", "PSNR percentile"),
        ("msssim_percentile_in_window", "MS-SSIM percentile"),
    ]
    y_rows: list[tuple[str, str]] = []
    data: list[list[float]] = []
    for codec in [SONY, NIKON]:
        for objective in ["psnr", "msssim"]:
            group = [row for row in regret if row["codec"] == codec and row["objective"] == objective]
            y_rows.append((LABELS[codec], OBJECTIVE_LABELS[objective]))
            data.append([median(finite_float(row[key]) for row in group) for key, _label in columns])
    arr = np.asarray(data, dtype=np.float64)

    fig, axes = plt.subplots(1, 2, figsize=(10.8, 5.3), dpi=170, gridspec_kw={"width_ratios": [1, 1]})
    regret_arr = arr[:, :2]
    perc_arr = arr[:, 2:]
    im0 = axes[0].imshow(regret_arr, cmap="viridis_r")
    axes[0].set_xticks(np.arange(2), labels=[label for _key, label in columns[:2]], rotation=20, ha="right")
    axes[0].set_yticks(np.arange(len(y_rows)), labels=[f"{codec} / {obj}" for codec, obj in y_rows])
    for i in range(regret_arr.shape[0]):
        for j in range(regret_arr.shape[1]):
            axes[0].text(j, i, f"{regret_arr[i, j]:.3f}", ha="center", va="center", color="white", fontsize=8)
    fig.colorbar(im0, ax=axes[0], label="Median regret; lower is better")

    im1 = axes[1].imshow(perc_arr, vmin=0, vmax=1, cmap="magma")
    axes[1].set_xticks(np.arange(2), labels=[label for _key, label in columns[2:]], rotation=20, ha="right")
    axes[1].set_yticks(np.arange(len(y_rows)), labels=[])
    for i in range(perc_arr.shape[0]):
        for j in range(perc_arr.shape[1]):
            axes[1].text(j, i, f"{perc_arr[i, j]:.2f}", ha="center", va="center", color="white", fontsize=8)
    fig.colorbar(im1, ax=axes[1], label="Median percentile; higher is better")
    fig.suptitle("Cross-objective cost of PSNR-opt vs MS-SSIM-opt selections")
    fig.tight_layout()
    path = out_dir / "fig_psnr_msssim_cross_objective_regret.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def make_family_chart(out_dir: Path, family_rows: list[dict[str, Any]], top_n: int) -> Path:
    plot_rows: list[dict[str, Any]] = []
    for codec in [SONY, NIKON]:
        for objective in ["psnr", "msssim"]:
            plot_rows.extend([row for row in family_rows if row["codec"] == codec and row["objective"] == objective][:top_n])
    labels = [f"{LABELS[row['codec']]}\n{OBJECTIVE_LABELS[row['objective']]}\n{str(row['policy_family'])[:52]}" for row in plot_rows]
    counts = [int(row["selection_count"]) for row in plot_rows]
    colors = [OBJECTIVE_COLORS[str(row["objective"])] for row in plot_rows]
    fig, ax = plt.subplots(figsize=(11.5, max(4.8, 0.34 * len(plot_rows))), dpi=170)
    y = np.arange(len(plot_rows))
    ax.barh(y, counts, color=colors, alpha=0.82)
    ax.set_yticks(y, labels=labels, fontsize=7)
    ax.invert_yaxis()
    ax.set_xlabel("Selected scene-bin count")
    ax.set_title("Most frequent PSNR/MS-SSIM optimized allocation families")
    ax.grid(True, axis="x", alpha=0.25)
    fig.tight_layout()
    path = out_dir / "fig_psnr_msssim_policy_family_frequency.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-csv", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--bins", default="1.5,2.0,2.5,3.0,4.0,5.0")
    parser.add_argument("--tolerance", type=float, default=0.125)
    parser.add_argument("--width", type=int, default=256)
    parser.add_argument("--height", type=int, default=256)
    parser.add_argument("--levels", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260605)
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--real-controls", type=Path, default=Path("out/production_fit_samples/real_bitstream_controls.csv"))
    parser.add_argument("--max-background-points-per-codec", type=int, default=70000)
    parser.add_argument("--top-family-count", type=int, default=5)
    args = parser.parse_args()

    centers = [float(item.strip()) for item in args.bins.split(",") if item.strip()]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()

    window_rows = load_window_rows(args.policy_csv, centers, args.tolerance)
    scene_names = sorted({row["source_id"] for row in window_rows}, key=list(strict.SCENES).index)
    sources = generate_sources(args.seed, args.width, args.height, scene_names)
    rows_by_scene: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in window_rows:
        rows_by_scene[row["source_id"]].append(row)

    tasks = [
        SceneTask(scene, sources[scene], tuple(rows_by_scene[scene]), args.levels, str(args.real_controls))
        for scene in scene_names
    ]
    print(
        json.dumps(
            {
                "policy_csv": str(args.policy_csv),
                "out_dir": str(args.out_dir),
                "window_rows": len(window_rows),
                "scene_count": len(tasks),
                "jobs": max(1, int(args.jobs)),
                "bins": centers,
                "tolerance": args.tolerance,
            },
            indent=2,
        ),
        flush=True,
    )

    metric_rows: list[dict[str, Any]] = []
    scene_summaries: list[dict[str, Any]] = []
    jobs = max(1, int(args.jobs))
    if jobs == 1:
        for task in tasks:
            scene, rows, summary = evaluate_scene_task(task)
            metric_rows.extend(rows)
            scene_summaries.append(summary)
            print(f"completed {scene}: {len(rows)} rows, {summary['unique_reconstruction_keys']} unique recon", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(evaluate_scene_task, task): task.source_id for task in tasks}
            for future in as_completed(futures):
                scene = futures[future]
                _scene, rows, summary = future.result()
                metric_rows.extend(rows)
                scene_summaries.append(summary)
                print(f"completed {scene}: {len(rows)} rows, {summary['unique_reconstruction_keys']} unique recon", flush=True)

    metric_rows.sort(key=lambda row: (str(row["codec"]), str(row["source_id"]), float(row["bin_center_actual_bpp"]), float(row["actual_bpp"])))
    selected, summary, regret = select_objective_rows(metric_rows)
    family_rows = summarize_policy_families(selected)

    write_csv(args.out_dir / "psnr_msssim_candidate_metrics.csv", metric_rows)
    write_csv(args.out_dir / "psnr_msssim_recommended_candidates.csv", selected)
    write_csv(args.out_dir / "psnr_msssim_recommendation_summary_by_bin.csv", summary)
    write_csv(args.out_dir / "psnr_msssim_cross_objective_regret.csv", regret)
    write_csv(args.out_dir / "psnr_msssim_policy_family_summary.csv", family_rows)
    write_csv(args.out_dir / "scene_runtime_summary.csv", scene_summaries)

    figures = {
        "psnr_scatter": str(
            make_metric_scatter(
                args.out_dir,
                metric_rows,
                selected,
                summary,
                "PSNR_raw_whole",
                "Whole RAW PSNR (dB)",
                "fig_psnr_msssim_optimized_on_psnr_cloud.png",
                args.max_background_points_per_codec,
                args.seed,
            )
        ),
        "msssim_scatter": str(
            make_metric_scatter(
                args.out_dir,
                metric_rows,
                selected,
                summary,
                "MS_SSIM_db_detail",
                "Detail MS-SSIM quality, -10log10(1-MS-SSIM)",
                "fig_psnr_msssim_optimized_on_msssim_cloud.png",
                args.max_background_points_per_codec,
                args.seed,
            )
        ),
        "regret_heatmap": str(make_regret_heatmap(args.out_dir, regret)),
        "family_frequency": str(make_family_chart(args.out_dir, family_rows, args.top_family_count)),
    }
    elapsed = time.perf_counter() - t0
    manifest = {
        "kind": "JPEG XS-style metric optimization over #824/#826 decoder-visible policy cloud",
        "boundary": (
            "This mirrors JPEG XS metric-aware gains/priorities optimization at the evaluation level, "
            "but searches the already enumerated decoder-visible RAW policy cloud.  It is not a production "
            "encoder and does not infer hidden camera encoder decisions."
        ),
        "rate_axis_rule": "actual syntax bpp only; bins are search windows, not target-bpp performance axes",
        "policy_csv": str(args.policy_csv),
        "out_dir": str(args.out_dir),
        "bins": centers,
        "tolerance": args.tolerance,
        "seed": args.seed,
        "width": args.width,
        "height": args.height,
        "levels": args.levels,
        "jobs": jobs,
        "window_rows": len(window_rows),
        "candidate_metric_rows": len(metric_rows),
        "recommended_rows": len(selected),
        "policy_family_rows": len(family_rows),
        "scene_runtime_summary": scene_summaries,
        "elapsed_seconds": elapsed,
        "figures": figures,
        "outputs": {
            "candidate_metrics": str(args.out_dir / "psnr_msssim_candidate_metrics.csv"),
            "recommended_candidates": str(args.out_dir / "psnr_msssim_recommended_candidates.csv"),
            "summary_by_bin": str(args.out_dir / "psnr_msssim_recommendation_summary_by_bin.csv"),
            "cross_objective_regret": str(args.out_dir / "psnr_msssim_cross_objective_regret.csv"),
            "policy_family_summary": str(args.out_dir / "psnr_msssim_policy_family_summary.csv"),
        },
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
