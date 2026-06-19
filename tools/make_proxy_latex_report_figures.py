#!/usr/bin/env python3
"""Generate figures for the proxy four-plane LaTeX report.

The figures are generated from the current CSV outputs plus the deterministic
scene generator. They intentionally describe an L2/L2.5 proxy benchmark, not a
production Nikon or Sony encoder.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

import proxy_four_plane_benchmark as bench


NIKON = bench.NIKON_CODEC_NAME
SONY = bench.SONY_CODEC_NAME


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


def plot_structure(fig_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 3.5))
    ax.axis("off")
    boxes = [
        (0.05, 0.70, "Same RGGB\nfour planes"),
        (0.30, 0.84, "Nikon-like #826\nIQX/IQP LUT code domain"),
        (0.55, 0.84, "step1/step2\n+ CDF 5/3"),
        (0.80, 0.84, "GCLI/GTLI\nproxy terms"),
        (0.30, 0.36, "Sony-like #824\n4096 LUT code domain"),
        (0.55, 0.36, "green backbone\n+ R/B residuals"),
        (0.80, 0.36, "LLVC3 width\nzero-run/sign terms"),
    ]
    for x, y, text in boxes:
        ax.text(
            x,
            y,
            text,
            ha="center",
            va="center",
            bbox=dict(boxstyle="round,pad=0.35", fc="#f7f7f2", ec="#333333", lw=0.8),
        )
    arrows = [
        ((0.13, 0.72), (0.23, 0.84)),
        ((0.40, 0.84), (0.49, 0.84)),
        ((0.65, 0.84), (0.74, 0.84)),
        ((0.13, 0.68), (0.23, 0.36)),
        ((0.40, 0.36), (0.49, 0.36)),
        ((0.65, 0.36), (0.74, 0.36)),
    ]
    for start, end in arrows:
        ax.annotate("", xy=end, xytext=start, arrowprops=dict(arrowstyle="->", lw=1.0))
    ax.text(
        0.50,
        0.08,
        "L2 compares transform-domain proxy quality; L2.5 adds decoder-visible syntax/header/LUT proxy costs.",
        ha="center",
        va="center",
    )
    save(fig, fig_dir / "fig_proxy_structure.png")


def plot_same_rate(base: Path, fig_dir: Path) -> None:
    rows = read_csv(base / "same_rate_summary.csv")
    targets = [f(r, "target_bpp") for r in rows]
    psnr = [f(r, "median_psnr_sony_minus_nikon_db") for r in rows]
    mae = [f(r, "median_mae_sony_minus_nikon") for r in rows]
    wins = [int(r["sony_psnr_wins"]) for r in rows]
    x = np.arange(len(targets))
    fig, ax1 = plt.subplots(figsize=(7.2, 3.6))
    ax1.axhline(0, color="#444444", lw=0.8)
    ax1.plot(x, psnr, marker="o", color="#174a7c", label="PSNR diff (Sony - Nikon)")
    ax1.set_ylabel("PSNR diff, dB")
    ax1.set_xticks(x, [f"{t:g}" for t in targets])
    ax1.set_xlabel("requested target bpp")
    ax2 = ax1.twinx()
    ax2.bar(x, mae, alpha=0.28, color="#b95037", label="MAE diff (Sony - Nikon)")
    ax2.set_ylabel("MAE diff, DN")
    for xi, w in zip(x, wins):
        y_text = psnr[xi] + (0.75 if psnr[xi] < -7.2 else -0.7)
        va = "bottom" if psnr[xi] < -7.2 else "top"
        ax1.text(xi, y_text, f"{w}/24", ha="center", va=va, fontsize=8)
    ax1.set_title("Same requested target: whole RAW median deltas")
    lines, labels = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines + lines2, labels + labels2, loc="lower left")
    save(fig, fig_dir / "fig_same_rate_summary.png")


def plot_detail_wins(base: Path, fig_dir: Path) -> None:
    rows = read_csv(base / "detail_summary.csv")
    metrics = ["ssim_mean", "ms_ssim_mean", "gmsd_mean"]
    labels = {"ssim_mean": "SSIM", "ms_ssim_mean": "MS-SSIM", "gmsd_mean": "GMSD"}
    targets = sorted({f(r, "target_bpp") for r in rows})
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    width = 0.24
    x = np.arange(len(targets))
    for i, metric in enumerate(metrics):
        vals = [
            int(next(r for r in rows if r["metric"] == metric and abs(f(r, "target_bpp") - t) < 1e-9)["sony_wins"])
            for t in targets
        ]
        ax.bar(x + (i - 1) * width, vals, width, label=labels[metric])
    ax.axhline(12, color="#666666", lw=0.8, ls="--")
    ax.set_xticks(x, [f"{t:g}" for t in targets])
    ax.set_xlabel("requested target bpp")
    ax.set_ylabel("Sony wins out of 24")
    ax.set_ylim(0, 24)
    ax.set_title("Structure-quality wins by metric")
    ax.legend()
    save(fig, fig_dir / "fig_detail_wins.png")


def bd_percent(value: float, invert: bool = True) -> float:
    if invert:
        return (1.0 / (1.0 + value) - 1.0) * 100.0
    return value * 100.0


def plot_bd_rate(base: Path, fig_dir: Path) -> None:
    files = [
        ("bd_rate_psnr.csv", "PSNR", "whole"),
        ("bd_rate_mae.csv", "MAE", "whole"),
        ("bd_rate_ssim.csv", "SSIM", "detail"),
        ("bd_rate_ms_ssim.csv", "MS-SSIM", "detail"),
        ("bd_rate_gmsd.csv", "GMSD", "detail"),
    ]
    labels: list[str] = []
    vals: list[float] = []
    lo: list[float] = []
    hi: list[float] = []
    for filename, label, group in files:
        row = next(r for r in read_csv(base / filename) if r["group"] == group)
        med = bd_percent(float(row["median_bd_rate"]))
        low = bd_percent(float(row["p975_bd_rate"]))
        high = bd_percent(float(row["p025_bd_rate"]))
        labels.append(label)
        vals.append(med)
        lo.append(med - low)
        hi.append(high - med)
    fig, ax = plt.subplots(figsize=(7.2, 3.5))
    x = np.arange(len(labels))
    ax.axhline(0, color="#444444", lw=0.8)
    ax.bar(x, vals, color="#386641")
    ax.errorbar(x, vals, yerr=[lo, hi], fmt="none", ecolor="#222222", capsize=3, lw=0.9)
    ax.set_xticks(x, labels)
    ax.set_ylabel("BD-rate %, Sony relative to Nikon")
    ax.set_title("Equal-quality BD-rate summary")
    save(fig, fig_dir / "fig_bd_rate_summary.png")


def plot_source_rank(base: Path, fig_dir: Path) -> None:
    rows = sorted(read_csv(base / "source_delta_summary.csv"), key=lambda r: f(r, "mean_psnr_sony_minus_nikon_db"))
    labels = [r["source_id"] for r in rows]
    vals = [f(r, "mean_psnr_sony_minus_nikon_db") for r in rows]
    fig, ax = plt.subplots(figsize=(7.5, 5.0))
    y = np.arange(len(labels))
    colors = ["#386641" if v < 0 else "#b95037" for v in vals]
    ax.barh(y, vals, color=colors)
    ax.axvline(0, color="#333333", lw=0.8)
    ax.set_yticks(y, labels)
    ax.set_xlabel("mean PSNR diff, dB (Sony - Nikon)")
    ax.set_title("Scene-level direction over six target points")
    save(fig, fig_dir / "fig_source_rank.png")


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


def encode_scene(scene: str, target: float) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray]]:
    rng = np.random.default_rng(20260602)
    generated: dict[str, dict[str, np.ndarray]] = {}
    for name in bench.DEFAULT_SCENES:
        generated[name] = bench.generate_scene(name, 256, 256, rng)
    src = generated[scene]
    nik = bench.encode_proxy(src, scene, bench.CODECS[NIKON], target, 4).recon
    son = bench.encode_proxy(src, scene, bench.CODECS[SONY], target, 4).recon
    return src, nik, son


def plot_roi(scene: str, target: float, fig_dir: Path, name: str, rect: tuple[int, int, int, int], gain: float = 1.0) -> None:
    src, nik, son = encode_scene(scene, target)
    mosaics = [make_mosaic(src), make_mosaic(nik), make_mosaic(son)]
    y0, x0, h, w = rect
    crops = [m[y0 : y0 + h, x0 : x0 + w] for m in mosaics]
    lo = min(float(np.percentile(c, 1)) for c in crops)
    hi = max(float(np.percentile(c, 99)) for c in crops)
    nik_err = (crops[1] - crops[0]) * gain
    son_err = (crops[2] - crops[0]) * gain
    fig, axes = plt.subplots(1, 5, figsize=(8.8, 2.2))
    titles = ["reference", "Nikon-like", "Sony-like", "Nikon err", "Sony err"]
    for ax, img, title in zip(axes[:3], crops, titles[:3]):
        ax.imshow(norm_img(img, lo, hi), cmap="gray", interpolation="nearest")
        ax.set_title(title)
        ax.axis("off")
    vmax = max(float(np.max(np.abs(nik_err))), float(np.max(np.abs(son_err))), 1.0)
    for ax, err, title in zip(axes[3:], [nik_err, son_err], titles[3:]):
        ax.imshow(err, cmap="coolwarm", vmin=-vmax, vmax=vmax, interpolation="nearest")
        ax.set_title(title)
        ax.axis("off")
    fig.suptitle(f"{scene}, {target:g} bpp")
    save(fig, fig_dir / name)


def plot_laplacian_roi(fig_dir: Path) -> None:
    src, nik, son = encode_scene("thin_black_lines", 2.0)
    y0, x0, h, w = (80, 80, 96, 120)
    imgs = []
    for planes in [src, nik, son]:
        mosaic = make_mosaic(planes)[y0 : y0 + h, x0 : x0 + w]
        lap = np.abs(
            -4 * mosaic
            + np.roll(mosaic, 1, 0)
            + np.roll(mosaic, -1, 0)
            + np.roll(mosaic, 1, 1)
            + np.roll(mosaic, -1, 1)
        )
        imgs.append(lap)
    fig, axes = plt.subplots(1, 3, figsize=(6.5, 2.3))
    titles = ["reference detail", "Nikon-like detail", "Sony-like detail"]
    hi = max(float(np.percentile(i, 99)) for i in imgs)
    for ax, img, title in zip(axes, imgs, titles):
        ax.imshow(norm_img(img, 0, hi), cmap="gray", interpolation="nearest")
        ax.set_title(title)
        ax.axis("off")
    save(fig, fig_dir / "fig_roi_thin_black_lines.png")


def plot_rb_roi(fig_dir: Path) -> None:
    src, nik, son = encode_scene("red_blue_fine_text", 2.0)
    y0, x0, h, w = (80, 80, 96, 120)
    fig, axes = plt.subplots(1, 5, figsize=(8.8, 2.2))
    rb_src = np.dstack([norm_img(src["R"]), np.zeros_like(src["R"]), norm_img(src["B"])])
    crops = [
        rb_src[y0 // 2 : y0 // 2 + h // 2, x0 // 2 : x0 // 2 + w // 2],
        nik["R"][y0 // 2 : y0 // 2 + h // 2, x0 // 2 : x0 // 2 + w // 2] - src["R"][y0 // 2 : y0 // 2 + h // 2, x0 // 2 : x0 // 2 + w // 2],
        son["R"][y0 // 2 : y0 // 2 + h // 2, x0 // 2 : x0 // 2 + w // 2] - src["R"][y0 // 2 : y0 // 2 + h // 2, x0 // 2 : x0 // 2 + w // 2],
        nik["B"][y0 // 2 : y0 // 2 + h // 2, x0 // 2 : x0 // 2 + w // 2] - src["B"][y0 // 2 : y0 // 2 + h // 2, x0 // 2 : x0 // 2 + w // 2],
        son["B"][y0 // 2 : y0 // 2 + h // 2, x0 // 2 : x0 // 2 + w // 2] - src["B"][y0 // 2 : y0 // 2 + h // 2, x0 // 2 : x0 // 2 + w // 2],
    ]
    titles = ["R/B reference", "Nikon R err", "Sony R err", "Nikon B err", "Sony B err"]
    axes[0].imshow(crops[0], interpolation="nearest")
    axes[0].set_title(titles[0])
    axes[0].axis("off")
    vmax = max(float(np.max(np.abs(c))) for c in crops[1:]) or 1.0
    for ax, crop, title in zip(axes[1:], crops[1:], titles[1:]):
        ax.imshow(crop, cmap="coolwarm", vmin=-vmax, vmax=vmax, interpolation="nearest")
        ax.set_title(title)
        ax.axis("off")
    save(fig, fig_dir / "fig_roi_red_blue_fine_text.png")


def plot_coding_layer(base: Path, fig_dir: Path) -> None:
    rows = read_csv(base / "coding_layer_summary.csv")
    targets = [f(r, "target_bpp") for r in rows]
    coded = [f(r, "median_coded_bpp_sony_minus_nikon") for r in rows]
    trans = [f(r, "median_transform_entropy_bpp_sony_minus_nikon") for r in rows]
    state = [f(r, "median_state_sideinfo_bpp_sony_minus_nikon") for r in rows]
    psnr = [f(r, "median_psnr_sony_minus_nikon_db") for r in rows]
    x = np.arange(len(targets))
    fig, ax1 = plt.subplots(figsize=(7.2, 3.7))
    width = 0.24
    ax1.bar(x - width, coded, width, label="coded proxy bpp", color="#386641")
    ax1.bar(x, state, width, label="state side-info bpp", color="#d7902f")
    ax1.bar(x + width, trans, width, label="transform entropy bpp", color="#8a8a8a")
    ax1.axhline(0, color="#444444", lw=0.8)
    ax1.set_ylabel("bpp diff (Sony - Nikon)")
    ax1.set_xticks(x, [f"{t:g}" for t in targets])
    ax1.set_xlabel("requested target bpp")
    ax2 = ax1.twinx()
    ax2.plot(x, psnr, color="#174a7c", marker="o", label="PSNR diff")
    ax2.set_ylabel("PSNR diff, dB")
    lines, labels = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines + lines2, labels + labels2, loc="upper left")
    ax1.set_title("L2.5 coding-layer proxy simulation")
    save(fig, fig_dir / "fig_coding_layer_summary.png")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dir", type=Path, default=Path("out/proxy_four_plane_benchmark_rerun_20260603_11page"))
    ap.add_argument("--fig-dir", type=Path, default=Path("docs/proxy-four-plane-latex-report/figures"))
    ns = ap.parse_args()
    set_style()
    ns.fig_dir.mkdir(parents=True, exist_ok=True)
    plot_structure(ns.fig_dir)
    plot_same_rate(ns.base_dir, ns.fig_dir)
    plot_detail_wins(ns.base_dir, ns.fig_dir)
    plot_bd_rate(ns.base_dir, ns.fig_dir)
    plot_source_rank(ns.base_dir, ns.fig_dir)
    plot_roi("shadow_noise", 2.0, ns.fig_dir, "fig_roi_shadow_noise.png", (60, 60, 120, 140), gain=8.0)
    plot_roi("high_iso_texture", 2.0, ns.fig_dir, "fig_roi_high_iso_texture.png", (70, 70, 120, 140), gain=2.0)
    plot_roi("highlight_rolloff", 2.0, ns.fig_dir, "fig_roi_highlight_rolloff.png", (50, 80, 120, 140), gain=2.0)
    plot_laplacian_roi(ns.fig_dir)
    plot_rb_roi(ns.fig_dir)
    plot_coding_layer(ns.base_dir, ns.fig_dir)
    print(f"wrote figures to {ns.fig_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
