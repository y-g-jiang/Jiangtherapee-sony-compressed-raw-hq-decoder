#!/usr/bin/env python3
"""Compare native final-green trace rows with the pure LLVC3 reconstruction.

This is a narrow reverse-engineering helper for the ARW6 tile edge case where
the pure decoder matches native output in the image interior but not in the
top/bottom v0 rows. It consumes a Frida trace from probe_llvc_decode_sizes.py
with --deep-trace and line_helper_ab570/line_helper_aafd0 events enabled.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from llvc3_entropy import decode_packet_arrays, integrate_type1_coefficients
from llvc3_math import synthesize_llvc3_level_stride


def contiguous_segments(indices: list[int]) -> list[list[int]]:
    if not indices:
        return []
    out: list[list[int]] = []
    start = prev = indices[0]
    for value in indices[1:]:
        if value == prev + 1:
            prev = value
            continue
        out.append([start, prev])
        start = prev = value
    out.append([start, prev])
    return out


def segment_by_delta(matches: list[tuple[int, int | None]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    start_index = 0
    prev_delta: int | None | str = "unset"
    for i, row in matches:
        delta: int | None = None if row is None else row - i
        if prev_delta == "unset":
            start_index = i
            prev_delta = delta
            continue
        if delta != prev_delta:
            prev_match = matches[i - 1]
            segments.append(
                {
                    "event_start": start_index,
                    "event_end": prev_match[0],
                    "delta": prev_delta,
                }
            )
            start_index = i
            prev_delta = delta
    if matches:
        segments.append(
            {
                "event_start": start_index,
                "event_end": matches[-1][0],
                "delta": None if prev_delta == "unset" else prev_delta,
            }
        )
    return segments


def first_exact_row(head: list[int], arr: np.ndarray) -> int | None:
    if not head:
        return None
    probe = np.asarray(head, dtype=np.int32)
    width = probe.size
    if arr.shape[1] < width:
        return None
    hits = np.flatnonzero(np.all(arr[:, :width] == probe, axis=1))
    return int(hits[0]) if hits.size else None


def best_rows(head: list[int], arr: np.ndarray, limit: int = 6) -> list[dict[str, Any]]:
    if not head:
        return []
    probe = np.asarray(head, dtype=np.int32)
    width = probe.size
    scored: list[tuple[float, int, int, int]] = []
    for row in range(arr.shape[0]):
        diff = probe - arr[row, :width]
        ad = np.abs(diff)
        scored.append((float(ad.mean()), int(ad.max()), int(np.median(diff)), row))
    return [
        {"row": row, "mean_abs": mean_abs, "max_abs": max_abs, "median_diff": median_diff}
        for mean_abs, max_abs, median_diff, row in sorted(scored)[:limit]
    ]


def decode_green_and_final_detail(arw: Path, stream_index: int, coded_half_height: int) -> tuple[np.ndarray, np.ndarray]:
    low_rows = (coded_half_height + 7) // 8
    g0, _meta = decode_packet_arrays(arw, 0, 0, stream_index=stream_index)
    green = integrate_type1_coefficients(g0[0][:low_rows], 2048) - 2048
    for group, edge_rows in ((1, 0), (2, 1), (3, 2)):
        planes, _meta = decode_packet_arrays(arw, group, 0, stream_index=stream_index)
        green = synthesize_llvc3_level_stride(green, planes[0], planes[1], planes[2], edge_rows)
    g4, _meta = decode_packet_arrays(arw, 4, 0, stream_index=stream_index)
    return green.astype(np.int32), g4[0].astype(np.int32)


def load_trace(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    trace = json.loads(path.read_text(encoding="utf-8"))
    events = trace.get("traceEvents") or []
    ab570 = [ev for ev in events if ev.get("name") == "line_helper_ab570_enter"]
    aafd0 = [ev for ev in events if ev.get("name") == "line_helper_aafd0_enter"]
    return ab570, aafd0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("arw")
    ap.add_argument("--trace", required=True)
    ap.add_argument("--stream-index", type=int, default=0)
    ap.add_argument("--coded-half-height", type=int, default=0)
    ap.add_argument("--sample-rows", default="0,1,2,3,4,5,6,7,-8,-7,-6,-5,-4,-3,-2,-1")
    ap.add_argument("--out", default="")
    ns = ap.parse_args()

    ab570, aafd0 = load_trace(Path(ns.trace))
    if not ab570:
        raise ValueError("trace has no line_helper_ab570_enter events")
    if ns.coded_half_height:
        half_height = ns.coded_half_height
    elif aafd0:
        half_height = len(aafd0)
    else:
        raise ValueError("provide --coded-half-height when no aafd0 rows are present")

    green, g4 = decode_green_and_final_detail(Path(ns.arw), ns.stream_index, half_height)

    detail_matches: list[tuple[int, int | None]] = []
    selected_matches: list[tuple[int, int | None]] = []
    for i, ev in enumerate(ab570):
        detail_matches.append((i, first_exact_row(ev.get("detailHead") or [], green)))
        selected_matches.append((i, first_exact_row(ev.get("src1Head") or [], g4)))

    sample_indices: list[int] = []
    for text in ns.sample_rows.split(","):
        if not text:
            continue
        value = int(text)
        sample_indices.append(value if value >= 0 else len(ab570) + value)
    samples: dict[str, Any] = {}
    for i in sample_indices:
        if i < 0 or i >= len(ab570):
            continue
        ev = ab570[i]
        detail_head = ev.get("detailHead") or []
        src1_head = ev.get("src1Head") or []
        samples[str(i)] = {
            "event_i7c": (ev.get("obj") or {}).get("i7c"),
            "detail_exact_green_row": detail_matches[i][1],
            "selected_exact_g4_row": selected_matches[i][1],
            "detail_best_green_rows": best_rows(detail_head, green),
            "selected_best_g4_rows": best_rows(src1_head, g4),
            "detail_head": detail_head,
            "selected_head": src1_head,
        }

    no_detail_match = [i for i, row in detail_matches if row is None]
    no_selected_match = [i for i, row in selected_matches if row is None and (ab570[i].get("src1Head") or [])]
    result = {
        "input": str(ns.arw),
        "trace": str(ns.trace),
        "stream_index": ns.stream_index,
        "coded_half_height": half_height,
        "pure_green_shape": list(green.shape),
        "g4_shape": list(g4.shape),
        "native_ab570_events": len(ab570),
        "native_aafd0_rows": len(aafd0),
        "detail_green_match_segments": segment_by_delta(detail_matches),
        "selected_g4_match_segments": segment_by_delta(selected_matches),
        "detail_no_exact_segments": contiguous_segments(no_detail_match),
        "selected_no_exact_segments": contiguous_segments(no_selected_match),
        "samples": samples,
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if ns.out:
        out = Path(ns.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
