#!/usr/bin/env python3
"""Diagnose LLVC3 v0 edge differences against a native Sony decoder dump.

The pure decoder can be essentially exact in the image interior while still
showing large top/bottom guard-row differences. This script compares code-domain
planes, after inverse-mapping the native LUT-expanded dump back to LLVC3 codes,
and reports where the remaining differences live.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np


PLANE_NAMES = ("c0", "c1", "c2")


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


def resolve_existing(path_text: str, base: Path) -> Path:
    path = Path(path_text)
    if path.exists() or path.is_absolute():
        return path
    candidate = base / path
    if candidate.exists():
        return candidate
    return path


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def infer_native_shape(native_probe: dict[str, Any] | None, plane: str) -> tuple[int, int] | None:
    if not native_probe:
        return None
    results = native_probe.get("results") or []
    if not results:
        return None
    first = results[0]
    widths = first.get("widths") or []
    heights = first.get("heights") or []
    index = PLANE_NAMES.index(plane)
    if index >= len(widths) or index >= len(heights):
        return None
    return int(heights[index]), int(widths[index])


def stream_plane_origin(summary: dict[str, Any], stream_index: int, plane: str) -> tuple[int, int]:
    streams = summary.get("llvc_streams") or []
    if stream_index < 0 or stream_index >= len(streams):
        raise ValueError(f"stream_index {stream_index} out of range for {len(streams)} streams")
    stream = streams[stream_index]
    x_divisor = 1 if plane == "c0" else 2
    # Stream tile coordinates are full raw coordinates. v0 planes are half-height.
    return int(stream.get("tile_y", 0)) // 2, int(stream.get("tile_x", 0)) // x_divisor


def contiguous_segments(indices: np.ndarray) -> list[list[int]]:
    if indices.size == 0:
        return []
    segments: list[list[int]] = []
    start = prev = int(indices[0])
    for value in indices[1:]:
        cur = int(value)
        if cur == prev + 1:
            prev = cur
            continue
        segments.append([start, prev])
        start = prev = cur
    segments.append([start, prev])
    return segments


def summarize_bands(absdiff: np.ndarray, top_rows: int, bottom_rows: int) -> dict[str, list[dict[str, Any]]]:
    h = absdiff.shape[0]
    top: list[dict[str, Any]] = []
    bottom: list[dict[str, Any]] = []
    for y in range(min(top_rows, h)):
        row = absdiff[y]
        top.append(
            {
                "y": y,
                "nonzero": int(np.count_nonzero(row)),
                "gt32": int(np.count_nonzero(row > 32)),
                "mean_abs": float(row.mean()),
                "max_abs": int(row.max()),
            }
        )
    for y in range(max(0, h - bottom_rows), h):
        row = absdiff[y]
        bottom.append(
            {
                "y": y,
                "nonzero": int(np.count_nonzero(row)),
                "gt32": int(np.count_nonzero(row > 32)),
                "mean_abs": float(row.mean()),
                "max_abs": int(row.max()),
            }
        )
    return {"top_rows": top, "bottom_rows": bottom}


def top_abs_entries(
    pred: np.ndarray, native_code: np.ndarray, native_sample: np.ndarray, diff: np.ndarray, limit: int
) -> list[dict[str, Any]]:
    absdiff = np.abs(diff)
    if absdiff.size == 0 or limit <= 0:
        return []
    limit = min(limit, absdiff.size)
    flat = np.argpartition(absdiff.ravel(), -limit)[-limit:]
    out: list[dict[str, Any]] = []
    for index in flat[np.argsort(-absdiff.ravel()[flat])]:
        y, x = np.unravel_index(int(index), absdiff.shape)
        out.append(
            {
                "y": int(y),
                "x": int(x),
                "pred_code": int(pred[y, x]),
                "native_code": int(native_code[y, x]),
                "native_sample": int(native_sample[y, x]),
                "diff": int(diff[y, x]),
                "abs": int(absdiff[y, x]),
            }
        )
    return out


def compare_plane(
    pure_path: Path,
    pure_shape: tuple[int, int],
    native_path: Path,
    native_shape: tuple[int, int],
    origin: tuple[int, int],
    inv: np.ndarray,
    pure_is_lut_sample: bool,
    native_is_code: bool,
    top_rows: int,
    bottom_rows: int,
    top_abs: int,
) -> dict[str, Any]:
    pure = np.fromfile(pure_path, dtype="<u2")
    if pure.size != pure_shape[0] * pure_shape[1]:
        raise ValueError(f"{pure_path} has {pure.size} samples, expected {pure_shape[0] * pure_shape[1]}")
    pure = pure.reshape(pure_shape)

    native_sample = np.fromfile(native_path, dtype="<u2")
    if native_sample.size != native_shape[0] * native_shape[1]:
        raise ValueError(f"{native_path} has {native_sample.size} samples, expected {native_shape[0] * native_shape[1]}")
    native_sample = native_sample.reshape(native_shape)

    y0, x0 = origin
    pred = pure[y0 : y0 + native_shape[0], x0 : x0 + native_shape[1]]
    if pred.shape != native_shape:
        raise ValueError(
            f"{pure_path} tile slice at origin {origin} has shape {pred.shape}, expected {native_shape}"
        )
    pred_code = inv_lut(pred, inv) if pure_is_lut_sample else np.clip(pred.astype(np.int32), 0, 4095)
    native_code = native_sample.astype(np.int32) if native_is_code else inv_lut(native_sample, inv)
    diff = pred_code - native_code
    absdiff = np.abs(diff)
    high = absdiff > 32
    nonzero_rows = np.flatnonzero(np.any(absdiff != 0, axis=1))
    high_rows = np.flatnonzero(np.any(high, axis=1))
    zero_rows = np.flatnonzero(np.all(absdiff == 0, axis=1))

    vals, counts = np.unique(diff[diff != 0], return_counts=True)
    order = np.argsort(-counts) if vals.size else np.array([], dtype=np.int64)

    out = {
        "pure_path": str(pure_path),
        "native_path": str(native_path),
        "pure_shape": list(pure_shape),
        "native_shape": list(native_shape),
        "origin": list(origin),
        "nonzero": int(np.count_nonzero(absdiff)),
        "gt32": int(np.count_nonzero(high)),
        "mean_abs": float(absdiff.mean()),
        "max_abs": int(absdiff.max()) if absdiff.size else 0,
        "nonzero_row_segments": contiguous_segments(nonzero_rows),
        "gt32_row_segments": contiguous_segments(high_rows),
        "exact_row_segments": contiguous_segments(zero_rows),
        "top_diff_values": [
            {"diff": int(vals[i]), "count": int(counts[i])} for i in order[:20]
        ],
        "top_abs": top_abs_entries(pred_code, native_code, native_sample, diff, top_abs),
    }
    out.update(summarize_bands(absdiff, top_rows, bottom_rows))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pure-summary", required=True, help="summary JSON from llvc3_pure_decode.py")
    ap.add_argument("--native-prefix", required=True, help="prefix such as out/.../native_tile0_v0")
    ap.add_argument("--native-probe", default="", help="optional native probe JSON with output widths/heights")
    ap.add_argument("--stream-index", type=int, default=0)
    ap.add_argument("--lut", default=str(Path(__file__).with_name("data") / "sony_llvc3_static_lut4096_padded_u16.bin"))
    ap.add_argument("--pure-is-lut-sample", action="store_true", help="inverse-LUT pure planes before comparing")
    ap.add_argument("--native-is-code", action="store_true", help="do not inverse-LUT native planes")
    ap.add_argument("--top-rows", type=int, default=12)
    ap.add_argument("--bottom-rows", type=int, default=12)
    ap.add_argument("--top-abs", type=int, default=16)
    ap.add_argument("--out", default="")
    ns = ap.parse_args()

    summary_path = Path(ns.pure_summary)
    summary = load_json(summary_path)
    base = Path.cwd()
    outputs = summary.get("outputs") or {}
    pure_paths = {
        "c0": resolve_existing(outputs["c0_green_u16"], base),
        "c1": resolve_existing(outputs["c1_red_u16"], base),
        "c2": resolve_existing(outputs["c2_blue_u16"], base),
    }
    pure_shapes = {
        name: tuple(int(v) for v in summary["sample_plane_stats"][name]["shape"])
        for name in PLANE_NAMES
    }
    native_probe_path = Path(ns.native_probe) if ns.native_probe else Path(ns.native_prefix).with_name("native_tile0_probe.json")
    native_probe = load_json(native_probe_path) if native_probe_path.exists() else None
    inv = build_inverse_lut(load_lut(Path(ns.lut)))
    pure_is_lut_sample = ns.pure_is_lut_sample or bool((summary.get("sample_lut") or {}).get("enabled"))

    planes: dict[str, Any] = {}
    for name in PLANE_NAMES:
        native_shape = infer_native_shape(native_probe, name)
        if native_shape is None:
            origin = stream_plane_origin(summary, ns.stream_index, name)
            tile_h = int((summary["llvc_streams"][ns.stream_index]["header"]["logical_height"]) // 2)
            tile_w = int(summary["llvc_streams"][ns.stream_index]["header"]["coded_width"])
            native_shape = (tile_h, tile_w if name == "c0" else tile_w // 2)
        else:
            origin = stream_plane_origin(summary, ns.stream_index, name)
        native_path = Path(f"{ns.native_prefix}_{name}.bin")
        planes[name] = compare_plane(
            pure_paths[name],
            pure_shapes[name],
            native_path,
            native_shape,
            origin,
            inv,
            pure_is_lut_sample=pure_is_lut_sample,
            native_is_code=ns.native_is_code,
            top_rows=ns.top_rows,
            bottom_rows=ns.bottom_rows,
            top_abs=ns.top_abs,
        )

    result = {
        "pure_summary": str(summary_path),
        "native_prefix": ns.native_prefix,
        "native_probe": str(native_probe_path) if native_probe_path.exists() else None,
        "stream_index": ns.stream_index,
        "comparison_domain": {
            "pure": "inverse_lut_sample_to_code" if pure_is_lut_sample else "code",
            "native": "code" if ns.native_is_code else "inverse_lut_sample_to_code",
            "gt32_threshold_code": 32,
        },
        "planes": planes,
        "totals": {
            "nonzero": int(sum(planes[name]["nonzero"] for name in PLANE_NAMES)),
            "gt32": int(sum(planes[name]["gt32"] for name in PLANE_NAMES)),
            "mean_abs_sum": float(sum(planes[name]["mean_abs"] for name in PLANE_NAMES)),
            "max_abs": int(max(planes[name]["max_abs"] for name in PLANE_NAMES)),
        },
    }

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if ns.out:
        Path(ns.out).parent.mkdir(parents=True, exist_ok=True)
        Path(ns.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
