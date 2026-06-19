#!/usr/bin/env python3
"""Enumerate decoder-visible #824/#826 policy combinations into bpp-quality clouds.

This tool deliberately avoids target-bpp as a performance axis.  It enumerates
policy knobs directly and records actual syntax bpp plus RAW sample-domain
quality.  The continuous Sony base step is sampled on a log grid; every other
default knob family is discrete.

The result is still a decoder-visible model, not a production encoder search.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import os
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import numpy as np

import policy_sweep_bpp_multiplicity as sweep
import strict_824_826_math_eval as strict


SONY_COMPONENTS = ("Gbase", "Gphase_final", "Rres2", "Bres2")
NIKON_COMPONENTS = sweep.NIKON_COMPONENTS


@dataclass(frozen=True)
class SceneTask:
    source_id: str
    planes: dict[str, np.ndarray]
    levels: int
    sony_policies: tuple[tuple[str, dict[str, Any]], ...]
    sony_base_steps: tuple[float, ...]
    nikon_offsets: tuple[tuple[str, dict[str, int]], ...]
    nikon_bias_count: int
    include_sony: bool
    include_nikon: bool


def parse_csv_floats(raw: str) -> tuple[float, ...]:
    return tuple(float(part.strip()) for part in raw.split(",") if part.strip())


def parse_csv_ints(raw: str) -> tuple[int, ...]:
    return tuple(int(part.strip()) for part in raw.split(",") if part.strip())


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


def finite_float(value: Any) -> float:
    try:
        out = float(value)
    except Exception:
        return float("nan")
    return out


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
    return clean[lo] * (1.0 - frac) + clean[hi] * frac


def logspace(min_value: float, max_value: float, count: int) -> tuple[float, ...]:
    if count <= 1:
        return (float(min_value),)
    return tuple(float(x) for x in np.geomspace(float(min_value), float(max_value), int(count)))


def selector_map_name(mapping: dict[str, int]) -> str:
    return "sel_" + "".join(str(mapping[name]) for name in SONY_COMPONENTS)


def build_sony_component_maps(selector_values: tuple[int, ...], tie_rb: bool) -> list[tuple[str, dict[str, Any]]]:
    policies: list[tuple[str, dict[str, Any]]] = []
    if tie_rb:
        for gbase, gphase, rb in itertools.product(selector_values, repeat=3):
            mapping = {"Gbase": gbase, "Gphase_final": gphase, "Rres2": rb, "Bres2": rb}
            policies.append((selector_map_name(mapping), {"class": "component_map", "map": mapping}))
    else:
        for values in itertools.product(selector_values, repeat=len(SONY_COMPONENTS)):
            mapping = dict(zip(SONY_COMPONENTS, (int(v) for v in values)))
            policies.append((selector_map_name(mapping), {"class": "component_map", "map": mapping}))
    return policies


def build_sony_policies(mode: str, selector_values: tuple[int, ...], tie_rb: bool, real_controls: Path) -> list[tuple[str, dict[str, Any]]]:
    policies: list[tuple[str, dict[str, Any]]] = []
    if mode in {"current", "current-plus-maps"}:
        policies.extend((name, dict(policy)) for name, policy in sweep.SONY_POLICIES.items())
        real_cycle = sweep.read_sony_real_selector_cycle(real_controls)
        policies.append(
            (
                "real_hq_selector_cycle",
                {
                    "class": "row_cycle",
                    "cycle": list(real_cycle),
                    "description": "Selector cycle fitted from public Sony HQ raw.pixls samples.",
                },
            )
        )
    if mode in {"maps", "current-plus-maps"}:
        policies.extend(build_sony_component_maps(selector_values, tie_rb))

    unique: dict[str, dict[str, Any]] = {}
    for name, policy in policies:
        unique[name] = policy
    return sorted(unique.items(), key=lambda item: item[0])


def offset_name(offsets: dict[str, int]) -> str:
    return "off_" + "_".join(str(offsets[name]).replace("-", "m") for name in NIKON_COMPONENTS)


def build_nikon_offsets(mode: str, offset_values: tuple[int, ...]) -> list[tuple[str, dict[str, int]]]:
    offsets: list[tuple[str, dict[str, int]]] = []
    if mode in {"current", "current-plus-grid"}:
        offsets.extend((name, {k: int(v) for k, v in policy["offsets"].items()}) for name, policy in sweep.NIKON_POLICIES.items())
    if mode in {"grid", "current-plus-grid"}:
        for values in itertools.product(offset_values, repeat=len(NIKON_COMPONENTS)):
            mapping = dict(zip(NIKON_COMPONENTS, (int(v) for v in values)))
            offsets.append((offset_name(mapping), mapping))

    unique: dict[str, dict[str, int]] = {}
    for name, mapping in offsets:
        unique[name] = mapping
    return sorted(unique.items(), key=lambda item: item[0])


def row_metrics(source_flat: np.ndarray, recon: dict[str, np.ndarray]) -> dict[str, float]:
    metrics = strict.metric_summary(source_flat, strict.flatten_planes(recon))
    return {
        "MSE_whole": float(metrics["MSE"]),
        "MAE_whole": float(metrics["MAE"]),
        "MAX_whole": float(metrics["MAX"]),
        "PSNR_raw_whole": float(metrics["PSNR_raw"]),
    }


def make_row(
    codec: str,
    source_id: str,
    policy_name: str,
    policy_class: str,
    actual_bpp: float,
    knob: float,
    syntax: dict[str, float],
    metrics: dict[str, float],
    extra: dict[str, Any],
    encode_ms: float,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "codec": codec,
        "source_id": source_id,
        "policy_name": policy_name,
        "policy_class": policy_class,
        "actual_bpp": f"{actual_bpp:.9f}",
        "knob": f"{knob:.9f}",
        "encode_ms": f"{encode_ms:.3f}",
    }
    row.update(extra)
    for key in (
        "syntax_total_bits",
        "control_bits",
        "selector_bits",
        "width_update_bits",
        "zero_run_bits",
        "payload_bits",
        "header_bits",
        "sig_bits",
        "gcli_bits",
        "data_bits",
        "sign_bits",
        "groups_total",
        "nonzero_groups",
        "mean_selector",
        "mean_bp",
        "mean_br",
        "mean_gtli",
        "mean_effective_gtli",
    ):
        if key in syntax:
            row[key] = f"{float(syntax[key]):.9f}"
    groups_total = float(syntax.get("groups_total", 0.0))
    nonzero_groups = float(syntax.get("nonzero_groups", 0.0))
    row["nonzero_group_fraction"] = f"{nonzero_groups / max(1.0, groups_total):.9f}"
    for key, value in metrics.items():
        row[key] = f"{float(value):.9f}"
    return row


def evaluate_sony_cloud(task: SceneTask, source_flat: np.ndarray) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    comps = strict.sony_forward(task.planes)
    coeffs, sizes = strict.transform_components(comps, task.levels)
    pixel_count = int(next(iter(task.planes.values())).size * 4)
    for policy_name, policy in task.sony_policies:
        for base_step in task.sony_base_steps:
            start = time.perf_counter()
            deq, syntax = sweep.sony_syntax_encode_policy(coeffs, float(base_step), policy)
            recon = strict.sony_inverse(strict.inverse_transform_components(deq, sizes))
            actual_bpp = float(syntax["syntax_total_bits"]) / pixel_count
            rows.append(
                make_row(
                    strict.SONY_CODEC,
                    task.source_id,
                    policy_name,
                    str(policy["class"]),
                    actual_bpp,
                    float(base_step),
                    syntax,
                    row_metrics(source_flat, recon),
                    {
                        "base_step": f"{float(base_step):.9f}",
                        "selector_map": json.dumps(policy.get("map", {}), sort_keys=True),
                        "selector_cycle_len": len(policy.get("cycle", [])),
                    },
                    (time.perf_counter() - start) * 1000.0,
                )
            )
    return rows


def evaluate_nikon_cloud(task: SceneTask, source_flat: np.ndarray) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    comps = strict.nikon_forward(task.planes)
    coeffs, sizes = strict.transform_components(comps, task.levels)
    pixel_count = int(next(iter(task.planes.values())).size * 4)
    keys = list(strict.GTLI_ROWS.keys())
    encode_cache: dict[tuple[int, int, int, int], tuple[dict[str, float], dict[str, float], float]] = {}
    for offset_name_value, offsets in task.nikon_offsets:
        for global_bias in range(task.nikon_bias_count):
            for row_index, (bp, br) in enumerate(keys):
                start = time.perf_counter()
                knob = float(global_bias * len(keys) + row_index)
                gtli_row = strict.GTLI_ROWS[(bp, br)]
                effective_key = tuple(
                    max(0, int(gtli_row[strict.NIKON_COMPONENT_TO_SUBBAND[name]]) - int(global_bias) - int(offsets.get(name, 0)))
                    for name in NIKON_COMPONENTS
                )
                cached = encode_cache.get(effective_key)
                if cached is None:
                    deq, syntax = sweep.nikon_syntax_encode_policy(coeffs, bp, br, global_bias, offsets)
                    recon = strict.nikon_inverse(strict.inverse_transform_components(deq, sizes))
                    metrics = row_metrics(source_flat, recon)
                    actual_bpp = float(syntax["syntax_total_bits"]) / pixel_count
                    encode_cache[effective_key] = (syntax, metrics, actual_bpp)
                    cache_hit = 0
                else:
                    syntax, metrics, actual_bpp = cached
                    cache_hit = 1
                rows.append(
                    make_row(
                        strict.NIKON_CODEC,
                        task.source_id,
                        offset_name_value,
                        "component_gtli_bias_grid",
                        actual_bpp,
                        knob,
                        syntax,
                        metrics,
                        {
                            "bp": bp,
                            "br": br,
                            "global_bias": global_bias,
                            "component_offsets": json.dumps(offsets, sort_keys=True),
                            "effective_gtli_tuple": json.dumps(effective_key),
                            "recon_cache_hit": cache_hit,
                        },
                        (time.perf_counter() - start) * 1000.0,
                    )
                )
    return rows


def evaluate_scene_task(task: SceneTask) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    t0 = time.perf_counter()
    source_flat = strict.flatten_planes(task.planes)
    rows: list[dict[str, Any]] = []
    if task.include_sony:
        rows.extend(evaluate_sony_cloud(task, source_flat))
    if task.include_nikon:
        rows.extend(evaluate_nikon_cloud(task, source_flat))
    elapsed = time.perf_counter() - t0
    return (
        task.source_id,
        rows,
        {
            "source_id": task.source_id,
            "candidate_count": len(rows),
            "elapsed_seconds": elapsed,
            "rows_per_second": len(rows) / max(elapsed, 1e-9),
        },
    )


def pareto_psnr(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_rate: dict[float, dict[str, Any]] = {}
    for row in rows:
        rate = finite_float(row.get("actual_bpp"))
        psnr = finite_float(row.get("PSNR_raw_whole"))
        if not (math.isfinite(rate) and math.isfinite(psnr)):
            continue
        old = best_by_rate.get(rate)
        if old is None or psnr > finite_float(old["PSNR_raw_whole"]):
            best_by_rate[rate] = row
    frontier: list[dict[str, Any]] = []
    best_psnr = -float("inf")
    for row in sorted(best_by_rate.values(), key=lambda r: (finite_float(r["actual_bpp"]), -finite_float(r["PSNR_raw_whole"]))):
        psnr = finite_float(row["PSNR_raw_whole"])
        if psnr > best_psnr + 1e-10:
            frontier.append(row)
            best_psnr = psnr
    return frontier


def summarize(rows: list[dict[str, Any]], bin_step: float, bin_min: float, bin_max: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    overall: list[dict[str, Any]] = []
    for codec in sorted({str(row["codec"]) for row in rows}):
        group = [row for row in rows if row["codec"] == codec]
        rates = [finite_float(row["actual_bpp"]) for row in group]
        psnrs = [finite_float(row["PSNR_raw_whole"]) for row in group]
        finite_pairs = [(r, q) for r, q in zip(rates, psnrs) if math.isfinite(r) and math.isfinite(q)]
        frontier_counts: list[int] = []
        for scene in sorted({str(row["source_id"]) for row in group}):
            frontier_counts.append(len(pareto_psnr([row for row in group if row["source_id"] == scene])))
        overall.append(
            {
                "codec": codec,
                "candidate_count": len(group),
                "source_count": len({str(row["source_id"]) for row in group}),
                "policy_count": len({str(row["policy_name"]) for row in group}),
                "actual_bpp_min": min((r for r, _q in finite_pairs), default=float("nan")),
                "actual_bpp_p25": percentile([r for r, _q in finite_pairs], 0.25),
                "actual_bpp_median": percentile([r for r, _q in finite_pairs], 0.50),
                "actual_bpp_p75": percentile([r for r, _q in finite_pairs], 0.75),
                "actual_bpp_max": max((r for r, _q in finite_pairs), default=float("nan")),
                "psnr_min": min((q for _r, q in finite_pairs), default=float("nan")),
                "psnr_p25": percentile([q for _r, q in finite_pairs], 0.25),
                "psnr_median": percentile([q for _r, q in finite_pairs], 0.50),
                "psnr_p75": percentile([q for _r, q in finite_pairs], 0.75),
                "psnr_max": max((q for _r, q in finite_pairs), default=float("nan")),
                "median_scene_psnr_frontier_count": statistics.median(frontier_counts) if frontier_counts else 0,
            }
        )

    per_scene_bin: list[dict[str, Any]] = []
    by_scene_codec: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        by_scene_codec.setdefault((str(row["source_id"]), str(row["codec"])), []).append(row)
    centers = list(np.arange(bin_min, bin_max + bin_step * 0.5, bin_step))
    for (scene, codec), group in sorted(by_scene_codec.items()):
        for center in centers:
            lo = center - bin_step * 0.5
            hi = center + bin_step * 0.5
            inside = [row for row in group if lo <= finite_float(row["actual_bpp"]) < hi]
            if not inside:
                continue
            best = max(inside, key=lambda r: finite_float(r["PSNR_raw_whole"]))
            psnrs = [finite_float(row["PSNR_raw_whole"]) for row in inside]
            per_scene_bin.append(
                {
                    "source_id": scene,
                    "codec": codec,
                    "bin_center_actual_bpp": f"{center:.6f}",
                    "bin_width": f"{bin_step:.6f}",
                    "candidate_count": len(inside),
                    "psnr_min": min(psnrs),
                    "psnr_median": percentile(psnrs, 0.50),
                    "psnr_max": max(psnrs),
                    "best_policy_name": best["policy_name"],
                    "best_actual_bpp": best["actual_bpp"],
                    "best_psnr": best["PSNR_raw_whole"],
                }
            )

    envelope: list[dict[str, Any]] = []
    by_codec_bin: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in per_scene_bin:
        by_codec_bin.setdefault((str(row["codec"]), str(row["bin_center_actual_bpp"])), []).append(row)
    for (codec, center), group in sorted(by_codec_bin.items(), key=lambda item: (item[0][0], float(item[0][1]))):
        bests = [finite_float(row["best_psnr"]) for row in group]
        counts = [int(row["candidate_count"]) for row in group]
        envelope.append(
            {
                "codec": codec,
                "bin_center_actual_bpp": center,
                "covered_scene_count": len(group),
                "median_candidate_count_per_scene": statistics.median(counts),
                "best_psnr_p25": percentile(bests, 0.25),
                "best_psnr_median": percentile(bests, 0.50),
                "best_psnr_p75": percentile(bests, 0.75),
                "best_psnr_min": min(bests),
                "best_psnr_max": max(bests),
            }
        )
    return overall, per_scene_bin, envelope


def make_figures(out_dir: Path, rows: list[dict[str, Any]], envelope: list[dict[str, Any]], max_scatter_points: int, seed: int) -> dict[str, str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {
        strict.SONY_CODEC: "#2f6fb0",
        strict.NIKON_CODEC: "#bf6a22",
    }
    labels = {
        strict.SONY_CODEC: "#824 Sony",
        strict.NIKON_CODEC: "#826 Nikon",
    }
    rng = np.random.default_rng(seed)
    paths: dict[str, str] = {}

    fig, axes = plt.subplots(1, 2, figsize=(13.2, 5.0), dpi=160, sharey=True)
    for ax, codec in zip(axes, [strict.SONY_CODEC, strict.NIKON_CODEC]):
        group = [row for row in rows if row["codec"] == codec and math.isfinite(finite_float(row["actual_bpp"])) and math.isfinite(finite_float(row["PSNR_raw_whole"]))]
        if len(group) > max_scatter_points:
            idx = rng.choice(len(group), size=max_scatter_points, replace=False)
            group = [group[int(i)] for i in idx]
        ax.scatter(
            [finite_float(row["actual_bpp"]) for row in group],
            [finite_float(row["PSNR_raw_whole"]) for row in group],
            s=3,
            alpha=0.10,
            linewidths=0,
            color=colors.get(codec, "#444444"),
        )
        ax.set_title(f"{labels.get(codec, codec)} point cloud")
        ax.set_xlabel("Actual syntax bpp")
        ax.grid(True, alpha=0.25)
    axes[0].set_ylabel("Whole RAW PSNR (dB)")
    fig.tight_layout()
    path = out_dir / "fig_bpp_quality_point_cloud.png"
    fig.savefig(path)
    plt.close(fig)
    paths["point_cloud"] = str(path)

    fig, ax = plt.subplots(figsize=(9.5, 5.2), dpi=160)
    for codec in [strict.SONY_CODEC, strict.NIKON_CODEC]:
        group = [row for row in envelope if row["codec"] == codec]
        xs = [finite_float(row["bin_center_actual_bpp"]) for row in group]
        med = [finite_float(row["best_psnr_median"]) for row in group]
        lo = [finite_float(row["best_psnr_p25"]) for row in group]
        hi = [finite_float(row["best_psnr_p75"]) for row in group]
        if not xs:
            continue
        ax.plot(xs, med, marker="o", ms=3, lw=1.6, color=colors[codec], label=labels[codec])
        ax.fill_between(xs, lo, hi, color=colors[codec], alpha=0.16, linewidth=0)
    ax.set_xlabel("Actual syntax bpp bin center")
    ax.set_ylabel("Median per-scene best PSNR (dB)")
    ax.set_title("Point-cloud upper envelope by actual-bpp bins")
    ax.grid(True, alpha=0.25)
    ax.legend(frameon=False)
    fig.tight_layout()
    path = out_dir / "fig_bpp_quality_envelope.png"
    fig.savefig(path)
    plt.close(fig)
    paths["envelope"] = str(path)

    fig, axes = plt.subplots(1, 2, figsize=(13.2, 5.0), dpi=160, sharey=True)
    for ax, codec in zip(axes, [strict.SONY_CODEC, strict.NIKON_CODEC]):
        group = [row for row in rows if row["codec"] == codec]
        x = np.asarray([finite_float(row["actual_bpp"]) for row in group], dtype=np.float64)
        y = np.asarray([finite_float(row["PSNR_raw_whole"]) for row in group], dtype=np.float64)
        mask = np.isfinite(x) & np.isfinite(y)
        if np.any(mask):
            hb = ax.hexbin(x[mask], y[mask], gridsize=65, mincnt=1, bins="log", cmap="viridis")
            fig.colorbar(hb, ax=ax, label="log10(N)")
        ax.set_title(f"{labels.get(codec, codec)} density")
        ax.set_xlabel("Actual syntax bpp")
        ax.grid(True, alpha=0.20)
    axes[0].set_ylabel("Whole RAW PSNR (dB)")
    fig.tight_layout()
    path = out_dir / "fig_bpp_quality_hexbin.png"
    fig.savefig(path)
    plt.close(fig)
    paths["hexbin"] = str(path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("out/exhaustive_bpp_quality_cloud_20260605"))
    parser.add_argument("--width", type=int, default=256)
    parser.add_argument("--height", type=int, default=256)
    parser.add_argument("--levels", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260605)
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--scenes", default="", help="Comma-separated subset for smoke tests.")
    parser.add_argument("--codec", choices=("both", "sony", "nikon"), default="both")
    parser.add_argument("--sony-policy-mode", choices=("current", "maps", "current-plus-maps"), default="current-plus-maps")
    parser.add_argument("--sony-selector-values", default="0,1,2,3")
    parser.add_argument("--sony-tie-rb", action="store_true", help="Tie Rres2/Bres2 selector for a smaller 4^3 map grid.")
    parser.add_argument("--sony-base-step-min", type=float, default=0.25)
    parser.add_argument("--sony-base-step-max", type=float, default=4096.0)
    parser.add_argument("--sony-base-step-count", type=int, default=32)
    parser.add_argument("--real-controls", type=Path, default=Path("out/production_fit_samples/real_bitstream_controls.csv"))
    parser.add_argument("--nikon-offset-mode", choices=("current", "grid", "current-plus-grid"), default="current-plus-grid")
    parser.add_argument("--nikon-offset-values", default="-1,0,1")
    parser.add_argument("--nikon-bias-count", type=int, default=7)
    parser.add_argument("--bin-min", type=float, default=0.5)
    parser.add_argument("--bin-max", type=float, default=7.0)
    parser.add_argument("--bin-step", type=float, default=0.25)
    parser.add_argument("--max-scatter-points", type=int, default=180000)
    args = parser.parse_args()

    selector_values = tuple(int(v) for v in parse_csv_ints(args.sony_selector_values))
    sony_policies = tuple(build_sony_policies(args.sony_policy_mode, selector_values, args.sony_tie_rb, args.real_controls))
    sony_base_steps = logspace(args.sony_base_step_min, args.sony_base_step_max, args.sony_base_step_count)
    nikon_offsets = tuple(build_nikon_offsets(args.nikon_offset_mode, parse_csv_ints(args.nikon_offset_values)))

    scene_names = list(strict.SCENES)
    if args.scenes.strip():
        wanted = {item.strip() for item in args.scenes.split(",") if item.strip()}
        scene_names = [name for name in scene_names if name in wanted]
        missing = sorted(wanted - set(scene_names))
        if missing:
            raise SystemExit(f"unknown scenes: {missing}")

    rng = np.random.default_rng(args.seed)
    sources = {scene: strict.generate_scene(scene, args.height // 2, args.width // 2, rng) for scene in scene_names}
    include_sony = args.codec in {"both", "sony"}
    include_nikon = args.codec in {"both", "nikon"}
    tasks = [
        SceneTask(
            source_id=scene,
            planes=planes,
            levels=args.levels,
            sony_policies=sony_policies,
            sony_base_steps=sony_base_steps,
            nikon_offsets=nikon_offsets,
            nikon_bias_count=args.nikon_bias_count,
            include_sony=include_sony,
            include_nikon=include_nikon,
        )
        for scene, planes in sources.items()
    ]

    expected_sony = len(scene_names) * len(sony_policies) * len(sony_base_steps) if include_sony else 0
    expected_nikon = len(scene_names) * len(nikon_offsets) * args.nikon_bias_count * len(strict.GTLI_ROWS) if include_nikon else 0
    expected_total = expected_sony + expected_nikon
    print(
        json.dumps(
            {
                "out_dir": str(args.out_dir),
                "scenes": len(scene_names),
                "sony_policy_count": len(sony_policies),
                "sony_base_step_count": len(sony_base_steps),
                "nikon_offset_count": len(nikon_offsets),
                "nikon_bias_count": args.nikon_bias_count,
                "nikon_gtli_row_count": len(strict.GTLI_ROWS),
                "expected_sony_rows": expected_sony,
                "expected_nikon_rows": expected_nikon,
                "expected_total_rows": expected_total,
                "jobs": max(1, int(args.jobs)),
            },
            indent=2,
        ),
        flush=True,
    )

    t0 = time.perf_counter()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    jobs = max(1, int(args.jobs))
    rows: list[dict[str, Any]] = []
    scene_summaries: list[dict[str, Any]] = []
    if jobs == 1:
        for task in tasks:
            source_id, scene_rows, summary = evaluate_scene_task(task)
            rows.extend(scene_rows)
            scene_summaries.append(summary)
            print(f"completed {source_id}: {len(scene_rows)} candidates", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(evaluate_scene_task, task): task.source_id for task in tasks}
            for future in as_completed(futures):
                source_id = futures[future]
                _source_id, scene_rows, summary = future.result()
                rows.extend(scene_rows)
                scene_summaries.append(summary)
                print(f"completed {source_id}: {len(scene_rows)} candidates", flush=True)

    rows.sort(key=lambda row: (str(row["codec"]), str(row["source_id"]), finite_float(row["actual_bpp"]), str(row["policy_name"])))
    write_csv(args.out_dir / "point_cloud_candidates.csv", rows)
    write_csv(args.out_dir / "scene_runtime_summary.csv", scene_summaries)
    overall_summary, per_scene_bin, envelope = summarize(rows, args.bin_step, args.bin_min, args.bin_max)
    write_csv(args.out_dir / "point_cloud_summary.csv", overall_summary)
    write_csv(args.out_dir / "actual_bpp_bin_per_scene.csv", per_scene_bin)
    write_csv(args.out_dir / "actual_bpp_bin_envelope_summary.csv", envelope)
    figure_paths = make_figures(args.out_dir, rows, envelope, args.max_scatter_points, args.seed)

    elapsed = time.perf_counter() - t0
    manifest = {
        "kind": "exhaustive-ish bpp-quality cloud over decoder-visible #824/#826 policy knobs",
        "boundary": (
            "Sony base_step is continuous and is sampled on a log grid; row-cycle policy space is not fully enumerated. "
            "Nikon GTLI/Bp/Br/global-bias/component-offset combinations are enumerated within the configured offset grid. "
            "For speed, Nikon combinations with the same four-component effective GTLI tuple reuse the first computed "
            "reconstruction and syntax row, so this is an effective-reconstruction cloud rather than an exact per-header-bit "
            "enumeration of every syntactically redundant Bp/Br row. "
            "This does not claim production encoder optimality."
        ),
        "out_dir": str(args.out_dir),
        "seed": args.seed,
        "width": args.width,
        "height": args.height,
        "levels": args.levels,
        "jobs_requested": jobs,
        "cpu_count": os.cpu_count(),
        "elapsed_seconds": elapsed,
        "scene_count": len(scene_names),
        "scenes": scene_names,
        "candidate_rows": len(rows),
        "expected_candidate_rows": expected_total,
        "sony_policy_mode": args.sony_policy_mode,
        "sony_policy_count": len(sony_policies),
        "sony_selector_values": list(selector_values),
        "sony_tie_rb": bool(args.sony_tie_rb),
        "sony_base_step_min": args.sony_base_step_min,
        "sony_base_step_max": args.sony_base_step_max,
        "sony_base_step_count": len(sony_base_steps),
        "nikon_offset_mode": args.nikon_offset_mode,
        "nikon_offset_values": list(parse_csv_ints(args.nikon_offset_values)),
        "nikon_offset_count": len(nikon_offsets),
        "nikon_bias_count": args.nikon_bias_count,
        "nikon_gtli_row_count": len(strict.GTLI_ROWS),
        "rate_axis_rule": "No target_bpp column is emitted; actual syntax bpp is the only RD x-axis.",
        "outputs": {
            "point_cloud_candidates": str(args.out_dir / "point_cloud_candidates.csv"),
            "point_cloud_summary": str(args.out_dir / "point_cloud_summary.csv"),
            "actual_bpp_bin_per_scene": str(args.out_dir / "actual_bpp_bin_per_scene.csv"),
            "actual_bpp_bin_envelope_summary": str(args.out_dir / "actual_bpp_bin_envelope_summary.csv"),
            "scene_runtime_summary": str(args.out_dir / "scene_runtime_summary.csv"),
            "figures": figure_paths,
        },
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
