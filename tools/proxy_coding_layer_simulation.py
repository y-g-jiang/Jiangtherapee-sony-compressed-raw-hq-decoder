#!/usr/bin/env python3
"""Coding-layer proxy simulation for the four-plane benchmark.

This is an L2.5 simulation. It does not claim to be a Sony ARW6 or Nikon HE
production encoder. It extends the transform proxy with decoder-visible
coding-performance terms: Sony LLVC3 adaptive width/zero-run/magnitude/sign
syntax, Nikon #826 Bp/Br/GTLI/GCLI plus significance/data/sign substreams,
packet/precinct overhead, and LUT code-domain error metrics.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from pathlib import Path

import numpy as np

import proxy_four_plane_benchmark as bench


CODEC_NIKON = bench.NIKON_CODEC_NAME
CODEC_SONY = bench.SONY_CODEC_NAME
NIKON_TONE_LUT_SIZE = 81792

NIKON_HE_GTLI_ROWS = {
    (4, 0):  [1,1,2,2,3,3, 2,3,3,4,4,4, 4, 2,3,3,4,4,4, 3,4, 4,4, 4, 4,4],
    (4, 3):  [0,1,2,2,3,3, 2,3,3,3,4,4, 4, 2,3,3,3,4,4, 3,4, 4,4, 4, 4,4],
    (4, 7):  [0,1,2,2,3,3, 1,2,3,3,4,4, 4, 1,2,3,3,4,4, 3,4, 4,4, 4, 4,4],
    (4, 12): [0,1,1,2,2,3, 1,2,3,3,3,4, 3, 1,2,3,3,3,4, 3,4, 4,4, 3, 4,4],
    (5, 12): [1,2,2,3,3,4, 2,3,4,4,4,5, 4, 2,3,4,4,4,5, 4,5, 5,5, 4, 5,5],
    (5, 16): [1,2,2,3,3,4, 2,3,4,4,4,4, 4, 2,3,4,4,4,4, 4,5, 4,5, 4, 4,5],
    (5, 20): [1,1,2,3,3,4, 2,3,3,4,4,4, 4, 2,3,3,4,4,4, 4,4, 4,5, 4, 4,5],
    (5, 24): [1,1,2,2,3,3, 2,3,3,4,4,4, 4, 2,3,3,4,4,4, 3,4, 4,4, 4, 4,5],
}


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"no rows to write for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def entropy_bits(values: np.ndarray) -> float:
    arr = np.asarray(values, dtype=np.int64).reshape(-1)
    if arr.size <= 1:
        return 0.0
    return bench.entropy_bits(arr)


def bit_length_abs(values: np.ndarray) -> np.ndarray:
    mag = np.abs(np.asarray(values, dtype=np.int64))
    out = np.zeros_like(mag, dtype=np.int64)
    mask = mag > 0
    if np.any(mask):
        out[mask] = np.floor(np.log2(mag[mask].astype(np.float64))).astype(np.int64) + 1
    return out


def unary_len(value: int) -> int:
    return max(0, int(value)) + 1


def sony_update_width_bits(prev_width: int, next_width: int) -> int:
    prev_width = max(0, int(prev_width))
    next_width = max(0, int(next_width))
    if next_width == prev_width:
        return 1
    if next_width > prev_width:
        return 2 + unary_len(next_width - prev_width - 1)
    drop = prev_width - next_width
    return 2 + min(drop, max(0, prev_width - 1))


def sony_zero_run_bits(run: int, remaining: int) -> int:
    if remaining <= 1:
        return 0
    run = max(1, min(int(run), int(remaining)))
    if run >= remaining:
        return (remaining - 1).bit_length()
    z = max(0, int(math.floor(math.log2(run))))
    return z + 1 + z


def sony_llvc3_syntax_bits(q_by_comp: dict[str, np.ndarray], group_coeffs: int = 4) -> tuple[float, float, float]:
    width_bits = 0.0
    data_bits = 0.0
    sign_bits = 0.0
    for q in q_by_comp.values():
        seq = np.asarray(q, dtype=np.int64).reshape(-1)
        pad = (-seq.size) % group_coeffs
        if pad:
            seq = np.pad(seq, (0, pad), mode="constant")
        groups = seq.reshape(-1, group_coeffs)
        widths = bit_length_abs(groups).max(axis=1).astype(np.int64)
        gi = 0
        prev_width = 0
        width_bits += sony_update_width_bits(0, int(widths[0]) if widths.size else 0)
        while gi < widths.size:
            width = int(widths[gi])
            if width == 0:
                run = 1
                while gi + run < widths.size and int(widths[gi + run]) == 0:
                    run += 1
                width_bits += sony_zero_run_bits(run, widths.size - gi)
                gi += run
                if gi < widths.size:
                    width_bits += unary_len(int(widths[gi]) - 1)
                prev_width = int(widths[gi]) if gi < widths.size else 0
                continue
            data_bits += width * group_coeffs
            sign_bits += float(np.count_nonzero(groups[gi]))
            next_width = int(widths[gi + 1]) if gi + 1 < widths.size else width
            if gi + 1 < widths.size:
                width_bits += sony_update_width_bits(width, next_width)
            prev_width = next_width
            gi += 1
    return width_bits + data_bits + sign_bits, width_bits, sign_bits


def run_token_bits(q: np.ndarray) -> float:
    seq = np.asarray(q, dtype=np.int64).reshape(-1)
    nz = np.flatnonzero(seq)
    if nz.size == 0:
        return 1.0

    runs: list[int] = []
    prev = -1
    for pos in nz.tolist():
        runs.append(pos - prev - 1)
        prev = pos
    runs.append(seq.size - prev - 1)

    nonzero = seq[nz]
    mags = np.abs(nonzero)
    signs = float(nonzero.size)
    token_overhead = 0.15 * float(len(runs) + nonzero.size)
    return entropy_bits(np.asarray(runs)) + entropy_bits(mags) + signs + token_overhead


def quantized_coefficients(
    planes: dict[str, np.ndarray],
    codec: bench.ProxyCodec,
    levels: int,
    base_step: float,
) -> tuple[dict[str, np.ndarray], dict[str, list[tuple[int, int]]]]:
    coeffs, sizes, weights = bench.transform_components(planes, codec, levels)
    q_by_comp: dict[str, np.ndarray] = {}
    for name, coeff in coeffs.items():
        step = base_step / math.sqrt(codec.component_weights[name]) / np.sqrt(weights[name])
        q_by_comp[name] = np.rint(coeff / step).astype(np.int64)
    return q_by_comp, sizes


def sony_width_state_bits(q_by_comp: dict[str, np.ndarray], block: int = 32) -> float:
    bits = 0.0
    for q in q_by_comp.values():
        widths = bit_length_abs(q).reshape(-1)
        pad = (-widths.size) % block
        if pad:
            widths = np.pad(widths, (0, pad), mode="constant")
        grouped = widths.reshape(-1, block).max(axis=1)
        bits += entropy_bits(grouped)
        bits += 1.5 * float(grouped.size)
    return bits


def choose_nikon_bp_br(target_bpp: float) -> tuple[int, int]:
    if target_bpp <= 1.5:
        return 4, 0
    if target_bpp <= 2.0:
        return 4, 3
    if target_bpp <= 2.5:
        return 4, 7
    if target_bpp <= 3.0:
        return 4, 12
    if target_bpp <= 4.0:
        return 5, 20
    return 5, 24


def iter_wavelet_subbands(q: np.ndarray, sizes: list[tuple[int, int]]):
    if not sizes:
        yield "full", q
        return
    for level_index, (h, w) in enumerate(sizes):
        lh = (h + 1) // 2
        lw = (w + 1) // 2
        yield f"L{level_index + 1}_HL", q[:lh, lw:w]
        yield f"L{level_index + 1}_LH", q[lh:h, :lw]
        yield f"L{level_index + 1}_HH", q[lh:h, lw:w]
    h, w = sizes[-1]
    yield "LL", q[: (h + 1) // 2, : (w + 1) // 2]


def nikon_gcli_bits(
    q_by_comp: dict[str, np.ndarray],
    sizes_by_comp: dict[str, list[tuple[int, int]]],
    target_bpp: float,
    group_coeffs: int = 4,
) -> tuple[float, dict[str, float | int]]:
    bp, br = choose_nikon_bp_br(target_bpp)
    gtli_row = NIKON_HE_GTLI_ROWS[(bp, br)]
    sig_bits = 0.0
    gcli_bits = 0.0
    data_bits = 0.0
    sign_bits = 0.0
    gtli_values: list[float] = []
    gcli_values: list[float] = []
    significant_groups = 0
    total_groups = 0
    subband_index = 0

    for comp, q in q_by_comp.items():
        for _subband_name, subband in iter_wavelet_subbands(q, sizes_by_comp[comp]):
            seq = bit_length_abs(subband).reshape(-1)
            if seq.size == 0:
                continue
            pad = (-seq.size) % group_coeffs
            if pad:
                seq = np.pad(seq, (0, pad), mode="constant")
            gcli = seq.reshape(-1, group_coeffs).max(axis=1)
            gtli = int(gtli_row[subband_index % len(gtli_row)])
            retained = np.maximum(gcli - gtli, 0)
            sig = (retained > 0).astype(np.int64)
            if subband_index in {12, 23}:
                pred = np.maximum(gtli, np.roll(gcli, 1))
                pred[0] = gtli
            else:
                pred = np.full_like(gcli, gtli)
            residual = np.maximum(gcli - pred, 0).astype(np.int64)

            sig_blocks = sig.reshape(-1, 8) if sig.size % 8 == 0 else np.pad(sig, (0, (-sig.size) % 8)).reshape(-1, 8)
            sig_block_flags = sig_blocks.max(axis=1)
            sig_bits += float(sig_block_flags.size)
            if np.any(sig > 0):
                gcli_bits += float(sum(unary_len(int(v)) for v in residual[sig > 0]))
            data_bits += float(np.sum(retained) * group_coeffs)
            sign_bits += float(np.count_nonzero(subband))

            gtli_values.extend([float(gtli)] * int(gcli.size))
            gcli_values.extend(gcli.astype(np.float64).tolist())
            significant_groups += int(np.sum(sig))
            total_groups += int(gcli.size)
            subband_index += 1

    stats: dict[str, float | int] = {
        "bp": bp,
        "br": br,
        "mean_gtli": float(statistics.mean(gtli_values)) if gtli_values else 0.0,
        "mean_gcli": float(statistics.mean(gcli_values)) if gcli_values else 0.0,
        "significant_group_fraction": (
            float(significant_groups) / float(total_groups) if total_groups else 0.0
        ),
        "subband_count": subband_index,
        "sig_bits": sig_bits,
        "gcli_bits": gcli_bits,
        "data_bits": data_bits,
        "sign_bits": sign_bits,
    }
    return sig_bits + gcli_bits + data_bits + sign_bits, stats


def coding_overhead_bits(codec_name: str, full_h: int, full_w: int, levels: int) -> tuple[float, float]:
    if codec_name == CODEC_SONY:
        tile_rows = max(1, math.ceil(full_h / 3336))
        tile_cols = 1
        tile_count = tile_rows * tile_cols
        packet_count = 4 * max(1, levels) + 2
        header_bits = tile_count * 1024.0 + packet_count * 96.0 + full_h * 4.0
        lut_param_bits = 0.0  # Sony LUT is treated as static decoder-visible state.
        return header_bits, lut_param_bits

    n_tiles = max(1, math.ceil(full_h / 64))
    file_precincts = n_tiles * 16 + 2
    prefix_bits = 0x9B * 8.0
    precinct_header_bits = file_precincts * 12.0 * 8.0
    alignment_pad_bits = max(0, file_precincts // 16) * 6.0 * 8.0
    profile_bits = 4.0 * max(1, levels) * 16.0
    header_bits = prefix_bits + precinct_header_bits + alignment_pad_bits + profile_bits
    lut_param_bits = 512.0  # compact side-info for the decoder-visible tone curve.
    return header_bits, lut_param_bits


def load_sony_lut(path: Path) -> np.ndarray:
    if path.suffix.lower() in {".bin", ".raw", ".lut"}:
        data = np.fromfile(path, dtype="<u2")
        if data.size >= 4096:
            return data[:4096].astype(np.float64)
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("code"):
                continue
            parts = line.replace(",", "\t").split()
            if len(parts) >= 2:
                rows.append(float(parts[1]))
    if len(rows) < 2:
        raise ValueError(f"could not load Sony LUT from {path}")
    return np.asarray(rows[:4096], dtype=np.float64)


def load_nikon_lut(path: Path) -> np.ndarray:
    if path.exists() and path.suffix.lower() in {".bin", ".raw", ".lut"}:
        if "i32" in path.stem:
            data = np.fromfile(path, dtype="<i4")
            if data.size >= NIKON_TONE_LUT_SIZE:
                sample = np.clip((data[:NIKON_TONE_LUT_SIZE].astype(np.int64) + 2) >> 2, 0, 16383)
                return sample.astype(np.float64)
        data = np.fromfile(path, dtype="<u2")
        if data.size >= NIKON_TONE_LUT_SIZE:
            return data[:NIKON_TONE_LUT_SIZE].astype(np.float64)
    if path.exists():
        rows = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("code"):
                    continue
                parts = line.replace(",", "\t").split()
                if len(parts) >= 3:
                    rows.append(float(parts[2]))
                elif len(parts) >= 2:
                    rows.append(float(parts[1]))
        if len(rows) >= NIKON_TONE_LUT_SIZE:
            return np.asarray(rows[:NIKON_TONE_LUT_SIZE], dtype=np.float64)
    return nikon_decoder_tone_lut_proxy()


def nikon_decoder_tone_lut_proxy() -> np.ndarray:
    code = np.arange(NIKON_TONE_LUT_SIZE, dtype=np.float64)
    x = code / float(NIKON_TONE_LUT_SIZE - 1)
    y = x.copy()
    knee = 0.78
    mask = x > knee
    if np.any(mask):
        t = (x[mask] - knee) / (1.0 - knee)
        shoulder = 1.0 - np.exp(-2.2 * t)
        shoulder /= 1.0 - np.exp(-2.2)
        y[mask] = knee + (1.0 - knee) * shoulder
    y = np.maximum.accumulate(y)
    return np.rint(y * 16383.0).astype(np.float64)


def inverse_lut(samples: np.ndarray, lut: np.ndarray) -> np.ndarray:
    table = np.asarray(lut, dtype=np.float64)
    x = np.asarray(samples, dtype=np.float64)
    idx = np.searchsorted(table, x, side="left")
    idx = np.clip(idx, 0, table.size - 1)
    prev = np.clip(idx - 1, 0, table.size - 1)
    choose_prev = np.abs(table[prev] - x) <= np.abs(table[idx] - x)
    return np.where(choose_prev, prev, idx).astype(np.int32)


def lut_code_metrics(
    source: dict[str, np.ndarray],
    recon: dict[str, np.ndarray],
    lut: np.ndarray,
) -> dict[str, float]:
    src = bench.flatten_planes(source)
    rec = bench.flatten_planes(recon)
    src_code = inverse_lut(src, lut)
    rec_code = inverse_lut(rec, lut)
    diff = rec_code.astype(np.float64) - src_code.astype(np.float64)
    abs_diff = np.abs(diff)
    hi_mask = src >= bench.WHITE * 0.90
    steps = np.diff(np.asarray(lut, dtype=np.float64), append=lut[-1])
    src_steps = steps[np.clip(src_code, 0, steps.size - 1)]
    if np.any(hi_mask):
        hi_abs = abs_diff[hi_mask]
        hi_steps = src_steps[hi_mask]
        hi_mae = float(np.mean(hi_abs))
        hi_p95_step = float(np.percentile(hi_steps, 95.0))
    else:
        hi_mae = 0.0
        hi_p95_step = 0.0
    return {
        "lut_code_mae": float(np.mean(abs_diff)),
        "lut_code_rmse": float(math.sqrt(np.mean(diff * diff))),
        "highlight_lut_code_mae": hi_mae,
        "lut_step_mean": float(np.mean(src_steps)),
        "highlight_lut_step_p95": hi_p95_step,
    }


def syntax_payload_bits(codec_name: str, payload_bits: float, explicit_bits: float) -> float:
    if payload_bits <= 0:
        return 0.0
    if explicit_bits <= 0:
        return payload_bits
    return explicit_bits


def simulate_one(
    source_id: str,
    planes: dict[str, np.ndarray],
    codec: bench.ProxyCodec,
    target_bpp: float,
    levels: int,
    full_h: int,
    full_w: int,
    sony_lut: np.ndarray,
    nikon_lut: np.ndarray,
) -> dict[str, object]:
    result = bench.encode_proxy(planes, source_id, codec, target_bpp, levels)
    q_by_comp, sizes_by_comp = quantized_coefficients(planes, codec, levels, result.base_step)
    full_pixels = int(next(iter(planes.values())).size * 4)
    payload_bits = result.actual_bpp * float(full_pixels)
    scan_bits = sum(run_token_bits(q) for q in q_by_comp.values())

    if codec.name == CODEC_SONY:
        explicit_bits, width_bits, sign_bits = sony_llvc3_syntax_bits(q_by_comp)
        state_bits = width_bits
        state_kind = "sony_llvc3_adaptive_width_zero_run"
        lut = sony_lut
        lut_model = "sony_static_4096_lut"
        nikon_state_stats: dict[str, float | int] = {
            "bp": 0,
            "br": 0,
            "mean_gtli": 0.0,
            "mean_gcli": 0.0,
            "significant_group_fraction": 0.0,
            "subband_count": 0,
            "syntax_explicit_bits": explicit_bits,
            "sign_bits": sign_bits,
        }
    else:
        explicit_bits, nikon_state_stats = nikon_gcli_bits(q_by_comp, sizes_by_comp, target_bpp)
        state_bits = float(nikon_state_stats["sig_bits"]) + float(nikon_state_stats["gcli_bits"])
        state_kind = "nikon_bp_br_gtli_gcli_proxy"
        lut = nikon_lut
        lut_model = "decoder_visible_81792_iqx_iqp_tone_lut_plus_bp_br_gtli_gcli"

    header_bits, lut_param_bits = coding_overhead_bits(codec.name, full_h, full_w, levels)
    syntax_bits = syntax_payload_bits(codec.name, payload_bits, explicit_bits)
    coded_bits = syntax_bits + header_bits + lut_param_bits
    quality = bench.metric_summary(bench.flatten_planes(planes), bench.flatten_planes(result.recon))
    lut_metrics = lut_code_metrics(planes, result.recon, lut)

    return {
        "codec": codec.name,
        "source_id": source_id,
        "target_bpp": f"{target_bpp:.6f}",
        "transform_entropy_bpp": f"{result.actual_bpp:.9f}",
        "scan_run_token_bpp": f"{scan_bits / full_pixels:.9f}",
        "syntax_payload_bpp": f"{syntax_bits / full_pixels:.9f}",
        "state_sideinfo_bpp": f"{state_bits / full_pixels:.9f}",
        "header_bpp": f"{header_bits / full_pixels:.9f}",
        "lut_param_bpp": f"{lut_param_bits / full_pixels:.9f}",
        "coded_proxy_bpp": f"{coded_bits / full_pixels:.9f}",
        "base_step": f"{result.base_step:.9f}",
        "psnr_raw": f"{quality['PSNR_raw']:.9f}",
        "mae_raw": f"{quality['MAE']:.9f}",
        "max_raw": f"{quality['MAX']:.9f}",
        "lut_code_mae": f"{lut_metrics['lut_code_mae']:.9f}",
        "lut_code_rmse": f"{lut_metrics['lut_code_rmse']:.9f}",
        "highlight_lut_code_mae": f"{lut_metrics['highlight_lut_code_mae']:.9f}",
        "lut_step_mean": f"{lut_metrics['lut_step_mean']:.9f}",
        "highlight_lut_step_p95": f"{lut_metrics['highlight_lut_step_p95']:.9f}",
        "lut_code_range": f"{float(lut.size - 1):.9f}",
        "nikon_bp_proxy": nikon_state_stats["bp"],
        "nikon_br_proxy": nikon_state_stats["br"],
        "nikon_mean_gtli_proxy": f"{float(nikon_state_stats['mean_gtli']):.9f}",
        "nikon_mean_gcli_proxy": f"{float(nikon_state_stats['mean_gcli']):.9f}",
        "nikon_significant_group_fraction": f"{float(nikon_state_stats['significant_group_fraction']):.9f}",
        "nikon_subband_count_proxy": nikon_state_stats["subband_count"],
        "syntax_explicit_bpp": f"{explicit_bits / full_pixels:.9f}",
        "syntax_sign_bpp": f"{float(nikon_state_stats.get('sign_bits', 0.0)) / full_pixels:.9f}",
        "state_kind": state_kind,
        "lut_model": lut_model,
    }


def write_summary(out_dir: Path, rows: list[dict[str, object]]) -> Path:
    by_key = {
        (str(r["source_id"]), float(r["target_bpp"]), str(r["codec"])): r
        for r in rows
    }
    sources = sorted({str(r["source_id"]) for r in rows})
    targets = sorted({float(r["target_bpp"]) for r in rows})
    summary_rows: list[dict[str, object]] = []
    for target in targets:
        coded_delta = []
        entropy_delta = []
        state_delta = []
        psnr_delta = []
        lut_norm_delta = []
        sony_coded_wins = 0
        sony_psnr_wins = 0
        for source_id in sources:
            n = by_key.get((source_id, target, CODEC_NIKON))
            s = by_key.get((source_id, target, CODEC_SONY))
            if n is None or s is None:
                continue
            cd = float(s["coded_proxy_bpp"]) - float(n["coded_proxy_bpp"])
            ed = float(s["transform_entropy_bpp"]) - float(n["transform_entropy_bpp"])
            sd = float(s["state_sideinfo_bpp"]) - float(n["state_sideinfo_bpp"])
            pd = float(s["psnr_raw"]) - float(n["psnr_raw"])
            ld = (
                float(s["lut_code_mae"]) / float(s["lut_code_range"])
                - float(n["lut_code_mae"]) / float(n["lut_code_range"])
            )
            coded_delta.append(cd)
            entropy_delta.append(ed)
            state_delta.append(sd)
            psnr_delta.append(pd)
            lut_norm_delta.append(ld)
            sony_coded_wins += int(cd < 0)
            sony_psnr_wins += int(pd > 0)
        summary_rows.append(
            {
                "target_bpp": f"{target:.6f}",
                "median_coded_bpp_sony_minus_nikon": f"{statistics.median(coded_delta):.9f}",
                "median_transform_entropy_bpp_sony_minus_nikon": f"{statistics.median(entropy_delta):.9f}",
                "median_state_sideinfo_bpp_sony_minus_nikon": f"{statistics.median(state_delta):.9f}",
                "median_psnr_sony_minus_nikon_db": f"{statistics.median(psnr_delta):.9f}",
                "median_norm_lut_code_mae_sony_minus_nikon": f"{statistics.median(lut_norm_delta):.9f}",
                "sony_lower_coded_bpp_wins": sony_coded_wins,
                "sony_psnr_wins": sony_psnr_wins,
                "source_count": len(coded_delta),
            }
        )
    path = out_dir / "coding_layer_summary.csv"
    write_csv(path, summary_rows)
    return path


def write_component_summary(out_dir: Path, rows: list[dict[str, object]]) -> Path:
    targets = sorted({float(r["target_bpp"]) for r in rows})
    codecs = [CODEC_NIKON, CODEC_SONY]
    fields = [
        "transform_entropy_bpp",
        "scan_run_token_bpp",
        "syntax_payload_bpp",
        "state_sideinfo_bpp",
        "header_bpp",
        "lut_param_bpp",
        "coded_proxy_bpp",
        "lut_code_mae",
        "highlight_lut_code_mae",
    ]
    summary_rows: list[dict[str, object]] = []
    for target in targets:
        for codec in codecs:
            subset = [
                r for r in rows
                if abs(float(r["target_bpp"]) - target) < 1e-9 and str(r["codec"]) == codec
            ]
            if not subset:
                continue
            out: dict[str, object] = {
                "target_bpp": f"{target:.6f}",
                "codec": codec,
                "source_count": len(subset),
            }
            for field in fields:
                out[f"median_{field}"] = f"{statistics.median(float(r[field]) for r in subset):.9f}"
            summary_rows.append(out)
    path = out_dir / "coding_layer_component_summary.csv"
    write_csv(path, summary_rows)
    return path


def load_sources(ns: argparse.Namespace) -> dict[str, dict[str, np.ndarray]]:
    if ns.input_raw:
        return {ns.input_raw.stem: bench.load_rggb_raw(ns.input_raw, ns.width, ns.height)}
    rng = np.random.default_rng(ns.seed)
    return {
        scene: bench.generate_scene(scene, ns.height // 2, ns.width // 2, rng)
        for scene in bench.DEFAULT_SCENES
    }


def parse_targets(text: str) -> list[float]:
    return bench.parse_targets(text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=Path("out/proxy_four_plane_benchmark_v2"))
    ap.add_argument("--width", type=int, default=512)
    ap.add_argument("--height", type=int, default=512)
    ap.add_argument("--levels", type=int, default=4)
    ap.add_argument("--targets", default="1.5,2.0,2.5,3.0,4.0,5.0")
    ap.add_argument("--seed", type=int, default=20260602)
    ap.add_argument("--input-raw", type=Path)
    ap.add_argument("--sony-lut", type=Path, default=Path("tools/data/sony_llvc3_static_lut4096.tsv"))
    ap.add_argument(
        "--nikon-lut",
        type=Path,
        default=Path("tools/data/nikon_he_iqx_iqp_lut81792_sample14_u16.bin"),
    )
    ns = ap.parse_args()

    if ns.width <= 0 or ns.height <= 0 or ns.width % 2 or ns.height % 2:
        raise ValueError("width and height must be positive even numbers")

    start = time.perf_counter()
    out_dir = ns.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    sources = load_sources(ns)
    targets = parse_targets(ns.targets)
    sony_lut = load_sony_lut(ns.sony_lut)
    nikon_lut = load_nikon_lut(ns.nikon_lut)

    rows: list[dict[str, object]] = []
    for source_id, planes in sources.items():
        for target in targets:
            for codec_name in (CODEC_NIKON, CODEC_SONY):
                rows.append(
                    simulate_one(
                        source_id=source_id,
                        planes=planes,
                        codec=bench.CODECS[codec_name],
                        target_bpp=target,
                        levels=ns.levels,
                        full_h=ns.height,
                        full_w=ns.width,
                        sony_lut=sony_lut,
                        nikon_lut=nikon_lut,
                    )
                )

    rows_path = out_dir / "coding_layer_simulation.csv"
    write_csv(rows_path, rows)
    summary_path = write_summary(out_dir, rows)
    component_summary_path = write_component_summary(out_dir, rows)
    manifest = {
        "kind": "L2.5 coding-layer proxy simulation",
        "evidence_level": "L2.5 decoder-visible coding-layer proxy simulation; not a production encoder",
        "not_production_encoder_evidence": True,
        "not_a_production_encoder": True,
        "run_id": out_dir.name,
        "base_transform_proxy": "tools/proxy_four_plane_benchmark.py",
        "seed": ns.seed,
        "input_mode": "single_input_raw" if ns.input_raw else "deterministic_synthetic_scenes",
        "input_raw": str(ns.input_raw) if ns.input_raw else "",
        "width": ns.width,
        "height": ns.height,
        "levels": ns.levels,
        "targets": targets,
        "targets_bpp": targets,
        "sources": list(sources.keys()),
        "source_count": len(sources),
        "codec_names": [CODEC_NIKON, CODEC_SONY],
        "codecs": [CODEC_NIKON, CODEC_SONY],
        "row_count": len(rows),
        "sony_lut": str(ns.sony_lut),
        "nikon_lut": str(ns.nikon_lut),
        "nikon_lut_model": (
            "decoder-visible Nikon HE IQX/IQP tone LUT extracted from #826 "
            "nikon_he_iqx_iqp_lut_data.h and materialized as an 81792-entry sample14 LUT"
        ),
        "coding_terms": [
            "transform coefficient entropy",
            "decoder-visible scan/run or bit-plane syntax cost",
            "Sony LLVC3 adaptive width/zero-run/magnitude/sign syntax",
            "Nikon #826 Bp/Br/GTLI/GCLI significance/data/sign syntax",
            "Nikon #826-style precinct prefix/header/alignment overhead",
            "Sony packet/header overhead",
            "Sony static LUT code-domain error",
            "Nikon extracted IQX/IQP tone LUT code-domain error",
        ],
        "outputs": {
            "coding_layer_simulation": str(rows_path),
            "coding_layer_summary": str(summary_path),
            "coding_layer_component_summary": str(component_summary_path),
        },
        "repro_command": [
            "python",
            "tools/proxy_coding_layer_simulation.py",
            "--out-dir",
            str(out_dir),
            "--width",
            str(ns.width),
            "--height",
            str(ns.height),
            "--levels",
            str(ns.levels),
            "--targets",
            ns.targets,
            "--seed",
            str(ns.seed),
        ],
        "elapsed_s": round(time.perf_counter() - start, 3),
    }
    manifest_path = out_dir / "coding_layer_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
