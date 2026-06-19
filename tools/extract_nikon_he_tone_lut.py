#!/usr/bin/env python3
"""Extract Nikon HE tone LUT breakpoints from LibRaw #826 source.

The current #826 branch stores the HE tone curve in
nikon_he_iqx_iqp_lut_data.h as 256 PWL breakpoints and materializes an
81792-entry table with integer linear interpolation. This tool reproduces that
materialization and writes both the raw i32 LUT and the 14-bit sample-domain
LUT used after the decoder's `(lut[idx] + 2) >> 2` step.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from pathlib import Path

import numpy as np


LUT_SIZE = 81792
BREAKPOINT_COUNT = 256


def default_header_path() -> Path:
    temp = Path(os.environ.get("TEMP", ""))
    return (
        temp
        / "libraw_pr826_nikon_he"
        / "src"
        / "decoders"
        / "nikon_he"
        / "nikon_he_iqx_iqp_lut_data.h"
    )


def parse_breakpoints(text: str) -> list[tuple[int, int]]:
    match = re.search(
        r"kIqxIqpBreakpoints\s*\[\s*256\s*\]\s*\[\s*2\s*\]\s*=\s*\{(.*?)\};",
        text,
        flags=re.S,
    )
    if not match:
        raise ValueError("could not find kIqxIqpBreakpoints[256][2] initializer")
    pairs = [
        (int(a), int(b))
        for a, b in re.findall(r"\{\s*(-?\d+)\s*,\s*(-?\d+)\s*\}", match.group(1))
    ]
    if len(pairs) != BREAKPOINT_COUNT:
        raise ValueError(f"expected {BREAKPOINT_COUNT} breakpoints, got {len(pairs)}")
    if pairs[0][0] != 0 or pairs[-1][0] != LUT_SIZE - 1:
        raise ValueError(f"unexpected endpoint x values: {pairs[0][0]}, {pairs[-1][0]}")
    for (xa, _ya), (xb, _yb) in zip(pairs, pairs[1:]):
        if xb <= xa:
            raise ValueError("breakpoint x values must be strictly increasing")
    return pairs


def materialize_i32_lut(breakpoints: list[tuple[int, int]]) -> np.ndarray:
    out = np.zeros(LUT_SIZE, dtype=np.int32)
    k = 0
    for i in range(LUT_SIZE):
        while k < BREAKPOINT_COUNT - 1 and breakpoints[k + 1][0] <= i:
            k += 1
        xa, ya = breakpoints[k]
        if k == BREAKPOINT_COUNT - 1:
            out[i] = ya
            continue
        xb, yb = breakpoints[k + 1]
        out[i] = ya if xb == xa else ya + (yb - ya) * (i - xa) // (xb - xa)
    return out


def i32_to_sample14(lut_i32: np.ndarray) -> np.ndarray:
    sample = (lut_i32.astype(np.int64) + 2) >> 2
    sample = np.clip(sample, 0, 16383)
    return sample.astype(np.uint16)


def write_breakpoints(path: Path, breakpoints: list[tuple[int, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(["index", "x_in", "y_out_i32", "sample14"])
        for i, (x, y) in enumerate(breakpoints):
            writer.writerow([i, x, y, max(0, min(16383, (y + 2) >> 2))])


def write_lut_tsv(path: Path, lut_i32: np.ndarray, lut_sample14: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(["code", "lut_i32", "sample14"])
        for code, (raw, sample) in enumerate(zip(lut_i32.tolist(), lut_sample14.tolist())):
            writer.writerow([code, raw, sample])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--header", type=Path, default=default_header_path())
    ap.add_argument("--out-dir", type=Path, default=Path("tools/data"))
    ns = ap.parse_args()

    text = ns.header.read_text(encoding="utf-8", errors="replace")
    breakpoints = parse_breakpoints(text)
    lut_i32 = materialize_i32_lut(breakpoints)
    lut_sample14 = i32_to_sample14(lut_i32)

    bp_path = ns.out_dir / "nikon_he_iqx_iqp_breakpoints.tsv"
    lut_i32_bin = ns.out_dir / "nikon_he_iqx_iqp_lut81792_i32.bin"
    lut_u16_bin = ns.out_dir / "nikon_he_iqx_iqp_lut81792_sample14_u16.bin"
    lut_tsv = ns.out_dir / "nikon_he_iqx_iqp_lut81792.tsv"
    summary_path = ns.out_dir / "nikon_he_iqx_iqp_lut_summary.json"

    write_breakpoints(bp_path, breakpoints)
    lut_i32.astype("<i4").tofile(lut_i32_bin)
    lut_sample14.astype("<u2").tofile(lut_u16_bin)
    write_lut_tsv(lut_tsv, lut_i32, lut_sample14)

    summary = {
        "source_header": str(ns.header),
        "breakpoint_count": len(breakpoints),
        "lut_size": int(lut_i32.size),
        "first_breakpoint": breakpoints[0],
        "last_breakpoint": breakpoints[-1],
        "lut_i32_min": int(lut_i32.min()),
        "lut_i32_max": int(lut_i32.max()),
        "sample14_min": int(lut_sample14.min()),
        "sample14_max": int(lut_sample14.max()),
        "monotone_i32": bool(np.all(np.diff(lut_i32) >= 0)),
        "monotone_sample14": bool(np.all(np.diff(lut_sample14.astype(np.int32)) >= 0)),
        "outputs": {
            "breakpoints": str(bp_path),
            "lut_i32_bin": str(lut_i32_bin),
            "lut_sample14_u16_bin": str(lut_u16_bin),
            "lut_tsv": str(lut_tsv),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
