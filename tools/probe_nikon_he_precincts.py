#!/usr/bin/env python3
"""Summarize Nikon HE/HE* precinct Bp/Br rows from a NEF raw strip."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from llvc3_bitstream_probe import iter_tiff_ifds, scalar_or_first


NIKON_NEF_COMPRESSION = 34713
NIKON_CFA_PHOTOMETRIC = 32803
JPEG_XS_SOC_CAP = bytes([0xFF, 0x10, 0xFF, 0x50])
PRECINCT_OFFSET_FROM_STRIP = 0x9B


def find_nikon_he_raw(path: Path) -> tuple[dict[int, Any], bytes]:
    data = path.read_bytes()
    for _off, tags in iter_tiff_ifds(data):
        if int(tags.get(0x0103, -1)) != NIKON_NEF_COMPRESSION:
            continue
        if int(tags.get(0x0106, -1)) != NIKON_CFA_PHOTOMETRIC:
            continue
        if 0x0111 not in tags or 0x0117 not in tags:
            continue
        width = int(tags.get(0x0100, 0))
        height = int(tags.get(0x0101, 0))
        strip_offset = scalar_or_first(tags[0x0111])
        strip_len = scalar_or_first(tags[0x0117])
        if width <= 0 or height <= 0 or strip_len <= 0:
            continue
        return tags, data[strip_offset : strip_offset + strip_len]
    raise ValueError(f"{path}: Nikon HE/HE* raw strip not found")


def walk_precincts(strip: bytes, image_height: int) -> list[dict[str, int]]:
    n_tiles = (image_height + 63) // 64
    max_precincts = n_tiles * 16 + 2
    pos = PRECINCT_OFFSET_FROM_STRIP
    out: list[dict[str, int]] = []
    for idx in range(max_precincts):
        if pos + 5 > len(strip):
            break
        size_minus_prefix = (strip[pos] << 16) | (strip[pos + 1] << 8) | strip[pos + 2]
        bp = strip[pos + 3]
        br = strip[pos + 4]
        if size_minus_prefix <= 0:
            break
        full_size = size_minus_prefix + 12
        if pos + full_size > len(strip):
            break
        out.append({
            "index": idx,
            "offset": pos,
            "size_minus_prefix": size_minus_prefix,
            "full_size": full_size,
            "Bp": bp,
            "Br": br,
        })
        pos += full_size
        if (idx & 0xF) == 15 and pos + 6 <= len(strip):
            pos += 6
    return out


def infer_variant(strip_bits_per_sample: float, counts: Counter[tuple[int, int]]) -> str:
    if 2.7 <= strip_bits_per_sample <= 3.3:
        return "HE"
    if 4.7 <= strip_bits_per_sample <= 5.3:
        return "HE*"
    if counts:
        bp_values = sorted({bp for bp, _br in counts})
        return f"unknown JPEG-XS stream bpp={strip_bits_per_sample:.3f}, Bp set {bp_values}"
    return "unknown"


def summarize(path: Path) -> dict[str, Any]:
    tags, strip = find_nikon_he_raw(path)
    width = int(tags[0x0100])
    height = int(tags[0x0101])
    has_marker = strip[:4] == JPEG_XS_SOC_CAP
    marker_hits = []
    start = 0
    while True:
        hit = strip.find(JPEG_XS_SOC_CAP, start)
        if hit < 0:
            break
        marker_hits.append(hit)
        start = hit + 1
    if not has_marker:
        return {
            "input": str(path),
            "width": width,
            "height": height,
            "strip_byte_count": len(strip),
            "strip_first_32": strip[:32].hex(" "),
            "has_jpeg_xs_soc_cap_at_strip_start": False,
            "jpeg_xs_marker_hits_in_strip": marker_hits,
            "precinct_count": 0,
            "first_precinct": None,
            "last_precinct": None,
            "bp_counts": {},
            "bp_br_counts": [],
            "inferred_variant": "not Nikon HE/HE* JPEG-XS stream",
        }
    precincts = walk_precincts(strip, height)
    counts = Counter((p["Bp"], p["Br"]) for p in precincts)
    samples = max(1, width * height)
    strip_bpp = len(strip) * 8 / samples
    return {
        "input": str(path),
        "width": width,
        "height": height,
        "strip_byte_count": len(strip),
        "strip_bits_per_sample": strip_bpp,
        "strip_first_32": strip[:32].hex(" "),
        "has_jpeg_xs_soc_cap_at_strip_start": has_marker,
        "jpeg_xs_marker_hits_in_strip": marker_hits,
        "precinct_count": len(precincts),
        "first_precinct": precincts[0] if precincts else None,
        "last_precinct": precincts[-1] if precincts else None,
        "bp_counts": dict(sorted(Counter(p["Bp"] for p in precincts).items())),
        "bp_br_counts": [
            {"Bp": bp, "Br": br, "count": count}
            for (bp, br), count in sorted(counts.items())
        ],
        "inferred_variant": infer_variant(strip_bpp, counts),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--out", type=Path)
    ns = ap.parse_args()

    reports = [summarize(path) for path in ns.paths]
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    if ns.out:
        ns.out.parent.mkdir(parents=True, exist_ok=True)
        ns.out.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
