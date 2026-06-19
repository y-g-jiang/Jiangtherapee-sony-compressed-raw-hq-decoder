#!/usr/bin/env python3
"""Summarize real ARW/NEF bitstream probes for production-fit constraints.

This tool does not decode pixels and does not claim production encoder
equivalence. It turns already-auditable probe JSON files into a compact
fingerprint of the real container/syntax controls that a decoder-visible
canonical encoder should respect.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


RAW_PIXLS_PROVENANCE = {
    "Sony_ILCE-7M5_full_compressed_HQ.ARW": {
        "origin": "raw.pixls.us",
        "url": "https://raw.pixls.us/data/SONY/ILCE-7M5/full_compressed_HQ.ARW",
        "role": "Sony ARW6 CRAW HQ full-frame single-stream production sample",
    },
    "Sony_ILCE-7M5_apsc_compressed_HQ.ARW": {
        "origin": "raw.pixls.us",
        "url": "https://raw.pixls.us/data/SONY/ILCE-7M5/apcs_compressed_hq.ARW",
        "role": "Sony ARW6 CRAW HQ APS-C single-stream production sample",
    },
    "Nikon_Z8_high_efficiency_low.NEF": {
        "origin": "raw.pixls.us",
        "url": "https://raw.pixls.us/data/Nikon/Z%208/Nikon_Z8_high_efficiency_low.NEF",
        "role": "Nikon Z8 HE 3 bpp supported-path sample under PR #826 heuristic",
    },
    "Nikon_Z8_high_efficiency_high.NEF": {
        "origin": "raw.pixls.us",
        "url": "https://raw.pixls.us/data/Nikon/Z%208/Nikon_Z8_raw_high_efficiency_hight.NEF",
        "role": "Nikon Z8 HE* 5 bpp unsupported-path sample under PR #826 heuristic",
    },
}


CSV_FIELDS = [
    "codec_family",
    "sample",
    "origin",
    "role",
    "source_url",
    "model",
    "raw_size",
    "file_size",
    "strip_byte_count",
    "strip_bpp",
    "ratio_vs_14bit_packed",
    "stream_count",
    "llvc_magic",
    "llvc_sequence_or_version",
    "llvc_mode",
    "packet_count",
    "packet_type_counts",
    "all_packet_validations_pass",
    "group_entry_pattern",
    "group_byte_fractions",
    "records_total",
    "block_count_sum",
    "control_count_unique",
    "width_marker_unique",
    "selector_hist",
    "selector_mean",
    "first_precinct_bp",
    "has_jpeg_xs_soc_cap",
    "inferred_variant",
    "fit_class",
    "probe_json",
]


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def sample_name(input_path: str) -> str:
    return Path(input_path).name


def provenance_for(name: str) -> dict[str, str]:
    return RAW_PIXLS_PROVENANCE.get(
        name,
        {
            "origin": "local",
            "url": "",
            "role": "local negative or coverage sample",
        },
    )


def fmt_jsonish(value: Any) -> str:
    if value in (None, ""):
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def finite_or_blank(value: Any, digits: int = 6) -> str:
    if value is None:
        return ""
    try:
        f = float(value)
    except Exception:
        return str(value)
    if not math.isfinite(f):
        return ""
    return f"{f:.{digits}f}"


def summarize_sony(path: Path, report: dict[str, Any]) -> list[dict[str, str]]:
    packets = report.get("packets", [])
    groups = report.get("directory", {}).get("groups", [])
    summary = report.get("summary", {})
    metrics = summary.get("metrics", {})
    raw = report.get("raw_subifd", {})
    header = report.get("llvc_header", {})
    streams = report.get("llvc_streams", [])

    packet_types = Counter(str(pkt.get("type2")) for pkt in packets)
    selectors: Counter[str] = Counter()
    block_counts: list[int] = []
    control_counts: set[int] = set()
    width_markers: set[int] = set()
    for pkt in packets:
        block_counts.append(int(pkt.get("block_count", 0)))
        control_counts.add(int(pkt.get("control_count", 0)))
        width_markers.add(int(pkt.get("width_marker", 0)))
        for record in pkt.get("records", []):
            for selector in record.get("selectors", []):
                selectors[str(selector)] += 1

    selector_total = sum(selectors.values())
    selector_mean = ""
    if selector_total:
        selector_mean = f"{sum(int(k) * v for k, v in selectors.items()) / selector_total:.6f}"

    total_group_bytes = sum(int(g.get("declared_length", 0)) for g in groups) or 1
    group_fracs = [round(int(g.get("declared_length", 0)) / total_group_bytes, 6) for g in groups]
    name = sample_name(str(report.get("input", path.name)))
    prov = provenance_for(name)

    row = {
        "codec_family": "sony_arw6_llvc3_hq",
        "sample": name,
        "origin": prov["origin"],
        "role": prov["role"],
        "source_url": prov["url"],
        "model": "",
        "raw_size": f"{raw.get('width', '')}x{raw.get('height', '')}",
        "file_size": "",
        "strip_byte_count": str(raw.get("strip_byte_count", "")),
        "strip_bpp": finite_or_blank(metrics.get("strip_bits_per_sample")),
        "ratio_vs_14bit_packed": finite_or_blank(metrics.get("compression_ratio_vs_14bit_packed")),
        "stream_count": str(len(streams)),
        "llvc_magic": str(header.get("magic", "")),
        "llvc_sequence_or_version": str(header.get("sequence_or_version", "")),
        "llvc_mode": str(header.get("mode", "")),
        "packet_count": str(len(packets)),
        "packet_type_counts": fmt_jsonish(dict(sorted(packet_types.items()))),
        "all_packet_validations_pass": str(bool(summary.get("all_packet_validations_pass", False))).lower(),
        "group_entry_pattern": fmt_jsonish([g.get("entry_count") for g in groups]),
        "group_byte_fractions": fmt_jsonish(group_fracs),
        "records_total": str(sum(block_counts)),
        "block_count_sum": str(sum(block_counts)),
        "control_count_unique": fmt_jsonish(sorted(control_counts)),
        "width_marker_unique": fmt_jsonish(sorted(width_markers)),
        "selector_hist": fmt_jsonish(dict(sorted(selectors.items(), key=lambda kv: int(kv[0])))),
        "selector_mean": selector_mean,
        "first_precinct_bp": "",
        "has_jpeg_xs_soc_cap": "",
        "inferred_variant": "",
        "fit_class": "real_packet_syntax_anchor",
        "probe_json": str(path),
    }
    return [row]


def nikon_fit_class(candidate: dict[str, Any]) -> str:
    if not candidate.get("has_jpeg_xs_soc_cap"):
        return "negative_not_he_marker"
    bp = candidate.get("first_precinct_bp")
    if bp in {4, 5}:
        return "he_supported_path"
    if bp in {1, 2, 3}:
        return "he_star_unsupported_path"
    return "unknown_jpeg_xs_like"


def summarize_nikon(path: Path, report: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    name = sample_name(str(report.get("input", path.name)))
    prov = provenance_for(name)
    for candidate in report.get("raw_candidates", []):
        rows.append(
            {
                "codec_family": "nikon_he_jpeg_xs_like",
                "sample": name,
                "origin": prov["origin"],
                "role": prov["role"],
                "source_url": prov["url"],
                "model": str(candidate.get("model", "")),
                "raw_size": f"{candidate.get('width', '')}x{candidate.get('height', '')}",
                "file_size": str(report.get("file_size", "")),
                "strip_byte_count": str(candidate.get("strip_byte_count", "")),
                "strip_bpp": finite_or_blank(candidate.get("strip_bits_per_sample")),
                "ratio_vs_14bit_packed": finite_or_blank(candidate.get("compression_ratio_vs_14bit_packed")),
                "stream_count": "",
                "llvc_magic": "",
                "llvc_sequence_or_version": "",
                "llvc_mode": "",
                "packet_count": "",
                "packet_type_counts": "",
                "all_packet_validations_pass": "",
                "group_entry_pattern": "",
                "group_byte_fractions": "",
                "records_total": "",
                "block_count_sum": "",
                "control_count_unique": "",
                "width_marker_unique": "",
                "selector_hist": "",
                "selector_mean": "",
                "first_precinct_bp": str(candidate.get("first_precinct_bp", "")),
                "has_jpeg_xs_soc_cap": str(bool(candidate.get("has_jpeg_xs_soc_cap", False))).lower(),
                "inferred_variant": str(candidate.get("inferred_variant", "")),
                "fit_class": nikon_fit_class(candidate),
                "probe_json": str(path),
            }
        )
    return rows


def build_constraints(rows: list[dict[str, str]]) -> dict[str, Any]:
    sony_rows = [r for r in rows if r["codec_family"] == "sony_arw6_llvc3_hq"]
    nikon_rows = [r for r in rows if r["codec_family"] == "nikon_he_jpeg_xs_like"]

    sony_selector_total: Counter[str] = Counter()
    for row in sony_rows:
        if row["selector_hist"]:
            sony_selector_total.update(json.loads(row["selector_hist"]))

    by_fit = Counter(r["fit_class"] for r in nikon_rows)
    nikon_bp_by_bpp: dict[str, list[str]] = defaultdict(list)
    nikon_marker_bp_by_bpp: dict[str, list[str]] = defaultdict(list)
    for row in nikon_rows:
        if row["strip_bpp"] and row["first_precinct_bp"]:
            nikon_bp_by_bpp[row["strip_bpp"]].append(row["first_precinct_bp"])
            if row["has_jpeg_xs_soc_cap"] == "true":
                nikon_marker_bp_by_bpp[row["strip_bpp"]].append(row["first_precinct_bp"])

    def numeric_values(key: str, source_rows: list[dict[str, str]]) -> list[float]:
        vals: list[float] = []
        for row in source_rows:
            try:
                vals.append(float(row[key]))
            except Exception:
                pass
        return vals

    sony_bpp = numeric_values("strip_bpp", sony_rows)
    nikon_he_bpp = numeric_values(
        "strip_bpp",
        [r for r in nikon_rows if r["fit_class"] in {"he_supported_path", "he_star_unsupported_path"}],
    )

    return {
        "sony_llvc3_hq": {
            "sample_count": len(sony_rows),
            "all_packet_validations_pass": all(r["all_packet_validations_pass"] == "true" for r in sony_rows),
            "strip_bpp_min": min(sony_bpp) if sony_bpp else None,
            "strip_bpp_max": max(sony_bpp) if sony_bpp else None,
            "packet_count_values": sorted(set(r["packet_count"] for r in sony_rows)),
            "packet_type_counts_values": sorted(set(r["packet_type_counts"] for r in sony_rows)),
            "group_entry_patterns": sorted(set(r["group_entry_pattern"] for r in sony_rows)),
            "selector_hist_total": dict(sorted(sony_selector_total.items(), key=lambda kv: int(kv[0]))),
        },
        "nikon_he": {
            "sample_count": len(nikon_rows),
            "jpeg_xs_marker_count": sum(r["has_jpeg_xs_soc_cap"] == "true" for r in nikon_rows),
            "fit_class_counts": dict(sorted(by_fit.items())),
            "strip_bpp_min_he_or_he_star": min(nikon_he_bpp) if nikon_he_bpp else None,
            "strip_bpp_max_he_or_he_star": max(nikon_he_bpp) if nikon_he_bpp else None,
            "first_precinct_bp_by_strip_bpp": dict(sorted(nikon_bp_by_bpp.items())),
            "jpeg_xs_first_precinct_bp_by_strip_bpp": dict(sorted(nikon_marker_bp_by_bpp.items())),
        },
        "policy_implications": [
            "Use actual strip/syntax bpp for comparisons; target bpp is only a requested operating point.",
            "Treat Sony packet syntax, group entry pattern, packet types, and selector distribution as real-stream constraints on future canonical policies.",
            "Treat Nikon first Bp and JPEG-XS marker routing as a real-stream support boundary; Bp 1/2/3 is not equivalent to the current supported HE decoder path.",
            "Do not claim production encoder equivalence until a same-source, multi-rate production/reference encoder corpus exists.",
        ],
    }


def discover_probe_jsons(base: Path) -> list[Path]:
    skip = {
        "production_fit_summary.json",
        "real_bitstream_controls.json",
    }
    return sorted(p for p in base.rglob("*.json") if p.name not in skip)


def summarize(paths: list[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen_inputs: set[str] = set()
    for path in paths:
        report = load_json(path)
        if not report:
            continue
        input_key = str(report.get("input", path)).lower()
        if input_key in seen_inputs:
            continue
        seen_inputs.add(input_key)
        if "llvc_header" in report and "packets" in report:
            rows.extend(summarize_sony(path, report))
        elif "raw_candidates" in report:
            rows.extend(summarize_nikon(path, report))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe-dir", type=Path, default=Path("out/production_fit_samples"))
    ap.add_argument("--out-csv", type=Path, default=Path("out/production_fit_samples/real_bitstream_controls.csv"))
    ap.add_argument("--out-json", type=Path, default=Path("out/production_fit_samples/production_fit_summary.json"))
    ns = ap.parse_args()

    probe_paths = discover_probe_jsons(ns.probe_dir)
    rows = summarize(probe_paths)
    if not rows:
        raise RuntimeError(f"no supported probe JSON files found under {ns.probe_dir}")

    ns.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with ns.out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    constraints = build_constraints(rows)
    out = {
        "probe_dir": str(ns.probe_dir),
        "probe_json_count": len(probe_paths),
        "row_count": len(rows),
        "rows_csv": str(ns.out_csv),
        "constraints": constraints,
    }
    ns.out_json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(out["constraints"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
