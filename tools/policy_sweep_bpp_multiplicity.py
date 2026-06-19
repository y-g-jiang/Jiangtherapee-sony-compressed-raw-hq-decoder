#!/usr/bin/env python3
"""Policy sweep for the non-uniqueness of bpp in strict #824/#826 modeling.

The strict evaluator intentionally fixes a canonical encoder policy. This
script expands several decoder-visible policy degrees of freedom and asks a
narrower question:

    At a similar actual syntax bpp, how far can quality move when the encoder
    chooses a different selector / GTLI allocation policy?

It remains a decoder-visible canonical simulation. It does not infer Sony or
Nikon production RD search.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import numpy as np

import strict_824_826_math_eval as strict


SONY_POLICIES: dict[str, dict[str, Any]] = {
    "canonical_component": {
        "class": "component_map",
        "map": {"Gbase": 0, "Gphase_final": 1, "Rres2": 2, "Bres2": 2},
        "description": "Strict baseline: green base finest, green phase mid, R/B residual coarser.",
    },
    "all_selector_0": {
        "class": "component_map",
        "map": {"Gbase": 0, "Gphase_final": 0, "Rres2": 0, "Bres2": 0},
        "description": "Uniform selector 0 for all components; base_step alone controls rate.",
    },
    "all_selector_1": {
        "class": "component_map",
        "map": {"Gbase": 1, "Gphase_final": 1, "Rres2": 1, "Bres2": 1},
        "description": "Uniform selector 1 for all components.",
    },
    "all_selector_2": {
        "class": "component_map",
        "map": {"Gbase": 2, "Gphase_final": 2, "Rres2": 2, "Bres2": 2},
        "description": "Uniform selector 2 for all components.",
    },
    "green_protect": {
        "class": "component_map",
        "map": {"Gbase": 0, "Gphase_final": 0, "Rres2": 2, "Bres2": 2},
        "description": "Spend relatively more precision on green components.",
    },
    "rb_protect": {
        "class": "component_map",
        "map": {"Gbase": 1, "Gphase_final": 2, "Rres2": 0, "Bres2": 0},
        "description": "Spend relatively more precision on red/blue residual components.",
    },
    "phase_protect": {
        "class": "component_map",
        "map": {"Gbase": 1, "Gphase_final": 0, "Rres2": 2, "Bres2": 2},
        "description": "Spend relatively more precision on green phase difference.",
    },
    "balanced_0123_cycle": {
        "class": "row_cycle",
        "cycle": [0, 1, 2, 3],
        "description": "Deterministic row-cycle selector distribution.",
    },
    "low_selector_cycle": {
        "class": "row_cycle",
        "cycle": [0, 0, 0, 1, 1, 2],
        "description": "Low-selector-biased deterministic row cycle.",
    },
}


NIKON_COMPONENTS = ("p1_LL_step1", "p2_LH_step1", "p3_HH_step1", "p4_HL_step1")

NIKON_POLICIES: dict[str, dict[str, Any]] = {
    "global_canonical": {
        "offsets": {name: 0 for name in NIKON_COMPONENTS},
        "description": "Strict baseline: one global quality bias applied to all components.",
    },
    "green_step_priority": {
        "offsets": {"p1_LL_step1": -1, "p2_LH_step1": 1, "p3_HH_step1": 1, "p4_HL_step1": -1},
        "description": "Spend relatively more precision on step1 green-reconstruction components.",
    },
    "rb_residual_priority": {
        "offsets": {"p1_LL_step1": 1, "p2_LH_step1": -1, "p3_HH_step1": -1, "p4_HL_step1": 1},
        "description": "Spend relatively more precision on red/blue residual endpoints.",
    },
    "p1_priority": {
        "offsets": {"p1_LL_step1": 2, "p2_LH_step1": -1, "p3_HH_step1": -1, "p4_HL_step1": -1},
        "description": "Overweight p1 while underweighting the other three components.",
    },
    "p4_priority": {
        "offsets": {"p1_LL_step1": -1, "p2_LH_step1": -1, "p3_HH_step1": -1, "p4_HL_step1": 2},
        "description": "Overweight p4 while underweighting the other three components.",
    },
    "detail_priority": {
        "offsets": {"p1_LL_step1": -1, "p2_LH_step1": 1, "p3_HH_step1": 1, "p4_HL_step1": 1},
        "description": "Overweight three detail-ish components relative to p1.",
    },
    "smooth_priority": {
        "offsets": {"p1_LL_step1": 1, "p2_LH_step1": 0, "p3_HH_step1": -1, "p4_HL_step1": 0},
        "description": "A smoother lowpass-biased allocation.",
    },
}


@dataclass(frozen=True)
class TaskConfig:
    source_id: str
    planes: dict[str, np.ndarray]
    targets: tuple[float, ...]
    levels: int
    nikon_bias_count: int
    codec: str
    policy_name: str
    policy: dict[str, Any]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"no rows for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def balanced_selector_cycle(hist: dict[int, int]) -> list[int]:
    total = sum(hist.values())
    if total <= 0:
        return [0, 1, 2, 3]
    counts = {selector: 0 for selector in hist}
    cycle: list[int] = []
    for i in range(total):
        selector = max(
            sorted(hist),
            key=lambda s: ((i + 1) * hist[s] / total) - counts[s],
        )
        cycle.append(selector)
        counts[selector] += 1
    return cycle


def read_sony_real_selector_cycle(path: Path) -> tuple[int, ...]:
    if not path.exists():
        return tuple(SONY_POLICIES["low_selector_cycle"]["cycle"])
    hist: dict[int, int] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("codec_family") != "sony_arw6_llvc3_hq":
                continue
            raw = row.get("selector_hist") or ""
            if not raw:
                continue
            parsed = json.loads(raw)
            for key, value in parsed.items():
                hist[int(key)] = hist.get(int(key), 0) + int(value)
    if not hist:
        return tuple(SONY_POLICIES["low_selector_cycle"]["cycle"])
    return tuple(balanced_selector_cycle(hist))


def sony_selector(policy: dict[str, Any], comp_index: int, name: str, row: int) -> int:
    if policy["class"] == "component_map":
        return int(policy["map"][name])
    cycle = policy["cycle"]
    return int(cycle[(comp_index * 1_000_003 + row) % len(cycle)])


def sony_syntax_encode_policy(
    coeffs: dict[str, np.ndarray],
    base_step: float,
    policy: dict[str, Any],
) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    deq: dict[str, np.ndarray] = {}
    payload_bits = 0
    sign_bits = 0
    selector_bits = 0
    control_bits = 0
    zero_run_bits = 0
    width_bits = 0
    groups_total = 0
    nonzero_groups = 0
    selector_sum = 0
    selector_count = 0
    selector_counts: dict[int, int] = {}

    for comp_index, (name, coeff) in enumerate(coeffs.items()):
        flat = np.asarray(coeff, dtype=np.float64).reshape(-1, coeff.shape[-1])
        out = np.empty_like(flat, dtype=np.float64)
        rows = flat.shape[0]
        control_bits += 128 + rows * (16 + 4)
        selector_bits += rows * 4
        for y in range(rows):
            row = flat[y]
            selector = sony_selector(policy, comp_index, name, y)
            selector_sum += selector
            selector_count += 1
            selector_counts[selector] = selector_counts.get(selector, 0) + 1
            groups = int(math.ceil(row.size / 4))
            width_state = 0
            gi = 0
            while gi < groups:
                vals = np.zeros(4, dtype=np.float64)
                start = gi * 4
                chunk = row[start:start + 4]
                vals[: chunk.size] = chunk
                q = strict.sony_quantize_group(vals, base_step, selector)
                width = int(max(0, max((abs(int(v)).bit_length() for v in q), default=0)))
                groups_total += 1
                width_bits += strict.bits_for_sony_width_update(width_state, width)
                if width == 0:
                    run = 1
                    while gi + run < groups:
                        nxt = np.zeros(4, dtype=np.float64)
                        ns = (gi + run) * 4
                        nchunk = row[ns:ns + 4]
                        nxt[: nchunk.size] = nchunk
                        if np.any(strict.sony_quantize_group(nxt, base_step, selector)):
                            break
                        run += 1
                    zero_run_bits += strict.bits_for_sony_zero_run(run, groups - gi)
                    for rz in range(run):
                        zs = (gi + rz) * 4
                        out[y, zs:zs + 4] = 0.0
                    gi += run
                    width_state = 0
                    continue
                nonzero_groups += 1
                payload_bits += width * 4
                sign_bits += int(np.count_nonzero(q))
                out[y, start:start + 4] = strict.sony_dequantize_group(q, base_step, selector)
                width_state = width
                gi += 1
        deq[name] = out.reshape(coeff.shape)

    total_bits = control_bits + selector_bits + width_bits + zero_run_bits + payload_bits + sign_bits
    return deq, {
        "syntax_total_bits": float(total_bits),
        "control_bits": float(control_bits),
        "selector_bits": float(selector_bits),
        "width_update_bits": float(width_bits),
        "zero_run_bits": float(zero_run_bits),
        "payload_bits": float(payload_bits),
        "sign_bits": float(sign_bits),
        "groups_total": float(groups_total),
        "nonzero_groups": float(nonzero_groups),
        "mean_selector": float(selector_sum / max(1, selector_count)),
        "selector_0_groups": float(selector_counts.get(0, 0)),
        "selector_1_groups": float(selector_counts.get(1, 0)),
        "selector_2_groups": float(selector_counts.get(2, 0)),
        "selector_3_groups": float(selector_counts.get(3, 0)),
    }


def nikon_syntax_encode_policy(
    coeffs: dict[str, np.ndarray],
    bp: int,
    br: int,
    global_bias: int,
    component_offsets: dict[str, int],
) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    deq: dict[str, np.ndarray] = {}
    header_bits = 0
    sig_bits = 0
    gcli_bits = 0
    data_bits = 0
    sign_bits = 0
    group_count = 0
    nonzero_groups = 0
    gtli_values: list[int] = []
    effective_gtli_values: list[int] = []
    gtli_row = strict.GTLI_ROWS[(bp, br)]

    for name, coeff in coeffs.items():
        sb = strict.NIKON_COMPONENT_TO_SUBBAND[name]
        gtli = max(0, int(gtli_row[sb]))
        effective_gtli = max(0, gtli - global_bias - int(component_offsets.get(name, 0)))
        gtli_values.append(gtli)
        effective_gtli_values.append(effective_gtli)
        flat = np.asarray(coeff, dtype=np.float64).reshape(-1)
        q, gcli = strict.nikon_quantize_nearest_dequant(flat, effective_gtli)
        q_groups = q.reshape(-1, 4) if q.size % 4 == 0 else np.pad(q, (0, 4 - q.size % 4)).reshape(-1, 4)
        dq = strict.nikon_dequantize(q_groups.reshape(-1), gcli, effective_gtli)[: flat.size].reshape(coeff.shape)
        deq[name] = dq

        groups = int(gcli.size)
        group_count += groups
        nonzero_groups += int(np.count_nonzero(np.any(q_groups != 0, axis=1)))
        header_bits += 24 + 8 + 8 + 56 + 56
        sig_blocks = int(math.ceil(groups / 8))
        sig_bits += sig_blocks
        for start in range(0, groups, 8):
            block = gcli[start:start + 8]
            if np.all(block == effective_gtli):
                continue
            for gv in block:
                gcli_bits += int(max(0, gv - effective_gtli)) + 1
        for gi, group in enumerate(q_groups):
            bitplanes = int(max(0, gcli[gi] - effective_gtli))
            data_bits += bitplanes * 4
            sign_bits += int(np.count_nonzero(group))

    total_bits = header_bits + sig_bits + gcli_bits + data_bits + sign_bits
    return deq, {
        "syntax_total_bits": float(total_bits),
        "header_bits": float(header_bits),
        "sig_bits": float(sig_bits),
        "gcli_bits": float(gcli_bits),
        "data_bits": float(data_bits),
        "sign_bits": float(sign_bits),
        "groups_total": float(group_count),
        "nonzero_groups": float(nonzero_groups),
        "mean_bp": float(bp),
        "mean_br": float(br),
        "mean_gtli": float(statistics.mean(gtli_values)),
        "mean_effective_gtli": float(statistics.mean(effective_gtli_values)),
    }


def collect_candidate_metrics(source: dict[str, np.ndarray], recon: dict[str, np.ndarray]) -> dict[str, float]:
    # The sweep can generate thousands of candidates.  Keep the full-candidate
    # pass focused on RAW sample-domain quality, which is the metric needed for
    # same-bpp range/Pareto discussion.  The main strict evaluator already
    # carries the heavier SSIM/MS-SSIM/GMSD evidence.
    return {f"{key}_whole": value for key, value in strict.metric_summary(strict.flatten_planes(source), strict.flatten_planes(recon)).items()}


def finish_row(
    source: dict[str, np.ndarray],
    source_id: str,
    codec: str,
    policy_name: str,
    policy_class: str,
    target_bpp: float,
    actual_bpp: float,
    knob: float,
    recon: dict[str, np.ndarray],
    syntax: dict[str, float],
    extra: dict[str, Any],
    encode_ms: float,
) -> dict[str, Any]:
    metrics = collect_candidate_metrics(source, recon)
    row: dict[str, Any] = {
        "codec": codec,
        "source_id": source_id,
        "policy_name": policy_name,
        "policy_class": policy_class,
        "target_bpp": f"{target_bpp:.6f}",
        "actual_bpp": f"{actual_bpp:.9f}",
        "rate_error_to_target": f"{actual_bpp - target_bpp:.9f}",
        "knob": f"{knob:.9f}",
        "encode_ms": f"{encode_ms:.3f}",
    }
    for key, value in extra.items():
        row[key] = value
    for key in [
        "syntax_total_bits", "control_bits", "selector_bits", "width_update_bits",
        "zero_run_bits", "payload_bits", "header_bits", "sig_bits", "gcli_bits",
        "data_bits", "sign_bits", "groups_total", "nonzero_groups", "mean_selector",
        "mean_bp", "mean_br", "mean_gtli", "mean_effective_gtli",
    ]:
        if key in syntax:
            row[key] = f"{float(syntax[key]):.9f}"
    groups_total = float(syntax.get("groups_total", 0.0))
    nonzero_groups = float(syntax.get("nonzero_groups", 0.0))
    row["nonzero_group_fraction"] = f"{nonzero_groups / max(1.0, groups_total):.9f}"
    for key, value in metrics.items():
        row[key] = f"{float(value):.9f}"
    return row


def encode_sony_policy_targets(
    source: dict[str, np.ndarray],
    source_id: str,
    targets: tuple[float, ...],
    levels: int,
    policy_name: str,
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    start_total = time.perf_counter()
    comps = strict.sony_forward(source)
    coeffs, sizes = strict.transform_components(comps, levels)
    pixel_count = int(next(iter(source.values())).size * 4)
    rows: list[dict[str, Any]] = []
    for target_bpp in targets:
        start = time.perf_counter()
        lo, hi = 0.25, 4096.0
        for _ in range(24):
            mid = math.sqrt(lo * hi)
            _deq, syntax = sony_syntax_encode_policy(coeffs, mid, policy)
            rate = syntax["syntax_total_bits"] / pixel_count
            if rate > target_bpp:
                lo = mid
            else:
                hi = mid
        knob = hi
        deq, syntax = sony_syntax_encode_policy(coeffs, knob, policy)
        actual_bpp = syntax["syntax_total_bits"] / pixel_count
        recon = strict.sony_inverse(strict.inverse_transform_components(deq, sizes))
        rows.append(
            finish_row(
                source,
                source_id,
                strict.SONY_CODEC,
                policy_name,
                str(policy["class"]),
                target_bpp,
                actual_bpp,
                knob,
                recon,
                syntax,
                {
                    "selector_policy": policy_name,
                    "selector_map": json.dumps(policy.get("map", {}), sort_keys=True),
                    "selector_cycle_len": len(policy.get("cycle", [])),
                },
                (time.perf_counter() - start) * 1000.0,
            )
        )
    elapsed = (time.perf_counter() - start_total) * 1000.0
    for row in rows:
        row["policy_total_ms"] = f"{elapsed:.3f}"
    return rows


def encode_nikon_policy_targets(
    source: dict[str, np.ndarray],
    source_id: str,
    targets: tuple[float, ...],
    levels: int,
    policy_name: str,
    offsets: dict[str, int],
    bias_count: int,
) -> list[dict[str, Any]]:
    start_total = time.perf_counter()
    comps = strict.nikon_forward(source)
    coeffs, sizes = strict.transform_components(comps, levels)
    pixel_count = int(next(iter(source.values())).size * 4)
    keys = list(strict.GTLI_ROWS.keys())
    best: dict[float, tuple[float, float, tuple[int, int], int, dict[str, np.ndarray], dict[str, float], float]] = {}
    for global_bias in range(bias_count):
        for row_index, (bp, br) in enumerate(keys):
            start = time.perf_counter()
            deq, syntax = nikon_syntax_encode_policy(coeffs, bp, br, global_bias, offsets)
            rate = syntax["syntax_total_bits"] / pixel_count
            encode_ms = (time.perf_counter() - start) * 1000.0
            knob = float(global_bias * len(keys) + row_index)
            for target_bpp in targets:
                score = abs(rate - target_bpp)
                if target_bpp not in best or score < best[target_bpp][0]:
                    best[target_bpp] = (score, knob, (bp, br), global_bias, deq, syntax, encode_ms)

    rows: list[dict[str, Any]] = []
    for target_bpp in targets:
        score, knob, (bp, br), global_bias, deq, syntax, encode_ms = best[target_bpp]
        actual_bpp = syntax["syntax_total_bits"] / pixel_count
        recon = strict.nikon_inverse(strict.inverse_transform_components(deq, sizes))
        rows.append(
            finish_row(
                source,
                source_id,
                strict.NIKON_CODEC,
                policy_name,
                "component_gtli_bias",
                target_bpp,
                actual_bpp,
                knob,
                recon,
                syntax,
                {
                    "gtli_policy": policy_name,
                    "bp": bp,
                    "br": br,
                    "global_bias": global_bias,
                    "component_offsets": json.dumps(offsets, sort_keys=True),
                    "nearest_rate_abs_error": f"{score:.9f}",
                },
                encode_ms,
            )
        )
    elapsed = (time.perf_counter() - start_total) * 1000.0
    for row in rows:
        row["policy_total_ms"] = f"{elapsed:.3f}"
    return rows


def evaluate_policy_task(config: TaskConfig) -> tuple[str, str, list[dict[str, Any]]]:
    if config.codec == strict.SONY_CODEC:
        rows = encode_sony_policy_targets(config.planes, config.source_id, config.targets, config.levels, config.policy_name, config.policy)
    elif config.codec == strict.NIKON_CODEC:
        rows = encode_nikon_policy_targets(
            config.planes,
            config.source_id,
            config.targets,
            config.levels,
            config.policy_name,
            {k: int(v) for k, v in config.policy["offsets"].items()},
            config.nikon_bias_count,
        )
    else:
        raise ValueError(f"unknown codec {config.codec}")
    return config.source_id, config.policy_name, rows


def higher_is_better(metric: str) -> bool:
    return metric in {"PSNR_raw_whole", "grad_psnr_detail", "ssim_mean_detail", "ms_ssim_mean_detail"}


def is_better(metric: str, a: float, b: float) -> bool:
    return a > b if higher_is_better(metric) else a < b


def metric_delta(metric: str, candidate: float, baseline: float) -> float:
    return candidate - baseline if higher_is_better(metric) else baseline - candidate


def pareto_front(rows: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    front: list[dict[str, Any]] = []
    for row in rows:
        rate = float(row["actual_bpp"])
        quality = float(row[metric])
        dominated = False
        for other in rows:
            if other is row:
                continue
            orate = float(other["actual_bpp"])
            oquality = float(other[metric])
            no_worse_rate = orate <= rate + 1e-12
            no_worse_quality = oquality >= quality - 1e-12 if higher_is_better(metric) else oquality <= quality + 1e-12
            strictly = orate < rate - 1e-12 or is_better(metric, oquality, quality)
            if no_worse_rate and no_worse_quality and strictly:
                dominated = True
                break
        if not dominated:
            front.append(row)
    return front


def summarize_policy_multiplicity(rows: list[dict[str, Any]], rate_tolerance: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["codec"]), str(row["source_id"]), str(row["target_bpp"]))
        by_key.setdefault(key, []).append(row)

    compare_rows: list[dict[str, Any]] = []
    pareto_rows: list[dict[str, Any]] = []
    for (codec, source_id, target_bpp), group in sorted(by_key.items()):
        baseline_name = "canonical_component" if codec == strict.SONY_CODEC else "global_canonical"
        baseline = next((r for r in group if r["policy_name"] == baseline_name), None)
        if baseline is None:
            continue
        baseline_rate = float(baseline["actual_bpp"])
        window = [r for r in group if abs(float(r["actual_bpp"]) - baseline_rate) <= rate_tolerance]
        if not window:
            window = [baseline]
        for metric in ["PSNR_raw_whole", "MAE_whole", "MAX_whole"]:
            values = [float(r[metric]) for r in window]
            best = max(window, key=lambda r: float(r[metric])) if higher_is_better(metric) else min(window, key=lambda r: float(r[metric]))
            worst = min(window, key=lambda r: float(r[metric])) if higher_is_better(metric) else max(window, key=lambda r: float(r[metric]))
            bval = float(baseline[metric])
            best_val = float(best[metric])
            worst_val = float(worst[metric])
            compare_rows.append(
                {
                    "codec": codec,
                    "source_id": source_id,
                    "target_bpp": target_bpp,
                    "metric": metric,
                    "rate_tolerance": f"{rate_tolerance:.6f}",
                    "candidate_count_window": len(window),
                    "policy_count_total": len(group),
                    "baseline_policy": baseline_name,
                    "baseline_actual_bpp": baseline["actual_bpp"],
                    "baseline_value": f"{bval:.9f}",
                    "best_policy_same_bpp": best["policy_name"],
                    "best_actual_bpp": best["actual_bpp"],
                    "best_value": f"{best_val:.9f}",
                    "best_gain_over_baseline": f"{metric_delta(metric, best_val, bval):.9f}",
                    "worst_policy_same_bpp": worst["policy_name"],
                    "worst_actual_bpp": worst["actual_bpp"],
                    "worst_value": f"{worst_val:.9f}",
                    "same_bpp_spread": f"{metric_delta(metric, best_val, worst_val):.9f}",
                    "value_min": f"{min(values):.9f}",
                    "value_median": f"{statistics.median(values):.9f}",
                    "value_max": f"{max(values):.9f}",
                    "actual_bpp_min_window": f"{min(float(r['actual_bpp']) for r in window):.9f}",
                    "actual_bpp_max_window": f"{max(float(r['actual_bpp']) for r in window):.9f}",
                }
            )
        front = pareto_front(group, "PSNR_raw_whole")
        front_names = {r["policy_name"] for r in front}
        pareto_rows.append(
            {
                "codec": codec,
                "source_id": source_id,
                "target_bpp": target_bpp,
                "policy_count_total": len(group),
                "pareto_count_psnr": len(front),
                "baseline_policy": baseline_name,
                "baseline_on_psnr_pareto": baseline_name in front_names,
                "pareto_policies_psnr": ",".join(sorted(front_names)),
            }
        )

    summary_rows: list[dict[str, Any]] = []
    summary_keyed: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in compare_rows:
        summary_keyed.setdefault((str(row["codec"]), str(row["target_bpp"]), str(row["metric"])), []).append(row)
    for (codec, target_bpp, metric), group in sorted(summary_keyed.items()):
        gains = [float(r["best_gain_over_baseline"]) for r in group]
        spreads = [float(r["same_bpp_spread"]) for r in group]
        counts = [int(r["candidate_count_window"]) for r in group]
        summary_rows.append(
            {
                "codec": codec,
                "target_bpp": target_bpp,
                "metric": metric,
                "source_count": len(group),
                "median_candidates_in_window": f"{statistics.median(counts):.3f}",
                "median_best_gain_over_baseline": f"{statistics.median(gains):.9f}",
                "p90_best_gain_over_baseline": f"{np.percentile(gains, 90):.9f}",
                "median_same_bpp_spread": f"{statistics.median(spreads):.9f}",
                "p90_same_bpp_spread": f"{np.percentile(spreads, 90):.9f}",
                "max_same_bpp_spread": f"{max(spreads):.9f}",
            }
        )

    pareto_keyed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in pareto_rows:
        pareto_keyed.setdefault((str(row["codec"]), str(row["target_bpp"])), []).append(row)
    for (codec, target_bpp), group in sorted(pareto_keyed.items()):
        flags = [str(r["baseline_on_psnr_pareto"]).lower() == "true" for r in group]
        summary_rows.append(
            {
                "codec": codec,
                "target_bpp": target_bpp,
                "metric": "PSNR_pareto_front",
                "source_count": len(group),
                "median_candidates_in_window": "",
                "median_best_gain_over_baseline": "",
                "p90_best_gain_over_baseline": "",
                "median_same_bpp_spread": "",
                "p90_same_bpp_spread": "",
                "max_same_bpp_spread": "",
                "baseline_on_pareto_fraction": f"{sum(flags) / max(1, len(flags)):.6f}",
                "median_pareto_count": f"{statistics.median(int(r['pareto_count_psnr']) for r in group):.3f}",
            }
        )

    return compare_rows, summary_rows, pareto_rows


def maybe_write_figures(out_dir: Path, rows: list[dict[str, Any]], summary_rows: list[dict[str, Any]]) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), dpi=150, sharey=False)
    for ax, codec in zip(axes, [strict.SONY_CODEC, strict.NIKON_CODEC]):
        codec_rows = [r for r in rows if r["codec"] == codec]
        policies = sorted({r["policy_name"] for r in codec_rows})
        cmap = plt.get_cmap("tab20")
        for idx, policy in enumerate(policies):
            pr = [r for r in codec_rows if r["policy_name"] == policy]
            ax.scatter(
                [float(r["actual_bpp"]) for r in pr],
                [float(r["PSNR_raw_whole"]) for r in pr],
                s=10,
                alpha=0.55,
                color=cmap(idx % 20),
                label=policy if idx < 10 else None,
            )
        ax.set_title("Sony #824 policy sweep" if codec == strict.SONY_CODEC else "Nikon #826 policy sweep")
        ax.set_xlabel("actual syntax bpp")
        ax.set_ylabel("whole PSNR (dB)")
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=6, loc="lower right", frameon=False)
    fig.tight_layout()
    fig.savefig(out_dir / "fig_policy_sweep_rate_psnr.png")
    plt.close(fig)


def parse_targets(raw: str) -> tuple[float, ...]:
    return tuple(float(x.strip()) for x in raw.split(",") if x.strip())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=Path("out/bpp_policy_multiplicity_20260604"))
    ap.add_argument("--targets", default="1.5,2.0,2.5,3.0,4.0,5.0")
    ap.add_argument("--width", type=int, default=256)
    ap.add_argument("--height", type=int, default=256)
    ap.add_argument("--levels", type=int, default=3)
    ap.add_argument("--seed", type=int, default=20260604)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--scenes", default="", help="Comma-separated subset of scene names for smoke tests.")
    ap.add_argument("--rate-tolerance", type=float, default=0.08)
    ap.add_argument("--nikon-bias-count", type=int, default=7)
    ap.add_argument("--real-controls", type=Path, default=Path("out/production_fit_samples/real_bitstream_controls.csv"))
    ns = ap.parse_args()

    targets = parse_targets(ns.targets)
    rng = np.random.default_rng(ns.seed)
    scene_names = strict.SCENES
    if ns.scenes.strip():
        wanted = {x.strip() for x in ns.scenes.split(",") if x.strip()}
        scene_names = [x for x in strict.SCENES if x in wanted]
        missing = sorted(wanted - set(scene_names))
        if missing:
            raise SystemExit(f"unknown scenes: {missing}")
    sources = {scene: strict.generate_scene(scene, ns.height // 2, ns.width // 2, rng) for scene in scene_names}
    real_cycle = read_sony_real_selector_cycle(ns.real_controls)
    sony_policies = dict(SONY_POLICIES)
    sony_policies["real_hq_selector_cycle"] = {
        "class": "row_cycle",
        "cycle": list(real_cycle),
        "description": "Selector cycle fitted from public Sony HQ raw.pixls samples.",
    }
    tasks: list[TaskConfig] = []
    for scene, planes in sources.items():
        for policy_name, policy in sony_policies.items():
            tasks.append(TaskConfig(scene, planes, targets, ns.levels, ns.nikon_bias_count, strict.SONY_CODEC, policy_name, policy))
        for policy_name, policy in NIKON_POLICIES.items():
            tasks.append(TaskConfig(scene, planes, targets, ns.levels, ns.nikon_bias_count, strict.NIKON_CODEC, policy_name, policy))

    t0 = time.perf_counter()
    all_rows: list[dict[str, Any]] = []
    jobs = max(1, int(ns.jobs))
    if jobs == 1:
        for task in tasks:
            _source_id, _policy, rows = evaluate_policy_task(task)
            all_rows.extend(rows)
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(evaluate_policy_task, task): (task.source_id, task.policy_name) for task in tasks}
            for fut in as_completed(futures):
                source_id, policy_name = futures[fut]
                _sid, _policy, rows = fut.result()
                all_rows.extend(rows)
                print(f"completed {source_id}/{policy_name}: {len(rows)} candidates", flush=True)

    ns.out_dir.mkdir(parents=True, exist_ok=True)
    all_rows.sort(key=lambda r: (str(r["codec"]), str(r["source_id"]), float(r["target_bpp"]), str(r["policy_name"])))
    write_csv(ns.out_dir / "policy_candidates.csv", all_rows)
    compare_rows, summary_rows, pareto_rows = summarize_policy_multiplicity(all_rows, ns.rate_tolerance)
    write_csv(ns.out_dir / "canonical_vs_same_bpp_best.csv", compare_rows)
    write_csv(ns.out_dir / "policy_dispersion_summary.csv", summary_rows)
    write_csv(ns.out_dir / "pareto_summary.csv", pareto_rows)
    maybe_write_figures(ns.out_dir, all_rows, summary_rows)

    manifest = {
        "kind": "bpp policy multiplicity sweep for strict #824/#826 decoder-visible canonical modeling",
        "seed": ns.seed,
        "width": ns.width,
        "height": ns.height,
        "levels": ns.levels,
        "targets_bpp": list(targets),
        "source_count": len(sources),
        "jobs_requested": jobs,
        "cpu_count": os.cpu_count(),
        "elapsed_seconds": time.perf_counter() - t0,
        "rate_tolerance": ns.rate_tolerance,
        "nikon_bias_count": ns.nikon_bias_count,
        "sony_policy_count": len(SONY_POLICIES) + 1,
        "nikon_policy_count": len(NIKON_POLICIES),
        "candidate_rows": len(all_rows),
        "comparison_rows": len(compare_rows),
        "strict_boundary": (
            "Policy multiplicity is evaluated inside decoder-visible syntax and math only. "
            "Rows do not claim production encoder optimality or equivalence."
        ),
        "outputs": {
            "policy_candidates": str(ns.out_dir / "policy_candidates.csv"),
            "canonical_vs_same_bpp_best": str(ns.out_dir / "canonical_vs_same_bpp_best.csv"),
            "policy_dispersion_summary": str(ns.out_dir / "policy_dispersion_summary.csv"),
            "pareto_summary": str(ns.out_dir / "pareto_summary.csv"),
        },
    }
    (ns.out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
