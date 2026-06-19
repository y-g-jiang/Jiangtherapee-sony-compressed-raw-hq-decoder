#!/usr/bin/env python3
"""Stage-separated mathematical insight evaluation for strict #824/#826.

The main strict evaluator reports sample-domain quality, syntax bpp and
BD-rate.  This companion script keeps the same decoder-visible canonical
encoders but separates:

* component/LUT projection and transform roundtrip;
* coefficient-domain quantization/dequantization;
* information/structure diagnostics that are useful for interpreting RD curves.

All rows remain canonical-simulation evidence only.  No row claims camera
production encoder equivalence.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import numpy as np

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import strict_824_826_math_eval as strict


SONY = strict.SONY_CODEC
NIKON = strict.NIKON_CODEC
EPS = 1e-12


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"no rows for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def flatten_named(arrays: dict[str, np.ndarray], names: Iterable[str]) -> np.ndarray:
    return np.concatenate([np.asarray(arrays[name], dtype=np.float64).ravel() for name in names])


def whole_metric_rows(
    codec: str,
    source_id: str,
    stage: str,
    src: dict[str, np.ndarray],
    rec: dict[str, np.ndarray],
    target_bpp: float | None = None,
    actual_bpp: float | None = None,
) -> list[dict[str, object]]:
    stats = strict.metric_summary(
        flatten_named(src, ("R", "G0", "G1", "B")),
        flatten_named(rec, ("R", "G0", "G1", "B")),
    )
    rows = []
    for metric, value in stats.items():
        rows.append(
            {
                "codec": codec,
                "source_id": source_id,
                "target_bpp": "" if target_bpp is None else f"{target_bpp:.6f}",
                "actual_bpp": "" if actual_bpp is None else f"{actual_bpp:.9f}",
                "stage": stage,
                "metric": metric,
                "value": f"{value:.9f}",
                "split": "whole",
                "higher_is_better": metric == "PSNR_raw",
            }
        )
    return rows


def final_ll_shape(sizes: list[tuple[int, int]]) -> tuple[int, int]:
    if not sizes:
        raise ValueError("empty transform sizes")
    h, w = sizes[-1]
    return (h + 1) // 2, (w + 1) // 2


def gini_abs(values: np.ndarray) -> float:
    x = np.sort(np.abs(values.astype(np.float64).ravel()))
    if x.size == 0:
        return 0.0
    total = float(np.sum(x))
    if total <= EPS:
        return 0.0
    idx = np.arange(1, x.size + 1, dtype=np.float64)
    return float((2.0 * np.sum(idx * x) / (x.size * total)) - (x.size + 1.0) / x.size)


def transform_compaction_metrics(
    coeffs: dict[str, np.ndarray],
    sizes: dict[str, list[tuple[int, int]]],
) -> dict[str, float]:
    total_energy = 0.0
    ll_energy = 0.0
    coeff_values: list[np.ndarray] = []
    for name, coeff in coeffs.items():
        arr = np.asarray(coeff, dtype=np.float64)
        ll_h, ll_w = final_ll_shape(sizes[name])
        total_energy += float(np.sum(arr * arr))
        ll = arr[:ll_h, :ll_w]
        ll_energy += float(np.sum(ll * ll))
        coeff_values.append(arr.ravel())
    detail_energy = max(total_energy - ll_energy, 0.0)
    all_coeffs = np.concatenate(coeff_values)
    ll_fraction = ll_energy / max(total_energy, EPS)
    return {
        "ll_energy_fraction": ll_fraction,
        "detail_energy_fraction": detail_energy / max(total_energy, EPS),
        "ll_to_detail_energy_db": 10.0 * math.log10((ll_energy + EPS) / (detail_energy + EPS)),
        "coefficient_abs_gini": gini_abs(all_coeffs),
        "mean_abs_coefficient": float(np.mean(np.abs(all_coeffs))),
    }


def coeff_quantization_metrics(
    coeffs: dict[str, np.ndarray],
    deq_coeffs: dict[str, np.ndarray],
    sizes: dict[str, list[tuple[int, int]]],
    syntax: dict[str, float],
) -> dict[str, float]:
    signal_energy = 0.0
    error_energy = 0.0
    hf_error_energy = 0.0
    low_abs_errors: list[np.ndarray] = []
    high_abs_errors: list[np.ndarray] = []
    all_coeff: list[np.ndarray] = []
    all_deq: list[np.ndarray] = []
    all_err: list[np.ndarray] = []

    for name, coeff in coeffs.items():
        src = np.asarray(coeff, dtype=np.float64)
        rec = np.asarray(deq_coeffs[name], dtype=np.float64)
        err = rec - src
        ll_h, ll_w = final_ll_shape(sizes[name])
        mask = np.zeros(src.shape, dtype=bool)
        mask[:ll_h, :ll_w] = True
        all_coeff.append(src.ravel())
        all_deq.append(rec.ravel())
        all_err.append(err.ravel())
        signal_energy += float(np.sum(src * src))
        e2 = err * err
        error_energy += float(np.sum(e2))
        hf_error_energy += float(np.sum(e2[~mask]))
        low_abs_errors.append(np.abs(err[mask]).ravel())
        high_abs_errors.append(np.abs(err[~mask]).ravel())

    coeff_flat = np.concatenate(all_coeff)
    deq_flat = np.concatenate(all_deq)
    err_flat = np.concatenate(all_err)
    mse = float(np.mean(err_flat * err_flat))
    mae = float(np.mean(np.abs(err_flat)))
    max_abs = float(np.max(np.abs(err_flat))) if err_flat.size else 0.0
    signal_mean_energy = float(np.mean(coeff_flat * coeff_flat))
    snr = float("inf") if mse <= 0 else 10.0 * math.log10((signal_mean_energy + EPS) / mse)
    groups = float(syntax.get("groups_total", 0.0))
    nonzero = float(syntax.get("nonzero_groups", 0.0))
    return {
        "coeff_MSE": mse,
        "coeff_MAE": mae,
        "coeff_MAX": max_abs,
        "coeff_SNR_db": snr,
        "quant_error_energy_ratio": error_energy / max(signal_energy, EPS),
        "quant_error_hf_fraction": hf_error_energy / max(error_energy, EPS),
        "quant_lowpass_MAE": float(np.mean(np.concatenate(low_abs_errors))) if low_abs_errors else 0.0,
        "quant_detail_MAE": float(np.mean(np.concatenate(high_abs_errors))) if high_abs_errors else 0.0,
        "dequant_zero_fraction": float(np.mean(deq_flat == 0.0)),
        "nonzero_group_fraction": nonzero / max(groups, 1.0),
    }


def corrcoef_safe(a: np.ndarray, b: np.ndarray) -> float:
    x = np.asarray(a, dtype=np.float64).ravel()
    y = np.asarray(b, dtype=np.float64).ravel()
    if x.size == 0 or y.size == 0:
        return 0.0
    x = x - float(np.mean(x))
    y = y - float(np.mean(y))
    denom = math.sqrt(float(np.sum(x * x)) * float(np.sum(y * y)))
    if denom <= EPS:
        return 0.0
    return float(np.sum(x * y) / denom)


def vifp_like_index(src: np.ndarray, rec: np.ndarray) -> float:
    """Transparent pixel-domain VIF-style information fidelity proxy.

    This follows the Sheikh-Bovik Gaussian-channel insight, but is intentionally
    labeled as a RAW-plane proxy rather than an exact MATLAB VIF reproduction.
    Inputs are normalized to the RAW black/white range before the multi-scale
    information ratio is computed.
    """
    x = np.clip((src.astype(np.float64) - strict.BLACK) / strict.RANGE, 0.0, 1.0)
    y = np.clip((rec.astype(np.float64) - strict.BLACK) / strict.RANGE, 0.0, 1.0)
    while max(x.shape) > 64:
        x = strict.downsample2(x)
        y = strict.downsample2(y)
    kernel = strict.gaussian_kernel1d(size=5, sigma=1.0)
    sigma_nsq = 1e-4
    numerator = 0.0
    denominator = 0.0
    for scale in range(4):
        ux = strict.separable_filter(x, kernel)
        uy = strict.separable_filter(y, kernel)
        vx = np.maximum(strict.separable_filter(x * x, kernel) - ux * ux, 0.0)
        vy = np.maximum(strict.separable_filter(y * y, kernel) - uy * uy, 0.0)
        vxy = strict.separable_filter(x * y, kernel) - ux * uy
        gain = vxy / (vx + EPS)
        noise_var = vy - gain * vxy
        bad = gain < 0.0
        gain = np.where(bad, 0.0, gain)
        noise_var = np.where(bad, vy, noise_var)
        noise_var = np.maximum(noise_var, EPS)
        numerator += float(np.sum(np.log2(1.0 + (gain * gain * vx) / (noise_var + sigma_nsq))))
        denominator += float(np.sum(np.log2(1.0 + vx / sigma_nsq)))
        if scale != 3:
            x = strict.downsample2(x)
            y = strict.downsample2(y)
    if denominator <= EPS:
        return 1.0 if np.allclose(src, rec) else 0.0
    return max(0.0, float(numerator / denominator))


def error_hf_energy_fraction(err: np.ndarray) -> float:
    arr = np.asarray(err, dtype=np.float64)
    if not np.any(arr):
        return 0.0
    spec = np.fft.rfft2(arr)
    energy = np.abs(spec) ** 2
    fy = np.fft.fftfreq(arr.shape[0])[:, None]
    fx = np.fft.rfftfreq(arr.shape[1])[None, :]
    radius = np.sqrt(fx * fx + fy * fy)
    high = radius >= 0.25
    return float(np.sum(energy[high]) / max(np.sum(energy), EPS))


def residual_neighbor_corr_abs(err: np.ndarray) -> float:
    arr = np.asarray(err, dtype=np.float64)
    vals = []
    if arr.shape[1] > 1:
        vals.append(abs(corrcoef_safe(arr[:, :-1], arr[:, 1:])))
    if arr.shape[0] > 1:
        vals.append(abs(corrcoef_safe(arr[:-1, :], arr[1:, :])))
    return float(statistics.mean(vals)) if vals else 0.0


def insight_metric_values(source: dict[str, np.ndarray], recon: dict[str, np.ndarray]) -> dict[str, float]:
    vif_scores = []
    residual_corr = []
    hf_frac = []
    edge_corr = []
    plane_mae = []
    for plane in ("R", "G0", "G1", "B"):
        src = source[plane]
        rec = recon[plane]
        err = rec - src
        vif_scores.append(vifp_like_index(src, rec))
        residual_corr.append(residual_neighbor_corr_abs(err))
        hf_frac.append(error_hf_energy_fraction(err))
        edge_corr.append(corrcoef_safe(np.abs(err), strict.gradient_magnitude(src)))
        plane_mae.append(strict.metric_summary(src.ravel(), rec.ravel())["MAE"])
    return {
        "vifp_mean": float(statistics.mean(vif_scores)),
        "residual_neighbor_corr_abs": float(statistics.mean(residual_corr)),
        "error_hf_energy_fraction": float(statistics.mean(hf_frac)),
        "edge_error_correlation": float(statistics.mean(edge_corr)),
        "phase_MAE_std": float(statistics.pstdev(plane_mae)),
        "phase_MAE_mean": float(statistics.mean(plane_mae)),
    }


def select_syntax(
    codec: strict.Codec,
    coeffs: dict[str, np.ndarray],
    target_bpp: float,
    pixel_count: int,
) -> tuple[float, dict[str, np.ndarray], dict[str, float]]:
    if codec.name == SONY:
        lo, hi = 0.25, 4096.0
        for _ in range(24):
            mid = math.sqrt(lo * hi)
            _deq, syntax = codec.syntax_encode(coeffs, mid)
            rate = syntax["syntax_total_bits"] / pixel_count
            if rate > target_bpp:
                lo = mid
            else:
                hi = mid
        knob = hi
        deq, syntax = codec.syntax_encode(coeffs, knob)
        return knob, deq, syntax

    best = None
    for idx in range(len(strict.GTLI_ROWS) * 6):
        knob = float(idx)
        deq, syntax = codec.syntax_encode(coeffs, knob)
        rate = syntax["syntax_total_bits"] / pixel_count
        score = abs(rate - target_bpp)
        if best is None or score < best[0]:
            best = (score, knob, deq, syntax)
    if best is None:
        raise RuntimeError("no Nikon syntax candidate generated")
    _score, knob, deq, syntax = best
    return knob, deq, syntax


def select_all_syntax(
    codec: strict.Codec,
    coeffs: dict[str, np.ndarray],
    targets: list[float],
    pixel_count: int,
) -> dict[float, tuple[float, dict[str, np.ndarray], dict[str, float]]]:
    if codec.name == SONY:
        return {target: select_syntax(codec, coeffs, target, pixel_count) for target in targets}

    best: dict[float, tuple[float, float, dict[str, np.ndarray], dict[str, float]]] = {}
    for idx in range(len(strict.GTLI_ROWS) * 6):
        knob = float(idx)
        deq, syntax = codec.syntax_encode(coeffs, knob)
        rate = syntax["syntax_total_bits"] / pixel_count
        for target in targets:
            score = abs(rate - target)
            if target not in best or score < best[target][0]:
                best[target] = (score, knob, deq, syntax)
    return {target: (knob, deq, syntax) for target, (_score, knob, deq, syntax) in best.items()}


def add_long_rows(
    rows: list[dict[str, object]],
    values: dict[str, float],
    codec: str,
    source_id: str,
    stage: str,
    split: str,
    target_bpp: float | None = None,
    actual_bpp: float | None = None,
    higher_is_better: dict[str, bool] | None = None,
) -> None:
    higher_is_better = higher_is_better or {}
    for metric, value in values.items():
        rows.append(
            {
                "codec": codec,
                "source_id": source_id,
                "target_bpp": "" if target_bpp is None else f"{target_bpp:.6f}",
                "actual_bpp": "" if actual_bpp is None else f"{actual_bpp:.9f}",
                "stage": stage,
                "split": split,
                "metric": metric,
                "value": f"{value:.9f}",
                "higher_is_better": bool(higher_is_better.get(metric, False)),
            }
        )


def evaluate_source_task(
    source_id: str,
    planes: dict[str, np.ndarray],
    targets: list[float],
    levels: int,
) -> tuple[str, list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    stage_rows: list[dict[str, object]] = []
    insight_rows: list[dict[str, object]] = []
    encode_rows: list[dict[str, object]] = []

    pixel_count = int(next(iter(planes.values())).size * 4)
    for codec in strict.CODECS.values():
        comps = codec.forward(planes)
        projection = codec.inverse(comps)
        stage_rows.extend(whole_metric_rows(codec.name, source_id, "component_lut_projection", planes, projection))

        coeffs, sizes = strict.transform_components(comps, levels)
        transform_roundtrip = codec.inverse(strict.inverse_transform_components(coeffs, sizes))
        stage_rows.extend(whole_metric_rows(codec.name, source_id, "transform_roundtrip", planes, transform_roundtrip))
        add_long_rows(
            stage_rows,
            transform_compaction_metrics(coeffs, sizes),
            codec.name,
            source_id,
            "transform_compaction",
            "coefficients",
            higher_is_better={
                "ll_energy_fraction": True,
                "ll_to_detail_energy_db": True,
                "coefficient_abs_gini": True,
            },
        )

        selected_by_target = select_all_syntax(codec, coeffs, targets, pixel_count)
        for target in targets:
            knob, deq_coeffs, syntax = selected_by_target[target]
            actual_bpp = syntax["syntax_total_bits"] / pixel_count
            recon = codec.inverse(strict.inverse_transform_components(deq_coeffs, sizes))
            encode_rows.append(
                {
                    "codec": codec.name,
                    "source_id": source_id,
                    "target_bpp": f"{target:.6f}",
                    "actual_bpp": f"{actual_bpp:.9f}",
                    "knob_name": codec.knob_name,
                    "knob": f"{knob:.9f}",
                }
            )
            add_long_rows(
                stage_rows,
                coeff_quantization_metrics(coeffs, deq_coeffs, sizes, syntax),
                codec.name,
                source_id,
                "quantization_dequantization",
                "coefficients",
                target,
                actual_bpp,
                higher_is_better={"coeff_SNR_db": True},
            )

            sample_stats = strict.metric_summary(
                strict.flatten_planes(planes),
                strict.flatten_planes(recon),
            )
            detail_stats = strict.detail_metric_summary(planes, recon)
            insight_values = insight_metric_values(planes, recon)
            combined = {
                "MSE": sample_stats["MSE"],
                "PSNR_raw": sample_stats["PSNR_raw"],
                "ssim_mean": detail_stats["ssim_mean"],
                "ms_ssim_mean": detail_stats["ms_ssim_mean"],
                "gmsd_mean": detail_stats["gmsd_mean"],
                **insight_values,
            }
            add_long_rows(
                insight_rows,
                combined,
                codec.name,
                source_id,
                "final_reconstruction",
                "information",
                target,
                actual_bpp,
                higher_is_better={
                    "PSNR_raw": True,
                    "ssim_mean": True,
                    "ms_ssim_mean": True,
                    "vifp_mean": True,
                },
            )

    return source_id, stage_rows, insight_rows, encode_rows


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def median_or_blank(values: list[float]) -> str:
    return "" if not values else f"{statistics.median(values):.9f}"


def summarize_long_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str, str], dict[str, list[float]]] = {}
    higher: dict[tuple[str, str, str, str], bool] = {}
    for row in rows:
        key = (
            str(row.get("stage", "")),
            str(row.get("split", "")),
            str(row.get("target_bpp", "")),
            str(row["metric"]),
        )
        grouped.setdefault(key, {}).setdefault(str(row["codec"]), []).append(float(row["value"]))
        higher[key] = parse_bool(row.get("higher_is_better", False))

    out: list[dict[str, object]] = []
    for key, by_codec in sorted(grouped.items()):
        stage, split, target, metric = key
        sony_vals = by_codec.get(SONY, [])
        nikon_vals = by_codec.get(NIKON, [])
        med_s = statistics.median(sony_vals) if sony_vals else math.nan
        med_n = statistics.median(nikon_vals) if nikon_vals else math.nan
        diff = med_s - med_n if sony_vals and nikon_vals else math.nan
        hib = higher.get(key, False)
        if not sony_vals or not nikon_vals:
            winner = ""
        elif abs(diff) <= 1e-12:
            winner = "tie"
        elif hib:
            winner = "Sony" if diff > 0 else "Nikon"
        else:
            winner = "Sony" if diff < 0 else "Nikon"
        out.append(
            {
                "stage": stage,
                "split": split,
                "target_bpp": target,
                "metric": metric,
                "higher_is_better": hib,
                "median_sony": "" if math.isnan(med_s) else f"{med_s:.9f}",
                "median_nikon": "" if math.isnan(med_n) else f"{med_n:.9f}",
                "median_sony_minus_nikon": "" if math.isnan(diff) else f"{diff:.9f}",
                "winner_by_median": winner,
                "n_sony": len(sony_vals),
                "n_nikon": len(nikon_vals),
            }
        )
    return out


def write_rd_slope_summary(out_dir: Path, insight_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    wanted = {"MSE", "PSNR_raw", "ssim_mean", "vifp_mean", "gmsd_mean"}
    points: dict[tuple[str, str], dict[float, dict[str, float]]] = {}
    for row in insight_rows:
        metric = str(row["metric"])
        if metric not in wanted:
            continue
        key = (str(row["codec"]), str(row["source_id"]))
        target = float(row["target_bpp"])
        points.setdefault(key, {}).setdefault(target, {"actual_bpp": float(row["actual_bpp"])})
        points[key][target][metric] = float(row["value"])

    slope_rows: list[dict[str, object]] = []
    for (codec, source_id), by_target in sorted(points.items()):
        ordered = sorted(by_target.values(), key=lambda item: item["actual_bpp"])
        for left, right in zip(ordered, ordered[1:]):
            dr = right["actual_bpp"] - left["actual_bpp"]
            if dr <= EPS:
                continue
            row = {
                "codec": codec,
                "source_id": source_id,
                "rate_left": f"{left['actual_bpp']:.9f}",
                "rate_right": f"{right['actual_bpp']:.9f}",
                "lambda_mse_drop_per_bpp": f"{((left['MSE'] - right['MSE']) / dr):.9f}",
                "lambda_psnr_gain_per_bpp": f"{((right['PSNR_raw'] - left['PSNR_raw']) / dr):.9f}",
                "lambda_ssim_gain_per_bpp": f"{((right['ssim_mean'] - left['ssim_mean']) / dr):.9f}",
                "lambda_vifp_gain_per_bpp": f"{((right['vifp_mean'] - left['vifp_mean']) / dr):.9f}",
                "lambda_gmsd_drop_per_bpp": f"{((left['gmsd_mean'] - right['gmsd_mean']) / dr):.9f}",
                "mse_nonmonotone": right["MSE"] > left["MSE"] + 1e-9,
                "vifp_nonmonotone": right["vifp_mean"] < left["vifp_mean"] - 1e-9,
            }
            slope_rows.append(row)
    write_csv(out_dir / "rd_slope_segments.csv", slope_rows)

    summary_rows: list[dict[str, object]] = []
    for codec in sorted({str(row["codec"]) for row in slope_rows}):
        rows = [row for row in slope_rows if row["codec"] == codec]
        for metric in [
            "lambda_mse_drop_per_bpp",
            "lambda_psnr_gain_per_bpp",
            "lambda_ssim_gain_per_bpp",
            "lambda_vifp_gain_per_bpp",
            "lambda_gmsd_drop_per_bpp",
        ]:
            vals = [float(row[metric]) for row in rows]
            summary_rows.append(
                {
                    "codec": codec,
                    "metric": metric,
                    "median": median_or_blank(vals),
                    "p025": median_or_blank(sorted(vals)[: max(1, len(vals) // 40)]) if vals else "",
                    "p975": median_or_blank(sorted(vals)[-(max(1, len(vals) // 40)) :]) if vals else "",
                    "n": len(vals),
                }
            )
        summary_rows.append(
            {
                "codec": codec,
                "metric": "mse_nonmonotone_segments",
                "median": sum(parse_bool(row["mse_nonmonotone"]) for row in rows),
                "p025": "",
                "p975": "",
                "n": len(rows),
            }
        )
        summary_rows.append(
            {
                "codec": codec,
                "metric": "vifp_nonmonotone_segments",
                "median": sum(parse_bool(row["vifp_nonmonotone"]) for row in rows),
                "p025": "",
                "p975": "",
                "n": len(rows),
            }
        )
    write_csv(out_dir / "rd_slope_summary.csv", summary_rows)
    return summary_rows


def write_combined_big_comparison(
    out_dir: Path,
    stage_summary: list[dict[str, object]],
    insight_summary: list[dict[str, object]],
    rd_summary: list[dict[str, object]],
) -> None:
    rows: list[dict[str, object]] = []
    selected_stage = {
        "transform_compaction": {"ll_energy_fraction", "ll_to_detail_energy_db", "coefficient_abs_gini"},
        "quantization_dequantization": {"coeff_SNR_db", "coeff_MAE", "quant_error_hf_fraction", "dequant_zero_fraction", "nonzero_group_fraction"},
        "transform_roundtrip": {"MAX", "MAE"},
    }
    selected_insight = {
        "final_reconstruction": {"vifp_mean", "residual_neighbor_corr_abs", "error_hf_energy_fraction", "edge_error_correlation", "phase_MAE_std"},
    }
    for row in stage_summary:
        stage = str(row["stage"])
        if str(row["metric"]) not in selected_stage.get(stage, set()):
            continue
        rows.append({"category": "stage_separation", **row})
    for row in insight_summary:
        stage = str(row["stage"])
        if str(row["metric"]) not in selected_insight.get(stage, set()):
            continue
        rows.append({"category": "mathematical_insight", **row})
    for row in rd_summary:
        rows.append(
            {
                "category": "rd_local_slope",
                "stage": "rd_curve",
                "split": "whole",
                "target_bpp": "adjacent_actual_rate",
                "metric": row["metric"],
                "higher_is_better": True,
                "median_sony": row["median"] if row["codec"] == SONY else "",
                "median_nikon": row["median"] if row["codec"] == NIKON else "",
                "median_sony_minus_nikon": "",
                "winner_by_median": "",
                "n_sony": row["n"] if row["codec"] == SONY else "",
                "n_nikon": row["n"] if row["codec"] == NIKON else "",
                "codec": row["codec"],
            }
        )
    write_csv(out_dir / "combined_big_comparison.csv", rows)


def self_checks() -> list[dict[str, object]]:
    x = np.linspace(strict.BLACK, strict.WHITE, 64 * 64, dtype=np.float64).reshape(64, 64)
    y = x.copy()
    noisy = np.clip(x + 40.0 * np.sin(np.arange(64)[None, :] / 3.0), strict.BLACK, strict.WHITE)
    coeff = {"c": np.arange(64, dtype=np.float64).reshape(8, 8)}
    sizes = {"c": [(8, 8), (4, 4), (2, 2)]}
    syntax = {"groups_total": 16.0, "nonzero_groups": 16.0}
    zero_q = coeff_quantization_metrics(coeff, {"c": coeff["c"].copy()}, sizes, syntax)
    vif_identity = vifp_like_index(x, y)
    vif_noisy = vifp_like_index(x, noisy)
    hf_identity = error_hf_energy_fraction(y - x)
    return [
        {"check": "vifp_identity_is_one", "value": vif_identity, "expected": 1.0, "passed": abs(vif_identity - 1.0) < 1e-7},
        {"check": "vifp_noise_below_identity", "value": vif_noisy, "expected": "<1", "passed": vif_noisy < 1.0},
        {"check": "hf_error_identity_zero", "value": hf_identity, "expected": 0.0, "passed": hf_identity == 0.0},
        {"check": "coeff_zero_quant_error", "value": zero_q["coeff_MSE"], "expected": 0.0, "passed": zero_q["coeff_MSE"] == 0.0},
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=Path("out/strict_824_826_math_insight_20260603"))
    ap.add_argument("--width", type=int, default=256)
    ap.add_argument("--height", type=int, default=256)
    ap.add_argument("--levels", type=int, default=3)
    ap.add_argument("--targets", default="1.5,2.0,2.5,3.0,4.0,5.0")
    ap.add_argument("--seed", type=int, default=20260603)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1), help="parallel scene workers")
    ns = ap.parse_args()

    if ns.width % 2 or ns.height % 2:
        raise ValueError("width and height must be even")
    targets = [float(x) for x in ns.targets.split(",") if x.strip()]
    rng = np.random.default_rng(ns.seed)
    sources = {scene: strict.generate_scene(scene, ns.height // 2, ns.width // 2, rng) for scene in strict.SCENES}
    ns.out_dir.mkdir(parents=True, exist_ok=True)
    jobs = max(1, min(ns.jobs, len(sources), 61 if os.name == "nt" else len(sources)))

    stage_rows: list[dict[str, object]] = []
    insight_rows: list[dict[str, object]] = []
    encode_rows: list[dict[str, object]] = []

    if jobs <= 1:
        results = [evaluate_source_task(source_id, planes, targets, ns.levels) for source_id, planes in sources.items()]
    else:
        results_by_source: dict[str, tuple[str, list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]] = {}
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = {
                pool.submit(evaluate_source_task, source_id, planes, targets, ns.levels): source_id
                for source_id, planes in sources.items()
            }
            for future in as_completed(futures):
                result = future.result()
                results_by_source[result[0]] = result
                print(f"finished insight {result[0]}", flush=True)
        results = [results_by_source[source_id] for source_id in sources]

    for _source_id, stage, insight, encodes in results:
        stage_rows.extend(stage)
        insight_rows.extend(insight)
        encode_rows.extend(encodes)

    stage_summary = summarize_long_rows(stage_rows)
    insight_summary = summarize_long_rows(insight_rows)
    rd_summary = write_rd_slope_summary(ns.out_dir, insight_rows)
    write_combined_big_comparison(ns.out_dir, stage_summary, insight_summary, rd_summary)

    write_csv(ns.out_dir / "stage_metrics.csv", stage_rows)
    write_csv(ns.out_dir / "stage_summary.csv", stage_summary)
    write_csv(ns.out_dir / "insight_metrics.csv", insight_rows)
    write_csv(ns.out_dir / "insight_target_summary.csv", insight_summary)
    write_csv(ns.out_dir / "insight_encodes.csv", encode_rows)

    checks = self_checks()
    manifest = {
        "kind": "strict #824/#826 mathematical insight and stage-separated evaluation",
        "seed": ns.seed,
        "width": ns.width,
        "height": ns.height,
        "levels": ns.levels,
        "targets_bpp": targets,
        "source_count": len(sources),
        "codec_names": [NIKON, SONY],
        "decoder_visible_only": True,
        "old_proxy_outputs_used": False,
        "jobs": jobs,
        "stage_policy": "component/LUT projection, transform roundtrip, and coefficient quantization/dequantization are reported as separate evidence layers",
        "insight_policy": "VIF-style information fidelity, residual structure, high-frequency error energy, phase imbalance and local RD slopes are interpretive diagnostics, not production encoder equivalence evidence",
        "literature_sources": {
            "rd_bd_rate": "Bjontegaard VCEG-M33 and later BD-rate methodology discussions",
            "rdo_slope": "Lagrangian RD optimization literature; local finite-difference slopes reported rather than private camera lambda claims",
            "vif": "Sheikh and Bovik information fidelity/VIF; implemented here as transparent RAW-plane VIF-style proxy",
            "ssim_family": "Wang et al. SSIM and MS-SSIM",
            "gmsd": "Xue et al. GMSD",
            "rdp": "Blau and Michaeli rate-distortion-perception tradeoff; used as interpretation boundary",
        },
        "self_checks": checks,
        "all_self_checks_passed": all(bool(check["passed"]) for check in checks),
        "row_counts": {
            "stage_metrics": len(stage_rows),
            "stage_summary": len(stage_summary),
            "insight_metrics": len(insight_rows),
            "insight_target_summary": len(insight_summary),
            "insight_encodes": len(encode_rows),
            "rd_slope_segments": len(list(csv.DictReader((ns.out_dir / "rd_slope_segments.csv").open(newline="", encoding="utf-8")))),
            "rd_slope_summary": len(rd_summary),
        },
    }
    (ns.out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
