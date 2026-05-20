#!/usr/bin/env python3
"""Compare the pure decoder's code-domain layers against native dumps."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llvc3_bitstream_probe import find_raw_subifd
from llvc3_entropy import decode_packet_arrays, integrate_type1_coefficients
from llvc3_math import (
    finalize_llvc3_color_planes,
    synthesize_llvc3_final_green,
    synthesize_llvc3_level_stride,
)


def load_lut(path: Path) -> np.ndarray:
    if path.suffix.lower() == ".tsv":
        return np.loadtxt(path, skiprows=1, usecols=1, dtype=np.int32)
    data = np.fromfile(path, dtype="<u2").astype(np.int32)
    return data[:4096]


def build_inverse_lut(lut: np.ndarray) -> np.ndarray:
    maxv = int(lut.max())
    inv = np.zeros(maxv + 1, dtype=np.int32)
    idx = 0
    for sample in range(maxv + 1):
        while idx + 1 < lut.size and abs(int(lut[idx + 1]) - sample) <= abs(int(lut[idx]) - sample):
            idx += 1
        inv[sample] = idx
    return inv


def inv_lut(samples: np.ndarray, inv: np.ndarray) -> np.ndarray:
    return inv[np.clip(samples.astype(np.int32), 0, inv.size - 1)]


def clamp_code(x: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(x, dtype=np.int32), 0, 4095)


def compare_code(pred_code: np.ndarray, native_path: Path, inv: np.ndarray, shape: tuple[int, int]) -> dict[str, Any]:
    native = np.fromfile(native_path, dtype="<u2")
    if native.size != shape[0] * shape[1]:
        raise ValueError(f"{native_path} has {native.size} samples, expected {shape[0] * shape[1]}")
    native_code = inv_lut(native.reshape(shape), inv)
    pred = clamp_code(pred_code)
    diff = pred - native_code
    nz = np.argwhere(diff != 0)
    out: dict[str, Any] = {
        "native_path": str(native_path),
        "shape": list(shape),
        "nonzero": int(nz.shape[0]),
        "max_abs_code": int(np.max(np.abs(diff))),
        "mean_abs_code": float(np.mean(np.abs(diff))),
    }
    if nz.size:
        y, x = nz[0]
        out["first_mismatch"] = {
            "y": int(y),
            "x": int(x),
            "pred_code": int(pred[y, x]),
            "native_code": int(native_code[y, x]),
            "diff": int(diff[y, x]),
        }
        vals, counts = np.unique(diff[diff != 0], return_counts=True)
        order = np.argsort(-counts)
        out["top_diff_values"] = [
            {"diff": int(vals[i]), "count": int(counts[i])} for i in order[:16]
        ]
    return out


def decode_levels(arw: Path) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    raw_info, _ = find_raw_subifd(arw)
    low_rows = raw_info.height // 16

    g0, _ = decode_packet_arrays(arw, 0, 0)
    green = integrate_type1_coefficients(g0[0][:low_rows], 2048) - 2048
    r0, _ = decode_packet_arrays(arw, 0, 1)
    red_res = integrate_type1_coefficients(r0[0][:low_rows], 0)
    b0, _ = decode_packet_arrays(arw, 0, 2)
    blue_res = integrate_type1_coefficients(b0[0][:low_rows], 0)

    levels: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {"v4": (green, red_res, blue_res)}
    for group, edge_rows in ((1, 0), (2, 1), (3, 2)):
        old_green, old_red_res, old_blue_res = green, red_res, blue_res

        planes, _ = decode_packet_arrays(arw, group, 0)
        green = synthesize_llvc3_level_stride(old_green, planes[0], planes[1], planes[2], edge_rows)

        planes, _ = decode_packet_arrays(arw, group, 1)
        edge_mode = "odd" if group == 3 else "even"
        red_res = synthesize_llvc3_level_stride(old_red_res, planes[0], planes[1], planes[2], edge_rows, edge_mode=edge_mode)

        planes, _ = decode_packet_arrays(arw, group, 2)
        blue_res = synthesize_llvc3_level_stride(old_blue_res, planes[0], planes[1], planes[2], edge_rows, edge_mode=edge_mode)

        levels[f"v{4 - group}"] = (green, red_res, blue_res)

    g4, _ = decode_packet_arrays(arw, 4, 0)
    full_green = synthesize_llvc3_final_green(green, g4[0])
    c0, c1, c2 = finalize_llvc3_color_planes(green, green + 2 * red_res, green + 2 * blue_res, full_green)
    levels["v0"] = (c0, (c1 - c0[:, 0::2]) // 2, (c2 - c0[:, 0::2]) // 2)
    levels["v0_planes"] = (c0, c1, c2)
    return levels


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("arw")
    ap.add_argument("--native-dir", default="out/crawhq_layers_native")
    ap.add_argument("--native-final-prefix", required=True)
    ap.add_argument("--lut", default="tools/data/sony_llvc3_static_lut4096.tsv")
    ap.add_argument("--out", default="")
    ns = ap.parse_args()

    arw = Path(ns.arw)
    inv = build_inverse_lut(load_lut(Path(ns.lut)))
    levels = decode_levels(arw)
    native_dir = Path(ns.native_dir)
    result: dict[str, Any] = {"input": str(arw), "layers": {}}

    for level in ("v4", "v3", "v2", "v1"):
        green, red_res, blue_res = levels[level]
        prefix = native_dir / f"{arw.stem.split('_')[0]}_{level}_{level}"
        result["layers"][level] = {
            "c0": compare_code(green + 2048, Path(f"{prefix}_c0.bin"), inv, green.shape),
            "c1": compare_code(green + 2 * red_res + 2048, Path(f"{prefix}_c1.bin"), inv, green.shape),
            "c2": compare_code(green + 2 * blue_res + 2048, Path(f"{prefix}_c2.bin"), inv, green.shape),
        }

    c0, c1, c2 = levels["v0_planes"]
    final_prefix = Path(ns.native_final_prefix)
    result["layers"]["v0"] = {
        "c0": compare_code(c0 + 2048, Path(f"{final_prefix}_c0.bin"), inv, c0.shape),
        "c1": compare_code(c1 + 2048, Path(f"{final_prefix}_c1.bin"), inv, c1.shape),
        "c2": compare_code(c2 + 2048, Path(f"{final_prefix}_c2.bin"), inv, c2.shape),
    }

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if ns.out:
        Path(ns.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
