#!/usr/bin/env python3
"""Generate strict #824/#826 figures for the LaTeX report."""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import strict_824_826_math_eval as strict


SONY = strict.SONY_CODEC
NIKON = strict.NIKON_CODEC


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def f(row: dict[str, str], key: str) -> float:
    return float(row[key])


def set_style() -> None:
    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.labelsize": 9,
            "legend.fontsize": 8,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "figure.dpi": 160,
            "savefig.dpi": 220,
        }
    )


def save(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def target_metric_rows(math_dir: Path, metric: str) -> list[dict[str, str]]:
    return [r for r in read_csv(math_dir / "target_request_summary.csv") if r["metric"] == metric]


def plot_structure(fig_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.4, 3.7))
    ax.axis("off")
    boxes = [
        (0.06, 0.64, "Same synthetic\nRGGB corpus"),
        (0.30, 0.82, "Sony #824\nLUT code domain"),
        (0.54, 0.82, "LLVC3 final-green\nR/B residual math"),
        (0.80, 0.82, "packet selectors\nwidth / zero-run / sign"),
        (0.30, 0.34, "Nikon #826\nIQX/IQP sample14"),
        (0.54, 0.34, "step1/step2\nCDF 5/3 coefficients"),
        (0.80, 0.34, "GTLI/GCLI\nbit-plane/sign syntax"),
    ]
    for x, y, text in boxes:
        ax.text(
            x,
            y,
            text,
            ha="center",
            va="center",
            bbox=dict(boxstyle="round,pad=0.35", fc="#f8f8f1", ec="#333333", lw=0.8),
        )
    for start, end in [
        ((0.14, 0.66), (0.23, 0.82)),
        ((0.39, 0.82), (0.47, 0.82)),
        ((0.65, 0.82), (0.73, 0.82)),
        ((0.14, 0.60), (0.23, 0.34)),
        ((0.39, 0.34), (0.47, 0.34)),
        ((0.65, 0.34), (0.73, 0.34)),
    ]:
        ax.annotate("", xy=end, xytext=start, arrowprops=dict(arrowstyle="->", lw=1.0))
    ax.text(
        0.50,
        0.08,
        "Evaluation is decoder-visible canonical simulation; private camera RD policy is not claimed.",
        ha="center",
        va="center",
    )
    save(fig, fig_dir / "fig_strict_structure.png")


def plot_same_target(math_dir: Path, fig_dir: Path) -> None:
    return
    psnr_rows = target_metric_rows(math_dir, "PSNR_raw")
    mae_rows = target_metric_rows(math_dir, "MAE")
    grad_rows = target_metric_rows(math_dir, "grad_psnr")
    ssim_rows = target_metric_rows(math_dir, "ssim_mean")
    targets = [f(r, "target_bpp") for r in psnr_rows]
    x = np.arange(len(targets))

    rates = read_csv(math_dir / "rate_summary.csv")
    bpp_diff = []
    for target in targets:
        sony = next(r for r in rates if r["codec"] == SONY and abs(f(r, "target_bpp") - target) < 1e-9)
        nikon = next(r for r in rates if r["codec"] == NIKON and abs(f(r, "target_bpp") - target) < 1e-9)
        bpp_diff.append(f(sony, "actual_bpp_median") - f(nikon, "actual_bpp_median"))

    fig, axes = plt.subplots(2, 1, figsize=(7.3, 5.0), sharex=True)
    axes[0].axhline(0, color="#444444", lw=0.8)
    axes[0].plot(x, [f(r, "median_sony_minus_nikon") for r in psnr_rows], marker="o", label="PSNR diff")
    axes[0].plot(x, [f(r, "median_sony_minus_nikon") for r in grad_rows], marker="s", label="grad-PSNR diff")
    axes[0].set_ylabel("dB, Sony - Nikon")
    axes[0].legend(loc="best")
    axes[0].set_title("Same requested target: quality deltas")

    axes[1].axhline(0, color="#444444", lw=0.8)
    axes[1].bar(x - 0.18, [f(r, "median_sony_minus_nikon") for r in mae_rows], width=0.35, label="MAE diff")
    axes[1].plot(x + 0.18, [f(r, "median_sony_minus_nikon") * 1000.0 for r in ssim_rows], marker="o", color="#386641", label="SSIM diff x1000")
    axes[1].plot(x, bpp_diff, marker="^", color="#7a3f98", label="actual bpp diff")
    axes[1].set_xticks(x, [f"{t:g}" for t in targets])
    axes[1].set_xlabel("requested target bpp")
    axes[1].set_ylabel("DN / bpp / scaled SSIM")
    axes[1].legend(loc="best")
    save(fig, fig_dir / "fig_strict_same_target_summary.png")


def plot_metric_matrix(math_dir: Path, fig_dir: Path) -> None:
    return
    metrics = [
        ("PSNR_raw", "PSNR dB"),
        ("grad_psnr", "grad dB"),
        ("MAE", "MAE DN"),
        ("ssim_mean", "SSIM x1e4"),
        ("ms_ssim_mean", "MS-SSIM x1e4"),
        ("gmsd_mean", "GMSD x1e4"),
    ]
    target_rows = read_csv(math_dir / "target_request_summary.csv")
    targets = sorted({f(r, "target_bpp") for r in target_rows})
    matrix = []
    for metric, _label in metrics:
        vals = []
        for target in targets:
            row = next(r for r in target_rows if r["metric"] == metric and abs(f(r, "target_bpp") - target) < 1e-9)
            value = f(row, "median_sony_minus_nikon")
            if metric in {"ssim_mean", "ms_ssim_mean", "gmsd_mean"}:
                value *= 10000.0
            vals.append(value)
        matrix.append(vals)

    fig, ax = plt.subplots(figsize=(7.3, 3.8))
    arr = np.asarray(matrix)
    vmax = max(float(np.max(np.abs(arr))), 1e-9)
    im = ax.imshow(arr, cmap="coolwarm", vmin=-vmax, vmax=vmax, aspect="auto")
    ax.set_xticks(np.arange(len(targets)), [f"{t:g}" for t in targets])
    ax.set_yticks(np.arange(len(metrics)), [label for _metric, label in metrics])
    ax.set_xlabel("requested target bpp")
    ax.set_title("Median deltas, Sony - Nikon")
    for yi in range(arr.shape[0]):
        for xi in range(arr.shape[1]):
            ax.text(xi, yi, f"{arr[yi, xi]:.2f}", ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax, shrink=0.85)
    save(fig, fig_dir / "fig_strict_metric_matrix.png")


def plot_bd_rate(math_dir: Path, fig_dir: Path) -> None:
    rowspec = [
        ("bd_rate_psnr.csv", "PSNR", "whole"),
        ("bd_rate_mae.csv", "MAE", "whole"),
        ("bd_rate_grad_psnr.csv", "grad-PSNR", "detail"),
        ("bd_rate_ssim.csv", "SSIM", "detail"),
        ("bd_rate_ms_ssim.csv", "MS-SSIM", "detail"),
        ("bd_rate_gmsd.csv", "GMSD", "detail"),
    ]
    labels, med, lo, hi, ok = [], [], [], [], []
    for filename, label, group in rowspec:
        row = next(r for r in read_csv(math_dir / filename) if r["group"] == group)
        m = f(row, "median_bd_rate") * 100.0
        p025 = f(row, "p025_bd_rate") * 100.0
        p975 = f(row, "p975_bd_rate") * 100.0
        labels.append(label)
        med.append(m)
        lo.append(m - p025)
        hi.append(p975 - m)
        ok.append(f"{row['ok_sources']}/24")
    fig, ax = plt.subplots(figsize=(7.3, 3.6))
    x = np.arange(len(labels))
    ax.axhline(0, color="#444444", lw=0.8)
    ax.bar(x, med, color="#255f85")
    ax.errorbar(x, med, yerr=[lo, hi], fmt="none", ecolor="#222222", capsize=3, lw=0.9)
    for xi, text in zip(x, ok):
        ax.text(xi, med[xi] + (2.0 if med[xi] >= 0 else -2.0), text, ha="center", va="bottom" if med[xi] >= 0 else "top", fontsize=8)
    ax.set_xticks(x, labels)
    ax.set_ylabel("BD-rate %, Nikon relative to Sony")
    ax.set_title("Equal-quality BD-rate with computable source counts")
    save(fig, fig_dir / "fig_strict_bd_rate_summary.png")


def plot_syntax(math_dir: Path, fig_dir: Path) -> None:
    return
    rates = read_csv(math_dir / "rate_summary.csv")
    targets = sorted({f(r, "target_bpp") for r in rates})
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.6))
    for codec, label, color in [(SONY, "Sony #824 canonical", "#b95037"), (NIKON, "Nikon #826 canonical", "#386641")]:
        vals = [f(next(r for r in rates if r["codec"] == codec and abs(f(r, "target_bpp") - target) < 1e-9), "actual_bpp_median") for target in targets]
        axes[0].plot(targets, vals, marker="o", label=label, color=color)
    axes[0].plot(targets, targets, ls="--", color="#555555", lw=0.9, label="requested")
    axes[0].set_xlabel("requested target bpp")
    axes[0].set_ylabel("median actual syntax bpp")
    axes[0].set_title("Rate-control behavior")
    axes[0].legend(loc="best")

    syntax = read_csv(math_dir / "syntax_summary.csv")
    target = 2.5
    bar_specs = [
        (SONY, ["control_bits", "selector_bits", "width_update_bits", "zero_run_bits", "payload_bits", "sign_bits"]),
        (NIKON, ["header_bits", "gcli_bits", "data_bits", "sign_bits"]),
    ]
    labels = ["Sony", "Nikon"]
    colors = ["#d8a47f", "#c17c74", "#7a9e9f", "#5c6f7b", "#2e86ab", "#6d597a"]
    bottom = np.zeros(2)
    component_names: list[str] = []
    for comp_idx, component in enumerate(["control_bits", "selector_bits", "width_update_bits", "zero_run_bits", "payload_bits", "header_bits", "gcli_bits", "data_bits", "sign_bits"]):
        vals = []
        for codec, _components in bar_specs:
            rows = [r for r in syntax if r["codec"] == codec and abs(f(r, "target_bpp") - target) < 1e-9 and r.get(component, "") != ""]
            vals.append(statistics.median(float(r[component]) for r in rows) / (256.0 * 256.0) if rows else 0.0)
        if any(v > 0 for v in vals):
            axes[1].bar(labels, vals, bottom=bottom, label=component.replace("_bits", ""), color=colors[comp_idx % len(colors)])
            bottom += np.asarray(vals)
            component_names.append(component)
    axes[1].set_ylabel("median component bpp")
    axes[1].set_title("Syntax components at 2.5 bpp request")
    axes[1].legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=3, fontsize=7)
    save(fig, fig_dir / "fig_strict_syntax_summary.png")


def plot_roundtrip(math_dir: Path, fig_dir: Path) -> None:
    rows = read_csv(math_dir / "roundtrip_audit.csv")
    labels = ["Sony #824", "Nikon #826"]
    codecs = [SONY, NIKON]
    med_mae = [statistics.median(f(r, "MAE") for r in rows if r["codec"] == codec) for codec in codecs]
    max_abs = [max(f(r, "MAX") for r in rows if r["codec"] == codec) for codec in codecs]
    fig, ax = plt.subplots(figsize=(5.8, 3.4))
    x = np.arange(2)
    ax.bar(x - 0.17, med_mae, width=0.34, label="median MAE")
    ax.bar(x + 0.17, max_abs, width=0.34, label="max abs")
    ax.set_xticks(x, labels)
    ax.set_ylabel("RAW sample DN")
    ax.set_title("Zero-quantization roundtrip projection")
    ax.legend()
    save(fig, fig_dir / "fig_strict_roundtrip.png")


def plot_scene_rank(math_dir: Path, fig_dir: Path) -> None:
    rows = [r for r in read_csv(math_dir / "metrics.csv") if r["metric"] == "PSNR_raw" and r["split"] == "whole"]
    by_key: dict[tuple[str, str], dict[str, float]] = {}
    for row in rows:
        by_key.setdefault((row["source_id"], row["target_bpp"]), {})[row["codec"]] = f(row, "value")
    scene_vals: dict[str, list[float]] = {}
    for (source_id, _target), vals in by_key.items():
        if SONY in vals and NIKON in vals:
            scene_vals.setdefault(source_id, []).append(vals[SONY] - vals[NIKON])
    ranked = sorted(((source, statistics.mean(vals)) for source, vals in scene_vals.items()), key=lambda item: item[1])
    fig, ax = plt.subplots(figsize=(7.4, 5.2))
    y = np.arange(len(ranked))
    vals = [v for _s, v in ranked]
    colors = ["#386641" if v < 0 else "#b95037" for v in vals]
    ax.barh(y, vals, color=colors)
    ax.axvline(0, color="#333333", lw=0.8)
    ax.set_yticks(y, [s for s, _v in ranked])
    ax.set_xlabel("mean whole PSNR diff, dB (Sony - Nikon)")
    ax.set_title("Scene-level direction over six target requests")
    save(fig, fig_dir / "fig_strict_scene_rank.png")


def summary_value(rows: list[dict[str, str]], stage: str, metric: str, codec: str, target: str = "") -> float:
    matches = [
        r for r in rows
        if r.get("stage") == stage and r.get("metric") == metric and r.get("target_bpp", "") == target
    ]
    if not matches:
        raise KeyError((stage, metric, codec, target))
    key = "median_sony" if codec == SONY else "median_nikon"
    return float(matches[0][key])


def plot_stage_separation(insight_dir: Path, fig_dir: Path) -> None:
    return
    rows = read_csv(insight_dir / "stage_summary.csv")
    targets = sorted(
        {r["target_bpp"] for r in rows if r["stage"] == "quantization_dequantization" and r["target_bpp"]},
        key=float,
    )
    fig, axes = plt.subplots(2, 1, figsize=(7.3, 5.2))

    labels = ["Sony", "Nikon"]
    x = np.arange(2)
    mae_vals = [summary_value(rows, "transform_roundtrip", "MAE", SONY), summary_value(rows, "transform_roundtrip", "MAE", NIKON)]
    max_vals = [summary_value(rows, "transform_roundtrip", "MAX", SONY), summary_value(rows, "transform_roundtrip", "MAX", NIKON)]
    axes[0].bar(x - 0.17, mae_vals, width=0.34, label="transform MAE")
    axes[0].bar(x + 0.17, max_vals, width=0.34, label="transform MAX")
    axes[0].set_yscale("symlog", linthresh=1e-6)
    axes[0].set_xticks(x, labels)
    axes[0].set_ylabel("RAW DN, symlog")
    axes[0].set_title("Transform roundtrip separated from quantization")
    axes[0].legend(loc="best")

    xt = np.arange(len(targets))
    snr_diff = [
        summary_value(rows, "quantization_dequantization", "coeff_SNR_db", SONY, target)
        - summary_value(rows, "quantization_dequantization", "coeff_SNR_db", NIKON, target)
        for target in targets
    ]
    mae_diff = [
        summary_value(rows, "quantization_dequantization", "coeff_MAE", SONY, target)
        - summary_value(rows, "quantization_dequantization", "coeff_MAE", NIKON, target)
        for target in targets
    ]
    axes[1].axhline(0, color="#444444", lw=0.8)
    axes[1].plot(xt, snr_diff, marker="o", label="coeff SNR diff, dB")
    axes[1].bar(xt, mae_diff, width=0.45, alpha=0.65, label="coeff MAE diff")
    axes[1].set_xticks(xt, [f"{float(t):g}" for t in targets])
    axes[1].set_xlabel("requested target bpp")
    axes[1].set_ylabel("Sony - Nikon")
    axes[1].set_title("Coefficient quantization/dequantization deltas")
    axes[1].legend(loc="best")
    save(fig, fig_dir / "fig_strict_stage_separation.png")


def plot_insight_metrics(insight_dir: Path, fig_dir: Path) -> None:
    return
    rows = read_csv(insight_dir / "insight_target_summary.csv")
    metrics = [
        ("vifp_mean", "VIF-style"),
        ("residual_neighbor_corr_abs", "resid corr"),
        ("error_hf_energy_fraction", "HF error"),
        ("edge_error_correlation", "edge corr"),
        ("phase_MAE_std", "phase MAE sd"),
    ]
    targets = sorted({r["target_bpp"] for r in rows if r["stage"] == "final_reconstruction"}, key=float)
    arr = []
    for metric, _label in metrics:
        vals = []
        for target in targets:
            match = next(
                r for r in rows
                if r["stage"] == "final_reconstruction" and r["metric"] == metric and r["target_bpp"] == target
            )
            vals.append(float(match["median_sony_minus_nikon"]))
        arr.append(vals)
    matrix = np.asarray(arr)
    vmax = max(float(np.max(np.abs(matrix))), 1e-9)
    fig, ax = plt.subplots(figsize=(7.3, 3.8))
    im = ax.imshow(matrix, cmap="coolwarm", vmin=-vmax, vmax=vmax, aspect="auto")
    ax.set_xticks(np.arange(len(targets)), [f"{float(t):g}" for t in targets])
    ax.set_yticks(np.arange(len(metrics)), [label for _metric, label in metrics])
    ax.set_xlabel("requested target bpp")
    ax.set_title("Mathematical insight metrics, Sony - Nikon")
    for yi in range(matrix.shape[0]):
        for xi in range(matrix.shape[1]):
            ax.text(xi, yi, f"{matrix[yi, xi]:.3g}", ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax, shrink=0.85)
    save(fig, fig_dir / "fig_strict_insight_metrics.png")


def plot_rd_slope(insight_dir: Path, fig_dir: Path) -> None:
    rows = read_csv(insight_dir / "rd_slope_summary.csv")
    metrics = [
        ("lambda_mse_drop_per_bpp", "MSE drop"),
        ("lambda_psnr_gain_per_bpp", "PSNR gain"),
        ("lambda_ssim_gain_per_bpp", "SSIM gain"),
        ("lambda_vifp_gain_per_bpp", "VIF gain"),
        ("lambda_gmsd_drop_per_bpp", "GMSD drop"),
    ]
    sony_vals, nikon_vals = [], []
    for metric, _label in metrics:
        sony_vals.append(float(next(r for r in rows if r["codec"] == SONY and r["metric"] == metric)["median"]))
        nikon_vals.append(float(next(r for r in rows if r["codec"] == NIKON and r["metric"] == metric)["median"]))
    fig, ax = plt.subplots(figsize=(7.3, 3.6))
    x = np.arange(len(metrics))
    ax.bar(x - 0.18, sony_vals, width=0.36, label="Sony")
    ax.bar(x + 0.18, nikon_vals, width=0.36, label="Nikon")
    ax.set_yscale("symlog", linthresh=1e-4)
    ax.set_xticks(x, [label for _metric, label in metrics], rotation=18, ha="right")
    ax.set_ylabel("median local slope, symlog")
    ax.set_title("Local finite-difference RD slopes")
    ax.legend(loc="best")
    save(fig, fig_dir / "fig_strict_rd_slope.png")


def make_mosaic(planes: dict[str, np.ndarray]) -> np.ndarray:
    h, w = next(iter(planes.values())).shape
    out = np.zeros((h * 2, w * 2), dtype=np.float64)
    out[0::2, 0::2] = planes["R"]
    out[0::2, 1::2] = planes["G0"]
    out[1::2, 0::2] = planes["G1"]
    out[1::2, 1::2] = planes["B"]
    return out


def norm_img(arr: np.ndarray, lo: float | None = None, hi: float | None = None) -> np.ndarray:
    x = np.asarray(arr, dtype=np.float64)
    if lo is None:
        lo = float(np.percentile(x, 1))
    if hi is None:
        hi = float(np.percentile(x, 99))
    if hi <= lo:
        hi = lo + 1.0
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def strict_sources(width: int, height: int, seed: int) -> dict[str, dict[str, np.ndarray]]:
    rng = np.random.default_rng(seed)
    return {scene: strict.generate_scene(scene, height // 2, width // 2, rng) for scene in strict.SCENES}


def encode_pair(planes: dict[str, np.ndarray], scene: str, target: float, levels: int) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    out = {}
    for codec in strict.CODECS.values():
        comps = codec.forward(planes)
        coeffs, sizes = strict.transform_components(comps, levels)
        if codec.name == NIKON:
            result = strict.encode_nikon_targets_precomputed(planes, scene, codec, [target], coeffs, sizes)[0]
        else:
            result = strict.encode_sony_precomputed(planes, scene, codec, target, coeffs, sizes)
        out[codec.name] = result.recon
    return out[NIKON], out[SONY]


def plot_roi(
    src: dict[str, np.ndarray],
    scene: str,
    target: float,
    levels: int,
    fig_dir: Path,
    filename: str,
    rect: tuple[int, int, int, int],
) -> None:
    nikon, sony = encode_pair(src, scene, target, levels)
    mosaics = [make_mosaic(src), make_mosaic(nikon), make_mosaic(sony)]
    y0, x0, h, w = rect
    crops = [m[y0 : y0 + h, x0 : x0 + w] for m in mosaics]
    lo = min(float(np.percentile(c, 1)) for c in crops)
    hi = max(float(np.percentile(c, 99)) for c in crops)
    nikon_err = crops[1] - crops[0]
    sony_err = crops[2] - crops[0]
    fig, axes = plt.subplots(1, 5, figsize=(8.8, 2.2))
    for ax, img, title in zip(axes[:3], crops, ["reference", "Nikon", "Sony"]):
        ax.imshow(norm_img(img, lo, hi), cmap="gray", interpolation="nearest")
        ax.set_title(title)
        ax.axis("off")
    vmax = max(float(np.max(np.abs(nikon_err))), float(np.max(np.abs(sony_err))), 1.0)
    for ax, err, title in zip(axes[3:], [nikon_err, sony_err], ["Nikon err", "Sony err"]):
        ax.imshow(err, cmap="coolwarm", vmin=-vmax, vmax=vmax, interpolation="nearest")
        ax.set_title(title)
        ax.axis("off")
    fig.suptitle(f"{scene}, {target:g} bpp request")
    save(fig, fig_dir / filename)


def roi_task(
    src: dict[str, np.ndarray],
    scene: str,
    target: float,
    levels: int,
    fig_dir: Path,
    filename: str,
    rect: tuple[int, int, int, int],
) -> str:
    plot_roi(src, scene, target, levels, fig_dir, filename, rect)
    return filename


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--math-dir", type=Path, default=Path("out/strict_824_826_math_eval_full_20260603"))
    ap.add_argument("--insight-dir", type=Path, default=Path("out/strict_824_826_math_insight_20260603"))
    ap.add_argument("--fig-dir", type=Path, default=Path("docs/proxy-four-plane-latex-report/figures"))
    ap.add_argument("--roi-jobs", type=int, default=min(5, max(1, (os.cpu_count() or 2) // 2)))
    ns = ap.parse_args()

    manifest = json.loads((ns.math_dir / "manifest.json").read_text(encoding="utf-8"))
    set_style()
    plot_structure(ns.fig_dir)
    plot_bd_rate(ns.math_dir, ns.fig_dir)
    plot_roundtrip(ns.math_dir, ns.fig_dir)
    plot_scene_rank(ns.math_dir, ns.fig_dir)
    plot_rd_slope(ns.insight_dir, ns.fig_dir)

    sources = strict_sources(int(manifest["width"]), int(manifest["height"]), int(manifest["seed"]))
    levels = int(manifest["levels"])
    roi_specs = [
        ("shadow_noise", "fig_strict_roi_shadow_noise.png", (30, 30, 96, 96)),
        ("high_iso_texture", "fig_strict_roi_high_iso_texture.png", (72, 72, 96, 96)),
        ("highlight_rolloff", "fig_strict_roi_highlight_rolloff.png", (38, 88, 96, 96)),
        ("thin_black_lines", "fig_strict_roi_thin_black_lines.png", (42, 42, 96, 96)),
        ("red_blue_fine_text", "fig_strict_roi_red_blue_fine_text.png", (64, 64, 96, 96)),
    ]
    roi_jobs = max(1, min(ns.roi_jobs, len(roi_specs), 61 if os.name == "nt" else len(roi_specs)))
    if roi_jobs <= 1:
        for scene, filename, rect in roi_specs:
            print(roi_task(sources[scene], scene, 2.0, levels, ns.fig_dir, filename, rect), flush=True)
    else:
        with ProcessPoolExecutor(max_workers=roi_jobs) as pool:
            futures = [
                pool.submit(roi_task, sources[scene], scene, 2.0, levels, ns.fig_dir, filename, rect)
                for scene, filename, rect in roi_specs
            ]
            for future in as_completed(futures):
                print(future.result(), flush=True)

    for path in sorted(ns.fig_dir.glob("fig_strict_*.png")):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
