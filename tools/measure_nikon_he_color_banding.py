#!/usr/bin/env python3
"""Measure Nikon HE/HE* color banding from LibRaw PGM/TIFF outputs.

The script assumes input NEFs have already been copied to a writable run
directory and decoded there, so the neighboring `.pgm` and `.tiff` files are
the artifacts to inspect. It reports row-level zero bands, RGB row chroma
outliers, and Bayer 2x2 chroma drift from the raw PGM.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def read_pgm_u16(path: Path) -> np.ndarray:
    with path.open("rb") as f:
        magic = f.readline().strip()
        if magic != b"P5":
            raise ValueError(f"{path}: unsupported PGM magic {magic!r}")
        line = f.readline()
        while line.startswith(b"#"):
            line = f.readline()
        width, height = map(int, line.split())
        maxv = int(f.readline())
        if maxv <= 255:
            dtype = np.uint8
        else:
            dtype = ">u2"
        data = np.frombuffer(f.read(), dtype=dtype)
    return data.reshape(height, width).astype(np.float32)


def row_mod_counts(rows: np.ndarray, mod: int) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows.tolist():
        k = str(int(r) % mod)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: int(kv[0])))


def exclude_border_rows(rows: np.ndarray, height: int, border: int = 64) -> np.ndarray:
    return rows[(rows >= border) & (rows < max(border, height - border))]


def contiguous_runs(rows: np.ndarray) -> list[list[int]]:
    if rows.size == 0:
        return []
    runs: list[list[int]] = []
    start = prev = int(rows[0])
    for row in rows[1:]:
        cur = int(row)
        if cur == prev + 1:
            prev = cur
            continue
        runs.append([start, prev])
        start = prev = cur
    runs.append([start, prev])
    return runs


def max_true_run(mask: np.ndarray) -> int:
    if mask.size == 0:
        return 0
    padded = np.concatenate(([False], mask.astype(bool), [False]))
    changes = np.flatnonzero(padded[1:] != padded[:-1])
    if changes.size == 0:
        return 0
    lengths = changes[1::2] - changes[::2]
    return int(lengths.max()) if lengths.size else 0


def raw_metrics(pgm_path: Path) -> dict[str, Any]:
    raw = read_pgm_u16(pgm_path)
    h, w = raw.shape
    zero = raw == 0
    row_zero = zero.mean(axis=1)
    bad_zero_rows = np.flatnonzero(row_zero > 0.05)
    bad_zero_interior = exclude_border_rows(bad_zero_rows, h)

    # RGGB 2x2 block means. Work on the visible even rectangle.
    even_h = h - (h % 2)
    even_w = w - (w % 2)
    a = raw[:even_h:2, :even_w:2]      # R
    b = raw[:even_h:2, 1:even_w:2]     # G1
    c = raw[1:even_h:2, :even_w:2]     # G2
    d = raw[1:even_h:2, 1:even_w:2]    # B
    eps = 1.0
    g = (b + c) * 0.5
    block_luma = (a + b + c + d) * 0.25
    valid = block_luma > np.percentile(block_luma, 5)
    rg = np.where(valid, (a - g) / (g + eps), np.nan)
    bg = np.where(valid, (d - g) / (g + eps), np.nan)
    chroma = np.nanmax(np.stack([np.abs(rg), np.abs(bg)]), axis=0)
    row_chroma = np.nanmedian(chroma, axis=1)
    baseline = np.nanmedian(row_chroma)
    row_chroma_dev = row_chroma - baseline
    bad_chroma_block_rows = np.flatnonzero(np.abs(row_chroma_dev) > 0.08) * 2
    bad_chroma_block_interior = exclude_border_rows(bad_chroma_block_rows, h)

    # Compare each block row to a 33-row running median to highlight bands.
    padded = np.pad(row_chroma, (16, 16), mode="edge")
    local = np.array([np.median(padded[i:i + 33]) for i in range(row_chroma.size)])
    local_dev = row_chroma - local
    bad_local_block_rows = np.flatnonzero(np.abs(local_dev) > 0.06) * 2
    bad_local_block_interior = exclude_border_rows(bad_local_block_rows, h)

    return {
        "pgm": str(pgm_path),
        "width": int(w),
        "height": int(h),
        "zero_fraction": float(zero.mean()),
        "bad_zero_rows": int(bad_zero_rows.size),
        "bad_zero_first20": bad_zero_rows[:20].astype(int).tolist(),
        "bad_zero_interior_count": int(bad_zero_interior.size),
        "bad_zero_interior_first20": bad_zero_interior[:20].astype(int).tolist(),
        "max_row_zero_fraction": float(row_zero.max()),
        "max_row_zero_row": int(row_zero.argmax()),
        "raw_block_chroma_dev_abs_p99": float(np.nanpercentile(np.abs(row_chroma_dev), 99)),
        "raw_block_chroma_dev_abs_max": float(np.nanmax(np.abs(row_chroma_dev))),
        "raw_block_bad_count_thr008": int(bad_chroma_block_rows.size),
        "raw_block_bad_first20_rows": bad_chroma_block_rows[:20].astype(int).tolist(),
        "raw_block_bad_interior_count_thr008": int(bad_chroma_block_interior.size),
        "raw_block_bad_interior_first20_rows": bad_chroma_block_interior[:20].astype(int).tolist(),
        "raw_block_bad_mod64": row_mod_counts(bad_chroma_block_rows, 64),
        "raw_block_local_dev_abs_p99": float(np.nanpercentile(np.abs(local_dev), 99)),
        "raw_block_local_dev_abs_max": float(np.nanmax(np.abs(local_dev))),
        "raw_block_local_bad_count_thr006": int(bad_local_block_rows.size),
        "raw_block_local_bad_first20_rows": bad_local_block_rows[:20].astype(int).tolist(),
        "raw_block_local_bad_interior_count_thr006": int(bad_local_block_interior.size),
        "raw_block_local_bad_interior_first20_rows": bad_local_block_interior[:20].astype(int).tolist(),
        "raw_block_local_bad_mod64": row_mod_counts(bad_local_block_rows, 64),
        "mean": float(raw.mean()),
        "std": float(raw.std()),
    }


def rgb_metrics(tiff_path: Path) -> dict[str, Any]:
    im = Image.open(tiff_path)
    arr = np.asarray(im)
    if arr.ndim != 3 or arr.shape[2] < 3:
        raise ValueError(f"{tiff_path}: expected RGB TIFF, got {arr.shape}")
    rgb = arr[..., :3].astype(np.float32)
    h, w, _ = rgb.shape
    luma = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    all_zero = np.all(rgb == 0, axis=2)
    row_zero = all_zero.mean(axis=1)
    bad_zero_rows = np.flatnonzero(row_zero > 0.05)
    bad_zero_interior = exclude_border_rows(bad_zero_rows, h)

    eps = 1.0
    valid = luma > np.percentile(luma, 5)
    rg = np.where(valid, rgb[..., 0] / (rgb[..., 1] + eps), np.nan)
    bg = np.where(valid, rgb[..., 2] / (rgb[..., 1] + eps), np.nan)
    row_rg = np.nanmedian(rg, axis=1)
    row_bg = np.nanmedian(bg, axis=1)
    # Compare to local medians so real scene color gradients do not dominate.
    def local_dev(row: np.ndarray, radius: int = 32) -> np.ndarray:
        padded = np.pad(row, (radius, radius), mode="edge")
        med = np.array([np.nanmedian(padded[i:i + 2 * radius + 1]) for i in range(row.size)])
        return row - med

    rg_dev = local_dev(row_rg)
    bg_dev = local_dev(row_bg)
    chroma_dev = np.nanmax(np.stack([np.abs(rg_dev), np.abs(bg_dev)]), axis=0)
    bad_chroma_rows = np.flatnonzero(chroma_dev > 0.12)
    bad_chroma_interior = exclude_border_rows(bad_chroma_rows, h)
    row_luma_mean = luma.mean(axis=1)
    adjacent_delta = np.abs(np.diff(row_luma_mean))

    return {
        "tiff": str(tiff_path),
        "mode": im.mode,
        "width": int(w),
        "height": int(h),
        "all_zero_fraction": float(all_zero.mean()),
        "bad_zero_rows": int(bad_zero_rows.size),
        "bad_zero_first20": bad_zero_rows[:20].astype(int).tolist(),
        "bad_zero_interior_count": int(bad_zero_interior.size),
        "bad_zero_interior_first20": bad_zero_interior[:20].astype(int).tolist(),
        "bad_zero_runs_first20": contiguous_runs(bad_zero_rows)[:20],
        "max_row_zero_fraction": float(row_zero.max()),
        "max_row_zero_row": int(row_zero.argmax()),
        "max_row_zero_contiguous_pixels": int(max_true_run(all_zero[int(row_zero.argmax())])),
        "rgb_row_chroma_local_dev_abs_p99": float(np.nanpercentile(chroma_dev, 99)),
        "rgb_row_chroma_local_dev_abs_max": float(np.nanmax(chroma_dev)),
        "rgb_row_bad_count_thr012": int(bad_chroma_rows.size),
        "rgb_row_bad_first30": bad_chroma_rows[:30].astype(int).tolist(),
        "rgb_row_bad_interior_count_thr012": int(bad_chroma_interior.size),
        "rgb_row_bad_interior_first30": bad_chroma_interior[:30].astype(int).tolist(),
        "rgb_row_bad_runs_first20": contiguous_runs(bad_chroma_rows)[:20],
        "rgb_row_bad_mod64": row_mod_counts(bad_chroma_rows, 64),
        "channel_mean": [float(x) for x in rgb.reshape(-1, 3).mean(axis=0)],
        "channel_p99": [float(x) for x in np.percentile(rgb.reshape(-1, 3), 99, axis=0)],
        "max_adjacent_row_luma_mean_delta": float(adjacent_delta.max()),
        "p99_adjacent_row_luma_mean_delta": float(np.percentile(adjacent_delta, 99)),
        "luma_mean": float(luma.mean()),
        "luma_std": float(luma.std()),
    }


def make_contact_sheet(items: list[tuple[str, Path]], out_path: Path) -> None:
    thumbs = []
    for label, tiff_path in items:
        im = Image.open(tiff_path).convert("RGB")
        w, h = im.size
        crop_h = min(h, 1024)
        crop_w = min(w, 1600)
        crop = im.crop(((w - crop_w) // 2, (h - crop_h) // 2,
                        (w + crop_w) // 2, (h + crop_h) // 2))
        crop.thumbnail((400, 256), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (420, 300), "white")
        canvas.paste(crop, ((420 - crop.width) // 2, 20))
        draw = ImageDraw.Draw(canvas)
        draw.text((10, 270), label, fill=(0, 0, 0))
        thumbs.append(canvas)
    if not thumbs:
        return
    cols = min(3, len(thumbs))
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 420, rows * 300), (240, 240, 240))
    for i, thumb in enumerate(thumbs):
        sheet.paste(thumb, ((i % cols) * 420, (i // cols) * 300))
    sheet.save(out_path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--out-json", type=Path, required=True)
    ap.add_argument("--contact-sheet", type=Path)
    ns = ap.parse_args()

    metrics: dict[str, Any] = {}
    sheet_items: list[tuple[str, Path]] = []
    for nef in sorted(ns.run_dir.glob("*.NEF")):
        pgm = nef.with_suffix(nef.suffix + ".pgm")
        tiff = nef.with_suffix(nef.suffix + ".tiff")
        entry: dict[str, Any] = {"nef": str(nef)}
        if pgm.exists():
            entry["raw"] = raw_metrics(pgm)
        else:
            entry["raw_missing"] = str(pgm)
        if tiff.exists():
            entry["rgb"] = rgb_metrics(tiff)
            sheet_items.append((nef.name, tiff))
        else:
            entry["rgb_missing"] = str(tiff)
        metrics[nef.name] = entry

    ns.out_json.parent.mkdir(parents=True, exist_ok=True)
    ns.out_json.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    if ns.contact_sheet:
        ns.contact_sheet.parent.mkdir(parents=True, exist_ok=True)
        make_contact_sheet(sheet_items, ns.contact_sheet)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
