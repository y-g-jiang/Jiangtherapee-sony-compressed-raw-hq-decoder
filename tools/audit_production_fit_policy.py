#!/usr/bin/env python3
"""Audit strict canonical points against real production bitstream anchors."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


SONY_CODEC = "sony_824_decoder_visible_packet_canonical"
NIKON_CODEC = "nikon_826_decoder_visible_precinct_canonical"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"no rows for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def as_float(value: str) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    if not math.isfinite(out):
        return None
    return out


def median(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return 0.5 * (values[mid - 1] + values[mid])


def fmt(value: float | None, digits: int = 6) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def load_real_controls(path: Path) -> tuple[list[float], dict[str, set[str]], dict[str, Any]]:
    rows = read_csv(path)
    sony_bpp: list[float] = []
    nikon_bp_by_bpp: dict[str, set[str]] = defaultdict(set)
    sony_patterns: set[str] = set()
    sony_type_counts: set[str] = set()
    sony_selector_hists: list[str] = []
    for row in rows:
        if row["codec_family"] == "sony_arw6_llvc3_hq":
            bpp = as_float(row["strip_bpp"])
            if bpp is not None:
                sony_bpp.append(bpp)
            if row["group_entry_pattern"]:
                sony_patterns.add(row["group_entry_pattern"])
            if row["packet_type_counts"]:
                sony_type_counts.add(row["packet_type_counts"])
            if row["selector_hist"]:
                sony_selector_hists.append(row["selector_hist"])
        elif row["codec_family"] == "nikon_he_jpeg_xs_like" and row["has_jpeg_xs_soc_cap"] == "true":
            if row["strip_bpp"] and row["first_precinct_bp"]:
                nikon_bp_by_bpp[row["strip_bpp"]].add(row["first_precinct_bp"])
    metadata = {
        "sony_packet_type_counts": sorted(sony_type_counts),
        "sony_group_entry_patterns": sorted(sony_patterns),
        "sony_selector_hist_samples": sony_selector_hists,
    }
    return sony_bpp, nikon_bp_by_bpp, metadata


def nearest(values: list[float], target: float) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    best = min(values, key=lambda x: abs(x - target))
    return best, abs(best - target)


def nikon_real_anchor_class(actual_bpp: float, nikon_bp_by_bpp: dict[str, set[str]]) -> tuple[str, str]:
    real_points = [(float(k), sorted(v)) for k, v in nikon_bp_by_bpp.items()]
    if not real_points:
        return "no_real_nikon_he_anchor", ""
    bpp, bps = min(real_points, key=lambda kv: abs(kv[0] - actual_bpp))
    bp_s = ",".join(bps)
    if abs(bpp - actual_bpp) <= 0.30:
        if any(bp in {"4", "5"} for bp in bps):
            return "near_supported_he_real_bpp", f"{bpp:.6f}/Bp={bp_s}"
        if any(bp in {"1", "2", "3"} for bp in bps):
            return "near_unsupported_he_star_real_bpp", f"{bpp:.6f}/Bp={bp_s}"
    return "canonical_only_rate_no_close_real_anchor", f"{bpp:.6f}/Bp={bp_s}"


def audit(strict_dir: Path, controls_csv: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rate_rows = read_csv(strict_dir / "rate_summary.csv")
    sony_real_bpp, nikon_bp_by_bpp, metadata = load_real_controls(controls_csv)
    rows: list[dict[str, Any]] = []
    for row in rate_rows:
        actual = as_float(row["actual_bpp_median"])
        target = as_float(row["target_bpp"])
        if actual is None or target is None:
            continue
        codec = row["codec"]
        if codec == SONY_CODEC:
            anchor_bpp, delta = nearest(sony_real_bpp, actual)
            fit = "near_real_hq_strip_bpp" if delta is not None and delta <= 0.5 else "canonical_only_rate_outside_hq_sample_range"
            anchor = fmt(anchor_bpp)
        elif codec == NIKON_CODEC:
            fit, anchor = nikon_real_anchor_class(actual, nikon_bp_by_bpp)
            delta = None
            if anchor:
                delta = abs(float(anchor.split("/", 1)[0]) - actual)
        else:
            continue
        rows.append(
            {
                "codec": codec,
                "target_bpp": row["target_bpp"],
                "actual_bpp_median": row["actual_bpp_median"],
                "real_anchor": anchor,
                "abs_delta_to_anchor_bpp": fmt(delta),
                "production_fit_class": fit,
                "median_PSNR_raw_whole": row.get("median_PSNR_raw_whole", ""),
                "median_MAE_whole": row.get("median_MAE_whole", ""),
                "interpretation": interpretation(codec, fit),
            }
        )
    summary = {
        "strict_dir": str(strict_dir),
        "controls_csv": str(controls_csv),
        "sony_real_bpp": sony_real_bpp,
        "nikon_jpeg_xs_bp_by_bpp": {k: sorted(v) for k, v in nikon_bp_by_bpp.items()},
        "metadata": metadata,
        "class_counts": count_classes(rows),
    }
    return rows, summary


def interpretation(codec: str, fit: str) -> str:
    if codec == SONY_CODEC and fit == "near_real_hq_strip_bpp":
        return "Rate point is close to a real Sony HQ strip bpp; syntax shape still remains canonical unless packet-control distributions are matched."
    if codec == SONY_CODEC:
        return "Rate point is useful for canonical RD shape but is outside the two downloaded real HQ strip-bpp anchors."
    if fit == "near_supported_he_real_bpp":
        return "Rate point is close to a real Nikon HE supported-path bpp/Bp anchor."
    if fit == "near_unsupported_he_star_real_bpp":
        return "Rate point is close to a real Nikon HE* bpp/Bp anchor, but that is outside the current supported #826 HE decoder path."
    return "Rate point is a decoder-visible canonical operating point without a close real Nikon HE/HE* anchor in this sample set."


def count_classes(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row["production_fit_class"])
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict-dir", type=Path, default=Path("out/strict_824_826_math_eval_full_20260603"))
    ap.add_argument("--controls-csv", type=Path, default=Path("out/production_fit_samples/real_bitstream_controls.csv"))
    ap.add_argument("--out-csv", type=Path, default=Path("out/production_fit_samples/production_fit_policy_audit.csv"))
    ap.add_argument("--out-json", type=Path, default=Path("out/production_fit_samples/production_fit_policy_audit.json"))
    ns = ap.parse_args()

    rows, summary = audit(ns.strict_dir, ns.controls_csv)
    write_csv(ns.out_csv, rows)
    summary["rows_csv"] = str(ns.out_csv)
    ns.out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
