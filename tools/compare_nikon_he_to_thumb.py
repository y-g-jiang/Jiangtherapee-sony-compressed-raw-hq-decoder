#!/usr/bin/env python3
"""Compare decoded Nikon HE TIFFs against embedded JPEG previews."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw


def norm_rgb(im: Image.Image) -> np.ndarray:
    arr = np.asarray(im.convert("RGB")).astype(np.float32)
    lo = np.percentile(arr, 0.5, axis=(0, 1), keepdims=True)
    hi = np.percentile(arr, 99.5, axis=(0, 1), keepdims=True)
    return np.clip((arr - lo) / np.maximum(hi - lo, 1.0), 0, 1)


def compare_pair(tiff: Path, thumb: Path, out_diff: Path | None = None) -> dict[str, Any]:
    dec = Image.open(tiff).convert("RGB")
    ref = Image.open(thumb).convert("RGB")
    # Use the decoded size as the comparison grid; embedded previews usually
    # have the same active aspect but slightly different border/crop.
    ref_rs = ref.resize(dec.size, Image.Resampling.BICUBIC)
    a = norm_rgb(dec)
    b = norm_rgb(ref_rs)
    diff = np.abs(a - b)
    ldiff = diff.mean(axis=2)
    row = ldiff.mean(axis=1)
    col = ldiff.mean(axis=0)
    # Ignore extreme borders for row-band scores.
    inner = row[max(8, row.size // 200): row.size - max(8, row.size // 200)]
    bad_rows = np.flatnonzero(row > np.percentile(inner, 95) + 0.04)
    if out_diff:
        vis = np.clip(diff * 4.0, 0, 1)
        img = Image.fromarray((vis * 255).astype(np.uint8), "RGB")
        small = img.resize((min(900, img.width), int(img.height * min(900, img.width) / img.width)),
                           Image.Resampling.BICUBIC)
        small.save(out_diff)
    return {
        "decoded": str(tiff),
        "thumb": str(thumb),
        "decoded_size": list(dec.size),
        "thumb_size": list(ref.size),
        "mean_abs_diff": float(ldiff.mean()),
        "p95_abs_diff": float(np.percentile(ldiff, 95)),
        "p99_abs_diff": float(np.percentile(ldiff, 99)),
        "row_diff_p99": float(np.percentile(row, 99)),
        "row_diff_max": float(row.max()),
        "row_diff_max_row": int(row.argmax()),
        "bad_diff_rows": int(bad_rows.size),
        "bad_diff_first30": bad_rows[:30].astype(int).tolist(),
        "col_diff_p99": float(np.percentile(col, 99)),
    }


def make_sheet(run_dir: Path, out_path: Path) -> None:
    tiles = []
    for nef in sorted(run_dir.glob("*.NEF")):
        tiff = nef.with_suffix(nef.suffix + ".tiff")
        jpg = nef.with_suffix(nef.suffix + ".thumb.jpg")
        if not tiff.exists() or not jpg.exists():
            continue
        dec = Image.open(tiff).convert("RGB")
        ref = Image.open(jpg).convert("RGB").resize(dec.size, Image.Resampling.BICUBIC)
        w, h = dec.size
        crop_w = min(w, 1600)
        crop_h = min(h, 1000)
        box = ((w - crop_w) // 2, (h - crop_h) // 2, (w + crop_w) // 2, (h + crop_h) // 2)
        dec_crop = dec.crop(box)
        ref_crop = ref.crop(box)
        pair = Image.new("RGB", (820, 320), "white")
        for idx, im in enumerate([dec_crop, ref_crop]):
            im = im.copy()
            im.thumbnail((390, 260), Image.Resampling.LANCZOS)
            pair.paste(im, (10 + idx * 410, 25))
        draw = ImageDraw.Draw(pair)
        draw.text((10, 5), f"{nef.name} decoded", fill=(0, 0, 0))
        draw.text((420, 5), "embedded preview", fill=(0, 0, 0))
        tiles.append(pair)
    if not tiles:
        return
    sheet = Image.new("RGB", (820, 320 * len(tiles)), (240, 240, 240))
    for i, tile in enumerate(tiles):
        sheet.paste(tile, (0, i * 320))
    sheet.save(out_path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--out-json", type=Path, required=True)
    ap.add_argument("--diff-dir", type=Path)
    ap.add_argument("--sheet", type=Path)
    ns = ap.parse_args()

    out: dict[str, Any] = {}
    if ns.diff_dir:
        ns.diff_dir.mkdir(parents=True, exist_ok=True)
    for nef in sorted(ns.run_dir.glob("*.NEF")):
        tiff = nef.with_suffix(nef.suffix + ".tiff")
        jpg = nef.with_suffix(nef.suffix + ".thumb.jpg")
        if not tiff.exists() or not jpg.exists():
            continue
        diff_path = ns.diff_dir / f"{nef.name}.diff.png" if ns.diff_dir else None
        out[nef.name] = compare_pair(tiff, jpg, diff_path)
    ns.out_json.parent.mkdir(parents=True, exist_ok=True)
    ns.out_json.write_text(json.dumps(out, indent=2), encoding="utf-8")
    if ns.sheet:
        ns.sheet.parent.mkdir(parents=True, exist_ok=True)
        make_sheet(ns.run_dir, ns.sheet)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
