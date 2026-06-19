#!/usr/bin/env python3
"""Run a Sony selector-distribution sensitivity experiment.

The strict canonical evaluator uses fixed component selectors. Real Sony HQ
packet probes show a lower selector mean. This script keeps the same synthetic
sources, transform, rate search, and metrics, but swaps only the Sony selector
assignment to a deterministic cycle fitted to the real selector histogram.
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

import numpy as np

import strict_824_826_math_eval as strict


DEFAULT_REAL_CONTROLS = Path("out/production_fit_samples/real_bitstream_controls.csv")


@dataclass
class FittedSelectorPolicy:
    cycle: tuple[int, ...]
    histogram: dict[int, int]


def read_real_selector_policy(path: Path) -> FittedSelectorPolicy:
    hist: dict[int, int] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("codec_family") != "sony_arw6_llvc3_hq":
                continue
            raw = row.get("selector_hist") or ""
            if not raw:
                continue
            for key, value in json.loads(raw).items():
                hist[int(key)] = hist.get(int(key), 0) + int(value)
    if not hist:
        raise RuntimeError(f"no Sony selector_hist rows in {path}")
    cycle = balanced_selector_cycle(hist)
    if not cycle:
        raise RuntimeError("empty fitted selector cycle")
    return FittedSelectorPolicy(cycle=tuple(cycle), histogram=hist)


def balanced_selector_cycle(hist: dict[int, int]) -> list[int]:
    """Build a deterministic low-discrepancy cycle from a selector histogram."""

    total = sum(hist.values())
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


def bits_for_sony_width_update(prev: int, new: int) -> int:
    return strict.bits_for_sony_width_update(prev, new)


def bits_for_sony_zero_run(run: int, remaining: int) -> int:
    return strict.bits_for_sony_zero_run(run, remaining)


def fitted_sony_syntax_encode(
    coeffs: dict[str, np.ndarray],
    base_step: float,
    selector_cycle: tuple[int, ...],
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
    selector_counts: dict[int, int] = {}
    cycle_len = len(selector_cycle)

    for comp_index, (name, coeff) in enumerate(coeffs.items()):
        flat = np.asarray(coeff, dtype=np.float64).reshape(-1, coeff.shape[-1])
        out = np.empty_like(flat, dtype=np.float64)
        rows = flat.shape[0]
        control_bits += 128 + rows * (16 + 4)
        selector_bits += rows * 4
        for y in range(rows):
            row = flat[y]
            groups = int(math.ceil(row.size / 4))
            width_state = 0
            gi = 0
            selector = selector_cycle[(comp_index * 1_000_003 + y) % cycle_len]
            selector_sum += selector
            selector_counts[selector] = selector_counts.get(selector, 0) + 1
            while gi < groups:
                vals = np.zeros(4, dtype=np.float64)
                start = gi * 4
                chunk = row[start:start+4]
                vals[: chunk.size] = chunk
                q = strict.sony_quantize_group(vals, base_step, selector)
                width = int(max(0, max((abs(int(v)).bit_length() for v in q), default=0)))
                groups_total += 1
                width_bits += bits_for_sony_width_update(width_state, width)
                if width == 0:
                    run = 1
                    while gi + run < groups:
                        nxt = np.zeros(4, dtype=np.float64)
                        ns = (gi + run) * 4
                        nchunk = row[ns:ns+4]
                        nxt[:nchunk.size] = nchunk
                        if np.any(strict.sony_quantize_group(nxt, base_step, selector)):
                            break
                        run += 1
                    zero_run_bits += bits_for_sony_zero_run(run, groups - gi)
                    for rz in range(run):
                        zs = (gi + rz) * 4
                        out[y, zs:zs+4] = 0.0
                    gi += run
                    width_state = 0
                    continue
                nonzero_groups += 1
                payload_bits += width * 4
                sign_bits += int(np.count_nonzero(q))
                out[y, start:start+4] = strict.sony_dequantize_group(q, base_step, selector)
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
        "mean_selector": float(selector_sum / max(1, sum(selector_counts.values()))),
        "selector_0_groups": float(selector_counts.get(0, 0)),
        "selector_1_groups": float(selector_counts.get(1, 0)),
        "selector_2_groups": float(selector_counts.get(2, 0)),
        "selector_3_groups": float(selector_counts.get(3, 0)),
        "canonical_policy": 0.0,
        "stream_fitted_policy": 1.0,
    }


def encode_fitted(
    planes: dict[str, np.ndarray],
    source_id: str,
    target_bpp: float,
    coeffs: dict[str, np.ndarray],
    sizes: dict[str, list[tuple[int, int]]],
    selector_cycle: tuple[int, ...],
) -> strict.EncodeResult:
    start = time.perf_counter()
    pixel_count = int(next(iter(planes.values())).size * 4)
    lo, hi = 0.25, 4096.0
    for _ in range(24):
        mid = math.sqrt(lo * hi)
        _deq, syntax = fitted_sony_syntax_encode(coeffs, mid, selector_cycle)
        rate = syntax["syntax_total_bits"] / pixel_count
        if rate > target_bpp:
            lo = mid
        else:
            hi = mid
    knob = hi
    deq_coeffs, syntax = fitted_sony_syntax_encode(coeffs, knob, selector_cycle)
    actual_bpp = syntax["syntax_total_bits"] / pixel_count
    recon_comps = strict.inverse_transform_components(deq_coeffs, sizes)
    recon = strict.sony_inverse(recon_comps)
    return strict.EncodeResult(
        "sony_824_stream_fitted_selector_sensitivity",
        source_id,
        target_bpp,
        actual_bpp,
        knob,
        (time.perf_counter() - start) * 1000.0,
        recon,
        syntax,
    )


def evaluate_source_task(
    source_id: str,
    planes: dict[str, np.ndarray],
    targets: list[float],
    levels: int,
    selector_cycle: tuple[int, ...],
) -> tuple[str, list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    comps = strict.sony_forward(planes)
    coeffs, sizes = strict.transform_components(comps, levels)
    encode_rows: list[dict[str, object]] = []
    metric_rows: list[dict[str, object]] = []
    syntax_rows: list[dict[str, object]] = []
    for target in targets:
        result = encode_fitted(planes, source_id, target, coeffs, sizes, selector_cycle)
        encode_rows.append(
            {
                "codec": result.codec,
                "source_id": source_id,
                "target_bpp": f"{result.target_bpp:.6f}",
                "actual_bpp": f"{result.actual_bpp:.9f}",
                "knob_name": "base_step_stream_fitted_selector",
                "knob": f"{result.knob:.9f}",
                "encode_ms": f"{result.encode_ms:.3f}",
            }
        )
        syntax_rows.append(
            {
                "codec": result.codec,
                "source_id": source_id,
                "target_bpp": f"{result.target_bpp:.6f}",
                **{k: f"{v:.9f}" for k, v in result.syntax.items()},
            }
        )
        metric_rows.extend(strict.collect_metrics(planes, result))
    return source_id, encode_rows, metric_rows, syntax_rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    strict.write_csv(path, rows)


def write_summaries(out_dir: Path, encode_rows: list[dict[str, object]], metric_rows: list[dict[str, object]]) -> None:
    metric_lookup: dict[tuple[str, str, str], list[float]] = {}
    for row in metric_rows:
        key = (str(row["target_bpp"]), str(row["metric"]), str(row["split"]))
        metric_lookup.setdefault(key, []).append(float(row["value"]))

    target_metric_rows: list[dict[str, object]] = []
    for target, metric, split in sorted(metric_lookup):
        values = metric_lookup[(target, metric, split)]
        target_metric_rows.append(
            {
                "target_bpp": target,
                "metric": metric,
                "split": split,
                "median": f"{statistics.median(values):.9f}",
                "min": f"{min(values):.9f}",
                "max": f"{max(values):.9f}",
                "n": len(values),
            }
        )
    write_csv(out_dir / "target_metric_summary.csv", target_metric_rows)

    rate_rows: list[dict[str, object]] = []
    for target in sorted({str(row["target_bpp"]) for row in encode_rows}):
        rows_for_target = [row for row in encode_rows if str(row["target_bpp"]) == target]
        rates = [float(row["actual_bpp"]) for row in rows_for_target]
        rate_rows.append(
            {
                "target_bpp": target,
                "codec": "sony_824_stream_fitted_selector_sensitivity",
                "actual_bpp_min": f"{min(rates):.9f}",
                "actual_bpp_median": f"{statistics.median(rates):.9f}",
                "actual_bpp_max": f"{max(rates):.9f}",
                "n": len(rates),
            }
        )
    write_csv(out_dir / "rate_summary.csv", rate_rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=Path("out/sony_stream_fitted_selector_eval_20260604"))
    ap.add_argument("--real-controls", type=Path, default=DEFAULT_REAL_CONTROLS)
    ap.add_argument("--width", type=int, default=256)
    ap.add_argument("--height", type=int, default=256)
    ap.add_argument("--levels", type=int, default=3)
    ap.add_argument("--targets", default="1.5,2.0,2.5,3.0,4.0,5.0")
    ap.add_argument("--seed", type=int, default=20260603)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ns = ap.parse_args()

    if ns.width % 2 or ns.height % 2:
        raise ValueError("width and height must be even")
    policy = read_real_selector_policy(ns.real_controls)
    targets = [float(x) for x in ns.targets.split(",") if x.strip()]
    rng = np.random.default_rng(ns.seed)
    sources = {scene: strict.generate_scene(scene, ns.height // 2, ns.width // 2, rng) for scene in strict.SCENES}
    ns.out_dir.mkdir(parents=True, exist_ok=True)
    max_windows_workers = 61 if os.name == "nt" else len(sources)
    jobs = max(1, min(ns.jobs, len(sources), max_windows_workers))

    if jobs <= 1:
        results = [
            evaluate_source_task(source_id, planes, targets, ns.levels, policy.cycle)
            for source_id, planes in sources.items()
        ]
    else:
        results_by_source: dict[str, tuple[str, list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]] = {}
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = {
                pool.submit(evaluate_source_task, source_id, planes, targets, ns.levels, policy.cycle): source_id
                for source_id, planes in sources.items()
            }
            for future in as_completed(futures):
                result = future.result()
                results_by_source[result[0]] = result
                print(f"finished {result[0]}", flush=True)
        results = [results_by_source[source_id] for source_id in sources]

    encode_rows: list[dict[str, object]] = []
    metric_rows: list[dict[str, object]] = []
    syntax_rows: list[dict[str, object]] = []
    for _source_id, enc, met, syn in results:
        encode_rows.extend(enc)
        metric_rows.extend(met)
        syntax_rows.extend(syn)

    write_csv(ns.out_dir / "encodes.csv", encode_rows)
    write_csv(ns.out_dir / "metrics.csv", metric_rows)
    write_csv(ns.out_dir / "syntax_summary.csv", syntax_rows)
    write_summaries(ns.out_dir, encode_rows, metric_rows)

    selector_total = sum(policy.histogram.values())
    manifest = {
        "kind": "Sony stream-fitted selector sensitivity evaluation",
        "seed": ns.seed,
        "width": ns.width,
        "height": ns.height,
        "levels": ns.levels,
        "targets_bpp": targets,
        "source_count": len(sources),
        "jobs": jobs,
        "real_controls": str(ns.real_controls),
        "selector_histogram": policy.histogram,
        "selector_mean": sum(k * v for k, v in policy.histogram.items()) / selector_total,
        "decoder_visible_only": True,
        "production_encoder_equivalence_claim": False,
        "changed_from_strict_canonical": "Sony selector assignment only; same transform, rate search, syntax cost model, sources, and metrics.",
        "row_counts": {
            "encodes": len(encode_rows),
            "metrics": len(metric_rows),
            "syntax_summary": len(syntax_rows),
        },
    }
    (ns.out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
