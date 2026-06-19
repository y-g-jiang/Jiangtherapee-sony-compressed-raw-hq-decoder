#!/usr/bin/env python3
"""Verify the strict #824/#826 full LaTeX report and math-evaluation gate."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import subprocess
import sys
from argparse import ArgumentParser
from pathlib import Path


EXPECTED_STATUS_COUNTS = {
    "exact_reverse": 8,
    "canonical_choice": 2,
    "not_decoder_determined": 2,
}

EXPECTED_ITEMS = [
    ("Sony #824 ARW6/LLVC3", "stream and tile directory syntax", "exact_reverse"),
    ("Sony #824 ARW6/LLVC3", "packet records, selectors, adaptive width, zero-run, magnitude and sign syntax", "exact_reverse"),
    ("Sony #824 ARW6/LLVC3", "hierarchical inverse 5/3-like synthesis groups 1..3", "exact_reverse"),
    ("Sony #824 ARW6/LLVC3", "final green and R/B residual relation", "exact_reverse"),
    ("Sony #824 ARW6/LLVC3", "camera encoder RD/selector/rate-control policy", "not_decoder_determined"),
    ("Sony #824 ARW6/LLVC3", "guarded non-16-aligned edge behavior", "canonical_choice"),
    ("Nikon #826 HE", "precinct header, Bp/Br/Dpb and LB substream sizes", "exact_reverse"),
    ("Nikon #826 HE", "GCLI/GTLI significance and predecessor prediction syntax", "exact_reverse"),
    ("Nikon #826 HE", "coefficient magnitude/sign bit-plane syntax and dequantization", "exact_reverse"),
    ("Nikon #826 HE", "tile orchestration and step1/step2 Bayer reconstruction", "exact_reverse"),
    ("Nikon #826 HE", "camera encoder Bp/Br/Dpb/GTLI rate-control policy", "not_decoder_determined"),
    ("Nikon #826 HE", "precinct/container writer and HE/HE* variant selection", "canonical_choice"),
]

SONY = "sony_824_decoder_visible_packet_canonical"
NIKON = "nikon_826_decoder_visible_precinct_canonical"

REQUIRED_REPORT_TOKENS = [
    "strict_824_826_math_eval_full_20260603",
    "strict_824_826_metric_validation",
    "strict_824_826_minimal_bitstream_closure",
    "strict_824_826_math_insight_20260603",
    "metric_reference_validation.json",
    "bitstream_closure.json",
    "stage_metrics.csv",
    "insight_metrics.csv",
    "combined_big_comparison.csv",
    "paper_numbers.json",
    "target_request_summary.csv",
    "rate_summary.csv",
    "bd_rate_psnr.csv",
    "bd_rate_ms_ssim.csv",
    "bd_rate_gmsd.csv",
    "sample14",
    "kMidpointScaleTable",
    "decoder-visible canonical simulation",
    "minimal core bitstream closure",
    "production encoder equivalence claim",
    "not decoder determined",
    "11/24",
    "7488",
    "MS-SSIM",
    "GMSD",
    "VIF-style",
    "rate-distortion-perception",
    "make_strict_824_826_latex_report_figures.py",
    "strict_824_826_insight_eval.py",
    "fig_strict_bd_rate_summary.png",
    "fig_strict_syntax_summary.png",
    "fig_strict_stage_separation.png",
    "fig_strict_insight_metrics.png",
    "fig_strict_rd_slope.png",
]

STALE_REPORT_TOKENS = [
    "strict_824_826_math_eval_final_20260603",
    "out/strict_824_826_math_eval_final_20260603",
    "out/proxy_four_plane_benchmark",
    "same_rate_summary.csv",
    "coding_layer_simulation.csv",
    "coding_layer_summary.csv",
    "fig_proxy_structure.png",
    "fig_same_rate_summary.png",
    "fig_bd_rate_summary.png",
    "fig_coding_layer_summary.png",
    "strict-only",
    "+24.42",
    "1.02--2.98",
]

MOJIBAKE_PATTERNS = [
    "\ufffd",
    "锟斤拷",
    "涓€",
    "鏂",
    "鍥",
    "琛",
    "骞",
]

BAD_LATEX_LOG_PATTERNS = [
    r"^!",
    r"Fatal error",
    r"Emergency stop",
    r"Undefined control sequence",
    r"LaTeX Error",
    r"Missing file",
    r"File .* not found",
    r"Missing character",
]

REQUIRED_FIGURES = [
    "fig_strict_structure.png",
    "fig_strict_same_target_summary.png",
    "fig_strict_metric_matrix.png",
    "fig_strict_bd_rate_summary.png",
    "fig_strict_syntax_summary.png",
    "fig_strict_stage_separation.png",
    "fig_strict_insight_metrics.png",
    "fig_strict_rd_slope.png",
    "fig_strict_roundtrip.png",
    "fig_strict_scene_rank.png",
    "fig_strict_roi_shadow_noise.png",
    "fig_strict_roi_high_iso_texture.png",
    "fig_strict_roi_highlight_rolloff.png",
    "fig_strict_roi_thin_black_lines.png",
    "fig_strict_roi_red_blue_fine_text.png",
]

EXPECTED_BD = {
    "bd_rate_psnr.csv": ("whole", 11, 13, 0.04757810999022993),
    "bd_rate_mae.csv": ("whole", 11, 13, 0.13613886320705038),
    "bd_rate_grad_psnr.csv": ("detail", 9, 15, 0.008281382931522918),
    "bd_rate_ssim.csv": ("detail", 11, 13, 0.01416336390325812),
    "bd_rate_ms_ssim.csv": ("detail", 11, 13, 0.0559800412931839),
    "bd_rate_gmsd.csv": ("detail", 11, 13, 0.0023739806518610074),
}

EXPECTED_SELECTED = {
    "whole_psnr_bd_median_percent": 4.757810999022993,
    "whole_mae_bd_median_percent": 13.613886320705038,
    "detail_grad_psnr_bd_median_percent": 0.8281382931522918,
    "detail_ssim_bd_median_percent": 1.416336390325812,
    "detail_ms_ssim_bd_median_percent": 5.59800412931839,
    "detail_gmsd_bd_median_percent": 0.23739806518610074,
    "target_2p5_psnr_nikon": 61.86829664,
    "target_3p0_psnr_sony": 64.014593013,
}


def check(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def as_float(value: str) -> float:
    return math.inf if value == "inf" else float(value)


def normalized_report_text(*texts: str) -> str:
    combined = "\n".join(texts)
    return (
        combined.replace(r"\_", "_")
        .replace(r"\#", "#")
        .replace(r"\%", "%")
        .replace(r"\\", " ")
    )


def check_audit(audit_path: Path, errors: list[str]) -> None:
    check(audit_path.exists() and audit_path.stat().st_size > 0, f"missing or empty audit JSON: {audit_path}", errors)
    if not audit_path.exists():
        return

    data = json.loads(read_text(audit_path))
    check(data.get("kind") == "strict #824/#826 decoder-visible encoder reversibility audit", "audit kind mismatch", errors)
    check(data.get("errors") == [], f"audit errors are not empty: {data.get('errors')}", errors)
    check(data.get("status_counts") == EXPECTED_STATUS_COUNTS, f"status_counts mismatch: {data.get('status_counts')}", errors)

    gate = data.get("strict_gate", {})
    check(gate.get("allows_decoder_visible_canonical_simulation") is True, "strict gate does not allow canonical simulation", errors)
    check(gate.get("allows_production_encoder_equivalence_claim") is False, "strict gate incorrectly allows production claims", errors)

    actual = {(item.get("codec"), item.get("item"), item.get("status")) for item in data.get("items", [])}
    for expected in EXPECTED_ITEMS:
        check(expected in actual, f"missing audit item: {expected}", errors)


def check_math(math_dir: Path, errors: list[str]) -> None:
    required = [
        "manifest.json",
        "encodes.csv",
        "metrics.csv",
        "syntax_summary.csv",
        "roundtrip_audit.csv",
        "target_request_summary.csv",
        "rate_summary.csv",
        "bd_rate_psnr.csv",
        "bd_rate_mae.csv",
        "bd_rate_grad_psnr.csv",
        "bd_rate_ssim.csv",
        "bd_rate_ms_ssim.csv",
        "bd_rate_gmsd.csv",
        "paper_numbers.json",
    ]
    for name in required:
        path = math_dir / name
        check(path.exists() and path.stat().st_size > 0, f"missing or empty math artifact: {path}", errors)
    if not (math_dir / "manifest.json").exists():
        return

    manifest = json.loads(read_text(math_dir / "manifest.json"))
    check(manifest.get("kind") == "strict #824/#826 decoder-visible math evaluation", "math manifest kind mismatch", errors)
    check(manifest.get("seed") == 20260603, "math manifest seed mismatch", errors)
    check(manifest.get("width") == 256 and manifest.get("height") == 256, "math manifest dimensions must be 256x256", errors)
    check(manifest.get("levels") == 3, "math manifest levels mismatch", errors)
    check(manifest.get("source_count") == 24, "math manifest source_count mismatch", errors)
    check(manifest.get("decoder_visible_only") is True, "math manifest must be decoder-visible only", errors)
    check(manifest.get("old_proxy_outputs_used") is False, "math manifest used old proxy outputs", errors)
    check(int(manifest.get("jobs", 0)) >= 2, "strict math eval was not run with multiple workers", errors)
    check(manifest.get("nikon_lut_column") == "sample14", "Nikon LUT column must be sample14", errors)
    check("kMidpointScaleTable" in manifest.get("nikon_midpoint_scale", ""), "Nikon midpoint table not sourced from #826 constants", errors)
    check(
        manifest.get("row_counts") == {"encodes": 288, "metrics": 7488, "syntax_summary": 288, "roundtrip_audit": 48},
        f"math row_counts mismatch: {manifest.get('row_counts')}",
        errors,
    )

    roundtrip = read_csv(math_dir / "roundtrip_audit.csv")
    by_codec: dict[str, list[dict[str, str]]] = {}
    for row in roundtrip:
        by_codec.setdefault(row["codec"], []).append(row)
    check(len(by_codec.get(SONY, [])) == 24 and len(by_codec.get(NIKON, [])) == 24, "roundtrip rows must be 24 per codec", errors)
    if by_codec.get(SONY):
        check(max(as_float(r["MAX"]) for r in by_codec[SONY]) <= 12.0, "Sony roundtrip projection max exceeds expected LUT/clamp tolerance", errors)
    if by_codec.get(NIKON):
        check(max(as_float(r["MAX"]) for r in by_codec[NIKON]) <= 1.0, "Nikon roundtrip max exceeds one sample", errors)

    encodes = read_csv(math_dir / "encodes.csv")
    metrics = read_csv(math_dir / "metrics.csv")
    target_rows = read_csv(math_dir / "target_request_summary.csv")
    syntax = read_csv(math_dir / "syntax_summary.csv")
    check(len(encodes) == 288, "encodes.csv row count mismatch", errors)
    check(len(metrics) == 7488, "metrics.csv row count mismatch", errors)
    check(len(target_rows) == 48, "target_request_summary.csv row count mismatch", errors)
    check(len(syntax) == 288, "syntax_summary.csv row count mismatch", errors)
    check({r["codec"] for r in encodes} == {SONY, NIKON}, "unexpected codec set in encodes.csv", errors)
    metric_set = {r["metric"] for r in metrics}
    for metric in ["PSNR_raw", "MAE", "MAX", "grad_psnr", "laplacian_mae", "ssim_mean", "ms_ssim_mean", "gmsd_mean"]:
        check(metric in metric_set, f"metric missing from metrics.csv: {metric}", errors)

    for filename, (group, ok, skipped, median) in EXPECTED_BD.items():
        rows = read_csv(math_dir / filename)
        match = [row for row in rows if row.get("group") == group]
        check(len(match) == 1, f"{filename} must contain exactly one {group} row", errors)
        if match:
            row = match[0]
            check(row.get("ok_sources") == str(ok), f"{filename} ok_sources changed", errors)
            check(row.get("skipped_sources") == str(skipped), f"{filename} skipped_sources changed", errors)
            check(abs(float(row.get("median_bd_rate", "nan")) - median) < 1e-12, f"{filename} median changed", errors)

    paper_numbers = json.loads(read_text(math_dir / "paper_numbers.json")) if (math_dir / "paper_numbers.json").exists() else {}
    selected = paper_numbers.get("selected", {})
    check(selected.get("whole_psnr_ok_sources") == 11, "paper_numbers selected ok_sources mismatch", errors)
    check(selected.get("whole_psnr_skipped_sources") == 13, "paper_numbers selected skipped_sources mismatch", errors)
    for key, expected in EXPECTED_SELECTED.items():
        check(abs(float(selected.get(key, "nan")) - expected) < 1e-9, f"paper_numbers selected {key} changed", errors)


def check_metric_validation(path: Path, errors: list[str]) -> None:
    check(path.exists() and path.stat().st_size > 0, f"missing or empty metric validation artifact: {path}", errors)
    if not path.exists():
        return
    data = json.loads(read_text(path))
    check(data.get("kind") == "strict #824/#826 metric reference validation", "metric validation kind mismatch", errors)
    check(data.get("errors") == [], f"metric validation errors are not empty: {data.get('errors')}", errors)
    summary = data.get("summary", {})
    check(summary.get("all_passed") is True, "metric validation did not pass", errors)
    check(int(summary.get("case_count", 0)) >= 3, "metric validation must include at least three deterministic cases", errors)
    check(int(summary.get("check_count", 0)) >= 24, "metric validation check count is too small", errors)
    check(float(summary.get("max_abs_delta", 1.0)) < 1e-3, "metric validation max_abs_delta is too large", errors)
    refs = data.get("references", [])
    ref_text = "\n".join(json.dumps(r, ensure_ascii=False) for r in refs)
    for token in ["SSIM", "MS-SSIM", "GMSD", "BD-rate", "ssim.pdf", "msssim.pdf", "GMSD.pdf", "Bjontegaard"]:
        check(token in ref_text, f"metric validation reference missing token: {token}", errors)
    cases = {case.get("case_id"): case for case in data.get("cases", [])}
    for case_id in ["identical_constant", "constant_offset", "gradient_noise_texture"]:
        check(case_id in cases, f"metric validation missing case: {case_id}", errors)
        if case_id in cases:
            check(cases[case_id].get("passed") is True, f"metric validation case failed: {case_id}", errors)


def json_path_to_artifact(root: Path, value: str) -> Path:
    p = Path(value)
    return p.resolve() if p.is_absolute() else (root / p).resolve()


def check_bitstream_closure(path: Path, root: Path, errors: list[str]) -> None:
    check(path.exists() and path.stat().st_size > 0, f"missing or empty bitstream closure artifact: {path}", errors)
    if not path.exists():
        return
    data = json.loads(read_text(path))
    check(data.get("kind") == "strict #824/#826 minimal core bitstream closure", "bitstream closure kind mismatch", errors)
    check(data.get("errors") == [], f"bitstream closure errors are not empty: {data.get('errors')}", errors)
    summary = data.get("summary", {})
    check(summary.get("all_passed") is True, "bitstream closure did not pass", errors)
    check(int(summary.get("sony_records_checked", 0)) >= 3, "Sony bitstream closure must check at least three records", errors)
    check(int(summary.get("nikon_gcli_groups_checked", 0)) >= 8, "Nikon GCLI closure must check at least eight groups", errors)
    check(int(summary.get("nikon_vertical_prediction_groups_checked", 0)) >= 8, "Nikon vertical prediction closure must check at least eight groups", errors)

    boundary = data.get("evidence_boundary", {})
    check(boundary.get("decoder_visible_core_syntax_bytes") is True, "bitstream closure must be decoder-visible core syntax", errors)
    check(boundary.get("full_sony_arw_container") is False, "bitstream closure must not claim full Sony ARW container", errors)
    check(boundary.get("full_nikon_nef_container") is False, "bitstream closure must not claim full Nikon NEF container", errors)
    check(boundary.get("allows_production_encoder_equivalence_claim") is False, "bitstream closure incorrectly allows production claims", errors)

    fp = data.get("source_fingerprints", {})
    check(bool(fp.get("sony_824_source_sha256")), "Sony source fingerprint missing", errors)
    nikon_files = fp.get("nikon_826_files", {})
    for name in ["nikon_he_precinct_header.cpp", "nikon_he_bit_reader.cpp", "nikon_he_gcli_decode.cpp", "nikon_he_coefficient_decode.cpp", "nikon_he_dequantize.cpp"]:
        check(bool(nikon_files.get(name, {}).get("sha256")), f"Nikon source fingerprint missing: {name}", errors)

    sony = data.get("sony", {})
    check(sony.get("full_arw_container") is False, "Sony closure must not claim full ARW container", errors)
    check(sony.get("all_packet_validations_pass") is True, "Sony packet validation did not pass", errors)
    check(sony.get("exact_roundtrip_match") is True, "Sony exact roundtrip did not pass", errors)
    for record in sony.get("decoded_records", []):
        check(record.get("exact_match") is True, f"Sony record mismatch: {record.get('record')}", errors)
    for key in ["stream_file", "packet_file"]:
        if sony.get(key):
            p = json_path_to_artifact(root, sony[key])
            check(p.exists() and p.stat().st_size > 0, f"missing Sony binary artifact: {p}", errors)
    if sony.get("stream_file") and sony.get("stream_sha256"):
        p = json_path_to_artifact(root, sony["stream_file"])
        if p.exists():
            check(sha256_file(p) == sony["stream_sha256"], "Sony stream SHA256 mismatch", errors)
    if sony.get("packet_file") and sony.get("packet_sha256"):
        p = json_path_to_artifact(root, sony["packet_file"])
        if p.exists():
            check(sha256_file(p) == sony["packet_sha256"], "Sony packet SHA256 mismatch", errors)

    nikon = data.get("nikon", {})
    check(nikon.get("full_nef_container") is False, "Nikon closure must not claim full NEF container", errors)
    check(nikon.get("offset_slices_match") is True, "Nikon header offsets did not recover substreams", errors)
    for key in ["gcli_zero_prediction", "coefficient_bitplanes_and_sign", "dequantization", "gcli_vertical_prediction"]:
        check(nikon.get(key, {}).get("exact_match") is True, f"Nikon closure mismatch: {key}", errors)
    if nikon.get("precinct_file"):
        p = json_path_to_artifact(root, nikon["precinct_file"])
        check(p.exists() and p.stat().st_size > 0, f"missing Nikon precinct artifact: {p}", errors)
        if p.exists() and nikon.get("precinct_sha256"):
            check(sha256_file(p) == nikon["precinct_sha256"], "Nikon precinct SHA256 mismatch", errors)


def check_insight(insight_dir: Path, errors: list[str]) -> None:
    required = [
        "manifest.json",
        "stage_metrics.csv",
        "stage_summary.csv",
        "insight_metrics.csv",
        "insight_target_summary.csv",
        "insight_encodes.csv",
        "rd_slope_segments.csv",
        "rd_slope_summary.csv",
        "bd_rate_vifp.csv",
        "bd_rate_error_hf.csv",
        "bd_rate_coeff_snr.csv",
        "combined_big_comparison.csv",
    ]
    for name in required:
        path = insight_dir / name
        check(path.exists() and path.stat().st_size > 0, f"missing or empty insight artifact: {path}", errors)
    if not (insight_dir / "manifest.json").exists():
        return

    manifest = json.loads(read_text(insight_dir / "manifest.json"))
    check(manifest.get("kind") == "strict #824/#826 mathematical insight and stage-separated evaluation", "insight manifest kind mismatch", errors)
    check(manifest.get("decoder_visible_only") is True, "insight manifest must be decoder-visible only", errors)
    check(manifest.get("old_proxy_outputs_used") is False, "insight manifest used old proxy outputs", errors)
    check(int(manifest.get("jobs", 0)) >= 2, "insight eval was not run with multiple workers", errors)
    check(manifest.get("all_self_checks_passed") is True, "insight self-checks did not pass", errors)
    row_counts = manifest.get("row_counts", {})
    check(int(row_counts.get("stage_metrics", 0)) >= 3000, f"stage_metrics row count too small: {row_counts}", errors)
    check(int(row_counts.get("insight_metrics", 0)) >= 3000, f"insight_metrics row count too small: {row_counts}", errors)
    check(int(row_counts.get("insight_encodes", 0)) == 288, f"insight_encodes row count mismatch: {row_counts}", errors)

    stage_rows = read_csv(insight_dir / "stage_summary.csv")
    stages = {row["stage"] for row in stage_rows}
    for stage in ["component_lut_projection", "transform_roundtrip", "transform_compaction", "quantization_dequantization"]:
        check(stage in stages, f"stage summary missing stage: {stage}", errors)
    stage_metrics = {row["metric"] for row in stage_rows}
    for metric in ["ll_energy_fraction", "ll_to_detail_energy_db", "coeff_SNR_db", "quant_error_hf_fraction", "dequant_zero_fraction"]:
        check(metric in stage_metrics, f"stage summary missing metric: {metric}", errors)

    insight_rows = read_csv(insight_dir / "insight_target_summary.csv")
    insight_metrics = {row["metric"] for row in insight_rows}
    for metric in ["vifp_mean", "residual_neighbor_corr_abs", "error_hf_energy_fraction", "edge_error_correlation", "phase_MAE_std"]:
        check(metric in insight_metrics, f"insight summary missing metric: {metric}", errors)
    targets = {row["target_bpp"] for row in insight_rows if row["metric"] == "vifp_mean"}
    check(targets == {"1.500000", "2.000000", "2.500000", "3.000000", "4.000000", "5.000000"}, f"VIF target coverage mismatch: {targets}", errors)

    combined = read_csv(insight_dir / "combined_big_comparison.csv")
    categories = {row["category"] for row in combined}
    for category in ["stage_separation", "mathematical_insight", "rd_local_slope"]:
        check(category in categories, f"combined big comparison missing category: {category}", errors)

    vif_bd = read_csv(insight_dir / "bd_rate_vifp.csv")
    match = [row for row in vif_bd if row.get("group") == "information"]
    check(len(match) == 1, "VIF-style BD-rate must contain one information row", errors)
    if match:
        check(int(match[0].get("ok_sources", "0")) >= 7, "VIF-style BD-rate has too few computable sources", errors)
    coeff_bd = read_csv(insight_dir / "bd_rate_coeff_snr.csv")
    match = [row for row in coeff_bd if row.get("group") == "quantization_dequantization|coefficients"]
    check(len(match) == 1, "coefficient SNR BD-rate must contain one quantization row", errors)
    if match:
        check(int(match[0].get("ok_sources", "0")) >= 11, "coefficient SNR BD-rate has too few computable sources", errors)


def check_report(report_dir: Path, require_pdf: bool, errors: list[str]) -> None:
    tex_path = report_dir / "main.tex"
    md_path = report_dir / "main_quick_edit.md"
    pdf_path = report_dir / "main.pdf"
    log_path = report_dir / "main.log"
    fig_dir = report_dir / "figures"

    check(tex_path.exists() and tex_path.stat().st_size > 0, f"missing or empty report source: {tex_path}", errors)
    check(md_path.exists() and md_path.stat().st_size > 0, f"missing or empty quick edit source: {md_path}", errors)
    tex = read_text(tex_path) if tex_path.exists() else ""
    md = read_text(md_path) if md_path.exists() else ""
    combined = normalized_report_text(tex, md)

    for token in REQUIRED_REPORT_TOKENS:
        check(token in combined, f"required strict report token missing: {token}", errors)
    for token in STALE_REPORT_TOKENS:
        check(token not in combined, f"stale proxy or small-report token referenced: {token}", errors)
    for token in MOJIBAKE_PATTERNS:
        check(token not in combined, f"possible mojibake token in report: {token}", errors)
    check("production encoder equivalence claim is allowed" not in combined.lower(), "report appears to allow production claims", errors)

    includegraphics = re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", tex)
    check(includegraphics, "report has no includegraphics entries", errors)
    for fig in includegraphics:
        filename = fig if fig.endswith(".png") else f"{fig}.png"
        check(filename.startswith("fig_strict_"), f"non-strict figure referenced: {filename}", errors)
        path = fig_dir / filename
        check(path.exists() and path.stat().st_size > 0, f"missing report figure: {path}", errors)
    for filename in REQUIRED_FIGURES:
        check(filename in {fig if fig.endswith(".png") else f"{fig}.png" for fig in includegraphics}, f"required strict figure not referenced: {filename}", errors)

    if require_pdf:
        check(pdf_path.exists() and pdf_path.stat().st_size > 0, f"missing or empty compiled PDF: {pdf_path}", errors)
        check(log_path.exists() and log_path.stat().st_size > 0, f"missing or empty LaTeX log: {log_path}", errors)
        if pdf_path.exists():
            try:
                info = subprocess.run(["pdfinfo", str(pdf_path)], check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
                match = re.search(r"^Pages:\s+(\d+)", info.stdout, re.MULTILINE)
                check(match is not None, "pdfinfo did not report pages", errors)
                if match:
                    pages = int(match.group(1))
                    check(pages >= 8, f"full report should be at least 8 pages, got {pages}", errors)
            except Exception as exc:
                errors.append(f"pdfinfo failed: {exc}")

    if log_path.exists():
        log = read_text(log_path)
        for pattern in BAD_LATEX_LOG_PATTERNS:
            check(not re.search(pattern, log, re.MULTILINE), f"LaTeX log problem: {pattern}", errors)


def main() -> int:
    ap = ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--audit", type=Path, default=Path("out/strict_824_826_encoder_reversibility/audit.json"))
    ap.add_argument("--math-dir", type=Path, default=Path("out/strict_824_826_math_eval_full_20260603"))
    ap.add_argument("--insight-dir", type=Path, default=Path("out/strict_824_826_math_insight_20260603"))
    ap.add_argument("--metric-validation", type=Path, default=Path("out/strict_824_826_metric_validation/metric_reference_validation.json"))
    ap.add_argument("--bitstream-closure", type=Path, default=Path("out/strict_824_826_minimal_bitstream_closure/bitstream_closure.json"))
    ap.add_argument("--report-dir", type=Path, default=Path("docs/proxy-four-plane-latex-report"))
    ap.add_argument("--require-pdf", action="store_true")
    ns = ap.parse_args()

    root = ns.root.resolve()
    audit_path = (root / ns.audit).resolve() if not ns.audit.is_absolute() else ns.audit.resolve()
    math_dir = (root / ns.math_dir).resolve() if not ns.math_dir.is_absolute() else ns.math_dir.resolve()
    insight_dir = (root / ns.insight_dir).resolve() if not ns.insight_dir.is_absolute() else ns.insight_dir.resolve()
    metric_validation = (root / ns.metric_validation).resolve() if not ns.metric_validation.is_absolute() else ns.metric_validation.resolve()
    bitstream_closure = (root / ns.bitstream_closure).resolve() if not ns.bitstream_closure.is_absolute() else ns.bitstream_closure.resolve()
    report_dir = (root / ns.report_dir).resolve() if not ns.report_dir.is_absolute() else ns.report_dir.resolve()
    errors: list[str] = []

    check_audit(audit_path, errors)
    check_math(math_dir, errors)
    check_insight(insight_dir, errors)
    check_metric_validation(metric_validation, errors)
    check_bitstream_closure(bitstream_closure, root, errors)
    check_report(report_dir, ns.require_pdf, errors)

    if errors:
        print("STRICT_FULL_REPORT_AUDIT_FAILED")
        for error in errors:
            print(f"- {error}")
        return 1

    print("STRICT_FULL_REPORT_AUDIT_OK")
    print(f"audit={audit_path}")
    print(f"math_dir={math_dir}")
    print(f"insight_dir={insight_dir}")
    print(f"metric_validation={metric_validation}")
    print(f"bitstream_closure={bitstream_closure}")
    print(f"report_dir={report_dir}")
    print(f"require_pdf={ns.require_pdf}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
