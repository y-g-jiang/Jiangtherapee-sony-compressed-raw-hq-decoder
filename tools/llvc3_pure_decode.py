#!/usr/bin/env python3
"""Pure Python path for the ARW6/LLVC3 sample stream I am reconstructing."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from llvc3_bitstream_probe import find_raw_subifd, parse_llvc_header, RAW_STREAM_OFFSET
from llvc3_entropy import decode_packet_arrays, integrate_type1_coefficients
from llvc3_math import (
    apply_sample_lut,
    clamp_signed_to_code_range,
    finalize_llvc3_color_planes,
    recombine_rggb,
    signed_to_sample,
    synthesize_llvc3_final_green,
    synthesize_llvc3_level_stride,
)
from recombine_llvc_planes import robust_preview, write_dng


DEFAULT_SAMPLE_LUT = Path(__file__).with_name("data") / "sony_llvc3_static_lut4096_padded_u16.bin"


def stats(arr: np.ndarray) -> dict[str, Any]:
    return {
        "shape": list(arr.shape),
        "dtype": str(arr.dtype),
        "min": int(arr.min()),
        "max": int(arr.max()),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
    }


def compare_plane(pred: np.ndarray, native_path: Path, shape: tuple[int, int]) -> dict[str, Any]:
    native = np.fromfile(native_path, dtype="<u2")
    if native.size != shape[0] * shape[1]:
        raise ValueError(f"{native_path} has {native.size} samples, expected {shape[0] * shape[1]}")
    native = native.reshape(shape)
    diff = pred.astype(np.int32) - native.astype(np.int32)
    nonzero = np.flatnonzero(diff)
    out: dict[str, Any] = {
        "native_path": str(native_path),
        "exact": bool(nonzero.size == 0),
        "nonzero": int(nonzero.size),
        "max_abs": int(np.max(np.abs(diff))) if diff.size else 0,
    }
    if nonzero.size:
        y, x = np.unravel_index(int(nonzero[0]), diff.shape)
        out["first_mismatch"] = {
            "y": int(y),
            "x": int(x),
            "pred": int(pred[y, x]),
            "native": int(native[y, x]),
            "diff": int(diff[y, x]),
        }
    return out


def load_sample_lut(path: Path) -> np.ndarray:
    """Load a 16-bit code-to-sample LUT from binary or two-column text."""

    suffix = path.suffix.lower()
    if suffix in {".bin", ".raw", ".lut"}:
        lut = np.fromfile(path, dtype="<u2")
    else:
        values: list[int] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", "\t").split()
            if len(parts) < 2:
                continue
            if parts[0].lower() in {"code", "input", "src"}:
                continue
            values.append(int(parts[1]))
        lut = np.asarray(values, dtype=np.uint16)
    if lut.size < 65536:
        pad_value = int(lut[-1]) if lut.size else 0
        lut = np.pad(lut, (0, 65536 - lut.size), constant_values=pad_value)
    return lut[:65536].astype(np.uint16)


def decode_signed_planes(arw: Path, blue_edge_fix: bool = True) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    """Decode to signed internal c0/c1/c2 planes; LUT mapping happens later."""

    timings: dict[str, float] = {}
    packet_meta: dict[str, Any] = {}
    blue_fix_pixels = 0

    t0 = time.perf_counter()
    raw_info, _strip = find_raw_subifd(arw)
    low_rows = raw_info.height // 16

    g0, meta = decode_packet_arrays(arw, 0, 0)
    packet_meta["g0i0"] = meta
    green = integrate_type1_coefficients(g0[0][:low_rows], 2048) - 2048

    r0, meta = decode_packet_arrays(arw, 0, 1)
    packet_meta["g0i1"] = meta
    red_residual = integrate_type1_coefficients(r0[0][:low_rows], 0)

    b0, meta = decode_packet_arrays(arw, 0, 2)
    packet_meta["g0i2"] = meta
    blue_residual = integrate_type1_coefficients(b0[0][:low_rows], 0)
    timings["group0_entropy_and_integrate_s"] = time.perf_counter() - t0

    for group, edge_rows in ((1, 0), (2, 1), (3, 2)):
        t_group = time.perf_counter()
        old_green = green
        old_red_residual = red_residual
        old_blue_residual = blue_residual

        planes, meta = decode_packet_arrays(arw, group, 0)
        packet_meta[f"g{group}i0"] = meta
        green = synthesize_llvc3_level_stride(old_green, planes[0], planes[1], planes[2], edge_rows)

        planes, meta = decode_packet_arrays(arw, group, 1)
        packet_meta[f"g{group}i1"] = meta
        edge_mode = "odd" if group == 3 else "even"
        red_residual = synthesize_llvc3_level_stride(
            old_red_residual, planes[0], planes[1], planes[2], edge_rows, edge_mode=edge_mode
        )

        planes, meta = decode_packet_arrays(arw, group, 2)
        packet_meta[f"g{group}i2"] = meta
        blue_residual = synthesize_llvc3_level_stride(
            old_blue_residual, planes[0], planes[1], planes[2], edge_rows, edge_mode=edge_mode
        )
        timings[f"group{group}_entropy_and_synthesis_s"] = time.perf_counter() - t_group

    t_final = time.perf_counter()
    g4, meta = decode_packet_arrays(arw, 4, 0)
    packet_meta["g4i0"] = meta
    full_green = synthesize_llvc3_final_green(green, g4[0])
    v1_red = green + 2 * red_residual
    v1_blue = green + 2 * blue_residual
    c0, c1, c2 = finalize_llvc3_color_planes(green, v1_red, v1_blue, full_green)
    timings["group4_entropy_and_final_cfa_s"] = time.perf_counter() - t_final

    meta_out = {
        "timings": timings,
        "packet_meta": packet_meta,
        "blue_residual_bottom_edge_fix_pixels": blue_fix_pixels,
        "signed_plane_stats": {"c0": stats(c0), "c1": stats(c1), "c2": stats(c2)},
    }
    return c0, c1, c2, meta_out


def decode_to_files(
    arw: Path,
    out_dir: Path,
    verify_native_prefix: Path | None = None,
    sample_lut_path: Path | None = None,
    blue_edge_fix: bool = True,
    black: int = 1024,
    white: int = 16383,
    shifted_black: int = 512,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = arw.stem

    raw_info, strip = find_raw_subifd(arw)
    header = parse_llvc_header(strip[RAW_STREAM_OFFSET:])
    if header.coded_width != raw_info.width or header.logical_height != raw_info.height or header.component_count != 3:
        raise ValueError(f"unexpected ARW6/LLVC3 header/raw mismatch: raw={raw_info}, header={header}")
    if raw_info.width % 16 or raw_info.height % 16:
        raise ValueError(f"decoder expects dimensions divisible by 16, got {raw_info.width}x{raw_info.height}")

    t0 = time.perf_counter()
    signed_c0, signed_c1, signed_c2, meta = decode_signed_planes(arw, blue_edge_fix=blue_edge_fix)
    code_c0 = signed_to_sample(signed_c0)
    code_c1 = signed_to_sample(signed_c1)
    code_c2 = signed_to_sample(signed_c2)
    if sample_lut_path is None and DEFAULT_SAMPLE_LUT.exists():
        sample_lut_path = DEFAULT_SAMPLE_LUT
    sample_lut = load_sample_lut(sample_lut_path) if sample_lut_path is not None else None
    if sample_lut is not None:
        sample_c0 = apply_sample_lut(signed_to_sample(clamp_signed_to_code_range(signed_c0)), sample_lut)
        sample_c1 = apply_sample_lut(signed_to_sample(clamp_signed_to_code_range(signed_c1)), sample_lut)
        sample_c2 = apply_sample_lut(signed_to_sample(clamp_signed_to_code_range(signed_c2)), sample_lut)
    else:
        sample_c0 = code_c0
        sample_c1 = code_c1
        sample_c2 = code_c2
    raw = recombine_rggb(sample_c0, sample_c1, sample_c2)
    meta["timings"]["total_decode_and_recombine_s"] = time.perf_counter() - t0

    c0_path = out_dir / f"{stem}_llvc3_pure_v0_c0.bin"
    c1_path = out_dir / f"{stem}_llvc3_pure_v0_c1.bin"
    c2_path = out_dir / f"{stem}_llvc3_pure_v0_c2.bin"
    raw_path = out_dir / f"{stem}_llvc3_pure_rggb_{raw_info.width}x{raw_info.height}_u16.raw"
    preview_path = out_dir / f"{stem}_llvc3_pure_preview.png"
    tiff_path = out_dir / f"{stem}_llvc3_pure_bl1024_wl16383_rggb.tiff"
    shifted_tiff_path = out_dir / f"{stem}_llvc3_pure_bl512_wl15871_rggb_shifted.tiff"

    sample_c0.tofile(c0_path)
    sample_c1.tofile(c1_path)
    sample_c2.tofile(c2_path)
    raw.tofile(raw_path)
    robust_preview(raw, black, white).save(preview_path)
    write_dng(tiff_path, raw, black, white)
    shifted = np.clip(raw.astype(np.int32) - (black - shifted_black), 0, white).astype(np.uint16)
    write_dng(shifted_tiff_path, shifted, shifted_black, white - (black - shifted_black))

    summary: dict[str, Any] = {
        "input": str(arw),
        "raw_subifd": raw_info.__dict__,
        "llvc_header": header.__dict__,
        "blue_edge_fix": blue_edge_fix,
        "sample_lut": {
            "path": str(sample_lut_path) if sample_lut_path is not None else None,
            "enabled": sample_lut is not None,
            "size": int(sample_lut.size) if sample_lut is not None else 0,
        },
        "outputs": {
            "c0_green_u16": str(c0_path),
            "c1_red_u16": str(c1_path),
            "c2_blue_u16": str(c2_path),
            "rggb_raw_u16": str(raw_path),
            "preview_png": str(preview_path),
            "dng_like_bl1024_tiff": str(tiff_path),
            "dng_like_bl512_shifted_tiff": str(shifted_tiff_path),
        },
        "code_plane_stats": {"c0": stats(code_c0), "c1": stats(code_c1), "c2": stats(code_c2)},
        "sample_plane_stats": {"c0": stats(sample_c0), "c1": stats(sample_c1), "c2": stats(sample_c2), "raw": stats(raw)},
        **meta,
    }

    if verify_native_prefix is not None:
        summary["native_verification"] = {
            "c0": compare_plane(sample_c0, Path(f"{verify_native_prefix}_c0.bin"), sample_c0.shape),
            "c1": compare_plane(sample_c1, Path(f"{verify_native_prefix}_c1.bin"), sample_c1.shape),
            "c2": compare_plane(sample_c2, Path(f"{verify_native_prefix}_c2.bin"), sample_c2.shape),
        }
        summary["native_verification"]["all_exact"] = all(
            summary["native_verification"][name]["exact"] for name in ("c0", "c1", "c2")
        )

    summary_path = out_dir / f"{stem}_llvc3_pure_summary.json"
    summary["outputs"]["summary_json"] = str(summary_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("arw", nargs="?", default="DSC00089.ARW")
    ap.add_argument("--out-dir", default="out/pure_decode")
    ap.add_argument("--verify-native-prefix", default="")
    ap.add_argument("--sample-lut", default="", help="optional 65536-entry uint16 code-to-sample LUT")
    ap.add_argument("--no-sample-lut", action="store_true", help="emit unsigned LLVC3 code-domain samples without Sony LUT expansion")
    ap.add_argument("--no-blue-edge-fix", action="store_true")
    ap.add_argument("--black", type=int, default=1024)
    ap.add_argument("--white", type=int, default=16383)
    ap.add_argument("--shifted-black", type=int, default=512)
    ns = ap.parse_args()

    summary = decode_to_files(
        Path(ns.arw),
        Path(ns.out_dir),
        Path(ns.verify_native_prefix) if ns.verify_native_prefix else None,
        None if ns.no_sample_lut else (Path(ns.sample_lut) if ns.sample_lut else None),
        blue_edge_fix=not ns.no_blue_edge_fix,
        black=ns.black,
        white=ns.white,
        shifted_black=ns.shifted_black,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
