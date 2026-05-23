#!/usr/bin/env python3
"""Turn dumped LLVC3 planes into a Bayer mosaic plus quick inspection files."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

import numpy as np
from PIL import Image
import tifffile


def read_plane(path: Path, shape: tuple[int, int]) -> np.ndarray:
    arr = np.fromfile(path, dtype="<u2")
    expected = shape[0] * shape[1]
    if arr.size != expected:
        raise ValueError(f"{path} has {arr.size} uint16 samples, expected {expected}")
    return arr.reshape(shape)


def recombine(c0: np.ndarray, c1: np.ndarray, c2: np.ndarray) -> np.ndarray:
    """Map LLVC planes to the TIFF-declared RGGB CFA.

    For this file Sony's full-resolution LLVC3 output looks like:
      c0: H/2 x W   -> both green sites, interleaved horizontally
      c1: H/2 x W/2 -> red sites
      c2: H/2 x W/2 -> blue sites

    The ARW SubIFD declares CFAPattern [0, 1, 1, 2] = RGGB.
    """

    half_h, width = c0.shape
    if c1.shape != (half_h, width // 2) or c2.shape != (half_h, width // 2):
        raise ValueError(f"unexpected plane shapes: {c0.shape}, {c1.shape}, {c2.shape}")
    out = np.empty((half_h * 2, width), dtype=np.uint16)
    out[0::2, 0::2] = c1
    out[0::2, 1::2] = c0[:, 1::2]
    out[1::2, 0::2] = c0[:, 0::2]
    out[1::2, 1::2] = c2
    return out


def robust_preview(raw: np.ndarray, black: int, white: int) -> Image.Image:
    signal = raw.astype(np.int32) - int(black)
    signal = np.clip(signal, 0, int(white) - int(black))
    # Average each 2x2 RGGB cell into a small monochrome preview.
    small = signal.reshape(raw.shape[0] // 2, 2, raw.shape[1] // 2, 2).mean(axis=(1, 3))
    lo, hi = np.percentile(small, [0.5, 99.5])
    if hi <= lo:
        hi = lo + 1
    img = np.clip((small - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)
    return Image.fromarray(img, mode="L")


def write_dng(
    path: Path,
    raw: np.ndarray,
    black: int,
    white: int,
    crop_origin: tuple[int, int] = (12, 8),
    crop_size: tuple[int, int] = (7008, 4672),
) -> None:
    extratags = [
        (50706, "B", 4, (1, 4, 0, 0), False),  # DNGVersion
        (50707, "B", 4, (1, 1, 0, 0), False),  # DNGBackwardVersion
        (50708, "s", 1, "Sony ILCE-7M5 LLVC3 scratch", False),
        (33421, "H", 2, (2, 2), False),  # CFARepeatPatternDim
        (33422, "B", 4, (0, 1, 1, 2), False),  # CFAPattern = RGGB
        (50714, "H", 1, (black,), False),  # BlackLevel
        (50717, "H", 1, (white,), False),  # WhiteLevel
        (50719, "I", 2, crop_origin, False),  # DefaultCropOrigin
        (50720, "I", 2, crop_size, False),  # DefaultCropSize
        (50721, "i", 9, (10000, 0, 0, 0, 10000, 0, 0, 0, 10000), False),  # ColorMatrix1
        (50728, "H", 3, (1, 1, 1), False),  # AsShotNeutral
        (50778, "H", 1, (21,), False),  # CalibrationIlluminant1 = D65
    ]
    tifffile.imwrite(
        path,
        raw,
        dtype=np.uint16,
        photometric="cfa",
        compression=None,
        metadata=None,
        extratags=extratags,
    )


def stats(arr: np.ndarray) -> dict:
    return {
        "shape": list(arr.shape),
        "min": int(arr.min()),
        "max": int(arr.max()),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="out/reverse/llvc_v0_kind2_clean_v0")
    ap.add_argument("--out-dir", default="out/recombined")
    ap.add_argument("--black", type=int, default=1024)
    ap.add_argument("--white", type=int, default=16383)
    ap.add_argument("--shifted-black", type=int, default=512)
    ns = ap.parse_args()

    prefix = Path(ns.prefix)
    out_dir = Path(ns.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    c0 = read_plane(Path(f"{prefix}_c0.bin"), (2344, 7040))
    c1 = read_plane(Path(f"{prefix}_c1.bin"), (2344, 3520))
    c2 = read_plane(Path(f"{prefix}_c2.bin"), (2344, 3520))
    raw = recombine(c0, c1, c2)

    raw_path = out_dir / "DSC00089_llvc3_kind2_recombined_rggb_7040x4688_u16.raw"
    raw.tofile(raw_path)

    preview_path = out_dir / "DSC00089_llvc3_kind2_recombined_rggb_preview.png"
    robust_preview(raw, ns.black, ns.white).save(preview_path)

    dng1024 = out_dir / "DSC00089_llvc3_kind2_7040x4688_bl1024_wl16383_rggb.tiff"
    write_dng(dng1024, raw, ns.black, ns.white)

    delta = ns.black - ns.shifted_black
    shifted = np.clip(raw.astype(np.int32) - delta, 0, ns.white).astype(np.uint16)
    dng512 = out_dir / "DSC00089_llvc3_kind2_7040x4688_bl512_wl15871_rggb_shifted.tiff"
    write_dng(dng512, shifted, ns.shifted_black, ns.white - delta)

    sidecar = {
        "input_prefix": str(prefix),
        "plane_mapping": {
            "cfa": "RGGB",
            "even_rows_even_cols": "c1",
            "even_rows_odd_cols": "c0 even columns",
            "odd_rows_even_cols": "c0 odd columns",
            "odd_rows_odd_cols": "c2",
        },
        "planes": {"c0": stats(c0), "c1": stats(c1), "c2": stats(c2)},
        "raw": stats(raw),
        "black_level_bottom_decoder_domain": ns.black,
        "white_level": ns.white,
        "sony_tag_0x7310_black_level": ns.shifted_black,
        "outputs": {
            "raw_u16": str(raw_path),
            "preview_png": str(preview_path),
            "dng_like_bl1024_tiff": str(dng1024),
            "dng_like_bl512_shifted_tiff": str(dng512),
        },
    }
    sidecar_path = out_dir / "DSC00089_llvc3_kind2_recombined_summary.json"
    sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    print(json.dumps(sidecar, indent=2))


if __name__ == "__main__":
    main()
