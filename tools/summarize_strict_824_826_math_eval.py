#!/usr/bin/env python3
"""Summarize strict #824/#826 math-eval CSV files into paper_numbers.json."""

from __future__ import annotations

import csv
import json
import math
from argparse import ArgumentParser
from pathlib import Path
from statistics import median


SONY = "sony_824_decoder_visible_packet_canonical"
NIKON = "nikon_826_decoder_visible_precinct_canonical"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def as_float(value: str) -> float:
    if value == "inf":
        return math.inf
    return float(value)


def rate_by_target(rows: list[dict[str, str]]) -> dict[str, dict[str, dict[str, float]]]:
    out: dict[str, dict[str, dict[str, float]]] = {}
    for row in rows:
        target = row["target_bpp"]
        codec = row["codec"]
        out.setdefault(target, {})[codec] = {
            "min": as_float(row["actual_bpp_min"]),
            "median": as_float(row["actual_bpp_median"]),
            "max": as_float(row["actual_bpp_max"]),
            "psnr": as_float(row["median_PSNR_raw_whole"]),
            "mae": as_float(row["median_MAE_whole"]),
            "grad_psnr": as_float(row["median_grad_psnr_detail"]),
            "ssim": as_float(row["median_ssim_mean_detail"]),
            "ms_ssim": as_float(row.get("median_ms_ssim_mean_detail", "")) if row.get("median_ms_ssim_mean_detail", "") else math.nan,
            "gmsd": as_float(row.get("median_gmsd_mean_detail", "")) if row.get("median_gmsd_mean_detail", "") else math.nan,
        }
    return out


def target_request_medians(rows: list[dict[str, str]]) -> dict[str, dict[str, dict[str, float]]]:
    out: dict[str, dict[str, dict[str, float]]] = {}
    for row in rows:
        target = row["target_bpp"]
        metric = row["metric"]
        out.setdefault(target, {})[metric] = {
            "median_sony": as_float(row["median_sony"]),
            "median_nikon": as_float(row["median_nikon"]),
            "median_sony_minus_nikon": as_float(row["median_sony_minus_nikon"]),
        }
    return out


def bd_rows(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in read_csv(path):
        rows.append(
            {
                "codec_a": row["codec_a"],
                "codec_b": row["codec_b"],
                "metric": row["metric"],
                "group": row["group"],
                "ok_sources": int(row["ok_sources"]),
                "skipped_sources": int(row["skipped_sources"]),
                "median_bd_rate": as_float(row["median_bd_rate"]),
                "p025_bd_rate": as_float(row["p025_bd_rate"]),
                "p975_bd_rate": as_float(row["p975_bd_rate"]),
            }
        )
    return rows


def roundtrip_summary(rows: list[dict[str, str]]) -> dict[str, dict[str, dict[str, float]]]:
    by_codec: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_codec.setdefault(row["codec"], []).append(row)
    out: dict[str, dict[str, dict[str, float]]] = {}
    for codec, codec_rows in by_codec.items():
        out[codec] = {}
        for metric in ["MSE", "MAE", "MAX", "PSNR_raw"]:
            vals = [as_float(row[metric]) for row in codec_rows]
            out[codec][metric] = {
                "max": max(vals),
                "median": median(vals),
            }
    return out


def find_group(rows: list[dict[str, object]], group: str) -> dict[str, object]:
    for row in rows:
        if row["group"] == group:
            return row
    raise KeyError(group)


def main() -> int:
    ap = ArgumentParser(description=__doc__)
    ap.add_argument("--math-dir", type=Path, required=True)
    ap.add_argument("--audit", type=Path, default=Path("out/strict_824_826_encoder_reversibility/audit.json"))
    ap.add_argument("--out", type=Path)
    ns = ap.parse_args()

    math_dir = ns.math_dir
    manifest = json.loads((math_dir / "manifest.json").read_text(encoding="utf-8"))
    audit = json.loads(ns.audit.read_text(encoding="utf-8"))
    rates = rate_by_target(read_csv(math_dir / "rate_summary.csv"))
    target_medians = target_request_medians(read_csv(math_dir / "target_request_summary.csv"))
    bd_psnr = bd_rows(math_dir / "bd_rate_psnr.csv")
    bd_mae = bd_rows(math_dir / "bd_rate_mae.csv")
    bd_grad = bd_rows(math_dir / "bd_rate_grad_psnr.csv")
    bd_ssim = bd_rows(math_dir / "bd_rate_ssim.csv")
    bd_ms_ssim = bd_rows(math_dir / "bd_rate_ms_ssim.csv")
    bd_gmsd = bd_rows(math_dir / "bd_rate_gmsd.csv")
    whole_psnr = find_group(bd_psnr, "whole")
    whole_mae = find_group(bd_mae, "whole")
    detail_grad = find_group(bd_grad, "detail")
    detail_ssim = find_group(bd_ssim, "detail")
    detail_ms_ssim = find_group(bd_ms_ssim, "detail")
    detail_gmsd = find_group(bd_gmsd, "detail")

    selected = {
        "whole_psnr_bd_median_percent": float(whole_psnr["median_bd_rate"]) * 100.0,
        "whole_psnr_p025_percent": float(whole_psnr["p025_bd_rate"]) * 100.0,
        "whole_psnr_p975_percent": float(whole_psnr["p975_bd_rate"]) * 100.0,
        "whole_psnr_ok_sources": int(whole_psnr["ok_sources"]),
        "whole_psnr_skipped_sources": int(whole_psnr["skipped_sources"]),
        "whole_mae_bd_median_percent": float(whole_mae["median_bd_rate"]) * 100.0,
        "whole_mae_p025_percent": float(whole_mae["p025_bd_rate"]) * 100.0,
        "whole_mae_p975_percent": float(whole_mae["p975_bd_rate"]) * 100.0,
        "detail_grad_psnr_bd_median_percent": float(detail_grad["median_bd_rate"]) * 100.0,
        "detail_grad_psnr_ok_sources": int(detail_grad["ok_sources"]),
        "detail_ssim_bd_median_percent": float(detail_ssim["median_bd_rate"]) * 100.0,
        "detail_ssim_ok_sources": int(detail_ssim["ok_sources"]),
        "detail_ms_ssim_bd_median_percent": float(detail_ms_ssim["median_bd_rate"]) * 100.0,
        "detail_ms_ssim_ok_sources": int(detail_ms_ssim["ok_sources"]),
        "detail_gmsd_bd_median_percent": float(detail_gmsd["median_bd_rate"]) * 100.0,
        "detail_gmsd_ok_sources": int(detail_gmsd["ok_sources"]),
    }
    for target in ["2.500000", "3.000000", "4.000000", "5.000000"]:
        selected[f"target_{target[:3].replace('.', 'p')}_actual_sony_median"] = rates[target][SONY]["median"]
        selected[f"target_{target[:3].replace('.', 'p')}_actual_nikon_median"] = rates[target][NIKON]["median"]
        selected[f"target_{target[:3].replace('.', 'p')}_psnr_sony"] = rates[target][SONY]["psnr"]
        selected[f"target_{target[:3].replace('.', 'p')}_psnr_nikon"] = rates[target][NIKON]["psnr"]
        selected[f"target_{target[:3].replace('.', 'p')}_mae_sony"] = rates[target][SONY]["mae"]
        selected[f"target_{target[:3].replace('.', 'p')}_mae_nikon"] = rates[target][NIKON]["mae"]

    summary = {
        "manifest": manifest,
        "audit": audit.get("status_counts", {}),
        "roundtrip": roundtrip_summary(read_csv(math_dir / "roundtrip_audit.csv")),
        "rates_by_target": rates,
        "target_request_medians": target_medians,
        "bd_rate_psnr": bd_psnr,
        "bd_rate_mae": bd_mae,
        "bd_rate_grad_psnr": bd_grad,
        "bd_rate_ssim": bd_ssim,
        "bd_rate_ms_ssim": bd_ms_ssim,
        "bd_rate_gmsd": bd_gmsd,
        "selected": selected,
    }
    out = ns.out or (math_dir / "paper_numbers.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"paper_numbers": str(out), "selected": selected}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
