#!/usr/bin/env python3
"""Convenience wrapper around Sony's own LLVC3 decoder path.

Fallback path while the pure Python decoder is still being checked: extract the
ARW raw strip, let Frida drive LLVCDecoder, then recombine the three dumped
planes into RGGB outputs.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from llvc3_bitstream_probe import find_raw_subifd
from recombine_llvc_planes import read_plane, recombine, robust_preview, stats, write_dng


def run_native_probe(arw: Path, strip_path: Path, out_dir: Path, width: int, height: int) -> tuple[Path, Path]:
    prefix = out_dir / f"{arw.stem}_llvc3_native_v0"
    report = out_dir / f"{arw.stem}_llvc3_native_probe.json"
    dump_bytes = width * (height // 2) * 2
    cmd = [
        sys.executable,
        str(Path(__file__).with_name("probe_llvc_decode_sizes.py")),
        "--raw",
        str(strip_path),
        "--variant",
        "0",
        "--line-limit",
        str(height // 2),
        "--batch-lines",
        "1",
        "--kind",
        "2",
        "--no-terminate",
        "--dump-prefix",
        str(prefix),
        "--dump-bytes",
        str(dump_bytes),
        "--out",
        str(report),
    ]
    subprocess.run(cmd, check=True)
    return prefix, report


def write_outputs(arw: Path, prefix: Path, out_dir: Path, width: int, height: int) -> dict[str, object]:
    half_h = height // 2
    c0 = read_plane(Path(f"{prefix}_v0_c0.bin"), (half_h, width))
    c1 = read_plane(Path(f"{prefix}_v0_c1.bin"), (half_h, width // 2))
    c2 = read_plane(Path(f"{prefix}_v0_c2.bin"), (half_h, width // 2))
    raw = recombine(c0, c1, c2)

    raw_path = out_dir / f"{arw.stem}_llvc3_native_rggb_{width}x{height}_u16.raw"
    raw.tofile(raw_path)

    preview_path = out_dir / f"{arw.stem}_llvc3_native_rggb_preview.png"
    robust_preview(raw, 1024, 16383).save(preview_path)

    tiff1024 = out_dir / f"{arw.stem}_llvc3_native_rggb_bl1024_wl16383.tiff"
    write_dng(tiff1024, raw, 1024, 16383)

    shifted = np.clip(raw.astype(np.int32) - 512, 0, 16383).astype(np.uint16)
    tiff512 = out_dir / f"{arw.stem}_llvc3_native_rggb_bl512_wl15871_shifted.tiff"
    write_dng(tiff512, shifted, 512, 15871)

    return {
        "planes": {"c0": stats(c0), "c1": stats(c1), "c2": stats(c2)},
        "raw": stats(raw),
        "outputs": {
            "raw_u16": str(raw_path),
            "preview_png": str(preview_path),
            "dng_like_bl1024_tiff": str(tiff1024),
            "dng_like_bl512_shifted_tiff": str(tiff512),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("arw", nargs="?", default="DSC00089.ARW")
    ap.add_argument("--out-dir", default="out/native_decode")
    ap.add_argument("--reuse-strip", action="store_true", help="reuse an existing extracted raw strip if present")
    ap.add_argument("--skip-native", action="store_true", help="only recombine already dumped native planes")
    ns = ap.parse_args()

    arw = Path(ns.arw)
    out_dir = Path(ns.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_info, strip = find_raw_subifd(arw)
    strip_path = out_dir / f"{arw.stem}_raw_strip.bin"
    if not ns.reuse_strip or not strip_path.exists():
        strip_path.write_bytes(strip)

    prefix = out_dir / f"{arw.stem}_llvc3_native_v0"
    report = out_dir / f"{arw.stem}_llvc3_native_probe.json"
    if not ns.skip_native:
        prefix, report = run_native_probe(arw, strip_path, out_dir, raw_info.width, raw_info.height)

    result = {
        "input": str(arw),
        "raw_subifd": raw_info.__dict__,
        "native_probe_report": str(report),
        **write_outputs(arw, prefix, out_dir, raw_info.width, raw_info.height),
    }
    sidecar = out_dir / f"{arw.stem}_llvc3_native_decode_summary.json"
    sidecar.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
