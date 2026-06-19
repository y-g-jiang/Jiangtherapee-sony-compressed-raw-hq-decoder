#!/usr/bin/env python3
"""Probe Nikon HE/HE* NEF containers for LibRaw decoder evaluation.

The LibRaw #826 dispatch recognizes Nikon Z HE/HE* streams by TIFF
Compression=34713 plus a JPEG XS SOC/CAP marker (ff 10 ff 50) at the raw strip
offset. The PR decoder then skips a fixed 0x9b-byte prefix and looks at the
first precinct Bp byte to distinguish supported HE from unsupported HE*.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from llvc3_bitstream_probe import iter_tiff_ifds, scalar_or_first


NIKON_NEF_COMPRESSION = 34713
JPEG_XS_SOC_CAP = bytes([0xFF, 0x10, 0xFF, 0x50])
PRECINCT_OFFSET_FROM_STRIP = 0x9B


@dataclass
class NikonRawCandidate:
    ifd_offset: int
    make: str | None
    model: str | None
    width: int
    height: int
    bits_per_sample: Any
    compression: int
    photometric: int | None
    strip_offset: int
    strip_byte_count: int
    strip_first_32: str
    has_jpeg_xs_soc_cap: bool
    precinct_offset: int
    first_precinct_bp: int | None
    inferred_variant: str
    strip_bits_per_sample: float
    compression_ratio_vs_u16: float
    compression_ratio_vs_14bit_packed: float


def ascii_tag(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        data = bytes(int(v) & 0xFF for v in value)
    elif isinstance(value, bytes):
        data = value
    else:
        return str(value)
    return data.split(b"\x00", 1)[0].decode("ascii", "replace")


def variant_from_bp(has_marker: bool, bp: int | None) -> str:
    if not has_marker:
        return "not Nikon HE/HE* JPEG-XS marker"
    if bp in {4, 5}:
        return "HE (PR #826 supported path)"
    if bp in {1, 2, 3}:
        return "HE* (PR #826 explicit unsupported path)"
    return f"unknown JPEG-XS-like stream (Bp={bp})"


def probe(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    candidates: list[NikonRawCandidate] = []
    global_make: str | None = None
    global_model: str | None = None

    for off, tags in iter_tiff_ifds(data):
        if global_make is None:
            global_make = ascii_tag(tags.get(0x010F))
        if global_model is None:
            global_model = ascii_tag(tags.get(0x0110))
        if int(tags.get(0x0103, -1)) != NIKON_NEF_COMPRESSION:
            continue
        if 0x0111 not in tags or 0x0117 not in tags:
            continue

        width = int(tags.get(0x0100, 0))
        height = int(tags.get(0x0101, 0))
        strip_offset = scalar_or_first(tags[0x0111])
        strip_len = scalar_or_first(tags[0x0117])
        if width <= 0 or height <= 0 or strip_len <= 0:
            continue
        strip = data[strip_offset : strip_offset + strip_len]
        has_marker = strip[:4] == JPEG_XS_SOC_CAP
        precinct_pos = PRECINCT_OFFSET_FROM_STRIP
        bp = strip[precinct_pos + 3] if len(strip) > precinct_pos + 3 else None
        samples = max(1, width * height)

        candidates.append(
            NikonRawCandidate(
                ifd_offset=off,
                make=ascii_tag(tags.get(0x010F)) or global_make,
                model=ascii_tag(tags.get(0x0110)) or global_model,
                width=width,
                height=height,
                bits_per_sample=tags.get(0x0102),
                compression=int(tags.get(0x0103)),
                photometric=int(tags[0x0106]) if 0x0106 in tags else None,
                strip_offset=strip_offset,
                strip_byte_count=strip_len,
                strip_first_32=strip[:32].hex(" "),
                has_jpeg_xs_soc_cap=has_marker,
                precinct_offset=strip_offset + PRECINCT_OFFSET_FROM_STRIP,
                first_precinct_bp=bp,
                inferred_variant=variant_from_bp(has_marker, bp),
                strip_bits_per_sample=strip_len * 8 / samples,
                compression_ratio_vs_u16=(samples * 2) / strip_len if strip_len else 0.0,
                compression_ratio_vs_14bit_packed=(samples * 14 / 8) / strip_len if strip_len else 0.0,
            )
        )

    return {
        "input": str(path),
        "file_size": path.stat().st_size,
        "candidate_count": len(candidates),
        "raw_candidates": [asdict(c) for c in candidates],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", type=Path)
    ap.add_argument("--out", type=Path)
    ns = ap.parse_args()

    report = probe(ns.path)
    print(
        json.dumps(
            {
                "input": report["input"],
                "candidate_count": report["candidate_count"],
                "raw_candidates": [
                    {
                        "model": c["model"],
                        "size": f"{c['width']}x{c['height']}",
                        "strip_byte_count": c["strip_byte_count"],
                        "first_precinct_bp": c["first_precinct_bp"],
                        "inferred_variant": c["inferred_variant"],
                        "strip_bits_per_sample": round(c["strip_bits_per_sample"], 3),
                        "compression_ratio_vs_14bit_packed": round(c["compression_ratio_vs_14bit_packed"], 2),
                    }
                    for c in report["raw_candidates"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if ns.out:
        ns.out.parent.mkdir(parents=True, exist_ok=True)
        ns.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
