#!/usr/bin/env python3
"""Compare decoded RAW/CFA outputs against a reference image.

This is a conformance helper for camera RAW decoder work. It compares two
uint16 CFA-domain images after optional crop/offset alignment and reports
overall, Bayer-site, highlight, and optional LUT-code-domain error statistics.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import tifffile


Shape = tuple[int, int]
Crop = tuple[int, int, int, int]


@dataclass
class ImageSpec:
    path: str
    shape: list[int]
    crop: list[int]
    add_offset: int


def parse_shape(text: str | None) -> Shape | None:
    if not text:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*[x,]\s*(\d+)\s*", text)
    if not m:
        raise ValueError(f"shape must be WxH or W,H, got {text!r}")
    width = int(m.group(1))
    height = int(m.group(2))
    if width <= 0 or height <= 0:
        raise ValueError(f"shape must be positive, got {text!r}")
    return height, width


def parse_crop(text: str | None, shape: Shape) -> Crop:
    height, width = shape
    if not text:
        return 0, 0, width, height
    m = re.fullmatch(r"\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*", text)
    if not m:
        raise ValueError(f"crop must be x,y,w,h, got {text!r}")
    x, y, w, h = (int(m.group(i)) for i in range(1, 5))
    if w <= 0 or h <= 0:
        raise ValueError(f"crop width/height must be positive, got {text!r}")
    if x < 0 or y < 0 or x + w > width or y + h > height:
        raise ValueError(f"crop {text!r} exceeds image shape {width}x{height}")
    return x, y, w, h


def infer_shape_from_name(path: Path) -> Shape | None:
    m = re.search(r"(\d{3,})x(\d{3,})", path.name)
    if not m:
        return None
    return int(m.group(2)), int(m.group(1))


def load_image(path: Path, shape: Shape | None) -> np.ndarray:
    suffix = path.suffix.lower()
    if suffix in {".tif", ".tiff", ".dng"}:
        arr = tifffile.imread(path)
        arr = np.asarray(arr)
        if arr.ndim == 3 and 1 in arr.shape:
            arr = np.squeeze(arr)
        if arr.ndim != 2:
            raise ValueError(f"{path} decoded to {arr.shape}, expected a 2D CFA image")
        if arr.dtype != np.uint16:
            arr = arr.astype(np.uint16, copy=False)
        return arr

    actual_shape = shape or infer_shape_from_name(path)
    if actual_shape is None:
        raise ValueError(f"{path} needs --shape/--candidate-shape/--reference-shape")
    height, width = actual_shape
    data = np.fromfile(path, dtype="<u2")
    expected = width * height
    if data.size != expected:
        raise ValueError(f"{path} has {data.size} uint16 samples, expected {expected}")
    return data.reshape((height, width))


def apply_crop_and_offset(arr: np.ndarray, crop: Crop, add_offset: int) -> np.ndarray:
    x, y, w, h = crop
    out = arr[y : y + h, x : x + w].astype(np.int32, copy=False)
    if add_offset:
        out = out + int(add_offset)
    return out


def bayer_masks(shape: Shape, pattern: str, x_offset: int, y_offset: int) -> dict[str, np.ndarray]:
    pattern = pattern.upper()
    if len(pattern) != 4 or any(ch not in "RGBG" for ch in pattern):
        raise ValueError(f"unsupported 2x2 CFA pattern {pattern!r}")
    labels = np.array([[pattern[0], pattern[1]], [pattern[2], pattern[3]]], dtype="<U2")
    height, width = shape
    yy, xx = np.indices((height, width))
    site = labels[(yy + y_offset) & 1, (xx + x_offset) & 1]

    masks: dict[str, np.ndarray] = {}
    for y in range(2):
        for x in range(2):
            label = labels[y, x]
            name = label if label != "G" else f"G{len([k for k in masks if k.startswith('G')])}"
            masks[name] = ((yy + y_offset) & 1 == y) & ((xx + x_offset) & 1 == x)
    masks["R"] = site == "R"
    masks["B"] = site == "B"
    masks["G"] = site == "G"
    return masks


def percentile(values: np.ndarray, q: float) -> float:
    if values.size == 0:
        return 0.0
    return float(np.percentile(values, q))


def diff_stats(candidate: np.ndarray, reference: np.ndarray, mask: np.ndarray | None, hist_limit: int) -> dict[str, Any]:
    if mask is not None:
        candidate = candidate[mask]
        reference = reference[mask]
    else:
        candidate = candidate.reshape(-1)
        reference = reference.reshape(-1)
    diff = candidate.astype(np.int64) - reference.astype(np.int64)
    absdiff = np.abs(diff)
    nonzero = np.flatnonzero(diff)
    result: dict[str, Any] = {
        "samples": int(diff.size),
        "exact": bool(nonzero.size == 0),
        "nonzero": int(nonzero.size),
        "nonzero_pct": float(nonzero.size / diff.size * 100.0) if diff.size else 0.0,
        "max_abs": int(absdiff.max()) if diff.size else 0,
        "mean_abs": float(absdiff.mean()) if diff.size else 0.0,
        "rmse": float(math.sqrt(float(np.mean(diff.astype(np.float64) ** 2)))) if diff.size else 0.0,
        "signed_mean": float(diff.mean()) if diff.size else 0.0,
        "abs_p50": percentile(absdiff, 50),
        "abs_p90": percentile(absdiff, 90),
        "abs_p99": percentile(absdiff, 99),
        "abs_p999": percentile(absdiff, 99.9),
    }
    if diff.size:
        result["abs_histogram"] = {str(i): int(np.count_nonzero(absdiff == i)) for i in range(hist_limit + 1)}
        result["abs_gt_hist_limit"] = int(np.count_nonzero(absdiff > hist_limit))
    if nonzero.size:
        idx = int(nonzero[0])
        result["first_mismatch_flat"] = idx
        result["first_mismatch"] = {
            "candidate": int(candidate[idx]),
            "reference": int(reference[idx]),
            "diff": int(diff[idx]),
        }
    return result


def first_mismatch_xy(candidate: np.ndarray, reference: np.ndarray) -> dict[str, Any] | None:
    diff = candidate.astype(np.int64) - reference.astype(np.int64)
    nz = np.flatnonzero(diff)
    if nz.size == 0:
        return None
    y, x = np.unravel_index(int(nz[0]), diff.shape)
    return {
        "x": int(x),
        "y": int(y),
        "candidate": int(candidate[y, x]),
        "reference": int(reference[y, x]),
        "diff": int(diff[y, x]),
    }


def load_lut(path: Path) -> np.ndarray:
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
    if lut.size == 0:
        raise ValueError(f"{path} has no LUT entries")
    return lut.astype(np.uint16, copy=False)


def build_inverse_lut(lut: np.ndarray) -> np.ndarray:
    table = np.asarray(lut[:4096], dtype=np.int32)
    inv = np.zeros(int(table.max()) + 1, dtype=np.int32)
    idx = 0
    for sample in range(inv.size):
        while idx + 1 < table.size and abs(int(table[idx + 1]) - sample) <= abs(int(table[idx]) - sample):
            idx += 1
        inv[sample] = idx
    return inv


def inverse_lut_samples(samples: np.ndarray, inv_lut: np.ndarray) -> np.ndarray:
    return inv_lut[np.clip(samples, 0, inv_lut.size - 1)]


def flatten_rows(section: str, stats_by_site: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for site, stats in stats_by_site.items():
        row = {"section": section, "site": site}
        for key in ("samples", "exact", "nonzero", "nonzero_pct", "max_abs", "mean_abs", "rmse", "signed_mean"):
            row[key] = stats.get(key)
        rows.append(row)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidate", required=True, type=Path, help="decoded output under test")
    ap.add_argument("--reference", required=True, type=Path, help="reference output")
    ap.add_argument("--shape", help="raw/bin shape for both inputs, WxH")
    ap.add_argument("--candidate-shape", help="raw/bin shape for candidate, WxH")
    ap.add_argument("--reference-shape", help="raw/bin shape for reference, WxH")
    ap.add_argument("--candidate-crop", help="candidate crop as x,y,w,h")
    ap.add_argument("--reference-crop", help="reference crop as x,y,w,h")
    ap.add_argument("--candidate-add", type=int, default=0, help="integer offset added to candidate before compare")
    ap.add_argument("--reference-add", type=int, default=0, help="integer offset added to reference before compare")
    ap.add_argument("--cfa", default="RGGB", help="2x2 CFA pattern for the compared common area")
    ap.add_argument("--cfa-offset", default="0,0", help="x,y phase offset for the compared common area")
    ap.add_argument("--highlight-threshold", type=int, default=14000, help="highlight subset threshold; <=0 disables")
    ap.add_argument("--lut", type=Path, help="optional code-to-sample LUT for inverse code-domain comparison")
    ap.add_argument("--hist-limit", type=int, default=16, help="absolute-diff histogram limit")
    ap.add_argument("--label", default="", help="free-form sample label")
    ap.add_argument("--out-json", type=Path, help="write full report JSON")
    ap.add_argument("--out-csv", type=Path, help="write compact per-section/site CSV")
    ns = ap.parse_args()

    shared_shape = parse_shape(ns.shape)
    cand = load_image(ns.candidate, parse_shape(ns.candidate_shape) or shared_shape)
    ref = load_image(ns.reference, parse_shape(ns.reference_shape) or shared_shape)
    cand_crop = parse_crop(ns.candidate_crop, cand.shape)
    ref_crop = parse_crop(ns.reference_crop, ref.shape)

    cand_cmp = apply_crop_and_offset(cand, cand_crop, ns.candidate_add)
    ref_cmp = apply_crop_and_offset(ref, ref_crop, ns.reference_add)
    if cand_cmp.shape != ref_cmp.shape:
        raise ValueError(f"aligned shapes differ: candidate {cand_cmp.shape}, reference {ref_cmp.shape}")

    cfa_x, cfa_y = (int(v) for v in ns.cfa_offset.split(","))
    masks = bayer_masks(cand_cmp.shape, ns.cfa, cfa_x, cfa_y)

    report: dict[str, Any] = {
        "label": ns.label,
        "candidate": asdict(ImageSpec(str(ns.candidate), list(cand.shape), list(cand_crop), ns.candidate_add)),
        "reference": asdict(ImageSpec(str(ns.reference), list(ref.shape), list(ref_crop), ns.reference_add)),
        "aligned_shape": list(cand_cmp.shape),
        "cfa": ns.cfa.upper(),
        "cfa_offset": [cfa_x, cfa_y],
        "overall": diff_stats(cand_cmp, ref_cmp, None, ns.hist_limit),
        "first_mismatch_xy": first_mismatch_xy(cand_cmp, ref_cmp),
        "by_site": {name: diff_stats(cand_cmp, ref_cmp, mask, ns.hist_limit) for name, mask in masks.items()},
    }

    if ns.highlight_threshold > 0:
        highlight = np.maximum(cand_cmp, ref_cmp) >= ns.highlight_threshold
        report["highlight_threshold"] = ns.highlight_threshold
        report["highlight"] = {
            "overall": diff_stats(cand_cmp, ref_cmp, highlight, ns.hist_limit),
            "by_site": {
                name: diff_stats(cand_cmp, ref_cmp, highlight & mask, ns.hist_limit)
                for name, mask in masks.items()
            },
        }

    if ns.lut:
        lut = load_lut(ns.lut)
        inv = build_inverse_lut(lut)
        cand_code = inverse_lut_samples(cand_cmp, inv)
        ref_code = inverse_lut_samples(ref_cmp, inv)
        report["lut_code_domain"] = {
            "lut": str(ns.lut),
            "overall": diff_stats(cand_code, ref_code, None, ns.hist_limit),
            "first_mismatch_xy": first_mismatch_xy(cand_code, ref_code),
            "by_site": {name: diff_stats(cand_code, ref_code, mask, ns.hist_limit) for name, mask in masks.items()},
        }
        if ns.highlight_threshold > 0:
            highlight = np.maximum(cand_cmp, ref_cmp) >= ns.highlight_threshold
            report["lut_code_domain"]["highlight"] = {
                "overall": diff_stats(cand_code, ref_code, highlight, ns.hist_limit),
                "by_site": {
                    name: diff_stats(cand_code, ref_code, highlight & mask, ns.hist_limit)
                    for name, mask in masks.items()
                },
            }

    text = {
        "label": report["label"],
        "shape": report["aligned_shape"],
        "overall_nonzero": report["overall"]["nonzero"],
        "overall_max_abs": report["overall"]["max_abs"],
        "overall_mean_abs": report["overall"]["mean_abs"],
        "first_mismatch_xy": report["first_mismatch_xy"],
    }
    print(json.dumps(text, ensure_ascii=False, indent=2))

    if ns.out_json:
        ns.out_json.parent.mkdir(parents=True, exist_ok=True)
        ns.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if ns.out_csv:
        ns.out_csv.parent.mkdir(parents=True, exist_ok=True)
        rows = flatten_rows("sample", {"overall": report["overall"], **report["by_site"]})
        if "highlight" in report:
            rows.extend(flatten_rows("highlight", {"overall": report["highlight"]["overall"], **report["highlight"]["by_site"]}))
        if "lut_code_domain" in report:
            rows.extend(flatten_rows("lut_code", {"overall": report["lut_code_domain"]["overall"], **report["lut_code_domain"]["by_site"]}))
            if "highlight" in report["lut_code_domain"]:
                rows.extend(
                    flatten_rows(
                        "lut_code_highlight",
                        {"overall": report["lut_code_domain"]["highlight"]["overall"], **report["lut_code_domain"]["highlight"]["by_site"]},
                    )
                )
        with ns.out_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    main()
